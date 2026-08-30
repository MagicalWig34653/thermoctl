"""The interfaces overview.

The value of this page rises and falls with one thing: whether its status
display is correct. "Configured" next to a connection that never actually
comes up is worse than no page at all -- then the bug gets looked for
somewhere else.
"""


import pytest
from sqlalchemy.orm import Session

from tests.helpers import user_with_permissions
from thermoctl.auth.tokens import token_ausstellen
from thermoctl.config import Settings
from thermoctl.domain.interfaces import uebersicht


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "_env_file": None,
        "database_url": "sqlite://",
        "secret_key": "s" * 32,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _state(interfaces: list, key: str) -> str:
    return next(s.state for s in interfaces if s.schluessel == key)


@pytest.mark.parametrize(
    ("overrides", "bridge", "expected"),
    [
        ({}, None, "aus"),
        ({"mqtt_enabled": True}, None, "fehlt"),
        ({"mqtt_enabled": True, "mqtt_host": "broker"}, None, "eingerichtet"),
        ({"mqtt_enabled": True, "mqtt_host": "broker"}, True, "laeuft"),
        ({"mqtt_enabled": True, "mqtt_host": "broker"}, False, "fehlt"),
    ],
)
def test_mqtt_state(
    session: Session, overrides: dict, bridge: bool | None, expected: str
) -> None:
    """Five distinguishable states -- and the difference between "turned on" and
    "actually comes through" is exactly the one a .env file cannot answer."""
    items = uebersicht(session, _settings(**overrides), bridge)
    assert _state(items, "mqtt") == expected


def test_rest_without_a_token_is_unusable(session: Session) -> None:
    """The interface *is* always there. Without a token, no one gets in anyway,
    and that is exactly what should show up here."""
    items = uebersicht(session, _settings(), None)
    rest = next(s for s in items if s.schluessel == "rest")
    assert rest.state == "eingerichtet"
    assert "kein gültiges Token" in rest.befund


def test_rest_with_a_token_is_running(session: Session) -> None:
    user = user_with_permissions(session, "schnittstellen-nutzer", [("zone.read", None)])
    token_ausstellen(session, user, "Probe", [("zone.read", None)], None)
    items = uebersicht(session, _settings(), None)
    assert _state(items, "rest") == "laeuft"


def test_no_secret_appears_on_the_page(session: Session) -> None:
    """Principle 2. The page says *whether* something is stored, never what."""
    secret = "streng-geheimes-passwort"
    items = uebersicht(
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
        f"{s.name} {s.befund} {s.hint or ''} "
        + " ".join(f"{a.name} {a.value} {a.source}" for a in s.angaben)
        for s in items
    )
    assert secret not in everything
    assert "hinterlegt" in everything


def test_origin_distinguishes_environment_from_default(session: Session) -> None:
    """The second question no .env answers: does this value stand there because
    someone set it, or because no one set it?"""
    items = uebersicht(session, _settings(mqtt_base_topic="eigenes"), None)
    mqtt = next(s for s in items if s.schluessel == "mqtt")
    sources = {a.name: a.source for a in mqtt.angaben}
    assert sources["Basis-Topic"] == "Umgebung"
    assert sources["TLS"] == "Standard"


def test_home_assistant_depends_on_mqtt(session: Session) -> None:
    """It now genuinely sends -- but only over MQTT.

    This used to say "not built", because only the payload had been designed.
    Without MQTT there is still no path there, and the page must not claim
    anything else.
    """
    without_mqtt = uebersicht(session, _settings(), None)
    assert _state(without_mqtt, "homeassistant") == "aus"

    with_mqtt = uebersicht(session, _settings(mqtt_enabled=True, mqtt_host="b"), None)
    assert _state(with_mqtt, "homeassistant") == "laeuft"


def test_every_interface_has_a_known_state(session: Session) -> None:
    """Counter-check for the template: it looks the label up in a table and
    would respond with a KeyError on an unknown value."""
    known = {"laeuft", "eingerichtet", "aus", "fehlt", "ungebaut"}
    for overrides in ({}, {"mqtt_enabled": True, "mqtt_host": "b"}):
        for bridge in (None, True, False):
            for s in uebersicht(session, _settings(**overrides), bridge):
                assert s.state in known, f"{s.schluessel}: {s.state}"
