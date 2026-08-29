import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session

from tests.hilfen import modus_anlegen
from thermoctl.config import Settings
from thermoctl.db.models.operations import Setting
from thermoctl.integrations import aktoren as aktoren_modul
from thermoctl.integrations.aktoren import MerossSchalter, Zigbee2MqttVentil


class MqttAttrappe:
    def __init__(self, *, fehler: Exception | None = None) -> None:
        self.aufrufe: list[tuple[str, str, bool]] = []
        self.fehler = fehler

    async def veroeffentlichen(self, topic: str, nutzlast: str, *, scharf: bool) -> bool:
        self.aufrufe.append((topic, nutzlast, scharf))
        if self.fehler:
            raise self.fehler
        return True


class HttpAttrappe:
    def __init__(
        self, *, fehler: Exception | None = None, antwort: dict[str, Any] | None = None
    ) -> None:
        self.aufrufe: list[tuple[str, dict[str, str], dict[str, str]]] = []
        self.fehler = fehler
        self.antwort = antwort or {"data": {"token": "ersatz-token"}}

    async def post(
        self, url: str, daten: dict[str, str], kopfzeilen: dict[str, str]
    ) -> dict[str, Any]:
        self.aufrufe.append((url, dict(daten), dict(kopfzeilen)))
        if self.fehler:
            raise self.fehler
        return self.antwort


class HttpAntwort:
    def __enter__(self) -> HttpAntwort:
        return self

    def __exit__(self, *_argumente: object) -> None:
        return None

    def read(self) -> bytes:
        return b'{"data": {"token": "ersatz-token"}}'


def _settings(**werte: object) -> Settings:
    return Settings(
        _env_file=None,
        database_url="sqlite://",
        secret_key="s" * 32,
        **werte,
    )


@pytest.mark.anyio
async def test_ohne_control_armed_wird_nichts_gesendet(session: Session) -> None:
    mqtt = MqttAttrappe()
    http = HttpAttrappe()
    anlagendaten = json.loads(Path("tests/daten/anlage-beispiele.json").read_text())
    name = anlagendaten["geraete"][-1]
    basis = _settings().mqtt_base_topic
    geraete_id = anlagendaten["geraete"][0]

    mqtt_ergebnis = await Zigbee2MqttVentil(
        session, mqtt, basis, name
    ).schalten(True)
    meross_ergebnis = await MerossSchalter(
        session, _settings(meross_email="konto@example.invalid", meross_password="geheim"),
        geraete_id, transport=http,
    ).schalten(False)

    assert mqtt.aufrufe == []
    assert http.aufrufe == []
    assert f"{basis}/{name}/set" in mqtt_ergebnis.beschreibung
    assert '{"state": "ON"}' in mqtt_ergebnis.beschreibung
    assert "Zustand OFF" in meross_ergebnis.beschreibung
    assert name in Zigbee2MqttVentil(session, mqtt, basis, name).beschreibung()
    assert geraete_id in MerossSchalter(
        session, _settings(), geraete_id, transport=http
    ).beschreibung()


