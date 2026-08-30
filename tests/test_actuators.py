import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session

from tests.helpers import create_mode
from thermoctl.config import Settings
from thermoctl.db.models.operations import Setting
from thermoctl.integrations import actuators as actuators_module
from thermoctl.integrations.actuators import MerossSwitch, Zigbee2MqttVentil


class MqttAttrappe:
    def __init__(self, *, errors: Exception | None = None) -> None:
        self.aufrufe: list[tuple[str, str, bool]] = []
        self.errors = errors

    async def publishing(self, topic: str, payload: str, *, switches: bool) -> bool:
        self.aufrufe.append((topic, payload, switches))
        if self.errors:
            raise self.errors
        return True


class HttpAttrappe:
    def __init__(
        self, *, errors: Exception | None = None, response: dict[str, Any] | None = None
    ) -> None:
        self.aufrufe: list[tuple[str, dict[str, str], dict[str, str]]] = []
        self.errors = errors
        self.response = response or {"data": {"token": "ersatz-token"}}

    async def post(
        self, url: str, daten: dict[str, str], kopfzeilen: dict[str, str]
    ) -> dict[str, Any]:
        self.aufrufe.append((url, dict(daten), dict(kopfzeilen)))
        if self.errors:
            raise self.errors
        return self.response


class HttpResponse:
    def __enter__(self) -> HttpResponse:
        return self

    def __exit__(self, *_argumente: object) -> None:
        return None

    def read(self) -> bytes:
        return b'{"data": {"token": "ersatz-token"}}'


def _settings(**values: object) -> Settings:
    return Settings(
        _env_file=None,
        database_url="sqlite://",
        secret_key="s" * 32,
        **values,
    )


@pytest.mark.anyio
async def test_ohne_control_armed_wird_nichts_gesendet(session: Session) -> None:
    mqtt = MqttAttrappe()
    http = HttpAttrappe()
    anlagendaten = json.loads(Path("tests/daten/anlage-beispiele.json").read_text())
    name = anlagendaten["geraete"][-1]
    basis = _settings().mqtt_base_topic
    devices_id = anlagendaten["geraete"][0]

    mqtt_result = await Zigbee2MqttVentil(
        session, mqtt, basis, name
    ).switching(True)
    meross_result = await MerossSwitch(
        session, _settings(meross_email="konto@example.invalid", meross_password="geheim"),
        devices_id, transport=http,
    ).switching(False)

    assert mqtt.aufrufe == []
    assert http.aufrufe == []
    assert f"{basis}/{name}/set" in mqtt_result.beschreibung
    assert '{"state": "ON"}' in mqtt_result.beschreibung
    assert "Zustand OFF" in meross_result.beschreibung
    assert name in Zigbee2MqttVentil(session, mqtt, basis, name).beschreibung()
    assert devices_id in MerossSwitch(
        session, _settings(), devices_id, transport=http
    ).beschreibung()


@pytest.mark.anyio
async def test_control_armed_baut_meross_anmeldung_und_schaltaufruf(session: Session) -> None:
    frost_protection = create_mode(session, "frostschutz")
    session.add(Setting(id=1, control_armed=True, frost_protection_mode_id=frost_protection.id))
    session.flush()
    anlagendaten = json.loads(Path("tests/daten/anlage-beispiele.json").read_text())
    devices_id = anlagendaten["geraete"][0]
    basis = _settings().mqtt_base_topic
    http = HttpAttrappe()
    result = await MerossSwitch(
        session,
        _settings(meross_email="konto@example.invalid", meross_password="geheim"),
        devices_id,
        kanal=2,
        transport=http,
        api_basis="https://meross.example.invalid",
    ).switching(True)
    assert result.ausgefuehrt is True
    assert [aufruf[0] for aufruf in http.aufrufe] == [
        "https://meross.example.invalid/v1/Auth/signIn",
        "https://meross.example.invalid/v1/Device/devControl",
    ]
    assert http.aufrufe[1][1] == {
        "uuid": devices_id, "channel": "2", "action": "ON"
    }

    ohne_http = HttpAttrappe()
    nicht_konfiguriert = await MerossSwitch(
        session, _settings(), devices_id, transport=ohne_http
    ).switching(True)
    assert nicht_konfiguriert.ausgefuehrt is False
    assert "Nicht konfiguriert" in nicht_konfiguriert.beschreibung
    assert ohne_http.aufrufe == []

    mqtt = MqttAttrappe(errors=ConnectionError("Gegenstelle nicht erreichbar"))
    errors = await Zigbee2MqttVentil(
        session, mqtt, basis, devices_id
    ).switching(True)
    assert errors.ausgefuehrt is False
    assert errors.errors == "Gegenstelle nicht erreichbar"

    abgewiesen = MqttAttrappe()
    abgewiesen.publishing = _reject_publication  # type: ignore[method-assign]
    mqtt_result = await Zigbee2MqttVentil(
        session, abgewiesen, basis, devices_id
    ).switching(False)
    assert mqtt_result.errors == "MQTT-Client hat die Veroeffentlichung abgewiesen"

    meross_error = await MerossSwitch(
        session,
        _settings(meross_email="konto@example.invalid", meross_password="geheim"),
        devices_id,
        transport=HttpAttrappe(errors=ConnectionError("Cloud nicht erreichbar")),
    ).switching(False)
    assert meross_error.errors == "Cloud nicht erreichbar"

    token_fehlt = await MerossSwitch(
        session,
        _settings(meross_email="konto@example.invalid", meross_password="geheim"),
        devices_id,
        transport=HttpAttrappe(response={"data": {}}),
    ).switching(False)
    assert token_fehlt.errors == "Meross-Anmeldung lieferte kein Token"


