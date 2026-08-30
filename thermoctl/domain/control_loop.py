"""Die Regelentscheidung: aus einer Lage wird eine Entscheidung samt Begruendung.

Rein im Sinne von CLAUDE.md und Abschnitt 3 der Spezifikation: keine Datenbank, kein Netz,
keine Uhr. Alles, was die Entscheidung braucht, steckt in `Lage`. Genau deshalb ist sie
erschoepfend testbar (`tests/test_regelung.py`), und genau deshalb kann Teilprojekt 4 sie
unveraendert scharf schalten.

In dieser Phase wird nicht geschaltet (Trockenlauf, Abschnitt 1 der Spezifikation) — das
Ergebnis landet nur im Schattenprotokoll.
"""

from dataclasses import dataclass
from decimal import Decimal

from thermoctl.domain.zone_settings import ControlParameters

# Diese Codes entsprechen woertlich der Spalte shadow_decision.outcome_code aus Abschnitt 4
# der Spezifikation. Wer hier einen neuen Code braucht, muss zuerst dort nachtragen.
GRUND_CODE_HEIZEN = "heizen"
GRUND_CODE_AUS = "aus"
REASON_CODE_UNCHANGED = "unveraendert"
REASON_CODE_BLOCKED_MINIMUM_DURATION = "gesperrt_mindestdauer"
REASON_CODE_WINDOW_OPEN = "fenster_offen"
REASON_CODE_FROST_SENSOR_FAILURE = "frostschutz_sensorausfall"
REASON_CODE_NO_SOURCE = "keine_quelle"


@dataclass(frozen=True)
class Lage:
    """Alles, was die Entscheidung sieht — unveraendert aus Abschnitt 6 der Spezifikation."""

    ist_c: Decimal | None
    soll_c: Decimal
    soll_grund: str
    # Der Frostschutz-Sollwert der Anlage. Getrennt von `soll_c`, weil er auch dann gilt,
    # wenn `soll_c` gerade etwas ganz anderes sagt: bei ausgefallenem Sensor faellt die
    # Regelung darauf zurueck, statt sich auf einen Sollwert zu stuetzen, den sie nicht
    # mehr ueberpruefen kann.
    frost_c: Decimal
    operating_mode: str  # auto | manual | off
    heizt_gerade: bool
    seit_s: int | None  # wie lange der aktuelle Zustand schon gilt
    window_open: bool
    window_closed_for_s: int | None
    sensor_status: str  # ok | veraltet | keine_quelle
    parameter: ControlParameters


@dataclass(frozen=True)
class Entscheidung:
    heizen: bool
    grund_code: str
    grund: str