@pytest.mark.anyio
async def test_control_armed_baut_meross_anmeldung_und_schaltaufruf(session: Session) -> None:
    frostschutz = modus_anlegen(session, "frostschutz")
    session.add(Setting(id=1, control_armed=True, frost_protection_mode_id=frostschutz.id))
    session.flush()
    anlagendaten = json.loads(Path("tests/daten/anlage-beispiele.json").read_text())
    geraete_id = anlagendaten["geraete"][0]
    basis = _settings().mqtt_base_topic
    http = HttpAttrappe()
    ergebnis = await MerossSchalter(
        session,
        _settings(meross_email="konto@example.invalid", meross_password="geheim"),
        geraete_id,
        kanal=2,
        transport=http,
        api_basis="https://meross.example.invalid",
    ).schalten(True)
    assert ergebnis.ausgefuehrt is True
    assert [aufruf[0] for aufruf in http.aufrufe] == [
        "https://meross.example.invalid/v1/Auth/signIn",
        "https://meross.example.invalid/v1/Device/devControl",
    ]
    assert http.aufrufe[1][1] == {
        "uuid": geraete_id, "channel": "2", "action": "ON"
    }

    ohne_http = HttpAttrappe()
    nicht_konfiguriert = await MerossSchalter(
        session, _settings(), geraete_id, transport=ohne_http
    ).schalten(True)
    assert nicht_konfiguriert.ausgefuehrt is False
    assert "Nicht konfiguriert" in nicht_konfiguriert.beschreibung
    assert ohne_http.aufrufe == []

    mqtt = MqttAttrappe(fehler=ConnectionError("Gegenstelle nicht erreichbar"))
    fehler = await Zigbee2MqttVentil(
        session, mqtt, basis, geraete_id
    ).schalten(True)
    assert fehler.ausgefuehrt is False
    assert fehler.fehler == "Gegenstelle nicht erreichbar"

    abgewiesen = MqttAttrappe()
    abgewiesen.veroeffentlichen = _veroeffentlichung_abweisen  # type: ignore[method-assign]
    mqtt_ergebnis = await Zigbee2MqttVentil(
        session, abgewiesen, basis, geraete_id
    ).schalten(False)
    assert mqtt_ergebnis.fehler == "MQTT-Client hat die Veroeffentlichung abgewiesen"

    meross_fehler = await MerossSchalter(
        session,
        _settings(meross_email="konto@example.invalid", meross_password="geheim"),
        geraete_id,
        transport=HttpAttrappe(fehler=ConnectionError("Cloud nicht erreichbar")),
    ).schalten(False)
    assert meross_fehler.fehler == "Cloud nicht erreichbar"

    token_fehlt = await MerossSchalter(
        session,
        _settings(meross_email="konto@example.invalid", meross_password="geheim"),
        geraete_id,
        transport=HttpAttrappe(antwort={"data": {}}),
    ).schalten(False)
    assert token_fehlt.fehler == "Meross-Anmeldung lieferte kein Token"


async def _veroeffentlichung_abweisen(
    _topic: str, _nutzlast: str, *, scharf: bool
) -> bool:
    return False


@pytest.mark.anyio
async def test_http_transport_kodiert_formular_und_liefert_objekt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    anfragen: list[object] = []

    def url_oeffnen(anfrage: object, *, timeout: int) -> HttpAntwort:
        anfragen.append(anfrage)
        assert timeout == 10
        return HttpAntwort()

    monkeypatch.setattr(aktoren_modul.request, "urlopen", url_oeffnen)
    ergebnis = await aktoren_modul.UrllibHttpTransport().post(
        "https://meross.example.invalid/v1/test",
        {"zustand": "AN"},
        {"Authorization": "Basic token"},
    )
    assert ergebnis == {"data": {"token": "ersatz-token"}}
    assert len(anfragen) == 1


def _scharf(session: Session) -> None:
    """Setzt control_armed — ausschliesslich in Tests, die den Riegel selbst pruefen."""
    frostschutz = modus_anlegen(session, "frostschutz")
    session.add(Setting(id=1, control_armed=True, frost_protection_mode_id=frostschutz.id))
    session.flush()


@pytest.mark.anyio
async def test_meross_ohne_zugangsdaten_meldet_sich_als_unkonfiguriert(
    session: Session,
) -> None:
    """Der Normalfall in dieser Phase: kein Konto hinterlegt, also kein Aufruf.

    Das ist ausdruecklich kein Fehler — der Adapter soll dann still nichts tun, statt
    einen Anmeldeversuch mit leeren Feldern zu unternehmen.
    """
    _scharf(session)
    http = HttpAttrappe()
    ergebnis = await MerossSchalter(session, _settings(), "geraet-1", transport=http).schalten(
        True
    )
    assert ergebnis.ausgefuehrt is False
    assert "Nicht konfiguriert" in ergebnis.beschreibung
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
    ergebnis = await ventil.schalten(True)
    assert f"zigbee2mqtt/{name}/set" in ergebnis.beschreibung


@pytest.mark.anyio
async def test_fehler_der_gegenstelle_wird_zum_ergebnis_nicht_zur_ausnahme(
    session: Session,
) -> None:
    """Ein Aktorfehler darf den Regelzyklus aller anderen Zonen nicht abbrechen."""
    _scharf(session)
    mqtt = MqttAttrappe(fehler=ConnectionError("Broker weg"))
    ventil = await Zigbee2MqttVentil(session, mqtt, "zigbee2mqtt", "Ventil").schalten(True)
    assert ventil.ausgefuehrt is False
    assert ventil.fehler is not None and "Broker weg" in ventil.fehler

    http = HttpAttrappe(fehler=TimeoutError("Cloud antwortet nicht"))
    meross = await MerossSchalter(
        session, _settings(meross_email="k@example.invalid", meross_password="geheim"),
        "geraet-1", transport=http,
    ).schalten(True)
    assert meross.ausgefuehrt is False
    assert meross.fehler is not None and "antwortet nicht" in meross.fehler


