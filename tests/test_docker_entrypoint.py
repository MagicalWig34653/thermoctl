"""Shell-level tests for ``docker/entrypoint.sh``.

Runs the real script with fake `alembic`/`thermoctl`/`python3` executables put
first on PATH, so this exercises exactly what the container does at start-up
without needing a real database or the installed package. The fake `thermoctl`
just dumps its environment, which is how the tests observe what the options
file turned into.
"""

import json
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENTRYPOINT = ROOT / "docker" / "entrypoint.sh"
OPTIONEN_SKRIPT = ROOT / "docker" / "thermoctl_optionen.py"


def _make_executable(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _fake_bin(tmp_path: Path) -> Path:
    """A PATH directory with stand-ins for everything the entrypoint calls.

    `alembic` and `python3` (which the entrypoint invokes as bare names) plus
    `thermoctl`, standing in for the real console script the entrypoint
    ultimately `exec`s. `thermoctl` prints every `THERMOCTL_*` variable it
    sees so the test can check what the options translation produced.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _make_executable(bin_dir / "alembic", "#!/bin/sh\nexit 0\n")
    _make_executable(
        bin_dir / "thermoctl",
        '#!/bin/sh\nenv | grep "^THERMOCTL_" | sort\n',
    )
    # sys.executable's path can contain spaces (this repo sits under a directory with
    # one), so it must be quoted -- an unquoted `exec` here fails with a confusing
    # "No such file or directory" for a path prefix, not the whole path.
    _make_executable(bin_dir / "python3", f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    return bin_dir


def _run_entrypoint(tmp_path: Path, extra_env: dict[str, str]) -> subprocess.CompletedProcess:
    bin_dir = _fake_bin(tmp_path)
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "THERMOCTL_ADDON_OPTIONS_SCRIPT": str(OPTIONEN_SKRIPT),
        **extra_env,
    }
    return subprocess.run(  # noqa: S603
        ["/bin/sh", str(ENTRYPOINT)],
        env=env,
        capture_output=True,
        text=True,
    )


def test_without_an_options_file_nothing_changes(tmp_path: Path) -> None:
    """Der gewoehnliche docker-compose-Betrieb: kein /data/options.json, also keine
    Uebersetzung -- die Umgebungsvariablen, die schon gesetzt sind, kommen unveraendert
    beim Anwendungsprozess an."""
    missing = tmp_path / "options.json"
    result = _run_entrypoint(
        tmp_path,
        {
            "THERMOCTL_ADDON_OPTIONS_FILE": str(missing),
            "THERMOCTL_DATABASE_URL": "sqlite:///./eigene.db",
            "THERMOCTL_SECRET_KEY": "x" * 32,
        },
    )
    assert result.returncode == 0, result.stderr
    assert "THERMOCTL_DATABASE_URL=sqlite:///./eigene.db" in result.stdout
    assert "THERMOCTL_SECRET_KEY=" + "x" * 32 in result.stdout


def test_options_file_is_translated_into_environment_variables(tmp_path: Path) -> None:
    options_file = tmp_path / "options.json"
    options_file.write_text(
        json.dumps({"secret_key": "y" * 40, "log_level": "DEBUG", "mqtt": {"enabled": True}})
    )
    result = _run_entrypoint(tmp_path, {"THERMOCTL_ADDON_OPTIONS_FILE": str(options_file)})
    assert result.returncode == 0, result.stderr
    assert "THERMOCTL_SECRET_KEY=" + "y" * 40 in result.stdout
    assert "THERMOCTL_LOG_LEVEL=DEBUG" in result.stdout
    assert "THERMOCTL_MQTT_ENABLED=true" in result.stdout


def test_an_operator_set_variable_wins_over_the_options_file(tmp_path: Path) -> None:
    options_file = tmp_path / "options.json"
    options_file.write_text(json.dumps({"secret_key": "aus-der-datei-die-nicht-gewinnt"}))
    result = _run_entrypoint(
        tmp_path,
        {
            "THERMOCTL_ADDON_OPTIONS_FILE": str(options_file),
            "THERMOCTL_SECRET_KEY": "vom-betreiber-gesetzt",
        },
    )
    assert result.returncode == 0, result.stderr
    assert "THERMOCTL_SECRET_KEY=vom-betreiber-gesetzt" in result.stdout
    assert "aus-der-datei-die-nicht-gewinnt" not in result.stdout


def test_a_value_with_shell_special_characters_arrives_intact(tmp_path: Path) -> None:
    tricky = "geheimnis mit $VAR und 'Anführungszeichen'"
    options_file = tmp_path / "options.json"
    options_file.write_text(json.dumps({"secret_key": tricky}))
    result = _run_entrypoint(tmp_path, {"THERMOCTL_ADDON_OPTIONS_FILE": str(options_file)})
    assert result.returncode == 0, result.stderr
    assert f"THERMOCTL_SECRET_KEY={tricky}" in result.stdout


def test_secrets_do_not_appear_on_stderr(tmp_path: Path) -> None:
    """Kein `set -x` ueber dem Optionsblock, kein Echo der Werte (Grundsatz 2)."""
    options_file = tmp_path / "options.json"
    options_file.write_text(json.dumps({"secret_key": "s3hr-geheim-und-lang-genug"}))
    result = _run_entrypoint(tmp_path, {"THERMOCTL_ADDON_OPTIONS_FILE": str(options_file)})
    assert result.returncode == 0, result.stderr
    assert "s3hr-geheim-und-lang-genug" not in result.stderr


def test_a_broken_options_file_aborts_the_start_instead_of_starting_half_configured(
    tmp_path: Path,
) -> None:
    """`set -e` muss den fehlschlagenden Python-Aufruf tatsaechlich abbrechen -- nicht nur
    das `eval` mit leerem Text erfolgreich aussehen lassen."""
    options_file = tmp_path / "options.json"
    options_file.write_text("das ist kein json")
    result = _run_entrypoint(tmp_path, {"THERMOCTL_ADDON_OPTIONS_FILE": str(options_file)})
    assert result.returncode != 0
    assert "THERMOCTL_" not in result.stdout


def test_the_script_that_ships_in_the_image_is_the_one_under_test() -> None:
    """Bindeglied zum Dockerfile: das Skript, das die Tests hier pruefen, ist dasselbe,
    das `docker/Dockerfile` in das Abbild kopiert -- unter demselben Pfad, den
    `docker/entrypoint.sh` aufruft."""
    dockerfile = (ROOT / "docker" / "Dockerfile").read_text()
    entrypoint = ENTRYPOINT.read_text()
    assert "docker/thermoctl_optionen.py /usr/local/bin/thermoctl_optionen.py" in dockerfile
    assert "/usr/local/bin/thermoctl_optionen.py" in entrypoint
    assert OPTIONEN_SKRIPT.exists()
