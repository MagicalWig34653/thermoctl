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
    """Beendet die absichtlich endlose Empfangsschleife ausserhalb ihrer Fehlerfaenge."""


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
        self.behalten: list[bool] = []
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
        self.behalten.append(retain)


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
async def test_abonnements_und_nachricht_werden_zugestellt(
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
async def test_handlerfehler_stoppt_naechste_nachricht_nicht(
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
async def test_kein_schaltbefehl_im_trockenlauf(mqtt_settings: Settings) -> None:
    client = MqttClient(mqtt_settings, _leerer_handler)
    falscher_client = FalscherClient()
    client._client = falscher_client

    result = await client.publishing("testbasis/Aktor/set", "ON", switches=True)

    assert result is False
    assert falscher_client.published == []


@pytest.mark.anyio
async def test_zustandsmeldung_geht_auch_im_trockenlauf_hinaus(
    mqtt_settings: Settings,
) -> None:
    """Der Riegel gilt dem Schalten, nicht dem Melden.

    Bis hierher sperrte er jede Veroeffentlichung. Das war richtig, solange gar nichts
    gesendet wurde -- es machte aber den Trockenlauf unpruefbar: Die Home-Assistant-
    Anbindung liess sich erst ausprobieren, nachdem man die Anlage scharf geschaltet
    hatte, also genau dann nicht mehr, wenn ein Fehler noch folgenlos gewesen waere.
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
async def test_passwort_taucht_in_keiner_logzeile_auf(
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
async def test_abstand_faellt_nach_erfolgreicher_verbindung_zurueck(
    mqtt_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sonst waechst die Wartezeit ueber die Lebensdauer des Dienstes monoton weiter.

    Eine Verbindung, die nach Tagen einmal abreisst, wuerde dann eine Minute warten
    statt einer Sekunde — obwohl gar keine Stoerungsserie vorliegt. Fuer eine Heizung
    im Winter ist das der Unterschied zwischen einer Luecke und einer Pause.
    """
    waited: list[float] = []
    FalscherClient.instanzen.clear()

    async def mitschreiben(seconds: float) -> None:
        waited.append(seconds)
        if len(waited) == 4:
            raise Schleifenende

    monkeypatch.setattr(client_modul, "schlafen", mitschreiben)
    # Drei Fehlversuche in Folge (1, 2, 4 s), dann eine Verbindung, die Nachrichten
    # liefert und erst danach abreisst — der vierte Abstand muss wieder 1 s sein.
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
async def test_kein_publish_auch_wenn_der_aufrufer_es_verlangt(
    mqtt_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der zweite Riegel: `schaltet=True` allein genuegt nicht.

    Hinter dem Ventil haengt eine bewohnte Wohnung. Ein einzelner Aufrufer, der eine
    Nachricht faelschlich als Schaltbefehl schickt, darf nicht ausreichen — der Client
    muss zusaetzlich mit `schalten_erlaubt=True` gebaut worden sein.
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
    raise AssertionError("Dieser Handler darf nie aufgerufen werden")


@pytest.mark.anyio
async def test_abgeschaltetes_mqtt_baut_keine_verbindung_auf(
    mqtt_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der Vorgabewert. Weder die Testsuite noch ein frisch gebauter Container darf
    versuchen, sich irgendwohin zu verbinden."""
    mqtt_settings.mqtt_enabled = False
    FalscherClient.instanzen.clear()
    monkeypatch.setattr(client_modul.aiomqtt, "Client", FalscherClient)

    await MqttClient(mqtt_settings, _leerer_handler).run()

    assert FalscherClient.instanzen == []


@pytest.mark.anyio
async def test_fehlender_host_wird_beim_namen_genannt(mqtt_settings: Settings) -> None:
    """Eine Fehlkonfiguration soll sagen, welche Angabe fehlt — nicht 'NoneType'."""
    mqtt_settings.mqtt_host = None
    with pytest.raises(ValueError, match="MQTT_HOST"):
        MqttClient(mqtt_settings, _leerer_handler)._newer_client()


@pytest.mark.anyio
async def test_scharf_gebauter_client_veroeffentlicht_wirklich(
    mqtt_settings: Settings,
) -> None:
    """Der Gegenbeweis zum Trockenlauf: Der Weg funktioniert, er ist nur verriegelt.

    Ohne diesen Test belegte die Suite nur, dass nichts gesendet wird — auch dann, wenn
    das Senden gar nicht gebaut waere. Teilprojekt 4 haengt daran.
    """
    kunde = MqttClient(mqtt_settings, _leerer_handler, switching_allowed=True)
    falscher = FalscherClient()
    kunde._client = falscher  # type: ignore[assignment]

    assert await kunde.publishing(
        "testbasis/Ventil/set", '{"state": "ON"}', switches=True
    )
    assert falscher.published == [("testbasis/Ventil/set", '{"state": "ON"}')]


@pytest.mark.anyio
async def test_ohne_verbindung_wird_nicht_veroeffentlicht(mqtt_settings: Settings) -> None:
    kunde = MqttClient(mqtt_settings, _leerer_handler, switching_allowed=True)
    assert await kunde.publishing("testbasis/Ventil/set", "{}", switches=True) is False


@pytest.mark.anyio
async def test_schlafen_wartet_wirklich(monkeypatch: pytest.MonkeyPatch) -> None:
    """Die Funktion existiert, damit Tests sie ersetzen koennen — dass sie im Betrieb
    wirklich wartet, prueft sonst niemand."""
    waited: list[float] = []

    async def gefaelscht(seconds: float) -> None:
        waited.append(seconds)

    monkeypatch.setattr(client_modul.asyncio, "sleep", gefaelscht)
    await client_modul.schlafen(2.5)
    assert waited == [2.5]


@pytest.mark.anyio
async def test_ein_scharf_gebauter_client_sendet_auch_zustaende(
    mqtt_settings: Settings,
) -> None:
    """Frueher stand hier das Gegenteil: Ein `scharf=False` wurde auch von einem scharf
    gebauten Client abgewiesen. Das war die Fassung, in der `scharf` zwei Dinge zugleich
    bedeutete -- die Absicht des Aufrufers und die Erlaubnis des Clients. Jetzt sagt
    `schaltet`, was die Nachricht bewirkt, und eine Zustandsmeldung bewirkt nichts."""
    kunde = MqttClient(mqtt_settings, _leerer_handler, switching_allowed=True)
    falscher = FalscherClient()
    kunde._client = falscher  # type: ignore[assignment]

    assert await kunde.publishing("thermoctl/availability", "online", switches=False)
    assert falscher.published == [("thermoctl/availability", "online")]
