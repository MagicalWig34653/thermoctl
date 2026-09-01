"""Require explicit review of physical vocabulary in user-visible text sources.

This guard does not understand language and does not prove that an approved statement is
true. The registry only proves that somebody saw the exact occurrence and accepted it for
the documented stage. Reviewers remain responsible for checking the statement itself.
"""

import ast
import json
import re
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
APPROVED_OCCURRENCES = ROOT / "tests/approved_physical_vocabulary.json"

# Keep this vocabulary short and concrete. A term belongs here when its occurrence can turn
# nearby prose into a claim about a physical heating effect. Adding vocabulary is cheap: the
# registry failure presents every existing occurrence for individual review.
PHYSICAL_VOCABULARY = {
    "valves": r"\b(?:thermostat)?ventil\w*|\bvalves?\b",
    "actuators": r"\baktors?\b|\baktoren\b|\bactuators?\b|\b(?:heizungs|schalt)?aktoren?\b",
    "radiators": r"\bheizkörper\w*|\bradiators?\b",
    "heating_equipment": (
        r"\bheizkreise?\b|\bfußbodenheizung\w*|\bfussbodenheizung\w*|"
        r"\bunderfloor\s+heating\b|\bstellantriebe?\b|\bboilers?\b|"
        r"\bbrenner\b|\bburners?\b|\b(?:umwälz)?pumpen?\b|\bpumps?\b"
    ),
    # Separable-verb prefixes stack in German: "aufgeheizt" is auf + ge + heizt, not just
    # one prefix. Both orders occur in the wild ("aufheizt" without "ge", "aufgeheizt"
    # with it), so the inner "ge" is itself optional, on top of the outer prefix.
    "heating": (
        r"\bheizung(?:en)?\b|"
        r"\b(?:be|ge|weiter|auf|vor|nach|er)?(?:ge)?heiz"
        r"(?:e|est|t|en|te|test|tet|ten|end\w*)\b|\bheat(?:s|ed|ing)?\b"
    ),
    "warmth": (
        r"\b(?:be|ge|weiter|auf|vor|nach|er)?(?:ge)?wärm(?:e|st|t|en|te|test|tet|ten|er)\b|"
        r"\b(?:be|ge|weiter|auf|vor|nach|er)?(?:ge)?waerm(?:e|st|t|en|te|test|tet|ten|er)\b|"
        r"\bwarm(?:e[rmns]?|er|ers|est|en|em|es|ed|ing|th)?\b"
    ),
    "switching": (
        r"\b(?:ein|aus|um)?schalt(?:e|est|et|en|ete|etest|etet|eten)\b|"
        r"\b(?:ein|aus|um)?geschaltet\b|\bswitch(?:es|ed|ing)?\b"
    ),
}
PHYSICAL_VOCABULARY_PATTERN = re.compile(
    "|".join(f"(?:{pattern})" for pattern in PHYSICAL_VOCABULARY.values()),
    re.IGNORECASE,
)
AMBIGUOUS_STATE_CHIP = re.compile(r">\s*Heiz(?:t|en)\s*<", re.IGNORECASE)


def _user_visible_sources(root: Path = ROOT) -> list[Path]:
    sources = list((root / "thermoctl").rglob("*.py"))
    sources += list((root / "thermoctl/web/templates").rglob("*.html"))
    # Own scripts only: the vendored bundles under static/vendor/ are third-party code we
    # did not author and whose comments and identifiers are not our claims to review.
    static_dir = root / "thermoctl/web/static"
    sources += [
        path
        for path in static_dir.glob("*.js")
        if path.is_file()
    ]
    sources += [root / "README.md", root / "CHANGELOG.md"]
    sources += [
        path
        for path in (root / "docs").glob("*.md")
        if path.name != "verlauf.md"
    ]
    return sorted(path for path in set(sources) if path.is_file())


def _registered_tool(function: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(
        isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Attribute)
        and isinstance(decorator.func.value, ast.Name)
        and decorator.func.value.id == "server"
        and decorator.func.attr == "tool"
        for decorator in function.decorator_list
    )


