"""Die Schwellen, nach denen ein Gerät auffällt.

Sie stehen in der Domäne, damit sie einmal dastehen -- und deshalb werden sie hier
einzeln geprüft und nicht nur durch die Seite hindurch: Eine Seite, die zufällig das
Richtige zeigt, weil zwei Fehler sich aufheben, ist grün und trotzdem falsch.
"""

from datetime import datetime, timedelta
from decimal import Decimal

from thermoctl.domain.geraeteschau import (
    BATTERIE_SCHWACH_PROZENT,
    FUNK_SCHWACH_LQI,
    Befund,
    Geraeteschau,
    befunde,
)

JETZT = datetime(2026, 8, 30, 12, 0)


def _befunde(**abweichung) -> list[Befund]:
    argumente = {
        "aktiv": True,
        "zuletzt_gehoert": JETZT,
        "erreichbarkeit": "online",
        "batterie": Decimal(80),
        "funkguete": 120,
        "stumm_nach_sekunden": 900,
        "jetzt": JETZT,
    }
    argumente.update(abweichung)
    return befunde(**argumente)


def test_ein_gesundes_geraet_hat_nichts_zu_melden() -> None:
    assert _befunde() == []


def test_abgeschaltet_offline_stumm_batterie_und_funk_werden_je_einzeln_erkannt() -> None:
    arten = {
        "abgeschaltet": _befunde(aktiv=False),
        "offline": _befunde(erreichbarkeit="OFFLINE"),
        "stumm": _befunde(zuletzt_gehoert=None),
        "batterie": _befunde(batterie=BATTERIE_SCHWACH_PROZENT),
        "funk": _befunde(funkguete=FUNK_SCHWACH_LQI - 1),
    }
    for art, gefunden in arten.items():
        assert [b.art for b in gefunden] == [art], art
        assert gefunden[0].text, "ein Befund ohne Text hilft niemandem"
    # Die Gegenprobe zu jeder Schwelle: knapp diesseits faellt nichts auf.
    assert _befunde(batterie=BATTERIE_SCHWACH_PROZENT + 1) == []
    assert _befunde(funkguete=FUNK_SCHWACH_LQI) == []
    assert _befunde(erreichbarkeit=None) == []


def test_stumm_erst_nach_der_vorgegebenen_frist_und_mit_alter_im_klartext() -> None:
    """Die Frist ist dieselbe, nach der die Regelung einen Sensor aufgibt."""
    assert _befunde(zuletzt_gehoert=JETZT - timedelta(seconds=900)) == []
    for verstrichen, wort in (
        (timedelta(minutes=30), "seit 30 Minuten still"),
        (timedelta(hours=5), "seit 5 Stunden still"),
        (timedelta(days=1), "seit 1 Tag still"),
        (timedelta(days=3), "seit 3 Tagen still"),
    ):
        gefunden = _befunde(zuletzt_gehoert=JETZT - verstrichen)
        assert [b.text for b in gefunden] == [wort]


def test_schwere_ordnet_ausfall_vor_ankuendigung() -> None:
    """Ein stummes Gerät steht vor einer schwachen Batterie.

    Ohne die Ordnung stuende die halb leere Zelle oben und der ausgefallene Sensor
    darunter -- die Seite raete dann zum Falschen.
    """

    def schau(*befunde_: Befund) -> Geraeteschau:
        return Geraeteschau(
            geraet_id=1,
            name="x",
            modell=None,
            anbindung="Zigbee2MQTT",
            ist_gruppe=False,
            faehigkeiten=[],
            zonen=[],
            zuletzt_gehoert=None,
            batterie=None,
            funkguete=None,
            befunde=list(befunde_),
        )

    offline = schau(Befund("offline", "o"))
    batterie = schau(Befund("batterie", "b"))
    gesund = schau()
    assert offline.schwere < batterie.schwere < gesund.schwere
    assert gesund.in_ordnung and not offline.in_ordnung
    # Mehrere Befunde: der dringendste bestimmt den Platz.
    assert schau(Befund("batterie", "b"), Befund("offline", "o")).schwere == offline.schwere
    # Eine unbekannte Art landet hinten statt zu stolpern.
    assert schau(Befund("neuartig", "?")).schwere > batterie.schwere
