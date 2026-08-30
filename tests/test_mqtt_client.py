import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest

from thermoctl.config import Settings
from thermoctl.integrations.mqtt import client as client_modul
from thermoctl.integrations.mqtt.client import MqttClient


@dataclass
class Message:
    topic: str
    payload: bytes


class Schleifenende(BaseException):
    """Ends the deliberately endless receive loop from outside its error handling."""


class MessageStream:
    def __init__(self, messages: list[Message], errors: Exception | None = None) -> None:
        self._messages = messages
        self._errors = errors

    async def __aiter__(self) -> AsyncIterator[Message]:
        for message in self._messages:
            yield message
        if self._errors is not None:
            raise self._errors


class FalscherClient:
    instanzen: list[FalscherClient] = []
    stroeme: list[MessageStream] = []

    def __init__(self, **argumente: Any) -> None:
        self.argumente = argumente
        self.abonniert: list[str] = []
        self.published: list[tuple[str, str]] = []
        self.retained: list[bool] = []
        self.messages = self.stroeme.pop(0) if self.stroeme else MessageStream([])
        self.instanzen.append(self)

    async def __aenter__(self) -> FalscherClient:
        return self

    async def __aexit__(self, *argumente: object) -> None:
        return None

    async def subscribe(self, topic: str) -> None:
        self.abonniert.append(topic)

    async def publish(self, topic: str, payload: str, retain: bool = False) -> None:
        self.published.append((topic, payload))
        self.retained.append(retain)


@pytest.fixture
def mqtt_settings() -> Settings:
    return Settings(
        _env_file=None,
        database_url="sqlite://",
        secret_key="m" * 32,
        mqtt_enabled=True,
        mqtt_host="mqtt.example.invalid",
        mqtt_username="leser",
        mqtt_password="auffaelliges-mqtt-testpasswort",
        mqtt_base_topic="testbasis",
    )


@pytest.fixture(autouse=True)
def falscher_client(monkeypatch: pytest.MonkeyPatch) -> None:
    FalscherClient.instanzen = []
    FalscherClient.stroeme = []
    monkeypatch.setattr(client_modul.aiomqtt, "Client", FalscherClient)


@pytest.mark.anyio
async def test_subscriptions_and_a_message_are_delivered(
    mqtt_settings: Settings,
) -> None:
    FalscherClient.stroeme = [
        MessageStream([Message("testbasis/Sensor", b"wert")])
    ]
    empfangen: list[tuple[str, bytes]] = []

    async def handler(topic: str, payload: bytes) -> None:
        empfangen.append((topic, payload))
        raise Schleifenende

    with pytest.raises(Schleifenende):
        await MqttClient(mqtt_settings, handler).run()

    assert FalscherClient.instanzen[0].abonniert == [
        "testbasis/bridge/devices",
        "testbasis/bridge/state",
        "testbasis/+",
        "testbasis/+/availability",
    ]
    assert empfangen == [("testbasis/Sensor", b"wert")]


@pytest.mark.anyio
async def test_a_handler_error_does_not_stop_the_next_message(
    mqtt_settings: Settings,
) -> None:
    FalscherClient.stroeme = [
        MessageStream(
            [Message("testbasis/Erste", b"kaputt"), Message("testbasis/Zweite", b"ok")]
        )
    ]
    empfangen: list[str] = []

    async def handler(topic: str, _payload: bytes) -> None:
        empfangen.append(topic)
        if topic.endswith("Erste"):
            raise ValueError("kaputte Nutzlast")
        raise Schleifenende

    with pytest.raises(Schleifenende):
        await MqttClient(mqtt_settings, handler).run()

    assert empfangen == ["testbasis/Erste", "testbasis/Zweite"]


