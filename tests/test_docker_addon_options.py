"""Tests for ``docker/thermoctl_optionen.py``.

This script translates a Home Assistant add-on's ``options.json`` into the
``THERMOCTL_*`` environment variables ``thermoctl.config.Settings`` reads. It
lives under ``docker/`` rather than in the package because it runs before the
application is even importable (the entrypoint calls it with a bare
``python3``, not through the installed console script) -- so it is loaded
here by file path instead of by module name.
"""

import importlib.util
import json
import shlex
import subprocess
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "docker" / "thermoctl_optionen.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("thermoctl_optionen", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


optionen = _load_module()


def test_empty_options_yield_the_default_sqlite_url_under_data() -> None:
    """No `database`-Option gesetzt -> SQLite unter /data, wie fuer das Add-on verlangt."""
    assert optionen.translate({}) == {"THERMOCTL_DATABASE_URL": "sqlite:////data/thermoctl.db"}


def test_explicit_sqlite_type_gives_the_same_default_url() -> None:
    options = {"database": {"type": "sqlite"}}
    assert optionen.translate(options)["THERMOCTL_DATABASE_URL"] == "sqlite:////data/thermoctl.db"


def test_complete_mariadb_options_build_a_connection_string() -> None:
    options = {
        "database": {
            "type": "mariadb",
            "host": "core-mariadb",
            "port": 3306,
            "user": "thermoctl",
            "password": "ein geheimnis",
            "database": "thermoctl",
        }
    }
    assert (
        optionen.translate(options)["THERMOCTL_DATABASE_URL"]
        == "mysql+pymysql://thermoctl:ein geheimnis@core-mariadb:3306/thermoctl"
    )


def test_incomplete_mariadb_options_leave_the_database_url_unset() -> None:
    """Fehlt ein Pflichtfeld (hier: password), gilt keine kaputte Verbindungszeichenfolge,
    sondern gar keine -- die Anwendung meldet dann ihren eigenen, klareren Fehler."""
    options = {
        "database": {
            "type": "mariadb",
            "host": "core-mariadb",
            "user": "thermoctl",
            "database": "thermoctl",
        }
    }
    assert "THERMOCTL_DATABASE_URL" not in optionen.translate(options)


def test_mqtt_enabled_true_becomes_the_string_true() -> None:
    assert optionen.translate({"mqtt": {"enabled": True}})["THERMOCTL_MQTT_ENABLED"] == "true"


def test_mqtt_enabled_false_becomes_the_string_false() -> None:
    """`False` ist kein leerer Wert -- er muss trotzdem uebertragen werden, sonst gilt der
    Anwendungsvorgabewert, der zufaellig auch False ist, aber aus dem falschen Grund."""
    assert optionen.translate({"mqtt": {"enabled": False}})["THERMOCTL_MQTT_ENABLED"] == "false"


def test_an_absent_option_is_not_in_the_translation() -> None:
    assert "THERMOCTL_MQTT_HOST" not in optionen.translate({})


def test_an_empty_string_option_is_not_in_the_translation() -> None:
    """Ein im Add-on-UI leer gelassenes Feld ist JSON-technisch ein leerer String, kein
    fehlender Schluessel -- muss ebenso wenig eine THERMOCTL_*-Variable erzeugen."""
    options = {"secret_key": ""}
    assert "THERMOCTL_SECRET_KEY" not in optionen.translate(options)


def test_mqtt_client_id_passes_through() -> None:
    """Der eigentliche Anlass: EMQX-Broker binden Rechte oft an die Client-ID, und
    ohne diese Option kam thermoctl an so einem Broker gar nicht erst durch."""
    assert (
        optionen.translate({"mqtt": {"client_id": "heizung-keller"}})["THERMOCTL_MQTT_CLIENT_ID"]
        == "heizung-keller"
    )


def test_mqtt_ca_cert_passes_through() -> None:
    assert (
        optionen.translate({"mqtt": {"ca_cert": "/ssl/mqtt-ca.pem"}})["THERMOCTL_MQTT_CA_CERT"]
        == "/ssl/mqtt-ca.pem"
    )


def test_log_format_passes_through() -> None:
    assert optionen.translate({"log_format": "plain"})["THERMOCTL_LOG_FORMAT"] == "plain"


def test_secret_key_and_meross_credentials_pass_through() -> None:
    options = {
        "secret_key": "x" * 40,
        "meross": {"email": "person@example.org", "password": "pw", "api_base": "https://x"},
    }
    result = optionen.translate(options)
    assert result["THERMOCTL_SECRET_KEY"] == "x" * 40
    assert result["THERMOCTL_MEROSS_EMAIL"] == "person@example.org"
    assert result["THERMOCTL_MEROSS_PASSWORD"] == "pw"
    assert result["THERMOCTL_MEROSS_API_BASE"] == "https://x"


#: THERMOCTL_DATABASE_URL is always in `translate()`'s output (SQLite defaults even for
#: an empty options dict) -- tests that are not about the database mark it as
#: already-set so it does not show up as noise in the lines under test.
_DATABASE_ALREADY_SET = {"THERMOCTL_DATABASE_URL": "irrelevant-fuer-diesen-test"}


def test_an_environment_variable_already_set_by_the_operator_wins() -> None:
    """Kern der Vorrangregel aus dem Auftrag: eine ausdruecklich gesetzte Umgebungsvariable
    gewinnt gegen die Optionsdatei, unabhaengig davon, was darin steht."""
    options = {"secret_key": "aus-der-optionsdatei"}
    environ = {"THERMOCTL_SECRET_KEY": "vom-betreiber-gesetzt", **_DATABASE_ALREADY_SET}
    assert optionen.exports_for(options, environ) == []


