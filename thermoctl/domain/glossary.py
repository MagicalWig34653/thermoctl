"""Das Glossar der Fachbegriffe: eine einzige Quelle für Oberfläche, Docs und Webseite.

Die Begriffe stehen in `thermoctl/data/glossar.json` und nirgends sonst. Was daraus
entsteht, ist abgeleitet:

* die Seite `/glossar` und die kleinen Hilfe-Symbole in der Oberfläche
  (`web/glossary_views.py`, `web/templates/glossar.html`),
* die Datei `docs/glossar.md` (`tools/glossar_erzeugen.py` ruft `render_markdown`
  hier auf, und ein Test hält beide Fassungen im Gleichschritt),
* später die öffentliche Webseite, die dieselbe JSON-Datei liest.

Wer einen Begriff ändert, ändert ihn deshalb in der JSON-Datei und lässt danach
`python -m tools.glossar_erzeugen` laufen.

Dieses Modul ist reine Logik: Es liest nur die eine, mitgelieferte Datei (oder einen
übergebenen Pfad) und kennt weder Datenbank noch Netz noch Uhr (Grundsatz 6).
`parse_glossary` prüft *strenger*, als das Lesen es bräuchte -- Pflichtfelder,
doppelte Begriffe, tote Verweise, unbekannte Felder. Der Grund: ein Glossar, das beim
ersten Öffnen einer Seite mit einem Fehler scheitert oder stillschweigend einen
Verweis ins Leere führt, fällt erst im Betrieb auf. Hier fällt es beim Laden auf, und
`tests/test_glossary.py` lädt es bei jedem Testlauf.
"""

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from pathlib import Path

#: Die Bereiche, in die sich die Begriffe einordnen. Reihenfolge = Reihenfolge in der
#: Übersicht der Docs; ein Begriff mit einem anderen Bereich ist ein Tippfehler.
AREAS: tuple[str, ...] = ("Regelung", "Sensorik", "Betrieb", "Oberfläche")

SUPPORTED_VERSION = 1

#: Obergrenzen, damit "Kurzfassung" und "Erklärung" ehrlich kurz bleiben. Die Zahlen
#: sind Leitplanken gegen ausufernde Einträge, keine Fachaussage.
MAX_SHORT_LENGTH = 180
MAX_EXPLANATION_LENGTH = 800

_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_REQUIRED_FIELDS = ("id", "term", "short", "explanation", "area")
_OPTIONAL_FIELDS = ("synonyms", "see_also")


class GlossaryError(ValueError):
    """Das Glossar ist fehlerhaft. Die Meldung nennt den Eintrag und das Feld."""


@dataclass(frozen=True)
class GlossaryEntry:
    """Ein Begriff mit Kurzfassung, Erklärung und Verweisen."""

    id: str
    term: str
    short: str
    explanation: str
    area: str
    synonyms: tuple[str, ...] = ()
    see_also: tuple[str, ...] = ()

    @property
    def sort_key(self) -> str:
        return _sort_key(self.term)

    @property
    def letter(self) -> str:
        """Der Anfangsbuchstabe für die Sprungmarken: ``Ä`` zählt als ``A``."""
        return self.sort_key[0].upper()

    @property
    def search_text(self) -> str:
        """Alles, worin die Suche trifft, kleingeschrieben und in einem Stück."""
        parts = (self.term, *self.synonyms, self.short, self.explanation)
        return " ".join(parts).casefold()


@dataclass(frozen=True)
class Glossary:
    """Alle Begriffe, alphabetisch nach Begriff geordnet."""

    entries: tuple[GlossaryEntry, ...]

    def get(self, entry_id: str) -> GlossaryEntry:
        """Der Eintrag zu einer Kennung; ein unbekannter Anker ist ein Fehler.

        Ausdrücklich keine stille Rückgabe von ``None``: ein Hilfe-Symbol, das auf einen
        nicht vorhandenen Anker zeigt, wäre ein toter Verweis, den niemand bemerkt.
        """
        for entry in self.entries:
            if entry.id == entry_id:
                return entry
        raise KeyError(f"Kein Glossareintrag mit der Kennung {entry_id!r}.")

    def ids(self) -> frozenset[str]:
        return frozenset(entry.id for entry in self.entries)

    def letters(self) -> tuple[str, ...]:
        """Die vorkommenden Anfangsbuchstaben in alphabetischer Reihenfolge."""
        return tuple(sorted({entry.letter for entry in self.entries}))

    def grouped(self) -> tuple[tuple[str, tuple[GlossaryEntry, ...]], ...]:
        """Die Einträge je Anfangsbuchstabe, beides alphabetisch."""
        return tuple(
            (letter, tuple(entry for entry in self.entries if entry.letter == letter))
            for letter in self.letters()
        )