async def _reject_publication(
    _topic: str, _payload: str, *, switches: bool
) -> bool:
    return False


@pytest.mark.anyio
async def test_http_transport_kodiert_formular_und_liefert_objekt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    anfragen: list[object] = []

    def url_oeffnen(anfrage: object, *, timeout: int) -> HttpResponse:
        anfragen.append(anfrage)
        assert timeout == 10
        return HttpResponse()

    monkeypatch.setattr(actuators_module.request, "urlopen", url_oeffnen)
    result = await actuators_module.UrllibHttpTransport().post(
        "https://meross.example.invalid/v1/test",
        {"zustand": "AN"},
        {"Authorization": "Basic token"},
    )
    assert result == {"data": {"token": "ersatz-token"}}
    assert len(anfragen) == 1


def _armed(session: Session) -> None:
    """Setzt control_armed — ausschliesslich in Tests, die den Riegel selbst pruefen."""
    frost_protection = create_mode(session, "frostschutz")
    session.add(Setting(id=1, control_armed=True, frost_protection_mode_id=frost_protection.id))
    session.flush()


@pytest.mark.anyio
async def test_meross_ohne_zugangsdaten_meldet_sich_als_unkonfiguriert(
    session: Session,
) -> None:
    """Der Normalfall in dieser Phase: kein Konto hinterlegt, also kein Aufruf.

    Das ist ausdruecklich kein Fehler — der Adapter soll dann still nichts tun, statt
    einen Anmeldeversuch mit leeren Feldern zu unternehmen.
    """
    _armed(session)
    http = HttpAttrappe()
    result = await MerossSwitch(session, _settings(), "geraet-1", transport=http).switching(
        True
    )
    assert result.ausgefuehrt is False
    assert "Nicht konfiguriert" in result.beschreibung
    assert http.aufrufe == []


@pytest.mark.anyio
async def test_geraetename_mit_umlaut_und_leerzeichen_ergibt_das_richtige_topic(
    session: Session,
) -> None:
    """Die Anlage fuehrt Namen wie 'Über Küche'. Ein Adapter, der sie verstuemmelt,
    schaltet spaeter ein anderes Geraet oder gar keines."""
    anlagendaten = json.loads(Path("tests/daten/anlage-beispiele.json").read_text())
    name = next(n for n in anlagendaten["geraete"] if " " in n and any(c in n for c in "äöüÄÖÜ"))
    ventil = Zigbee2MqttVentil(session, MqttAttrappe(), "zigbee2mqtt", name)
    result = await ventil.switching(True)
    assert f"zigbee2mqtt/{name}/set" in result.beschreibung


