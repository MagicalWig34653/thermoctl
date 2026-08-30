"""Empfangender MQTT-Client mit dauerhafter Wiederverbindung."""

import asyncio
import logging
import ssl
from collections.abc import Awaitable, Callable

import aiomqtt

from thermoctl.config import Settings
from thermoctl.integrations.mqtt.zigbee2mqtt import abonnements

log = logging.getLogger(__name__)


async def schlafen(sekunden: float) -> None:
    """Wartet vor dem naechsten Verbindungsversuch."""
    await asyncio.sleep(sekunden)


class MqttClient:
    def __init__(
        self,
        settings: Settings,
        handler: Callable[[str, bytes], Awaitable[None]],
        *,
        schalten_erlaubt: bool = False,
        zusatz_abonnements: list[str] | None = None,
    ) -> None:
        """`schalten_erlaubt` ist die harte Grenze des Trockenlaufs -- fuer das Schalten.

        Sie gilt fuer Nachrichten, die ein Ventil bewegen (`schaltet=True`). Solange sie
        False ist, geht keine davon hinaus, auch wenn ein Aufrufer es verlangt. Zwei
        Riegel statt einem, weil hinter dem Ventil eine bewohnte Wohnung haengt und ein
        einzelner vergessener Aufrufer sonst genuegt: dieser hier beim Bau des Clients,
        der zweite bei jedem Aufruf in `integrations/aktoren.py`.

        **Nicht betroffen sind Zustandsmeldungen und die Home-Assistant-Anmeldung.**
        Bis hierher sperrte dieser Riegel jede Veroeffentlichung, auch die. Das war
        richtig, solange gar nichts gesendet wurde -- es machte aber den Trockenlauf
        unpruefbar: Man konnte die Anbindung erst ausprobieren, nachdem man die Anlage
        scharf geschaltet hatte, also genau dann nicht mehr, wenn ein Fehler noch
        folgenlos gewesen waere. Eine Zustandsmeldung bewegt nichts.
        """
        self._settings = settings
        self._handler = handler
        self._schalten_erlaubt = schalten_erlaubt
        # Ueber die Zigbee2MQTT-Abonnements hinaus: die eigenen Befehls-Topics. Sie
        # stehen nicht in `abonnements()`, weil das die vier bewusst eng begrenzten
        # Zigbee2MQTT-Themen liefert und nichts anderes.
        self._zusatz_abonnements = list(zusatz_abonnements or [])
        self._client: aiomqtt.Client | None = None

    def _tls_context(self) -> ssl.SSLContext | None:
        if not self._settings.mqtt_tls:
            return None
        return ssl.create_default_context(cafile=self._settings.mqtt_ca_cert)

    def _neuer_client(self) -> aiomqtt.Client:
        host = self._settings.mqtt_host
        if host is None:
            raise ValueError("MQTT ist aktiviert, aber THERMOCTL_MQTT_HOST fehlt")
        passwort = (
            self._settings.mqtt_password.get_secret_value()
            if self._settings.mqtt_password is not None
            else None
        )
        return aiomqtt.Client(
            hostname=host,
            port=self._settings.mqtt_port,
            username=self._settings.mqtt_username,
            password=passwort,
            identifier=self._settings.mqtt_client_id,
            tls_context=self._tls_context(),
        )

    async def laufen(self) -> None:
        """Empfaengt Nachrichten und verbindet nach Fehlern unbegrenzt neu."""
        if not self._settings.mqtt_enabled:
            log.info("MQTT-Empfang ist deaktiviert")
            return

        abstand = 1.0
        while True:
            try:
                client = self._neuer_client()
                async with client:
                    self._client = client
                    log.info(
                        "MQTT-Verbindung hergestellt",
                        extra={"host": self._settings.mqtt_host, "port": self._settings.mqtt_port},
                    )

                    for topic in [
                        *abonnements(self._settings.mqtt_base_topic),
                        *self._zusatz_abonnements,
                    ]:
                        await client.subscribe(topic)
                    async for nachricht in client.messages:
                        # Der Abstand faellt erst zurueck, wenn wirklich etwas ankam,
                        # nicht schon beim Verbindungsaufbau. Sonst waere ein Broker,
                        # der annimmt und sofort wieder trennt, eine Endlosschleife
                        # ohne Pause. Umgekehrt darf der Abstand auch nicht ueber die
                        # Lebensdauer des Dienstes monoton weiterwachsen: Eine
                        # Verbindung, die nach Tagen einmal abreisst, soll nach einer
                        # Sekunde wiederkommen, nicht nach einer Minute.
                        #
                        # Dass ueberhaupt etwas ankommt, ist verlaesslich: Auf
                        # `bridge/devices` liegt eine retained-Nachricht, die bei jeder
                        # Verbindung sofort zugestellt wird.
                        abstand = 1.0
                        try:
                            await self._handler(str(nachricht.topic), bytes(nachricht.payload))
                        except Exception:
                            log.exception(
                                "MQTT-Nachricht konnte nicht verarbeitet werden",
                                extra={"topic": str(nachricht.topic)},
                            )
            except Exception:
                log.exception(
                    "MQTT-Verbindung verloren; neuer Versuch folgt",
                    extra={
                        "host": self._settings.mqtt_host,
                        "port": self._settings.mqtt_port,
                        "wartezeit_s": abstand,
                    },
                )
            finally:
                self._client = None

            await schlafen(abstand)
            abstand = min(abstand * 2, 60.0)

    async def veroeffentlichen(
        self, topic: str, nutzlast: str, *, schaltet: bool
    ) -> bool:
        """Sendet eine Nachricht. `schaltet=True` verlangt zusaetzlich den Riegel.

        `schaltet` beschreibt, was die Nachricht **bewirkt**, nicht wie dringend der
        Aufrufer sie meint: Ein Ventilbefehl bewegt etwas, eine Zustandsmeldung nicht.
        Frueher hiess der Parameter `scharf` und bedeutete beides zugleich -- der
        Aufrufer musste ihn setzen, *und* der Client musste scharf gebaut sein. Dabei
        fiel die Zustandsmeldung unter dieselbe Sperre wie der Ventilbefehl.
        """
        if schaltet and not self._schalten_erlaubt:
            log.warning(
                "Trockenlauf: Schaltbefehl abgewiesen, obwohl der Aufrufer ihn "
                "verlangt hat",
                extra={"topic": topic},
            )
            return False
        if self._client is None:
            log.error("MQTT-Nachricht kann ohne Verbindung nicht veroeffentlicht werden")
            return False
        await self._client.publish(topic, nutzlast)
        return True