def _sort_key(term: str) -> str:
    """Ordnungsschlüssel nach deutscher Gepflogenheit: ä=a, ö=o, ü=u, ß=ss.

    Führende Zeichen ohne Buchstabenwert (Klammern, Anführungszeichen) zählen nicht --
    sonst stünde „(Beta)…“ vor „Abwesenheit“.
    """
    text = term.casefold()
    for source, target in (("ä", "a"), ("ö", "o"), ("ü", "u"), ("ß", "ss")):
        text = text.replace(source, target)
    stripped = text.lstrip(" \t\"'„“‚‘(")
    return stripped or text


def _text_field(raw: dict[str, object], name: str, label: str, *, maximum: int | None) -> str:
    value = raw.get(name)
    if not isinstance(value, str) or not value.strip():
        raise GlossaryError(f"{label}: Pflichtfeld „{name}“ fehlt oder ist leer.")
    text = value.strip()
    if maximum is not None and len(text) > maximum:
        raise GlossaryError(
            f"{label}: „{name}“ ist mit {len(text)} Zeichen zu lang (höchstens {maximum}). "
            "Ein Glossareintrag soll kurz bleiben."
        )
    return text


def _text_list(raw: dict[str, object], name: str, label: str) -> tuple[str, ...]:
    value = raw.get(name, [])
    if not isinstance(value, list):
        raise GlossaryError(f"{label}: „{name}“ muss eine Liste sein.")
    items: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise GlossaryError(f"{label}: „{name}“ enthält einen leeren oder keinen Text.")
        items.append(item.strip())
    return tuple(items)


def _parse_entry(raw: object, position: int) -> GlossaryEntry:
    if not isinstance(raw, dict):
        raise GlossaryError(f"Eintrag Nr. {position}: muss ein Objekt sein.")
    label = f"Eintrag Nr. {position}"
    identifier = raw.get("id")
    if isinstance(identifier, str) and identifier:
        label = f"Eintrag „{identifier}“"
    unknown = sorted(set(raw) - set(_REQUIRED_FIELDS) - set(_OPTIONAL_FIELDS))
    if unknown:
        raise GlossaryError(
            f"{label}: unbekannte Felder {', '.join(unknown)} (vertippt?). "
            f"Erlaubt sind {', '.join(_REQUIRED_FIELDS + _OPTIONAL_FIELDS)}."
        )
    entry_id = _text_field(raw, "id", label, maximum=None)
    if not _ID_PATTERN.fullmatch(entry_id):
        raise GlossaryError(
            f"{label}: Kennung „{entry_id}“ ist als Anker ungeeignet "
            "(erlaubt: Kleinbuchstaben a-z, Ziffern und einzelne Bindestriche)."
        )
    area = _text_field(raw, "area", label, maximum=None)
    if area not in AREAS:
        raise GlossaryError(
            f"{label}: Bereich „{area}“ ist unbekannt. Erlaubt sind {', '.join(AREAS)}."
        )
    return GlossaryEntry(
        id=entry_id,
        term=_text_field(raw, "term", label, maximum=None),
        short=_text_field(raw, "short", label, maximum=MAX_SHORT_LENGTH),
        explanation=_text_field(raw, "explanation", label, maximum=MAX_EXPLANATION_LENGTH),
        area=area,
        synonyms=_text_list(raw, "synonyms", label),
        see_also=_text_list(raw, "see_also", label),
    )