@pytest.mark.anyio
async def test_fehler_der_gegenstelle_wird_zum_ergebnis_nicht_zur_ausnahme(
    session: Session,
) -> None:
    """Ein Aktorfehler darf den Regelzyklus aller anderen Zonen nicht abbrechen."""
    _armed(session)
    mqtt = MqttAttrappe(errors=ConnectionError("Broker weg"))
    ventil = await Zigbee2MqttVentil(session, mqtt, "zigbee2mqtt", "Ventil").switching(True)
    assert ventil.ausgefuehrt is False
    assert ventil.errors is not None and "Broker weg" in ventil.errors

    http = HttpAttrappe(errors=TimeoutError("Cloud antwortet nicht"))
    meross = await MerossSwitch(
        session, _settings(meross_email="k@example.invalid", meross_password="geheim"),
        "geraet-1", transport=http,
    ).switching(True)
    assert meross.ausgefuehrt is False
    assert meross.errors is not None and "antwortet nicht" in meross.errors


@pytest.mark.anyio
async def test_abgewiesene_veroeffentlichung_wird_als_fehler_gemeldet(
    session: Session,
) -> None:
    """Der zweite Riegel im MQTT-Client greift — der Aktor darf das nicht als Erfolg
    verbuchen, sonst stuende im Protokoll 'geschaltet', wo nichts geschaltet wurde."""
    _armed(session)
    result = await Zigbee2MqttVentil(
        session, _AbweisenderClient(), "zigbee2mqtt", "Ventil"
    ).switching(True)
    assert result.ausgefuehrt is False
    assert result.errors is not None and "abgewiesen" in result.errors


class _AbweisenderClient:
    async def publishing(self, topic: str, payload: str, *, switches: bool) -> bool:
        return False


@pytest.mark.anyio
async def test_anmeldung_ohne_token_wird_zum_fehler(session: Session) -> None:
    """Antwortet die Cloud ohne Token, ist der Schaltaufruf sinnlos — und der Adapter
    darf ihn nicht trotzdem absetzen."""
    _armed(session)
    http = HttpAttrappe(response={"data": {}})
    result = await MerossSwitch(
        session, _settings(meross_email="k@example.invalid", meross_password="geheim"),
        "geraet-1", transport=http,
    ).switching(True)
    assert result.ausgefuehrt is False
    assert result.errors is not None
    assert len(http.aufrufe) == 1, "Nach der gescheiterten Anmeldung darf nichts folgen"


@pytest.mark.anyio
async def test_scharfes_ventil_sendet_wirklich(session: Session) -> None:
    """Der Gegenbeweis zum Trockenlauf: Der Weg funktioniert, er ist nur verriegelt.

    Ohne diesen Test belegte die Suite nur, dass nichts gesendet wird — auch dann, wenn
    das Senden gar nicht gebaut waere. Phase 4 haengt daran.
    """
    _armed(session)
    mqtt = MqttAttrappe()
    result = await Zigbee2MqttVentil(session, mqtt, "zigbee2mqtt", "Ventil").switching(True)
    assert result.ausgefuehrt is True
    assert mqtt.aufrufe == [("zigbee2mqtt/Ventil/set", '{"state": "ON"}', True)]


@pytest.mark.anyio
async def test_unerwartete_meross_antwort_wird_zum_fehler(session: Session) -> None:
    """Antwortet die Cloud mit etwas anderem als einem Objekt mit Token, ist das ein
    Fehler des Adapters und kein Grund, den Schaltaufruf blind abzusetzen."""
    _armed(session)
    http = HttpAttrappe(response={"data": "unerwartet"})
    result = await MerossSwitch(
        session, _settings(meross_email="k@example.invalid", meross_password="geheim"),
        "geraet-1", transport=http,
    ).switching(True)
    assert result.ausgefuehrt is False
    assert result.errors is not None and "Token" in result.errors


def test_http_transport_weist_nicht_objekt_antworten_ab() -> None:
    """Die HTTP-Huelle liefert nur Objekte weiter — eine Liste waere fuer jeden Aufrufer
    eine Ueberraschung, die erst weiter unten auffiele."""
    import pytest as _pytest

    class _List:
        def __enter__(self) -> _List:
            return self

        def __exit__(self, *_a: object) -> None:
            return None

        def read(self) -> bytes:
            return b"[1, 2]"

    transport = actuators_module.UrllibHttpTransport()
    with _pytest.MonkeyPatch.context() as mp:
        mp.setattr(actuators_module.request, "urlopen", lambda *_a, **_k: _List())
        with _pytest.raises(ValueError, match="kein Objekt"):
            transport._post_synchron("https://example.invalid", {}, {})


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
