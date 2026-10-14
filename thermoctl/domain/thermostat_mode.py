"""Die drei Betriebsarten eines Heizkörperthermostats (Zigbee2MQTT-Ventil).

Eine Stelle, die jeder Aufrufer fragt (Grundsatz 6) -- ob thermoctl dem Ventil einen
Sollwert schreibt, einen Istwert schreibt oder es ein- und ausschaltet, steht nirgends
sonst:

* `controlled` -- thermoctl entscheidet Ein/Aus (Hysterese, Mindestschaltdauer,
  Fensterkontakt) und fährt das Ventil. Der bisherige Standard.
* `self_regulating` -- das Ventil regelt selbst. thermoctl schreibt Sollwert **und**
  Istwert (die anderswo gemessene Raumtemperatur).
* `reading_only` -- thermoctl schreibt **nur** den Istwert. Der Sollwert am Gerät wird
  nie angefasst: nicht für Zeitplan, Übersteuerung oder Boost, nicht bei offenem Fenster,
  nicht als Frostschutz und auch nicht bei der Übergabe im Notbetrieb. Das Ventil
  folgt dem, was am Gerät selbst (oder von Hand) eingestellt ist.

Der Code ist ein kurzer String in `zone_device.thermostat_mode`, ohne ENUM und ohne
CHECK-Constraint (datenbankagnostisch, Grundsatz 3); gültig ist, was `validate_mode`
durchlässt.
"""

MODE_CONTROLLED = "controlled"
MODE_SELF_REGULATING = "self_regulating"
MODE_READING_ONLY = "reading_only"

MODES = (MODE_CONTROLLED, MODE_SELF_REGULATING, MODE_READING_ONLY)

MODE_LABELS = {
    MODE_CONTROLLED: "thermoctl schaltet",
    MODE_SELF_REGULATING: "Ventil regelt selbst",
    MODE_READING_ONLY: "Nur Raumtemperatur melden",
}

# Ein erklärender Satz je Betriebsart, für die Oberfläche.
MODE_DESCRIPTIONS = {
    MODE_CONTROLLED: (
        "thermoctl entscheidet über die Ein/Aus-Anforderung — mit Hysterese, "
        "Mindestschaltdauer und Fensterkontakt — und gibt sie im scharfen Betrieb "
        "nach einem Neustart an diesen Aktor aus."
    ),
    MODE_SELF_REGULATING: (
        "thermoctl schreibt Soll- und, wenn das Gerät es annimmt, Ist-Temperatur; "
        "das Ventil regelt damit selbst."
    ),
    MODE_READING_ONLY: (
        "thermoctl schreibt nur die gemessene Raumtemperatur (Istwert) an das Ventil, "
        "nie einen Sollwert und kein Ein/Aus. Zeitplan, Übersteuerung und Boost wirken "
        "auf dieses Ventil nicht; der Sollwert bleibt, wie er am Gerät eingestellt ist."
    ),
}


def validate_mode(mode: str) -> str:
    """Gibt `mode` zurück, wenn er eine bekannte Betriebsart ist, sonst `ValueError`."""
    if mode not in MODES:
        raise ValueError(f"Unbekannte Thermostat-Betriebsart: {mode!r}")
    return mode


def writes_setpoint(mode: str) -> bool:
    """Ob thermoctl diesem Ventil einen Sollwert schreibt (Zeitplan, Frostschutz, Notbetrieb)."""
    return mode == MODE_SELF_REGULATING


def writes_actual(mode: str) -> bool:
    """Ob thermoctl diesem Ventil die gemessene Raumtemperatur (Istwert) schreibt."""
    return mode in (MODE_SELF_REGULATING, MODE_READING_ONLY)


def switches(mode: str) -> bool:
    """Ob thermoctl dieses Ventil ein- und ausschaltet (Hysterese, Mindestschaltdauer)."""
    return mode == MODE_CONTROLLED
