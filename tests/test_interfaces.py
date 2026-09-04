"""The interfaces overview.

The value of this page rises and falls with one thing: whether its status
display is correct. "Configured" next to a connection that never actually
comes up is worse than no page at all -- then the bug gets looked for
somewhere else.
"""


import pytest
from sqlalchemy.orm import Session

from tests.helpers import integration, user_with_permissions
from thermoctl.auth.tokens import issue_token
from thermoctl.config import Settings
from thermoctl.db.models.device import Device
from thermoctl.domain.interfaces import overview


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "_env_file": None,
        "database_url": "sqlite://",
        "secret_key": "s" * 32,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _state(interfaces: list, key: str) -> str:
    return next(s.state for s in interfaces if s.key == key)


@pytest.mark.parametrize(
    ("overrides", "bridge", "expected"),
    [
        ({}, None, "off"),
        ({"mqtt_enabled": True}, None, "missing"),
        ({"mqtt_enabled": True, "mqtt_host": "broker"}, None, "configured"),
        ({"mqtt_enabled": True, "mqtt_host": "broker"}, True, "running"),
        ({"mqtt_enabled": True, "mqtt_host": "broker"}, False, "missing"),
    ],
)
def test_mqtt_state(
    session: Session, overrides: dict, bridge: bool | None, expected: str
) -> None:
    """Five distinguishable states -- and the difference between "turned on" and
    "actually comes through" is exactly the one a .env file cannot answer."""
    items = overview(session, _settings(**overrides), bridge)
    assert _state(items, "mqtt") == expected


def test_rest_without_a_token_is_unusable(session: Session) -> None:
    """The interface *is* always there. Without a token, no one gets in anyway,
    and that is exactly what should show up here."""
    items = overview(session, _settings(), None)
    rest = next(s for s in items if s.key == "rest")
    assert rest.state == "configured"
    assert "kein gültiges Token" in rest.finding


def test_rest_with_a_token_is_running(session: Session) -> None:
    user = user_with_permissions(session, "schnittstellen-nutzer", [("zone.read", None)])
    issue_token(session, user, "Probe", [("zone.read", None)], None)
    items = overview(session, _settings(), None)
    assert _state(items, "rest") == "running"


def test_no_secret_appears_on_the_page(session: Session) -> None:
    """Principle 2. The page says *whether* something is stored, never what."""
    secret = "streng-geheimes-passwort"
    items = overview(
        session,
        _settings(
            mqtt_enabled=True,
            mqtt_host="broker",
            mqtt_password=secret,
            meross_email="jemand@example.org",
            meross_password=secret,
            mcp_token=secret,
            notify_webhook_token=secret,
        ),
        True,
    )
    everything = " ".join(
        f"{s.name} {s.finding} {s.hint or ''} "
        + " ".join(f"{a.name} {a.value} {a.source}" for a in s.details)
        for s in items
    )
    assert secret not in everything
    assert "hinterlegt" in everything


def test_origin_distinguishes_environment_from_default(session: Session) -> None:
    """The second question no .env answers: does this value stand there because
    someone set it, or because no one set it?"""
    items = overview(session, _settings(mqtt_base_topic="eigenes"), None)
    mqtt = next(s for s in items if s.key == "mqtt")
    sources = {a.name: a.source for a in mqtt.details}
    assert sources["Basis-Topic"] == "Umgebung"
    assert sources["TLS"] == "Standard"


def test_home_assistant_depends_on_mqtt(session: Session) -> None:
    """It now genuinely sends -- but only over MQTT.

    This used to say "not built", because only the payload had been designed.
    Without MQTT there is still no path there, and the page must not claim
    anything else.
    """
    without_mqtt = overview(session, _settings(), None)
    assert _state(without_mqtt, "homeassistant") == "off"

    with_mqtt = overview(session, _settings(mqtt_enabled=True, mqtt_host="b"), None)
    assert _state(with_mqtt, "homeassistant") == "running"
    home_assistant = next(s for s in with_mqtt if s.key == "homeassistant")
    assert "gespeicherten ersten Riegel" in home_assistant.finding
    assert "Erst nach einem Neustart" in home_assistant.finding
    assert "Ein/Aus-Befehle an gewöhnliche Aktoren" in home_assistant.finding


def test_every_interface_has_a_known_state(session: Session) -> None:
    """Counter-check for the template: it looks the label up in a table and
    would respond with a KeyError on an unknown value."""
    known = {"running", "configured", "off", "missing"}
    for overrides in ({}, {"mqtt_enabled": True, "mqtt_host": "b"}):
        for bridge in (None, True, False):
            for s in overview(session, _settings(**overrides), bridge):
                assert s.state in known, f"{s.key}: {s.state}"


