from datetime import datetime
from pathlib import Path

from fastapi.templating import Jinja2Templates

from thermoctl.db.base import utcnow

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Bootstrap und HTMX liegen als Dateien in diesem Verzeichnis (siehe
# static/HERKUNFT.md) und werden lokal ausgeliefert, nicht ueber ein CDN --
# `thermoctl` soll auch ohne Internetzugang im Heimnetz benutzbar bleiben.
STATIC_DIR = Path(__file__).parent / "static"


def alter_in_worten(zeitpunkt: datetime | None, jetzt: datetime | None = None) -> str:
    """Wie lange etwas her ist, in Worten — 'vor 3 Minuten' statt eines Zeitstempels.

    Fuer eine Heizungssteuerung ist genau das die Frage: Ist dieser Messwert frisch oder
    liegt er seit gestern herum? Ein roher Zeitstempel mit Mikrosekunden beantwortet sie
    nicht, er verlangt Kopfrechnen — und im Zweifel rechnet man falsch.

    Alle Zeitpunkte sind naive UTC, wie im ganzen Projekt.
    """
    if zeitpunkt is None:
        return "noch nie"
    verstrichen = ((jetzt or utcnow()) - zeitpunkt).total_seconds()
    if verstrichen < 0:
        # Ein leicht falsch gestellter Sensor darf nicht 'in -3 Minuten' anzeigen.
        return "gerade eben"
    for grenze, teiler, einzahl, mehrzahl in (
        (60, 1, "Sekunde", "Sekunden"),
        (3600, 60, "Minute", "Minuten"),
        (86400, 3600, "Stunde", "Stunden"),
    ):
        if verstrichen < grenze:
            wert = int(verstrichen // teiler)
            return f"vor {wert} {einzahl if wert == 1 else mehrzahl}"
    tage = int(verstrichen // 86400)
    return f"vor {tage} {'Tag' if tage == 1 else 'Tagen'}"


templates.env.filters["alter"] = alter_in_worten
