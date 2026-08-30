"""Die Schwellen, nach denen ein Gerät auffällt.

Sie stehen in der Domäne, damit sie einmal dastehen -- und deshalb werden sie hier
einzeln geprüft und nicht nur durch die Seite hindurch: Eine Seite, die zufällig das
Richtige zeigt, weil zwei Fehler sich aufheben, ist grün und trotzdem falsch.
"""

from datetime import datetime, timedelta
from decimal import Decimal

from thermoctl.domain.device_survey import (
    BATTERY_LOW_PERCENT,
    RADIO_WEAK_LQI,
    DeviceSurvey,
    Finding,
    befunde,
)

NOW = datetime(2026, 8, 30, 12, 0)


def _befunde(**deviation) -> list[Finding]:
    argumente = {
        "active": True,
        "last_heard": NOW,
        "availability": "online",
        "battery": Decimal(80),
        "radio_quality": 120,
        "silent_after_seconds": 900,
        "now": NOW,
    }
    argumente.update(deviation)
    return befunde(**argumente)


def test_ein_gesundes_geraet_hat_nichts_zu_melden() -> None:
    assert _befunde() == []


def test_abgeschaltet_offline_stumm_batterie_und_funk_werden_je_einzeln_erkannt() -> None:
    kinds = {
        "disabled": _befunde(active=False),
        "offline": _befunde(availability="OFFLINE"),
        "silent": _befunde(last_heard=None),
        "battery": _befunde(battery=BATTERY_LOW_PERCENT),
        "radio": _befunde(radio_quality=RADIO_WEAK_LQI - 1),
    }
    for kind, gefunden in kinds.items():
        assert [b.kind for b in gefunden] == [kind], kind
        assert gefunden[0].text, "ein Befund ohne Text hilft niemandem"
    # Die Gegenprobe zu jeder Schwelle: knapp diesseits faellt nichts auf.
    assert _befunde(battery=BATTERY_LOW_PERCENT + 1) == []
    assert _befunde(radio_quality=RADIO_WEAK_LQI) == []
    assert _befunde(availability=None) == []


def test_stumm_erst_nach_der_vorgegebenen_frist_und_mit_alter_im_klartext() -> None:
    """Die Frist ist dieselbe, nach der die Regelung einen Sensor aufgibt."""
    assert _befunde(last_heard=NOW - timedelta(seconds=900)) == []
    for elapsed, wort in (
        (timedelta(minutes=30), "seit 30 Minuten still"),
        (timedelta(hours=5), "seit 5 Stunden still"),
        (timedelta(days=1), "seit 1 Tag still"),
        (timedelta(days=3), "seit 3 Tagen still"),
    ):
        gefunden = _befunde(last_heard=NOW - elapsed)
        assert [b.text for b in gefunden] == [wort]


def test_schwere_ordnet_ausfall_vor_ankuendigung() -> None:
    """Ein stummes Gerät steht vor einer schwachen Batterie.

    Ohne die Ordnung stuende die halb leere Zelle oben und der ausgefallene Sensor
    darunter -- die Seite raete dann zum Falschen.
    """

    def schau(*befunde_: Finding) -> DeviceSurvey:
        return DeviceSurvey(
            device_id=1,
            name="x",
            modell=None,
            integration="Zigbee2MQTT",
            ist_group=False,
            capabilities=[],
            zones=[],
            last_heard=None,
            battery=None,
            radio_quality=None,
            befunde=list(befunde_),
        )

    offline = schau(Finding("offline", "o"))
    battery = schau(Finding("battery", "b"))
    gesund = schau()
    assert offline.schwere < battery.schwere < gesund.schwere
    assert gesund.in_ordnung and not offline.in_ordnung
    # Mehrere Befunde: der dringendste bestimmt den Platz.
    assert schau(Finding("battery", "b"), Finding("offline", "o")).schwere == offline.schwere
    # Eine unbekannte Art landet hinten statt zu stolpern.
    assert schau(Finding("neuartig", "?")).schwere > battery.schwere
