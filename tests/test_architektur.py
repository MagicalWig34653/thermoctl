import ast
from pathlib import Path

WURZEL = Path(__file__).resolve().parent.parent / "thermoctl"
VERBOTEN_FUER_DOMAIN = ("thermoctl.web", "thermoctl.api", "fastapi")


def _importe(datei: Path) -> set[str]:
    baum = ast.parse(datei.read_text(encoding="utf-8"))
    namen: set[str] = set()
    for knoten in ast.walk(baum):
        if isinstance(knoten, ast.Import):
            namen.update(a.name for a in knoten.names)
        elif isinstance(knoten, ast.ImportFrom) and knoten.module:
            namen.add(knoten.module)
    return namen


def test_domaene_kennt_keinen_adapter() -> None:
    """Eine Regel wird einmal implementiert (Grundsatz 6).

    Sobald die Domaene einen Adapter importiert, weicht diese Trennung schleichend auf —
    deshalb steht sie hier als Test und nicht nur als Absicht in der Spezifikation.
    """
    verstoesse = [
        f"{datei.relative_to(WURZEL)} importiert {name}"
        for datei in (WURZEL / "domain").rglob("*.py")
        for name in _importe(datei)
        if name.startswith(VERBOTEN_FUER_DOMAIN)
    ]
    assert not verstoesse, "\n".join(verstoesse)


def test_mcp_kennt_keinen_anderen_adapter() -> None:
    """Die drei Adapter bleiben gleichberechtigte Nachbarn."""
    mcp_pfad = WURZEL / "mcp"
    verstoesse = [
        f"{datei.relative_to(WURZEL)} importiert {name}"
        for datei in mcp_pfad.rglob("*.py")
        for name in _importe(datei)
        if name.startswith(("thermoctl.web", "thermoctl.api"))
    ]
    assert not verstoesse, "\n".join(verstoesse)


def test_kein_modell_nutzt_verbotene_spaltentypen() -> None:
    """Kein ENUM, kein SET, keine JSON-Spalte — SQLite kann sie nicht."""
    verstoesse = [
        f"{datei.relative_to(WURZEL)}: {wort}"
        for datei in (WURZEL / "db" / "models").rglob("*.py")
        for wort in ("Enum(", "JSON(", "SET(")
        if wort in datei.read_text(encoding="utf-8")
    ]
    assert not verstoesse, "\n".join(verstoesse)


def test_keine_zweite_vorlagen_umgebung() -> None:
    """Alle Ansichten benutzen `thermoctl.web.templates`, nicht ihre eigene.

    Der Anlass ist ein echter Fehler: `start_views.py` baute eine eigene
    `Jinja2Templates`-Instanz mit dem **relativen** Pfad `thermoctl/web/templates`. Das
    ging oertlich gut, weil die Tests im Projektverzeichnis laufen — im Container liegt
    das Paket in `site-packages` und das Arbeitsverzeichnis ist `/app`. Dort haette die
    Startseite mit einem Fehler geantwortet, und kein Test haette es gemerkt.

    Zweitens sieht eine eigene Umgebung die gemeinsamen Filter nicht. Genau daran ist es
    schliesslich aufgefallen: Ein neuer Filter wirkte auf jeder Seite ausser dieser.
    """
    verstoesse = [
        str(datei.relative_to(WURZEL))
        for datei in (WURZEL / "web").rglob("*.py")
        if "Jinja2Templates(" in datei.read_text(encoding="utf-8")
        and datei.name != "__init__.py"
    ]
    assert not verstoesse, (
        "Eigene Vorlagen-Umgebung statt der gemeinsamen aus thermoctl.web: "
        + ", ".join(verstoesse)
    )
