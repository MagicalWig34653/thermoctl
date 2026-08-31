"""Was das Paket über sich selbst sagt."""

import re
import tomllib
from pathlib import Path

import thermoctl

ROOT = Path(__file__).resolve().parent.parent


def test_package_has_a_version() -> None:
    assert thermoctl.__version__


def test_the_version_is_the_same_in_both_places() -> None:
    """`pyproject.toml` und `thermoctl/__init__.py` müssen übereinstimmen.

    Sie werden von Hand gepflegt und von verschiedenen Stellen gelesen: Das Rad trägt
    die eine, `/healthz` meldet die andere. Laufen sie auseinander, meldet ein
    Container eine Version, die nicht die ist, die er enthält — und genau das fällt
    erst auf, wenn jemand einem Fehlerbericht nachgeht und der Version glaubt.

    Aufgefallen beim Release 0.2.0: Nach 161 Commits und 15 Migrationen standen beide
    noch auf 0.1.0, und der Tag dazu war längst vergeben.
    """
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["version"] == thermoctl.__version__


def test_the_changelog_describes_the_current_version() -> None:
    """Eine Version ohne Eintrag im CHANGELOG ist eine, die niemand einordnen kann.

    Geprüft wird nur, dass die Überschrift existiert — was darin steht, kann kein Test
    beurteilen.
    """
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    headings = set(re.findall(r"^## \[?v?(\d+\.\d+\.\d+)\]?", changelog, re.MULTILINE))
    assert thermoctl.__version__ in headings, (
        f"CHANGELOG.md beschreibt {thermoctl.__version__} nicht; gefunden: {sorted(headings)}"
    )