def test_an_unset_environment_variable_is_exported() -> None:
    options = {"secret_key": "x" * 40}
    lines = optionen.exports_for(options, dict(_DATABASE_ALREADY_SET))
    assert lines == [f"export THERMOCTL_SECRET_KEY={shlex.quote('x' * 40)}"]


def test_a_value_with_shell_special_characters_survives_a_round_trip_through_the_shell() -> None:
    """Die eigentliche Probe fuer das Quoting: der Wert enthaelt Leerzeichen, ein
    Anfuehrungszeichen und ein Dollarzeichen -- alles, was eine naive Einbettung in
    `export NAME=$wert` zerlegen oder als Befehl ausfuehren wuerde."""
    tricky = "geheimnis mit $VAR und 'Anführungszeichen' und \"noch mehr\""
    (line,) = optionen.exports_for({"secret_key": tricky}, dict(_DATABASE_ALREADY_SET))
    parsed = subprocess.run(  # noqa: S602,S603 -- fester Shell-Einzeiler, kein Fremdeingang
        f"{line}; printf '%s' \"$THERMOCTL_SECRET_KEY\"",
        shell=True,
        capture_output=True,
        text=True,
        check=True,
    )
    assert parsed.stdout == tricky


def test_output_is_sorted_for_a_stable_and_reviewable_diff() -> None:
    options = {"secret_key": "s" * 32, "log_level": "DEBUG"}
    lines = optionen.exports_for(options, {})
    assert lines == sorted(lines)


def test_main_without_an_options_file_prints_nothing(tmp_path: Path) -> None:
    """Der Normalfall ausserhalb eines Add-ons: keine Datei, kein Verhalten aendert sich."""
    missing = tmp_path / "options.json"
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(SCRIPT)],
        env={"THERMOCTL_ADDON_OPTIONS_FILE": str(missing), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout == ""
    assert result.returncode == 0


def test_main_with_an_options_file_prints_export_lines(tmp_path: Path) -> None:
    options_file = tmp_path / "options.json"
    options_file.write_text(json.dumps({"secret_key": "s" * 32, "log_level": "DEBUG"}))
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(SCRIPT)],
        env={"THERMOCTL_ADDON_OPTIONS_FILE": str(options_file), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=True,
    )
    assert "export THERMOCTL_SECRET_KEY=" in result.stdout
    assert "export THERMOCTL_LOG_LEVEL=DEBUG" in result.stdout


def test_main_reports_invalid_json_on_stderr_and_fails(tmp_path: Path) -> None:
    options_file = tmp_path / "options.json"
    options_file.write_text("{ das ist kein json")
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(SCRIPT)],
        env={"THERMOCTL_ADDON_OPTIONS_FILE": str(options_file), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert result.stdout == ""
    assert "nicht lesbar" in result.stderr


def test_main_rejects_a_json_array_as_the_options_file(tmp_path: Path) -> None:
    options_file = tmp_path / "options.json"
    options_file.write_text("[1, 2, 3]")
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(SCRIPT)],
        env={"THERMOCTL_ADDON_OPTIONS_FILE": str(options_file), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "kein JSON-Objekt" in result.stderr


def test_main_never_prints_a_secret_value_to_stderr_on_success(tmp_path: Path) -> None:
    """Grundsatz 2: selbst zu Debugzwecken duerfen Zugangsdaten nicht ins Log. Bei einem
    erfolgreichen Lauf bleibt stderr also leer, wie auch immer die Werte lauten."""
    options_file = tmp_path / "options.json"
    options_file.write_text(json.dumps({"mqtt": {"password": "s3hr-geheim"}}))
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(SCRIPT)],
        env={"THERMOCTL_ADDON_OPTIONS_FILE": str(options_file), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stderr == ""
    assert "s3hr-geheim" not in result.stderr


def test_every_settings_field_is_translated_or_deliberately_excluded() -> None:
    """The guard against the actual bug this task fixed.

    The MQTT client id was missed not because nobody knew about it -- it was in
    `thermoctl.config.Settings` and in `.env.example` all along -- but because
    nothing compared the add-on translation against the source of truth for what
    settings exist. This closes exactly that gap: every field on `Settings` must
    show up either in `ABGEBILDETE_FELDER` (this script actually maps it) or in
    `BEWUSST_AUSGELASSEN` (mapped nowhere, on purpose, with why). A new setting
    landing in neither dict fails here before it ever reaches an operator's add-on
    configuration screen missing an option they need.
    """
    from thermoctl.config import Settings

    settings_fields = set(Settings.model_fields)
    handled = set(optionen.ABGEBILDETE_FELDER)
    excluded = set(optionen.BEWUSST_AUSGELASSEN)

    overlap = handled & excluded
    assert not overlap, (
        "Felder sowohl als abgebildet als auch als bewusst ausgelassen gefuehrt: "
        f"{sorted(overlap)}"
    )

    missing = settings_fields - handled - excluded
    assert not missing, (
        "Settings-Felder weder in ABGEBILDETE_FELDER noch in BEWUSST_AUSGELASSEN "
        f"eingetragen: {sorted(missing)}"
    )

    stale = (handled | excluded) - settings_fields
    assert not stale, (
        "ABGEBILDETE_FELDER/BEWUSST_AUSGELASSEN verweisen auf Settings-Felder, die es "
        f"nicht mehr gibt: {sorted(stale)}"
    )
