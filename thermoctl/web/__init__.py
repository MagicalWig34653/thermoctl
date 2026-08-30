from datetime import datetime
from decimal import Decimal
from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from thermoctl.db.base import utcnow

TEMPLATES_DIR = Path(__file__).parent / "templates"
def _logged_in_user(request: Request) -> dict[str, object]:
    """Stellt jeder Vorlage den angemeldeten Benutzer bereit.

    Ohne das trug die Kopfleiste auf der Startseite den Namen und ueberall sonst das
    Wort "Konto" -- die Startseite war die einzige Ansicht, die `benutzer` in ihren
    Kontext legte. Ein Bedienelement, das je nach Seite anders heisst, sieht aus wie ein
    anderes Bedienelement.

    Der Benutzer wird nicht erneut aufgeloest: Die Anmeldung hat ihn ohnehin schon
    ermittelt und unter `request.state` hinterlegt. Fehlt er dort, steht schlicht nichts
    da -- die Anmeldeseite hat keinen.
    """
    return {"kopf_benutzer": getattr(request.state, "user", None)}


templates = Jinja2Templates(
    directory=str(TEMPLATES_DIR), context_processors=[_logged_in_user]
)

# Bootstrap und HTMX liegen als Dateien in diesem Verzeichnis (siehe
# static/HERKUNFT.md) und werden lokal ausgeliefert, nicht ueber ein CDN --
# `thermoctl` soll auch ohne Internetzugang im Heimnetz benutzbar bleiben.
STATIC_DIR = Path(__file__).parent / "static"


def ist_teilaustausch(request: Request) -> bool:
    """Ob htmx nur ein Stueck der Seite nachlaedt -- und nicht eine ganze Seite holt.

    `HX-Request` allein reicht dafuer **nicht**: Seit `hx-boost="true"` am <body> haengt,
    traegt jede gewoehnliche Navigation diesen Kopf. Sechs Ansichten haben daraufhin nur
    ihren Inhalt ohne Rahmen gerendert -- wer ueber das Menue auf /geraete oder /audit
    ging, verlor die Kopfleiste und kam nur durch Neuladen zurueck. Beim Direktaufruf
    derselben Adresse war alles in Ordnung, weshalb es niemandem auffiel, der die Seite
    zum Pruefen einfach aufrief.

    `HX-Boosted` setzt htmx zusaetzlich, wenn die Anfrage aus einem geboosteten Verweis
    stammt. Nur ohne diesen Kopf ist es wirklich ein Teilaustausch.
    """
    return "HX-Request" in request.headers and "HX-Boosted" not in request.headers


def age_in_words(moment: datetime | None, now: datetime | None = None) -> str:
    """Wie lange etwas her ist, in Worten — 'vor 3 Minuten' statt eines Zeitstempels.

    Fuer eine Heizungssteuerung ist genau das die Frage: Ist dieser Messwert frisch oder
    liegt er seit gestern herum? Ein roher Zeitstempel mit Mikrosekunden beantwortet sie
    nicht, er verlangt Kopfrechnen — und im Zweifel rechnet man falsch.

    Alle Zeitpunkte sind naive UTC, wie im ganzen Projekt.
    """
    if moment is None:
        return "noch nie"
    elapsed = ((now or utcnow()) - moment).total_seconds()
    if elapsed < 0:
        # Ein leicht falsch gestellter Sensor darf nicht 'in -3 Minuten' anzeigen.
        return "gerade eben"
    for limit, teiler, einzahl, mehrzahl in (
        (60, 1, "Sekunde", "Sekunden"),
        (3600, 60, "Minute", "Minuten"),
        (86400, 3600, "Stunde", "Stunden"),
    ):
        if elapsed < limit:
            value = int(elapsed // teiler)
            return f"vor {value} {einzahl if value == 1 else mehrzahl}"
    days = int(elapsed // 86400)
    return f"vor {days} {'Tag' if days == 1 else 'Tagen'}"


# Der Bereich, ueber den Temperaturen als Farbe dargestellt werden. Nicht die Grenzen
# der Eingabe, sondern der Bereich, in dem sich Wohnraumtemperaturen
# tatsaechlich bewegen -- sonst laege alles in derselben blassen Mitte.
SPUR_KALT = Decimal("15")
SPUR_WARM = Decimal("23")


def waermeanteil(temperature: Decimal | None) -> float:
    """0 = kuehlster darstellbarer Sollwert, 1 = waermster. Ausserhalb wird gekappt.

    Steht hier und nicht in einer der beiden Ansichten: Startseite und Wochenansicht
    zeigen dieselbe Groesse, und zwei Skalen fuer dieselbe Aussage waeren zwei Skalen,
    die auseinanderlaufen.
    """
    if temperature is None:
        return 0.5
    anteil = (temperature - SPUR_KALT) / (SPUR_WARM - SPUR_KALT)
    return float(min(max(anteil, Decimal(0)), Decimal(1)))


def grad(value: Decimal | float | None, stellen: int = 1) -> str:
    """Eine Temperatur, wie man sie im Deutschen schreibt: mit Komma.

    Nur fuer die Anzeige. In `value`-Attributen von `<input type="number">` hat ein
    Komma nichts zu suchen -- der Browser verwirft den Wert dann still, und das Feld
    steht leer da, ohne dass jemand sagen kann warum.
    """
    if value is None:
        return "–"
    return f"{value:.{stellen}f}".replace(".", ",")


templates.env.filters["age"] = age_in_words
templates.env.filters["grad"] = grad