def parse_glossary(data: object) -> Glossary:
    """Prüft die geladene JSON-Struktur und baut daraus das Glossar.

    Prüft in dieser Reihenfolge: Aufbau, Felder jedes Eintrags, eindeutige Kennungen,
    eindeutige Namen (Begriff *und* Synonyme, ohne Beachtung der Groß-/Kleinschreibung --
    zwei Einträge, die auf denselben Suchbegriff antworten, wären mehrdeutig) und zuletzt
    die Verweise „siehe auch“.
    """
    if not isinstance(data, dict):
        raise GlossaryError("Das Glossar muss ein Objekt mit „version“ und „entries“ sein.")
    if data.get("version") != SUPPORTED_VERSION:
        raise GlossaryError(
            f"Glossar-Version {data.get('version')!r} wird nicht unterstützt "
            f"(erwartet: {SUPPORTED_VERSION})."
        )
    raw_entries = data.get("entries")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise GlossaryError("„entries“ muss eine nicht leere Liste sein.")

    entries = [_parse_entry(raw, position) for position, raw in enumerate(raw_entries, 1)]

    seen_ids: set[str] = set()
    for entry in entries:
        if entry.id in seen_ids:
            raise GlossaryError(f"Die Kennung „{entry.id}“ kommt mehrfach vor.")
        seen_ids.add(entry.id)

    owners: dict[str, str] = {}
    for entry in entries:
        names = (entry.term, *entry.synonyms)
        for name in names:
            key = name.casefold()
            previous = owners.get(key)
            if previous is not None:
                where = "doppelt" if previous == entry.id else f"auch bei „{previous}“"
                raise GlossaryError(
                    f"Eintrag „{entry.id}“: Der Name „{name}“ kommt {where} vor. "
                    "Jeder Begriff und jedes Synonym darf nur zu einem Eintrag gehören."
                )
            owners[key] = entry.id

    for entry in entries:
        seen_links: set[str] = set()
        for target in entry.see_also:
            if target == entry.id:
                raise GlossaryError(
                    f"Eintrag „{entry.id}“: „siehe auch“ verweist auf sich selbst."
                )
            if target not in seen_ids:
                raise GlossaryError(
                    f"Eintrag „{entry.id}“: „siehe auch“ verweist auf „{target}“, "
                    "den es nicht gibt."
                )
            if target in seen_links:
                raise GlossaryError(
                    f"Eintrag „{entry.id}“: „siehe auch“ nennt „{target}“ mehrfach."
                )
            seen_links.add(target)

    return Glossary(entries=tuple(sorted(entries, key=lambda item: (item.sort_key, item.id))))


def load_glossary(path: Path) -> Glossary:
    """Liest und prüft eine Glossar-Datei."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise GlossaryError(f"{path.name}: kein gültiges JSON ({exc}).") from exc
    return parse_glossary(data)


@lru_cache(maxsize=1)
def default_glossary() -> Glossary:
    """Das mitgelieferte Glossar, einmal geladen und geprüft."""
    resource = resources.files("thermoctl.data").joinpath("glossar.json")
    try:
        data = json.loads(resource.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:  # pragma: no cover - die Datei gehört dem Paket
        raise GlossaryError(f"glossar.json: kein gültiges JSON ({exc}).") from exc
    return parse_glossary(data)


MARKDOWN_INTRO = (
    "Die Fachbegriffe der Oberfläche in einfachen Worten. Dieselben Erklärungen stehen "
    "in der Weboberfläche unter `/glossar`, und die kleinen Fragezeichen neben "
    "Einstellungen führen direkt hierher."
)

MARKDOWN_NOTICE = (
    "<!-- Diese Datei wird erzeugt. Nicht von Hand ändern: Quelle ist "
    "thermoctl/data/glossar.json, Erzeugung mit `python -m tools.glossar_erzeugen`. -->"
)


def render_markdown(glossary: Glossary) -> str:
    """Die Docs-Fassung des Glossars (`docs/glossar.md`), vollständig und deterministisch.

    Lebt hier und nicht im Werkzeugskript, damit der Synchronitätstest dieselbe
    Funktion aufruft wie das Skript: zwei Wege, dieselbe Datei zu erzeugen, würden
    irgendwann auseinanderlaufen.
    """
    lines: list[str] = ["# Glossar", "", MARKDOWN_NOTICE, "", MARKDOWN_INTRO, ""]
    lines.append(
        "**Bereiche:** " + " · ".join(AREAS) + " — der Bereich steht unter jedem Begriff."
    )
    lines.append("")
    lines.append(" · ".join(f"[{letter}](#{letter.lower()})" for letter in glossary.letters()))
    lines.append("")
    for letter, group in glossary.grouped():
        lines.extend([f"## {letter}", ""])
        for entry in group:
            lines.extend(
                [
                    f'<a id="{entry.id}"></a>',
                    f"### {entry.term}",
                    "",
                    f"*{entry.area}* — {entry.short}",
                    "",
                    entry.explanation,
                    "",
                ]
            )
            if entry.synonyms:
                lines.extend([f"Auch: {', '.join(entry.synonyms)}", ""])
            if entry.see_also:
                links = ", ".join(
                    f"[{glossary.get(target).term}](#{target})" for target in entry.see_also
                )
                lines.extend([f"Siehe auch: {links}", ""])
    return "\n".join(lines).rstrip("\n") + "\n"