def _python_text_fragments(path: Path) -> list[tuple[int, str]]:
    """Return runtime strings and registered tool descriptions, not internal prose."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    internal_docstrings: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        if not node.body:
            continue
        first = node.body[0]
        if not (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _registered_tool(node):
            continue
        internal_docstrings.add(id(first.value))

    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in internal_docstrings
    ]


def _javascript_text_fragments(path: Path) -> list[tuple[int, str]]:
    """Return string- and template-literal contents, not comments or identifiers.

    A small hand-rolled scanner, not a full JS parser: it tracks line comments, block
    comments, and single/double/backtick-quoted literals well enough for this codebase's
    own scripts (checked to contain no backtick inside an actual template literal, only
    inside comments). It exists because comments here carry design rationale in prose that
    would otherwise trip the guard on words the user never sees.
    """
    text = path.read_text(encoding="utf-8")
    fragments: list[tuple[int, str]] = []
    line = 1
    index = 0
    length = len(text)
    while index < length:
        character = text[index]
        if character == "\n":
            line += 1
            index += 1
            continue
        if character == "/" and index + 1 < length and text[index + 1] == "/":
            newline = text.find("\n", index)
            index = length if newline == -1 else newline
            continue
        if character == "/" and index + 1 < length and text[index + 1] == "*":
            end = text.find("*/", index + 2)
            end = length if end == -1 else end + 2
            line += text.count("\n", index, end)
            index = end
            continue
        if character in ("'", '"', "`"):
            quote = character
            start_line = line
            cursor = index + 1
            content: list[str] = []
            while cursor < length and text[cursor] != quote:
                if text[cursor] == "\\" and cursor + 1 < length:
                    content.append(text[cursor + 1])
                    if text[cursor + 1] == "\n":
                        line += 1
                    cursor += 2
                    continue
                if text[cursor] == "\n":
                    line += 1
                content.append(text[cursor])
                cursor += 1
            fragments.append((start_line, "".join(content)))
            index = cursor + 1
            continue
        index += 1
    return fragments


def _blank_markdown_code(match: re.Match[str]) -> str:
    return "".join("\n" if character == "\n" else " " for character in match.group())


def _text_lines(path: Path) -> list[tuple[int, str, str]]:
    """Return source line, reviewed text, and searchable text for each visible fragment."""
    if path.suffix == ".py":
        return [
            (first_line + offset, line, line)
            for first_line, fragment in _python_text_fragments(path)
            for offset, line in enumerate(fragment.splitlines())
        ]

    if path.suffix == ".js":
        return [
            (first_line + offset, line, line)
            for first_line, fragment in _javascript_text_fragments(path)
            for offset, line in enumerate(fragment.splitlines())
        ]

    text = path.read_text(encoding="utf-8")
    searchable = text
    if path.suffix == ".md":
        searchable = re.sub(r"```.*?```", _blank_markdown_code, searchable, flags=re.DOTALL)
        searchable = re.sub(r"`[^`\n]+`", _blank_markdown_code, searchable)
    return [
        (line_number, reviewed_line, searchable_line)
        for line_number, (reviewed_line, searchable_line) in enumerate(
            zip(text.splitlines(), searchable.splitlines(), strict=True), start=1
        )
    ]


def _physical_vocabulary_occurrences(
    paths: list[Path], root: Path = ROOT
) -> list[tuple[str, str]]:
    occurrences: list[tuple[str, str]] = []
    for path in paths:
        relative = str(path.relative_to(root))
        for _line_number, reviewed_line, searchable_line in _text_lines(path):
            if PHYSICAL_VOCABULARY_PATTERN.search(searchable_line):
                occurrences.append((relative, reviewed_line.strip()))
    return sorted(occurrences)


def _load_approved_occurrences(path: Path = APPROVED_OCCURRENCES) -> list[tuple[str, str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError("Das Verzeichnis körperlichen Vokabulars braucht Version 1.")
    by_path = data.get("occurrences")
    if not isinstance(by_path, dict):
        raise ValueError("Im Verzeichnis fehlt das Objekt 'occurrences'.")

    approved: list[tuple[str, str]] = []
    for relative, lines in by_path.items():
        if not isinstance(relative, str) or not isinstance(lines, list):
            raise ValueError("Jede Datei im Verzeichnis braucht eine Liste von Textzeilen.")
        for line in lines:
            if not isinstance(line, str):
                raise ValueError("Jede genehmigte Fundstelle muss ihren Text enthalten.")
            approved.append((relative, line))
    return sorted(approved)


def _format_occurrences(occurrences: Counter[tuple[str, str]]) -> str:
    lines: list[str] = []
    for (relative, text), count in sorted(occurrences.items()):
        suffix = f" ({count} Vorkommen)" if count > 1 else ""
        lines.append(f"{relative}: {text}{suffix}")
    return "\n".join(lines)


def test_physical_vocabulary_occurrences_are_explicitly_reviewed() -> None:
    """The registry records review, not whether an approved physical claim is true."""
    current = Counter(_physical_vocabulary_occurrences(_user_visible_sources()))
    approved = Counter(_load_approved_occurrences())
    unreviewed = current - approved
    stale = approved - current

    problems: list[str] = []
    if unreviewed:
        problems.append(
            "Diese Zeile behauptet etwas über eine körperliche Wirkung. Prüfe, ob sie "
            "stimmt, und trag sie ein:\n" + _format_occurrences(unreviewed)
        )
    if stale:
        problems.append(
            "Diese genehmigte Fundstelle fehlt oder wurde geändert. Prüfe die Änderung "
            "und aktualisiere das Verzeichnis bewusst:\n" + _format_occurrences(stale)
        )
    assert not problems, "\n\n".join(problems)


def test_state_chips_name_a_decision_instead_of_physical_heating() -> None:
    ambiguous: list[str] = []
    for path in (ROOT / "thermoctl/web/templates").glob("*.html"):
        for index, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
            if AMBIGUOUS_STATE_CHIP.search(line):
                ambiguous.append(f"{path.relative_to(ROOT)}:{index + 1}: {line.strip()}")
    assert not ambiguous, "Mehrdeutiger Heiz-Zustandschip:\n" + "\n".join(ambiguous)


def test_mcp_control_description_names_the_explicit_unknown_startup_latch_state() -> None:
    source = (ROOT / "thermoctl/mcp/server.py").read_text(encoding="utf-8")
    assert '"mqtt_startup_latch_state": "unknown_from_mcp_process"' in source
    assert "explicitly reported as unknown" in source
    assert "is not visible here" not in source
    assert "armed operation moves a valve" not in source


def test_effect_text_guard_covers_every_user_visible_source_category() -> None:
    sources = {str(path.relative_to(ROOT)) for path in _user_visible_sources()}
    assert "README.md" in sources
    assert "CHANGELOG.md" in sources
    assert "docs/mcp.md" in sources
    assert "thermoctl/api/routes.py" in sources
    assert "thermoctl/mcp/server.py" in sources
    assert "thermoctl/domain/control_loop.py" in sources
    assert "thermoctl/domain/interfaces.py" in sources
    assert "thermoctl/integrations/mqtt/publication.py" in sources
    assert "thermoctl/integrations/actuators.py" in sources
    assert "thermoctl/services/publishing.py" in sources
    assert "thermoctl/web/control_views.py" in sources
    assert "thermoctl/web/templates/control.html" in sources
    assert "docs/verlauf.md" not in sources
    assert not any("docs/superpowers" in source for source in sources)


ATTACK_CASES = [
    ("README.md", "Derzeit schaltet thermoctl das Ventil."),
    ("CHANGELOG.md", "Das Ventil wird von der Regelung geöffnet."),
    ("docs/current.md", "Der Heizkörper wird warm."),
    ("thermoctl/web/templates/probe.html", "Die Wohnung wird beheizt."),
    ("thermoctl/web/probe.py", "Warmwasser fließt durch den Heizkörper."),
    ("thermoctl/services/probe.py", "Die Fußbodenheizung springt an."),
    ("thermoctl/domain/probe.py", "Der Aktor schaltet die Heizung ein."),
    ("thermoctl/api/probe.py", "thermoctl sorgt für einen warmen Raum."),
    ("thermoctl/mcp/probe.py", "thermoctl opens the valve."),
    ("thermoctl/integrations/mqtt/probe.py", "The radiator is warmed by the controller."),
    ("thermoctl/integrations/probe.py", "Heat flows into the room."),
    ("thermoctl/probe.py", "The home gets warmer."),
]


@pytest.mark.parametrize(
    ("relative", "sentence"),
    ATTACK_CASES,
    ids=[relative for relative, _sentence in ATTACK_CASES],
)
def test_physical_vocabulary_detects_each_source_category(
    tmp_path: Path, relative: str, sentence: str
) -> None:
    probe = tmp_path / relative
    probe.parent.mkdir(parents=True, exist_ok=True)
    content = f"MESSAGE = {sentence!r}" if probe.suffix == ".py" else sentence
    probe.write_text(content, encoding="utf-8")

    occurrences = _physical_vocabulary_occurrences(
        _user_visible_sources(tmp_path), root=tmp_path
    )

    assert occurrences == [(relative, sentence)]