@pytest.mark.anyio
async def test_abgewiesene_veroeffentlichung_wird_als_fehler_gemeldet(
    session: Session,
) -> None:
    """Der zweite Riegel im MQTT-Client greift — der Aktor darf das nicht als Erfolg
    verbuchen, sonst stuende im Protokoll 'geschaltet', wo nichts geschaltet wurde."""
    _scharf(session)
    ergebnis = await Zigbee2MqttVentil(
        session, _AbweisenderClient(), "zigbee2mqtt", "Ventil"
    ).schalten(True)
    assert ergebnis.ausgefuehrt is False
    assert ergebnis.fehler is not None and "abgewiesen" in ergebnis.fehler


class _AbweisenderClient:
    async def veroeffentlichen(self, topic: str, nutzlast: str, *, scharf: bool) -> bool:
        return False


@pytest.mark.anyio
async def test_anmeldung_ohne_token_wird_zum_fehler(session: Session) -> None:
    """Antwortet die Cloud ohne Token, ist der Schaltaufruf sinnlos — und der Adapter
    darf ihn nicht trotzdem absetzen."""
    _scharf(session)
    http = HttpAttrappe(antwort={"data": {}})
    ergebnis = await MerossSchalter(
        session, _settings(meross_email="k@example.invalid", meross_password="geheim"),
        "geraet-1", transport=http,
    ).schalten(True)
    assert ergebnis.ausgefuehrt is False
    assert ergebnis.fehler is not None
    assert len(http.aufrufe) == 1, "Nach der gescheiterten Anmeldung darf nichts folgen"


@pytest.mark.anyio
async def test_scharfes_ventil_sendet_wirklich(session: Session) -> None:
    """Der Gegenbeweis zum Trockenlauf: Der Weg funktioniert, er ist nur verriegelt.

    Ohne diesen Test belegte die Suite nur, dass nichts gesendet wird — auch dann, wenn
    das Senden gar nicht gebaut waere. Phase 4 haengt daran.
    """
    _scharf(session)
    mqtt = MqttAttrappe()
    ergebnis = await Zigbee2MqttVentil(session, mqtt, "zigbee2mqtt", "Ventil").schalten(True)
    assert ergebnis.ausgefuehrt is True
    assert mqtt.aufrufe == [("zigbee2mqtt/Ventil/set", '{"state": "ON"}', True)]


@pytest.mark.anyio
async def test_unerwartete_meross_antwort_wird_zum_fehler(session: Session) -> None:
    """Antwortet die Cloud mit etwas anderem als einem Objekt mit Token, ist das ein
    Fehler des Adapters und kein Grund, den Schaltaufruf blind abzusetzen."""
    _scharf(session)
    http = HttpAttrappe(antwort={"data": "unerwartet"})
    ergebnis = await MerossSchalter(
        session, _settings(meross_email="k@example.invalid", meross_password="geheim"),
        "geraet-1", transport=http,
    ).schalten(True)
    assert ergebnis.ausgefuehrt is False
    assert ergebnis.fehler is not None and "Token" in ergebnis.fehler


def test_http_transport_weist_nicht_objekt_antworten_ab() -> None:
    """Die HTTP-Huelle liefert nur Objekte weiter — eine Liste waere fuer jeden Aufrufer
    eine Ueberraschung, die erst weiter unten auffiele."""
    import pytest as _pytest

    class _Liste:
        def __enter__(self) -> _Liste:
            return self

        def __exit__(self, *_a: object) -> None:
            return None

        def read(self) -> bytes:
            return b"[1, 2]"

    transport = aktoren_modul.UrllibHttpTransport()
    with _pytest.MonkeyPatch.context() as mp:
        mp.setattr(aktoren_modul.request, "urlopen", lambda *_a, **_k: _Liste())
        with _pytest.raises(ValueError, match="kein Objekt"):
            transport._post_synchron("https://example.invalid", {}, {})


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
