"""What the installed package contains -- not what the source tree contains.

This file exists because a container image built cleanly, passed the CI step called
"Docker-Image-Build", and then would not start:

    RuntimeError: Directory '/usr/local/.../thermoctl/web/static' does not exist

`setuptools` installs `.py` files and nothing else unless it is told otherwise. Every
image ever built was therefore missing all 36 templates and every stylesheet and
script -- half the application. Nothing caught it: the test suite runs against the
source tree, where the files are simply there, and building an image proves only that
it builds, never that it runs.
"""

import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "thermoctl"


def _shipped_files() -> set[str]:
    """Builds a wheel the way the container does and returns what ended up inside.

    **Built from a copy without `.git`**, and that is the whole point. With a
    repository present, setuptools finds data files through the version control
    system all by itself, so a wheel built here would contain the templates even
    with no `package-data` at all -- and the test would pass while the container
    stays broken. The `Dockerfile` copies only `pyproject.toml` and `thermoctl/`
    into the build stage; nothing else. This reproduces exactly that.
    """
    import shutil
    import tempfile

    with tempfile.TemporaryDirectory() as workspace:
        source = Path(workspace) / "src"
        source.mkdir()
        shutil.copy2(ROOT / "pyproject.toml", source / "pyproject.toml")
        shutil.copytree(
            PACKAGE, source / "thermoctl", ignore=shutil.ignore_patterns("__pycache__")
        )
        target = Path(workspace) / "wheel"
        result = subprocess.run(  # noqa: S603 -- fester Befehl, kein Fremdeingang
            [sys.executable, "-m", "pip", "wheel", "--no-deps", "-q", "-w",
             str(target), str(source)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        wheels = list(target.glob("*.whl"))
        assert len(wheels) == 1, f"erwartet genau ein Wheel, gefunden: {wheels}"
        with zipfile.ZipFile(wheels[0]) as wheel:
            return {
                name[len("thermoctl/") :]
                for name in wheel.namelist()
                if name.startswith("thermoctl/")
            }


@pytest.mark.packaging
def _ist_konfliktkopie(name: str) -> bool:
    """Erkennt die Kopien, die iCloud bei schnellen Schreibvorgaengen anlegt.

    Dieselbe Form wie das ``--ignore-glob`` in ``pyproject.toml`` und der ``omit``
    der Abdeckungsmessung: ein Leerzeichen, eine Zahl, dann die Endung.
    """
    stamm = name.rsplit(".", 1)[0]
    teile = stamm.rsplit(" ", 1)
    return len(teile) == 2 and teile[1].isdigit()


def test_every_non_python_file_of_the_package_is_actually_shipped() -> None:
    """Templates and static files must be in the wheel, not just in the repository.

    Deliberately not a check of the `package-data` patterns: those can look right and
    still miss a subdirectory. This asks the built artefact itself, which is what a
    container installs.
    """
    expected = {
        str(path.relative_to(PACKAGE))
        for path in PACKAGE.rglob("*")
        if path.is_file()
        and path.suffix != ".py"
        and "__pycache__" not in path.parts
        # Nichts, was das Betriebssystem oder die Dateisynchronisation nebenher
        # anlegt: .DS_Store legt der Finder in jedem Ordner an, den jemand oeffnet,
        # und iCloud stellt Konfliktkopien der Form "datei 2.json" daneben. Beides
        # gehoert nicht ins Wheel, und beides taucht ohne Zutun wieder auf -- ein
        # Test, der daran scheitert, meldet den Zustand des Schreibtischs und nicht
        # den des Pakets.
        and path.name != ".DS_Store"
        and not _ist_konfliktkopie(path.name)
    }
    assert expected, "keine Nicht-Python-Dateien gefunden — Pfad falsch?"

    missing = sorted(expected - _shipped_files())
    assert not missing, (
        "Diese Dateien fehlen im gebauten Wheel und damit im Container-Abbild:\n  "
        + "\n  ".join(missing)
    )
