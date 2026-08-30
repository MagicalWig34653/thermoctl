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
        """`schalten_erlaubt` ist die harte Grenze des Trockenlaufs.

        Solange sie False ist, veroeffentlicht dieser Client nichts — auch dann nicht,
        wenn ein Aufrufer es mit `scharf=True` ausdruecklich verlangt. In Teilprojekt 2
        setzt sie niemand auf True; erst Teilprojekt 4 leitet sie aus
        `setting.control_armed` ab. Zwei Riegel statt einem, weil hinter dem Ventil eine
        bewohnte Wohnung haengt und ein einzelner vergessener Aufrufer sonst genuegt.
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
        self, topic: str, nutzlast: str, *, scharf: bool
    ) -> bool:
        """Sendet nur, wenn der Aufrufer es verlangt UND der Client scharf gebaut wurde."""
        if not self._schalten_erlaubt:
            log.warning(
                "Trockenlauf: Veroeffentlichung abgewiesen, obwohl der Aufrufer sie "
                "verlangt hat",
                extra={"topic": topic, "scharf_verlangt": scharf},
            )
            return False
        if not scharf:
            log.info(
                "Trockenlauf: MQTT-Nachricht wird nicht veroeffentlicht",
                extra={"topic": topic, "nutzlast": nutzlast},
            )
            return False
        if self._client is None:
            log.error("MQTT-Nachricht kann ohne Verbindung nicht veroeffentlicht werden")
            return False
        await self._client.publish(topic, nutzlast)
        return True
