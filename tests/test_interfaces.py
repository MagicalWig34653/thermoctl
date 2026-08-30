"""Die Schnittstellen-Uebersicht.

Der Wert dieser Seite steht und faellt mit einer Sache: dass ihre Zustandsangabe stimmt.
"Eingerichtet" neben einer Verbindung, die gar nicht zustande kommt, ist schlimmer als
keine Seite -- dann sucht man den Fehler anderswo.
"""


import pytest
from sqlalchemy.orm import Session

from tests.helpers import user_with_permissions
from thermoctl.auth.tokens import token_ausstellen
from thermoctl.config import Settings
from thermoctl.domain.interfaces import uebersicht


def _settings(**abweichungen: object) -> Settings:
    grund: dict[str, object] = {
        "_env_file": None,
        "database_url": "sqlite://",
        "secret_key": "s" * 32,
    }
    grund.update(abweichungen)
    return Settings(**grund)  # type: ignore[arg-type]


def _state(interfaces: list, schluessel: str) -> str:
    return next(s.state for s in interfaces if s.schluessel == schluessel)


@pytest.mark.parametrize(
    ("abweichungen", "bridge", "expected"),
    [
        ({}, None, "aus"),
        ({"mqtt_enabled": True}, None, "fehlt"),
        ({"mqtt_enabled": True, "mqtt_host": "broker"}, None, "eingerichtet"),
        ({"mqtt_enabled": True, "mqtt_host": "broker"}, True, "laeuft"),
        ({"mqtt_enabled": True, "mqtt_host": "broker"}, False, "fehlt"),
    ],
)
def test_mqtt_zustand(
    session: Session, abweichungen: dict, bridge: bool | None, expected: str
) -> None:
    """Fuenf unterscheidbare Lagen -- und der Unterschied zwischen "eingeschaltet" und
    "kommt wirklich an" ist genau der, den eine .env nicht beantwortet."""
    items = uebersicht(session, _settings(**abweichungen), bridge)
    assert _state(items, "mqtt") == expected


def test_rest_ohne_token_ist_nicht_benutzbar(session: Session) -> None:
    """Die Schnittstelle *ist* immer da. Ohne Token kommt trotzdem niemand hinein, und
    genau das soll dastehen."""
    items = uebersicht(session, _settings(), None)
    rest = next(s for s in items if s.schluessel == "rest")
    assert rest.state == "eingerichtet"
    assert "kein gültiges Token" in rest.befund


def test_rest_mit_token_laeuft(session: Session) -> None:
    nutzer = user_with_permissions(session, "schnittstellen-nutzer", [("zone.read", None)])
    token_ausstellen(session, nutzer, "Probe", [("zone.read", None)], None)
    items = uebersicht(session, _settings(), None)
    assert _state(items, "rest") == "laeuft"


def test_kein_geheimnis_steht_auf_der_seite(session: Session) -> None:
    """Grundsatz 2. Die Seite sagt, *ob* etwas hinterlegt ist, nie was."""
    geheim = "streng-geheimes-passwort"
    items = uebersicht(
        session,
        _settings(
            mqtt_enabled=True,
            mqtt_host="broker",
            mqtt_password=geheim,
            meross_email="jemand@example.org",
            meross_password=geheim,
            mcp_token=geheim,
            notify_webhook_token=geheim,
        ),
        True,
    )
    alles = " ".join(
        f"{s.name} {s.befund} {s.hint or ''} "
        + " ".join(f"{a.name} {a.value} {a.source}" for a in s.angaben)
        for s in items
    )
    assert geheim not in alles
    assert "hinterlegt" in alles


def test_herkunft_unterscheidet_umgebung_und_standard(session: Session) -> None:
    """Die zweite Frage, die keine .env beantwortet: Steht dieser Wert so da, weil ihn
    jemand gesetzt hat, oder weil ihn niemand gesetzt hat?"""
    items = uebersicht(session, _settings(mqtt_base_topic="eigenes"), None)
    mqtt = next(s for s in items if s.schluessel == "mqtt")
    sources = {a.name: a.source for a in mqtt.angaben}
    assert sources["Basis-Topic"] == "Umgebung"
    assert sources["TLS"] == "Standard"


def test_home_assistant_haengt_an_mqtt(session: Session) -> None:
    """Sie sendet inzwischen wirklich -- aber nur ueber MQTT.

    Frueher stand hier "ungebaut", weil nur die Nutzlast entworfen war. Ohne MQTT gibt
    es weiterhin keinen Weg dorthin, und die Seite darf dann nichts anderes behaupten.
    """
    ohne = uebersicht(session, _settings(), None)
    assert _state(ohne, "homeassistant") == "aus"

    mit = uebersicht(session, _settings(mqtt_enabled=True, mqtt_host="b"), None)
    assert _state(mit, "homeassistant") == "laeuft"


def test_jede_schnittstelle_hat_einen_bekannten_zustand(session: Session) -> None:
    """Gegenprobe zur Vorlage: Sie schlaegt die Marke in einer Tabelle nach und wuerde
    bei einem unbekannten Wert mit einem KeyError antworten."""
    bekannt = {"laeuft", "eingerichtet", "aus", "fehlt", "ungebaut"}
    for abweichungen in ({}, {"mqtt_enabled": True, "mqtt_host": "b"}):
        for bridge in (None, True, False):
            for s in uebersicht(session, _settings(**abweichungen), bridge):
                assert s.state in bekannt, f"{s.schluessel}: {s.state}"
