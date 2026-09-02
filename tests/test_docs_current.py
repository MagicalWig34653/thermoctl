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
from thermoctl.mcp import server as mcp_server

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
    registered: set[str] = set()

    class RecordingServer:
        def tool(self, name: str | None = None):  # type: ignore[no-untyped-def]
            def decorator(function):  # type: ignore[no-untyped-def]
                assert name is not None
                registered.add(name)
                return function

            return decorator

        def run(self, transport: str = "stdio", **kwargs: object) -> None:
            raise AssertionError("The registration guard never starts a transport")

    mcp_server._register_tools(RecordingServer(), object(), "unused")  # type: ignore[arg-type]
    assert registered, "keine Werkzeuge registriert"

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


def test_readme_actuator_claim_matches_what_is_actually_wired() -> None:
    """README.md once claimed on/off decisions reach no actuator in any stage.

    That was true until ordinary actuators were wired to the control loop
    (`services/publishing.py::_WIRED_INTEGRATIONS`) -- after which it was quietly
    false for a whole release, discovered only by a release review that happened to
    read `services/publishing.py` next to the README. This ties the claim to the
    set that actually decides it, so the next integration added there cannot repeat
    that silently.
    """
    from thermoctl.integrations.actuators import (
        MerossSwitch,
        Zigbee2MqttThermostat,
        Zigbee2MqttValve,
    )
    from thermoctl.services import publishing

    text = (ROOT / "README.md").read_text(encoding="utf-8")
    wired = publishing._WIRED_INTEGRATIONS
    if wired:
        assert "erreichen in keiner Stufe einen Aktor" not in text, (
            "README.md behauptet weiterhin, Ein/Aus-Entscheidungen erreichten "
            f"nie einen Aktor, dabei sind diese Anbindungen verdrahtet: {sorted(wired)}"
        )
        assert "noch nicht mit dem Regelkreis verdrahtet" not in text
        assert "Ein/Aus-Befehle an gewöhnliche Aktoren" in text, (
            "README.md sollte beschreiben, dass gewöhnliche Aktoren nach "
            "'scharf und neu gestartet' geschaltet werden"
        )
    else:  # pragma: no cover - not the current state, kept for symmetry
        assert "erreichen in keiner Stufe einen Aktor" in text

    assert wired == {"zigbee2mqtt", "meross"}
    assert publishing.Zigbee2MqttValve is Zigbee2MqttValve
    assert publishing.Zigbee2MqttThermostat is Zigbee2MqttThermostat
    assert publishing.MerossSwitch is MerossSwitch
    for documented_path in (
        "Zigbee2MQTT-Schalter",
        "Zigbee-Thermostatventile ohne eigene Regelung",
        "Meross-Steckdosen",
        "selbstregelnde Ventile",
    ):
        assert documented_path in text


def test_living_docs_do_not_restore_the_pre_actuator_state() -> None:
    stale_claims = (
        "Ein/Aus-Entscheidungen erreichen keinen Aktor",
        "Ein/Aus-Entscheidungen erreichen noch keinen Aktor",
        "Ein/Aus-Entscheidungen erreichen weiterhin keinen Aktor",
        "Ein/Aus-Aktoren sind noch nicht verdrahtet",
    )
    current_sections = (
        "README.md",
        "docs/self-hosting.md",
        "docs/mqtt.md",
        "docs/mcp.md",
        "docs/sicherheitsdurchsicht.md",
        "docs/roadmap.md",
    )
    stale = [
        f"{name}: {claim}"
        for name in current_sections
        for claim in stale_claims
        if claim in (ROOT / name).read_text(encoding="utf-8")
    ]
    assert not stale, "Lebende Dokumentation beschreibt den alten Aktorstand: " + ", ".join(stale)


def test_roadmap_uses_the_registered_mcp_tool_count() -> None:
    server_source = (ROOT / "thermoctl" / "mcp" / "server.py").read_text(encoding="utf-8")
    registered = set(re.findall(r'@server\.tool\(name="([^"]+)"\)', server_source))
    text = (ROOT / "docs" / "roadmap.md").read_text(encoding="utf-8")
    match = re.search(r"MCP-Server — ([0-9]+) Werkzeuge", text)
    assert match
    assert int(match.group(1)) == len(registered)


def test_security_review_names_every_outbound_connection_kind() -> None:
    from thermoctl.config import Settings
    from thermoctl.integrations.forecast import OPEN_METEO_URL

    text = (ROOT / "docs" / "sicherheitsdurchsicht.md").read_text(encoding="utf-8")
    assert OPEN_METEO_URL in (
        ROOT / "thermoctl" / "integrations" / "forecast.py"
    ).read_text(encoding="utf-8")
    assert Settings.model_fields.keys() >= {
        "mqtt_host",
        "notify_webhook",
        "meross_api_base",
        "meross_email",
        "meross_password",
    }
    for connection in ("MQTT", "Webhook", "Wetterdienst", "Meross"):
        assert connection in text
    assert "Open-Meteo" in text


def test_security_review_names_the_fixed_raw_sql_exceptions() -> None:
    engine_source = (ROOT / "thermoctl" / "db" / "engine.py").read_text(encoding="utf-8")
    schema_source = (ROOT / "thermoctl" / "db" / "schema_state.py").read_text(encoding="utf-8")
    documented = (ROOT / "docs" / "sicherheitsdurchsicht.md").read_text(encoding="utf-8")

    assert 'cursor.execute("PRAGMA foreign_keys=ON")' in engine_source
    assert 'text("SELECT version_num FROM alembic_version")' in schema_source
    assert "`PRAGMA foreign_keys=ON`" in documented
    assert "`alembic_version`" in documented
    assert "Roh-SQL oder zusammengebaute Abfragen | keine" not in documented


def test_api_documentation_does_not_claim_identical_adapter_scope() -> None:
    text = (ROOT / "docs" / "api.md").read_text(encoding="utf-8")
    assert "genau das, was in der Oberfläche möglich ist, und umgekehrt" not in text
    assert "nicht über REST" in text


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
