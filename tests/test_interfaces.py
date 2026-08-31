"""The interfaces overview.

The value of this page rises and falls with one thing: whether its status
display is correct. "Configured" next to a connection that never actually
comes up is worse than no page at all -- then the bug gets looked for
somewhere else.
"""


import pytest
from sqlalchemy.orm import Session

from tests.helpers import user_with_permissions
from thermoctl.auth.tokens import issue_token
from thermoctl.config import Settings
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


def test_every_interface_has_a_known_state(session: Session) -> None:
    """Counter-check for the template: it looks the label up in a table and
    would respond with a KeyError on an unknown value."""
    known = {"running", "configured", "off", "missing", "not_built"}
    for overrides in ({}, {"mqtt_enabled": True, "mqtt_host": "b"}):
        for bridge in (None, True, False):
            for s in overview(session, _settings(**overrides), bridge):
                assert s.state in known, f"{s.key}: {s.state}"


def test_meross_says_it_is_not_built_even_with_credentials(session: Session) -> None:
    """Zugangsdaten machen aus einer halben Anbindung keine ganze.

    Gebaut ist nur die schaltende Hälfte: Der Adapter kann eine bekannte Steckdose
    ein- und ausschalten. Es gibt keine Geräteerkennung für Meross — Geräte entstehen
    ausschliesslich aus der Zigbee2MQTT-Liste (`services/ingest.py` ist die einzige
    Stelle, die `Device`-Zeilen anlegt), und von Hand anlegen lässt sich keines.

    Die Seite meldete trotzdem „Eingerichtet", sobald eine Adresse in der Umgebung
    stand. Aus dem Betrieb gemeldet als „die Meross-Schalter tauchen nirgends auf" —
    und die Seite hatte genau das Gegenteil behauptet.
    """
    items = overview(
        session,
        _settings(meross_email="jemand@example.invalid", meross_password="geheim"),
        None,
    )
    meross = next(s for s in items if s.key == "meross")

    assert meross.state == "not_built"
    assert "keine gefunden" in meross.finding
    assert meross.hint is not None and "keine Geräteerkennung" in meross.hint


def test_no_device_can_enter_the_system_except_through_zigbee2mqtt(session: Session) -> None:
    """Der Grund, warum Meross „noch nicht gebaut" heisst, an seiner Wurzel.

    Wenn irgendwann ein zweiter Weg entsteht — eine Meross-Erkennung, ein Formular zum
    Anlegen von Hand —, muss dieser Test rot werden. Er ist die Stelle, an der jemand
    dann merkt, dass die Schnittstellenseite nachzuziehen ist.
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
    assert stellen == ["thermoctl/services/ingest.py:54"], (
        "Geraete entstehen an einer neuen Stelle: " + ", ".join(stellen)
    )
