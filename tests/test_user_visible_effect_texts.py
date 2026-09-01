"""Guard user-visible text against unconditional claims of physical heating effects."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# These sources describe a different system or a future target, not thermoctl's current
# output. Keeping the exceptions exact makes changed or newly added claims fail review.
REVIEWED_EXCEPTIONS = {
    ("docs/bestandsaufnahme-altsystem.md", "schaltet Meross-Ventile"),
    ("docs/bestandsaufnahme-altsystem.md", "schaltet MSS710-Steckdosen"),
    ("docs/bestandsaufnahme-altsystem.md", "schaltet das Ventil"),
    ("docs/bestandsaufnahme-altsystem.md", "schaltet Fußbodenheizungskreis"),
    ("docs/technisches_konzept.md", "Schaltet den Heizkreis"),
    ("docs/technisches_konzept.md", "Schaltet den eigentlichen Heizkreis"),
    ("docs/technisches_konzept.md", "bevor geschaltet wird"),
    ("docs/technisches_konzept.md", "werden von der Steuerung immer gemeinsam geschaltet"),
}

EFFECT_CLAIM = re.compile(
    r"(?:wird|werden|wurde|wurden) (?:wirklich )?(?:geschaltet|geheizt)"
    r"|(?:bewegt|bewegen|moves?) (?:das |die |ein |a )?Ventil"
    r"|(?:geht|gehen) an (?:das |die )?Ventil"
    r"|(?:thermoctl|Anlage|Aktor|Ventil|Regelung) schaltet",
    re.IGNORECASE,
)
AMBIGUOUS_STATE_CHIP = re.compile(r">\s*Heiz(?:t|en)\s*<", re.IGNORECASE)
STAGE_CONTEXT = re.compile(
    r"unscharf|Trockenlauf|Schatten|würde|hätte|Altsystem|derzeit|zukünftig|"
    r"Phase [2345]|Teilprojekt [2345]|nach (?:dem )?Neustart|selbstregelnd|"
    r"kein(?:en)? Aktor|nicht verdrahtet|Verdrahtung|Sollwert",
    re.IGNORECASE,
)


def _user_visible_sources() -> list[Path]:
    sources = list((ROOT / "thermoctl/web/templates").glob("*.html"))
    sources += list((ROOT / "thermoctl/domain").glob("*.py"))
    sources += [ROOT / "thermoctl/api/routes.py", ROOT / "thermoctl/mcp/server.py"]
    sources += list((ROOT / "thermoctl/integrations/mqtt").glob("*.py"))
    sources += [ROOT / "README.md"]
    sources += [
        path
        for path in (ROOT / "docs").glob("*.md")
        if path.name != "verlauf.md"
    ]
    return sorted(set(sources))


def test_physical_effect_claims_name_the_stage_or_are_reviewed() -> None:
    """A new unconditional physical-effect promise must become a conscious exception."""
    unqualified: list[str] = []
    for path in _user_visible_sources():
        relative = str(path.relative_to(ROOT))
        lines = path.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            if not EFFECT_CLAIM.search(line):
                continue
            context = " ".join(lines[max(0, index - 2) : index + 3])
            if STAGE_CONTEXT.search(context):
                continue
            if any(
                allowed_path == relative and fragment.casefold() in line.casefold()
                for allowed_path, fragment in REVIEWED_EXCEPTIONS
            ):
                continue
            unqualified.append(f"{relative}:{index + 1}: {line.strip()}")

    assert not unqualified, (
        "Unbedingte Aussage über eine körperliche Heizwirkung ohne Stufenangabe "
        "oder geprüfte Ausnahme:\n" + "\n".join(unqualified)
    )


def test_state_chips_name_a_decision_instead_of_physical_heating() -> None:
    ambiguous: list[str] = []
    for path in (ROOT / "thermoctl/web/templates").glob("*.html"):
        for index, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
            if AMBIGUOUS_STATE_CHIP.search(line):
                ambiguous.append(f"{path.relative_to(ROOT)}:{index + 1}: {line.strip()}")
    assert not ambiguous, "Mehrdeutiger Heiz-Zustandschip:\n" + "\n".join(ambiguous)


def test_mcp_control_description_discloses_the_unavailable_startup_latch() -> None:
    source = (ROOT / "thermoctl/mcp/server.py").read_text(encoding="utf-8")
    assert "startup-built MQTT latch" in source
    assert "is not visible here" in source
    assert "armed operation moves a valve" not in source


def test_effect_text_guard_covers_every_user_visible_source_category() -> None:
    sources = {str(path.relative_to(ROOT)) for path in _user_visible_sources()}
    assert "README.md" in sources
    assert "docs/mcp.md" in sources
    assert "thermoctl/api/routes.py" in sources
    assert "thermoctl/mcp/server.py" in sources
    assert "thermoctl/domain/control_loop.py" in sources
    assert "thermoctl/domain/interfaces.py" in sources
    assert "thermoctl/integrations/mqtt/publication.py" in sources
    assert "thermoctl/web/templates/control.html" in sources
    assert "docs/verlauf.md" not in sources
    assert not any("docs/superpowers" in source for source in sources)