def entscheiden(lage: Lage) -> Entscheidung:
    """Die Rangfolge aus Abschnitt 6 der Spezifikation, Regel fuer Regel.

    Die erste zutreffende Regel gewinnt und liefert sofort zurueck — absichtlich als frueher
    Ausstieg je Regel geschrieben, statt als eine grosse Bedingung, damit die Rangfolge im
    Code so aussieht wie in der Spezifikation und niemand sie aus dem Ausdruck rekonstruieren
    muss.
    """
    # Regel 1 — Sensorausfall schlaegt alles. Ohne verlaesslichen Ist-Wert ist "voll heizen"
    # falsch (Ueberhitzung ohne Rueckmeldung) — "dauerhaft aus" aber ebenso, und zwar
    # gefaehrlicher: Genau so friert im Januar eine Leitung ein. Deshalb faellt die Regelung
    # auf den Frostschutz-Sollwert zurueck und regelt mit dem letzten bekannten Wert
    # weiter. Der Frostschutzwert liegt tief; die Anlage kann damit hoechstens auf ein
    # unbedenkliches Niveau heizen, haelt die Wohnung aber ueber der Frostgrenze.
    #
    # Liegt gar kein Wert vor, bleibt nur "aus" — es gibt nichts, woran zu regeln waere.
    if lage.sensor_status == "keine_quelle" or lage.ist_c is None:
        return Entscheidung(
            heizen=False,
            grund_code=REASON_CODE_NO_SOURCE,
            grund=(
                "Keine verwertbare Messung fuer die Zone — es gibt nichts, woran zu regeln "
                "waere. Ventil bleibt zu."
            ),
        )

    # Die Kalibrierung des Sensors ist eine Eigenschaft der Messung, keine der Regel —
    # deshalb hier und nur hier auf den Ist-Wert gerechnet, vor jeder weiteren Regel.
    ist_c = lage.ist_c + lage.parameter.temperature_offset_k

    # Regel 2 — Betriebsart 'off' heisst Frostschutz, nicht stromlos. Der Aufrufer loest den
    # Sollwert bereits vor uns auf (`aufgeloester_sollwert`), die dort bei 'off' den
    # Frostschutzwert liefert. `lage.soll_c` ist also im Fall 'off' bereits der
    # Frostschutz-Sollwert, und die "normale Regel" ist genau das, was ab Regel 3 folgt —
    # diese Funktion muss die Betriebsart deshalb nicht gesondert verzweigen, nur die
    # Herkunft des Sollwerts (`soll_grund`) landet unveraendert in der Begruendung.

    # Der wirksame Sollwert. Bei ausgefallenem Sensor ist das der Frostschutzwert (Regel 1),
    # sonst der aufgeloeste Sollwert der Zone. Ab hier laeuft in beiden Faellen dieselbe
    # Regel — das ist der Kern von "Aus heisst Frostschutz, nicht stromlos".
    sensor_ausgefallen = lage.sensor_status == "veraltet"
    soll_c = lage.frost_c if sensor_ausgefallen else lage.soll_c
    soll_grund = (
        f"Sensorwert veraltet — Frostschutz {lage.frost_c} °C statt {lage.soll_c} °C"
        if sensor_ausgefallen
        else lage.soll_grund
    )

    # Regel 3 — Fenster offen: aus, ungeachtet der Temperatur.
    if lage.window_open:
        return Entscheidung(
            heizen=False,
            grund_code=REASON_CODE_WINDOW_OPEN,
            grund=f"Fenster offen — Ist {ist_c} °C, Soll {soll_c} °C ({soll_grund}).",
        )

    # Regel 4 — Wiederanlaufverzoegerung: Fenster ist zu, aber der Raum kuehlt noch nach.
    # 'None' bei fenster_zu_seit_s heisst "kein anstehender Nachlauf" (Fenster war seit
    # Beginn der Aufzeichnung nie offen) — dann gibt es nichts abzuwarten.
    verzoegerung = lage.parameter.window_resume_delay_seconds
    if (
        lage.window_closed_for_s is not None
        and lage.window_closed_for_s < verzoegerung
    ):
        return Entscheidung(
            heizen=False,
            grund_code=GRUND_CODE_AUS,
            grund=(
                f"Fenster seit {lage.window_closed_for_s}s zu, Wiederanlauf erst nach "
                f"{verzoegerung}s — Raum kuehlt noch nach."
            ),
        )

    # Regel 5 — Mindestschaltdauer schuetzt das Ventil vor Kurztakten. 'None' bei seit_s
    # heisst "Dauer des aktuellen Zustands unbekannt" — typischerweise der erste Zyklus nach
    # einem Neustart, ohne Vorgeschichte. Eine Sperre gegen eine Dauer zu verhaengen, die wir
    # nicht kennen, waere selbst willkuerlich; deshalb greift die Sperre nur, wenn seit_s
    # bekannt UND zu kurz ist. Ein frisch gestarteter Dienst darf also sofort nach Hysterese
    # entscheiden, statt bis zum Ablauf einer Frist zu warten, die nie zu laufen begann.
    minimum_duration = (
        lage.parameter.min_on_seconds if lage.heizt_gerade else lage.parameter.min_off_seconds
    )
    if lage.seit_s is not None and lage.seit_s < minimum_duration:
        state = "Heizen" if lage.heizt_gerade else "Aus"
        return Entscheidung(
            heizen=lage.heizt_gerade,
            grund_code=REASON_CODE_BLOCKED_MINIMUM_DURATION,
            grund=(
                f"Zustand '{state}' erst seit {lage.seit_s}s, Mindestdauer {minimum_duration}s "
                "— Ventil bleibt unangetastet."
            ),
        )

    # Regel 6 — Hysterese. Das Altsystem kennt sie nicht (`if ist < soll: an, sonst aus`) und
    # schaltet am Sollwert in jedem Zyklus um; `h` ist genau die Bandbreite, die das
    # verhindert.
    h = lage.parameter.hysteresis_k
    if not lage.heizt_gerade and ist_c < soll_c - h:
        return Entscheidung(
            heizen=True,
            grund_code=(
                REASON_CODE_FROST_SENSOR_FAILURE if sensor_ausgefallen
                else GRUND_CODE_HEIZEN
            ),
            grund=(
                f"Ist {ist_c} °C unter Soll {soll_c} °C minus Hysterese {h}K "
                f"({soll_grund})."
            ),
        )
    if lage.heizt_gerade and ist_c > soll_c + h:
        return Entscheidung(
            heizen=False,
            grund_code=GRUND_CODE_AUS,
            grund=(
                f"Ist {ist_c} °C ueber Soll {soll_c} °C plus Hysterese {h}K "
                f"({soll_grund})."
            ),
        )
    return Entscheidung(
        heizen=lage.heizt_gerade,
        grund_code=(
            REASON_CODE_FROST_SENSOR_FAILURE if sensor_ausgefallen
            else REASON_CODE_UNCHANGED
        ),
        grund=(
            f"Ist {ist_c} °C innerhalb der Hysterese um Soll {soll_c} °C ± {h}K "
            f"({soll_grund}) — Zustand bleibt."
        ),
    )
