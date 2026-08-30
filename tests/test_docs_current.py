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
    from thermoctl.mcp import server

    text = (ROOT / "docs" / "mcp.md").read_text(encoding="utf-8")
    # The module's public tool functions, excluding the `main` entry point.
    tools = [
        name
        for name in dir(server)
        if not name.startswith("_")
        and name != "main"
        and callable(getattr(server, name))
        and getattr(getattr(server, name), "__module__", "") == server.__name__
    ]
    assert tools, "No tools were found at all -- the test would check nothing."
    missing = [name for name in tools if f"`{name}(" not in text]
    assert not missing, "docs/mcp.md does not describe these MCP tools: " + ", ".join(missing)


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
