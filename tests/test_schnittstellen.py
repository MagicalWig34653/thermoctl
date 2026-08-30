"""Die Schnittstellen-Uebersicht.

Der Wert dieser Seite steht und faellt mit einer Sache: dass ihre Zustandsangabe stimmt.
"Eingerichtet" neben einer Verbindung, die gar nicht zustande kommt, ist schlimmer als
keine Seite -- dann sucht man den Fehler anderswo.
"""


import pytest
from sqlalchemy.orm import Session

from tests.hilfen import benutzer_mit_rechten
from thermoctl.auth.tokens import token_ausstellen
from thermoctl.config import Settings
from thermoctl.domain.schnittstellen import uebersicht


def _einstellungen(**abweichungen: object) -> Settings:
    grund: dict[str, object] = {
        "_env_file": None,
        "database_url": "sqlite://",
        "secret_key": "s" * 32,
    }
    grund.update(abweichungen)
    return Settings(**grund)  # type: ignore[arg-type]


def _zustand(schnittstellen: list, schluessel: str) -> str:
    return next(s.zustand for s in schnittstellen if s.schluessel == schluessel)


@pytest.mark.parametrize(
    ("abweichungen", "bruecke", "erwartet"),
    [
        ({}, None, "aus"),
        ({"mqtt_enabled": True}, None, "fehlt"),
        ({"mqtt_enabled": True, "mqtt_host": "broker"}, None, "eingerichtet"),
        ({"mqtt_enabled": True, "mqtt_host": "broker"}, True, "laeuft"),
        ({"mqtt_enabled": True, "mqtt_host": "broker"}, False, "fehlt"),
    ],
)
def test_mqtt_zustand(
    session: Session, abweichungen: dict, bruecke: bool | None, erwartet: str
) -> None:
    """Fuenf unterscheidbare Lagen -- und der Unterschied zwischen "eingeschaltet" und
    "kommt wirklich an" ist genau der, den eine .env nicht beantwortet."""
    liste = uebersicht(session, _einstellungen(**abweichungen), bruecke)
    assert _zustand(liste, "mqtt") == erwartet


def test_rest_ohne_token_ist_nicht_benutzbar(session: Session) -> None:
    """Die Schnittstelle *ist* immer da. Ohne Token kommt trotzdem niemand hinein, und
    genau das soll dastehen."""
    liste = uebersicht(session, _einstellungen(), None)
    rest = next(s for s in liste if s.schluessel == "rest")
    assert rest.zustand == "eingerichtet"
    assert "kein gültiges Token" in rest.befund


def test_rest_mit_token_laeuft(session: Session) -> None:
    nutzer = benutzer_mit_rechten(session, "schnittstellen-nutzer", [("zone.read", None)])
    token_ausstellen(session, nutzer, "Probe", [("zone.read", None)], None)
    liste = uebersicht(session, _einstellungen(), None)
    assert _zustand(liste, "rest") == "laeuft"


def test_kein_geheimnis_steht_auf_der_seite(session: Session) -> None:
    """Grundsatz 2. Die Seite sagt, *ob* etwas hinterlegt ist, nie was."""
    geheim = "streng-geheimes-passwort"
    liste = uebersicht(
        session,
        _einstellungen(
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
        f"{s.name} {s.befund} {s.hinweis or ''} "
        + " ".join(f"{a.name} {a.wert} {a.quelle}" for a in s.angaben)
        for s in liste
    )
    assert geheim not in alles
    assert "hinterlegt" in alles


def test_herkunft_unterscheidet_umgebung_und_standard(session: Session) -> None:
    """Die zweite Frage, die keine .env beantwortet: Steht dieser Wert so da, weil ihn
    jemand gesetzt hat, oder weil ihn niemand gesetzt hat?"""
    liste = uebersicht(session, _einstellungen(mqtt_base_topic="eigenes"), None)
    mqtt = next(s for s in liste if s.schluessel == "mqtt")
    quellen = {a.name: a.quelle for a in mqtt.angaben}
    assert quellen["Basis-Topic"] == "Umgebung"
    assert quellen["TLS"] == "Standard"


def test_home_assistant_verspricht_nichts(session: Session) -> None:
    """Sie ist entworfen, aber sendet nicht. Eine Seite, die sie als 'eingerichtet'
    fuehrte, waere eine Zusage, die niemand einloest."""
    liste = uebersicht(session, _einstellungen(), None)
    assert _zustand(liste, "homeassistant") == "ungebaut"


def test_jede_schnittstelle_hat_einen_bekannten_zustand(session: Session) -> None:
    """Gegenprobe zur Vorlage: Sie schlaegt die Marke in einer Tabelle nach und wuerde
    bei einem unbekannten Wert mit einem KeyError antworten."""
    bekannt = {"laeuft", "eingerichtet", "aus", "fehlt", "ungebaut"}
    for abweichungen in ({}, {"mqtt_enabled": True, "mqtt_host": "b"}):
        for bruecke in (None, True, False):
            for s in uebersicht(session, _einstellungen(**abweichungen), bruecke):
                assert s.zustand in bekannt, f"{s.schluessel}: {s.zustand}"
