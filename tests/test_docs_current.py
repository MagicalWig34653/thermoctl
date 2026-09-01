"""The documentation must describe what actually exists.

Reason: when asked about it, it turned out that `docs/mcp.md` named only two of nine
tools, `.env.example` did not know about a setting, and `docs/api.md` still claimed
tokens could only be issued through a Python function -- which had been wrong since
subproject 3.

A description that has fallen behind is worse than none at all: it gets believed.
These tests keep the three places where docs and code drift apart fastest honest.
They deliberately check only *completeness* -- whether the text next to it is still
correct is still something a person has to read.
"""

import re
from pathlib import Path

from tests.helpers import alle_api_routen
from thermoctl.app import create_app
from thermoctl.config import Settings

ROOT = Path(__file__).resolve().parent.parent
WRITING_AND_READING = {"GET", "POST", "PUT", "DELETE", "PATCH"}


def test_every_rest_endpoint_is_listed_in_the_documentation() -> None:
    text = (ROOT / "docs" / "api.md").read_text(encoding="utf-8")
    missing = [
        f"{method} {route.path}"
        for route in alle_api_routen(create_app())
        if route.path.startswith("/api/")
        for method in sorted(route.methods & WRITING_AND_READING)
        if f"{method} {route.path}" not in text
    ]
    assert not missing, "docs/api.md does not describe these endpoints: " + ", ".join(missing)


def test_every_mcp_tool_is_listed_in_the_documentation() -> None:
    """The documented name has to be the **registered** one.

    An assistant reads that table and calls what it says. This used to compare the
    module's function names instead, and those are not what a client calls: the tool
    registered as `override` is written `def mcp_override` and was reachable in the
    module as `override_zone`. The documentation said `override_zone`, the test found
    a function of that name, and everything looked consistent -- while every call
    built from the documentation hit a tool that does not exist.
    """
    server_source = (ROOT / "thermoctl" / "mcp" / "server.py").read_text(encoding="utf-8")
    registered = set(re.findall(r'@server\.tool\(name="([^"]+)"\)', server_source))
    assert registered, "keine Werkzeuge gefunden — Muster veraltet?"

    text = (ROOT / "docs" / "mcp.md").read_text(encoding="utf-8")
    documented = set(re.findall(r"\| `(\w+)\(", text))
    missing = sorted(registered - documented)
    invented = sorted(documented - registered)
    assert not missing, f"docs/mcp.md beschreibt diese MCP-Werkzeuge nicht: {missing}"
    assert not invented, f"docs/mcp.md nennt Werkzeuge, die es nicht gibt: {invented}"

    prose_count = re.search(r"^([0-9]+) Stück,", text, re.MULTILINE)
    assert prose_count, "docs/mcp.md nennt die Werkzeugzahl nicht im erwarteten Format"
    assert int(prose_count.group(1)) == len(registered), (
        "docs/mcp.md nennt im Fließtext eine falsche Werkzeugzahl: "
        f"{prose_count.group(1)} statt {len(registered)}"
    )


def test_mcp_documentation_acknowledges_writable_control_parameters() -> None:
    text = (ROOT / "docs" / "mcp.md").read_text(encoding="utf-8")
    paragraph = next(
        (
            paragraph
            for paragraph in text.split("\n\n")
            if "set_control_parameters" in paragraph and "bewusste Ausnahme" in paragraph
        ),
        "",
    )
    assert "bewusste Ausnahme" in paragraph
    assert "setzt genau einen benannten Parameter" in paragraph
    assert "Schreibende Werkzeuge für die Konfiguration" not in text


def test_every_setting_is_listed_in_the_example_file() -> None:
    """`.env.example` is the only list an operator ever gets to see.

    A setting missing from it does not exist for them -- they would only ever
    find it by reading the source code.
    """
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    present = set(re.findall(r"^(THERMOCTL_[A-Z_]+)=", text, re.MULTILINE))
    expected = {f"THERMOCTL_{name.upper()}" for name in Settings.model_fields}
    missing = sorted(expected - present)
    assert not missing, ".env.example does not know these settings: " + ", ".join(missing)



LIVING_DOCS = [
    "README.md",
    "docs/STATUS.md",
    "docs/verlauf.md",
    "docs/api.md",
    "docs/mcp.md",
    "docs/mqtt.md",
    "docs/offene-entscheidungen.md",
    "docs/roadmap.md",
    "docs/self-hosting.md",
    "docs/sicherheitsdurchsicht.md",
]


def test_the_living_docs_name_no_file_that_no_longer_exists() -> None:
    """A documentation that points at a renamed module sends the reader nowhere.

    Only the *living* documents are checked -- the ones that describe the system as it
    is now. The specs and plans under `docs/superpowers/` are deliberately left out:
    they record what was decided at a point in time, and rewriting them to match
    today's names would falsify the record. `bestandsaufnahme-altsystem.md` describes
    the old system and is not ours to rename either.

    Grew out of the English translation: half a dozen documents went on naming the
    actuator, remote-control and control-loop modules under their old German file names
    for two days after those files had been renamed.

    **The convention this rests on:** a file name in backticks claims that the file
    exists. A name that no longer does belongs in running text -- "die Aktoren hießen
    einmal anders" -- not in backticks. Otherwise a document could not report its own
    history without failing this test, which is exactly what happened the first time:
    the passage in STATUS.md describing this very repair listed the old names in
    backticks and went red.
    """
    existing = {path.name for path in ROOT.rglob("*.py") if ".venv" not in path.parts}
    existing |= {path.name for path in (ROOT / "thermoctl/web/static").glob("*.js")}

    dead: list[str] = []
    for name in LIVING_DOCS:
        text = (ROOT / name).read_text(encoding="utf-8")
        for mentioned in sorted(set(re.findall(r"`[\w/]*?(\w+\.(?:py|js))`", text))):
            if mentioned not in existing:
                dead.append(f"{name}: {mentioned}")
    assert not dead, "Dokumentation nennt Dateien, die es nicht gibt:\n  " + "\n  ".join(dead)
