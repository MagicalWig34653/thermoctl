"""Receiving MQTT client with persistent reconnection."""

import asyncio
import logging
import ssl
from collections.abc import Awaitable, Callable

import aiomqtt

from thermoctl.config import Settings
from thermoctl.integrations.mqtt.zigbee2mqtt import subscriptions

log = logging.getLogger(__name__)


async def sleep(seconds: float) -> None:
    """Waits before the next connection attempt."""
    await asyncio.sleep(seconds)


class MqttClient:
    def __init__(
        self,
        settings: Settings,
        handler: Callable[[str, bytes], Awaitable[None]],
        *,
        switching_allowed: bool = False,
        extra_subscriptions: list[str] | None = None,
    ) -> None:
        """`switching_allowed` is the hard limit of the dry run -- for switching.

        It applies to messages that move a valve (`switches=True`). As long as it is
        False, none of those go out, even if a caller demands it. Two bolts instead of
        one, because an inhabited apartment hangs behind the valve, and a single
        forgotten caller would otherwise be enough: this one here when the client is
        built, the second one on every call in `integrations/aktoren.py`.

        **State messages and the Home Assistant registration are not affected.** Up
        until now this bolt blocked every publication, including those. That was
        correct as long as nothing at all was sent -- but it made the dry run
        unverifiable: you could only try out the integration after arming the plant,
        i.e. exactly when an error would no longer have been harmless. A state message
        doesn't move anything.
        """
        self._settings = settings
        self._handler = handler
        self._switching_allowed = switching_allowed
        # Beyond the Zigbee2MQTT subscriptions: our own command topics. They are not
        # in `abonnements()`, because that delivers the four deliberately narrow
        # Zigbee2MQTT topics and nothing else.
        self._zusatz_abonnements = list(extra_subscriptions or [])
        self._client: aiomqtt.Client | None = None

    def _tls_context(self) -> ssl.SSLContext | None:
        if not self._settings.mqtt_tls:
            return None
        return ssl.create_default_context(cafile=self._settings.mqtt_ca_cert)

    def _newer_client(self) -> aiomqtt.Client:
        host = self._settings.mqtt_host
        if host is None:
            raise ValueError("MQTT ist aktiviert, aber THERMOCTL_MQTT_HOST fehlt")
        password = (
            self._settings.mqtt_password.get_secret_value()
            if self._settings.mqtt_password is not None
            else None
        )
        return aiomqtt.Client(
            hostname=host,
            port=self._settings.mqtt_port,
            username=self._settings.mqtt_username,
            password=password,
            identifier=self._settings.mqtt_client_id,
            tls_context=self._tls_context(),
        )

    async def run(self) -> None:
        """Receives messages and reconnects indefinitely after errors."""
        if not self._settings.mqtt_enabled:
            log.info("MQTT-Empfang ist deaktiviert")
            return

        interval = 1.0
        while True:
            try:
                client = self._newer_client()
                async with client:
                    self._client = client
                    log.info(
                        "MQTT-Verbindung hergestellt",
                        extra={"host": self._settings.mqtt_host, "port": self._settings.mqtt_port},
                    )

                    for topic in [
                        *subscriptions(self._settings.mqtt_base_topic),
                        *self._zusatz_abonnements,
                    ]:
                        await client.subscribe(topic)
                    async for message in client.messages:
                        # The interval only resets once something actually arrives, not
                        # already on connection setup. Otherwise a broker that accepts
                        # and immediately disconnects again would become an infinite
                        # loop with no pause. Conversely, the interval must also not
                        # keep growing monotonically over the service's lifetime: a
                        # connection that drops once after days should come back after
                        # a second, not after a minute.
                        #
                        # That something arrives at all is reliable: `bridge/devices`
                        # carries a retained message that gets delivered immediately on
                        # every connection.
                        interval = 1.0
                        try:
                            await self._handler(str(message.topic), bytes(message.payload))
                        except Exception:
                            log.exception(
                                "MQTT-Nachricht konnte nicht verarbeitet werden",
                                extra={"topic": str(message.topic)},
                            )
            except Exception:
                log.exception(
                    "MQTT-Verbindung verloren; neuer Versuch folgt",
                    extra={
                        "host": self._settings.mqtt_host,
                        "port": self._settings.mqtt_port,
                        "wartezeit_s": interval,
                    },
                )
            finally:
                self._client = None

            await sleep(interval)
            interval = min(interval * 2, 60.0)

    async def publishing(
        self, topic: str, payload: str, *, switches: bool, retained: bool = False
    ) -> bool:
        """Sends a message. `switches=True` additionally requires the bolt.

        `switches` describes what the message **does**, not how urgently the caller
        means it: a valve command moves something, a state message doesn't. The
        parameter used to be called `scharf` and meant both at once -- the caller had
        to set it, *and* the client had to be built armed. That put the state message
        under the same lock as the valve command.
        """
        if switches and not self._switching_allowed:
            log.warning(
                "Trockenlauf: Schaltbefehl abgewiesen, obwohl der Aufrufer ihn "
                "verlangt hat",
                extra={"topic": topic},
            )
            return False
        if self._client is None:
            log.error("MQTT-Nachricht kann ohne Verbindung nicht veroeffentlicht werden")
            return False
        # `behalten` belongs on the publish call, not in the discovery payload: the
        # `retain` key there means "send *commands* with the retain flag" in Home
        # Assistant -- a retained command would be redelivered and re-executed on
        # every reconnect. Retain belongs on registration and state: without it, Home
        # Assistant shows an empty card after a restart until this service sends
        # something the next time.
        await self._client.publish(topic, payload, retain=retained)
        return True