@pytest.mark.anyio
async def test_verbindungsabbruch_verwendet_wachsenden_abstand(
    mqtt_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    FalscherClient.stroeme = [
        MessageStream([], ConnectionError("eins")),
        MessageStream([], ConnectionError("zwei")),
        MessageStream([], ConnectionError("drei")),
    ]
    waited: list[float] = []

    async def wait(seconds: float) -> None:
        waited.append(seconds)
        if len(waited) == 3:
            raise Schleifenende

    monkeypatch.setattr(client_modul, "schlafen", wait)

    async def handler(_topic: str, _payload: bytes) -> None:
        return None

    with pytest.raises(Schleifenende):
        await MqttClient(mqtt_settings, handler).run()

    assert waited == [1.0, 2.0, 4.0]
    assert len(FalscherClient.instanzen) == 3


@pytest.mark.anyio
async def test_tls_beruecksichtigt_ca_zertifikat(
    mqtt_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    mqtt_settings.mqtt_tls = True
    mqtt_settings.mqtt_ca_cert = "/konfiguration/ca.pem"
    tls_context = object()
    ca_pathe: list[str | None] = []

    def context_erzeugen(*, cafile: str | None) -> object:
        ca_pathe.append(cafile)
        return tls_context

    async def abbrechen(_seconds: float) -> None:
        raise Schleifenende

    monkeypatch.setattr(client_modul.ssl, "create_default_context", context_erzeugen)
    monkeypatch.setattr(client_modul, "schlafen", abbrechen)
    FalscherClient.stroeme = [MessageStream([], ConnectionError("getrennt"))]

    with pytest.raises(Schleifenende):
        await MqttClient(mqtt_settings, _leerer_handler).run()

    assert ca_pathe == ["/konfiguration/ca.pem"]
    assert FalscherClient.instanzen[0].argumente["tls_context"] is tls_context


@pytest.mark.anyio
async def test_no_switching_command_in_dry_run(mqtt_settings: Settings) -> None:
    client = MqttClient(mqtt_settings, _leerer_handler)
    falscher_client = FalscherClient()
    client._client = falscher_client

    result = await client.publishing("testbasis/Aktor/set", "ON", switches=True)

    assert result is False
    assert falscher_client.published == []


@pytest.mark.anyio
async def test_a_state_message_goes_out_in_dry_run_too(
    mqtt_settings: Settings,
) -> None:
    """The bolt applies to switching, not to reporting.

    Up to this point it blocked every publication. That was right as long as nothing
    was sent at all -- but it made dry run untestable: the Home Assistant integration
    could only be tried out after the installation was armed, which is exactly the
    moment when a mistake would no longer be consequence-free.
    """
    client = MqttClient(mqtt_settings, _leerer_handler)
    falscher_client = FalscherClient()
    client._client = falscher_client

    result = await client.publishing(
        "thermoctl/zones/1/state/current_temperature", "21.5", switches=False
    )

    assert result is True
    assert falscher_client.published == [
        ("thermoctl/zones/1/state/current_temperature", "21.5")
    ]


@pytest.mark.anyio
async def test_the_password_shows_up_in_no_log_line(
    mqtt_settings: Settings,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FalscherClient.stroeme = [MessageStream([], ConnectionError("getrennt"))]

    async def abbrechen(_seconds: float) -> None:
        raise Schleifenende

    monkeypatch.setattr(client_modul, "schlafen", abbrechen)
    caplog.set_level(logging.INFO)
    with pytest.raises(Schleifenende):
        await MqttClient(mqtt_settings, _leerer_handler).run()

    assert "auffaelliges-mqtt-testpasswort" not in caplog.text


@pytest.mark.anyio
async def test_the_backoff_falls_back_after_a_successful_connection(
    mqtt_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Otherwise the wait time would grow monotonically over the service's lifetime.

    A connection that drops once after running for days would then wait a minute
    instead of a second -- even though there is no series of faults at all. For a
    heating system in winter, that is the difference between a gap and a pause.
    """
    waited: list[float] = []
    FalscherClient.instanzen.clear()

    async def mitschreiben(seconds: float) -> None:
        waited.append(seconds)
        if len(waited) == 4:
            raise Schleifenende

    monkeypatch.setattr(client_modul, "schlafen", mitschreiben)
    # Three failed attempts in a row (1, 2, 4 s), then a connection that delivers
    # messages and only afterwards drops -- the fourth interval must be 1 s again.
    FalscherClient.stroeme = [
        MessageStream([], ConnectionError("getrennt")),
        MessageStream([], ConnectionError("getrennt")),
        MessageStream([], ConnectionError("getrennt")),
        MessageStream([Message("testbasis/Geraet", b"{}")], ConnectionError("getrennt")),
    ]

    with pytest.raises(Schleifenende):
        await MqttClient(mqtt_settings, _leerer_handler).run()

    assert waited == [1.0, 2.0, 4.0, 1.0]


async def _leerer_handler(_topic: str, _payload: bytes) -> None:
    return None


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_no_publish_even_when_the_caller_asks_for_it(
    mqtt_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The second bolt: `schaltet=True` alone is not enough.

    Behind the valve hangs an inhabited home. A single caller that mistakenly sends
    a message as a switching command must not be sufficient -- the client must also
    have been built with `schalten_erlaubt=True`.
    """
    FalscherClient.instanzen.clear()
    monkeypatch.setattr(client_modul.aiomqtt, "Client", FalscherClient)
    kunde = MqttClient(mqtt_settings, _kein_handler)
    falscher = FalscherClient()
    kunde._client = falscher  # type: ignore[assignment]

    assert (
        await kunde.publishing("zigbee2mqtt/irgendwas/set", "{}", switches=True)
        is False
    )
    assert falscher.published == []


async def _kein_handler(topic: str, payload: bytes) -> None:
    raise AssertionError("this handler must never be called")


@pytest.mark.anyio
async def test_disabled_mqtt_opens_no_connection(
    mqtt_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default value. Neither the test suite nor a freshly built container may
    try to connect anywhere."""
    mqtt_settings.mqtt_enabled = False
    FalscherClient.instanzen.clear()
    monkeypatch.setattr(client_modul.aiomqtt, "Client", FalscherClient)

    await MqttClient(mqtt_settings, _leerer_handler).run()

    assert FalscherClient.instanzen == []


@pytest.mark.anyio
async def test_a_missing_host_is_named_explicitly(mqtt_settings: Settings) -> None:
    """A misconfiguration should say which setting is missing -- not 'NoneType'."""
    mqtt_settings.mqtt_host = None
    with pytest.raises(ValueError, match="MQTT_HOST"):
        MqttClient(mqtt_settings, _leerer_handler)._newer_client()


@pytest.mark.anyio
async def test_a_client_built_armed_really_publishes(
    mqtt_settings: Settings,
) -> None:
    """The counter-proof to dry run: the path works, it is just locked.

    Without this test, the suite only proved that nothing is sent -- even if sending
    had never been implemented at all. Subproject 4 depends on this.
    """
    kunde = MqttClient(mqtt_settings, _leerer_handler, switching_allowed=True)
    falscher = FalscherClient()
    kunde._client = falscher  # type: ignore[assignment]

    assert await kunde.publishing(
        "testbasis/Ventil/set", '{"state": "ON"}', switches=True
    )
    assert falscher.published == [("testbasis/Ventil/set", '{"state": "ON"}')]


@pytest.mark.anyio
async def test_nothing_is_published_without_a_connection(mqtt_settings: Settings) -> None:
    kunde = MqttClient(mqtt_settings, _leerer_handler, switching_allowed=True)
    assert await kunde.publishing("testbasis/Ventil/set", "{}", switches=True) is False


@pytest.mark.anyio
async def test_schlafen_wartet_wirklich(monkeypatch: pytest.MonkeyPatch) -> None:
    """The function exists so tests can replace it -- nobody else checks that it
    really waits in production."""
    waited: list[float] = []

    async def gefaelscht(seconds: float) -> None:
        waited.append(seconds)

    monkeypatch.setattr(client_modul.asyncio, "sleep", gefaelscht)
    await client_modul.schlafen(2.5)
    assert waited == [2.5]


@pytest.mark.anyio
async def test_a_client_built_armed_also_sends_state(
    mqtt_settings: Settings,
) -> None:
    """This used to say the opposite: a `scharf=False` was also rejected by a client
    built armed. That was the version in which `scharf` meant two things at once --
    the caller's intent and the client's permission. Now `schaltet` says what the
    message causes, and a state message causes nothing."""
    kunde = MqttClient(mqtt_settings, _leerer_handler, switching_allowed=True)
    falscher = FalscherClient()
    kunde._client = falscher  # type: ignore[assignment]

    assert await kunde.publishing("thermoctl/availability", "online", switches=False)
    assert falscher.published == [("thermoctl/availability", "online")]