def test_meross_with_credentials_but_no_device_yet_is_configured_not_running(
    session: Session,
) -> None:
    """Kreuzreview-Befund 4: Zugangsdaten allein sagen nichts darüber, ob je ein
    Abgleich gelang -- nur „hinterlegt", nicht „läuft". Ohne ein gefundenes Gerät (der
    einzige Beleg, dass sich die Anmeldung je durchgesetzt hat) bleibt die Seite bei
    „configured", nicht „running".
    """
    items = overview(
        session,
        _settings(meross_email="jemand@example.invalid", meross_password="geheim"),
        None,
    )
    meross = next(s for s in items if s.key == "meross")

    assert meross.state == "configured"
    assert "noch kein Gerät gefunden" in meross.finding


def test_meross_says_it_runs_once_a_device_was_found(session: Session) -> None:
    """Der Gegentest zu dem, was hier lange stand.

    Bis 0.2.0 meldete die Seite „noch nicht gebaut", und das war richtig: Es gab keine
    Geräteerkennung für Meross, Geräte entstanden ausschließlich aus der
    Zigbee2MQTT-Liste, und eine Steckdose konnte in dieser Anlage gar nicht auftauchen.
    Aus dem Betrieb gemeldet als „die Meross-Schalter tauchen nirgends auf".

    Beides ist jetzt da — Erkennung *und* Schaltweg, gegen ein echtes Konto geprüft.
    „running" heißt hier aber erst etwas, wenn tatsächlich ein Gerät aus einem
    Abgleich hervorgegangen ist -- Zugangsdaten allein reichen nicht mehr
    (Kreuzreview-Befund 4). Der Hinweis muss weiter sagen, was daran noch nicht
    verdrahtet ist: der Regelkreis selbst, nicht das Schalten (das ist inzwischen
    nachgemessen).
    """
    anbindung = integration(session, "meross")
    session.add(
        Device(
            integration_id=anbindung.id,
            external_id="1111",
            display_name="Wohnzimmer",
            is_enabled=True,
        )
    )
    session.flush()

    items = overview(
        session,
        _settings(meross_email="jemand@example.invalid", meross_password="geheim"),
        None,
    )
    meross = next(s for s in items if s.key == "meross")

    assert meross.state == "running"
    assert "gefunden" in meross.finding
    assert meross.hint is not None
    assert "404" in meross.hint, "Der tote HTTP-Pfad muss dokumentiert bleiben"
    assert "SETACK" in meross.hint, "Das nachgemessene SET muss dokumentiert bleiben"
    assert "Regelkreis" in meross.hint, "Die fehlende Verdrahtung muss benannt bleiben"


def test_meross_without_credentials_is_off_not_broken(session: Session) -> None:
    """Ohne Konto ist nichts abzugleichen — das ist kein Fehler, sondern der
    Normalfall für alle, die Meross nicht benutzen."""
    items = overview(session, _settings(), None)
    meross = next(s for s in items if s.key == "meross")

    assert meross.state == "off"
    assert "Keine Zugangsdaten" in meross.finding


def test_meross_with_only_half_the_credentials_cannot_be_running(session: Session) -> None:
    """Kreuzreview-Befund 4: `hat_meross` prüfte bisher nur die E-Mail-Adresse. Ein
    Passwort ohne E-Mail -- oder umgekehrt -- kann sich nicht anmelden und darf nicht
    als eingerichtet gelten."""
    only_email = overview(session, _settings(meross_email="jemand@example.invalid"), None)
    assert _state(only_email, "meross") == "missing"

    only_password = overview(session, _settings(meross_password="geheim"), None)
    assert _state(only_password, "meross") == "missing"


def test_devices_enter_the_system_only_on_the_two_known_paths(session: Session) -> None:
    """Jeder Weg, auf dem ein Gerät entsteht, steht hier namentlich.

    Der Test hieß einmal „nur über Zigbee2MQTT" und war damit richtig — bis die
    Meross-Erkennung dazukam und ihn rot machte. Genau dafür ist er da: Entsteht ein
    dritter Weg — ein Formular zum Anlegen von Hand, eine weitere Anbindung —, muss
    jemand hier vorbei und die Schnittstellenseite nachziehen.
    """
    import re
    from pathlib import Path as _Path

    quelle = _Path(__file__).resolve().parent.parent / "thermoctl"
    stellen = [
        f"{pfad.relative_to(quelle.parent)}:{nummer}"
        for pfad in quelle.rglob("*.py")
        for nummer, zeile in enumerate(pfad.read_text(encoding="utf-8").splitlines(), 1)
        # `Device(` als Konstruktoraufruf: nicht `DeviceProperty(` und Verwandte,
        # und nicht die Klassendefinition selbst.
        if re.search(r"(?<![A-Za-z])Device\(", zeile) and not zeile.lstrip().startswith("class ")
    ]
    assert stellen == [
        "thermoctl/services/ingest.py:54",
        "thermoctl/services/meross_discovery.py:89",
    ], "Geraete entstehen an einer neuen Stelle: " + ", ".join(stellen)
