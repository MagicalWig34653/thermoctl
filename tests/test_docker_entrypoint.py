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
INGRESS_SKRIPT = ROOT / "docker" / "thermoctl_ingress.py"


def _make_executable(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _fake_bin(tmp_path: Path) -> Path:
    """A PATH directory with stand-ins for everything the entrypoint calls.

    `alembic` and `python3` (which the entrypoint invokes as bare names) plus
    `thermoctl`, standing in for the real console script the entrypoint
    ultimately `exec`s. `thermoctl` prints every `THERMOCTL_*` variable it
    sees so the test can check what the options translation produced.

    Also stand-ins for the privilege-drop machinery (`id`, `chown`, `setpriv`),
    used only by the tests that simulate a root start -- see
    ``THERMOCTL_TEST_ALS_ROOT`` and ``THERMOCTL_TEST_AUFRUFPROTOKOLL`` below.
    Every other test leaves both unset, so `id -u` falls through to the real
    binary (the test process's own, non-root uid) and `chown`/`setpriv` are
    never reached -- entrypoint.sh's root branch only runs when `id -u` is `0`.
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
    # "No such file or directory" for a path prefix, not the whole path. Logs each
    # invocation (script path only, never argv beyond that -- no values) so a test can
    # check the options script does not run twice, e.g. once as root and once, doomed to
    # fail, again after the privilege drop.
    _make_executable(
        bin_dir / "python3",
        "#!/bin/sh\n"
        'if [ -n "$THERMOCTL_TEST_AUFRUFPROTOKOLL" ]; then\n'
        '    echo "python3 $1" >> "$THERMOCTL_TEST_AUFRUFPROTOKOLL"\n'
        "fi\n"
        f'exec "{sys.executable}" "$@"\n',
    )
    _make_executable(
        bin_dir / "id",
        '#!/bin/sh\n'
        'if [ "$1" = "-u" ] && [ -n "$THERMOCTL_TEST_ALS_ROOT" ]; then\n'
        "    echo 0\n"
        "else\n"
        '    exec /usr/bin/id "$@"\n'
        "fi\n",
    )
    _make_executable(
        bin_dir / "chown",
        "#!/bin/sh\n"
        'if [ -n "$THERMOCTL_TEST_AUFRUFPROTOKOLL" ]; then\n'
        '    echo "chown $*" >> "$THERMOCTL_TEST_AUFRUFPROTOKOLL"\n'
        "fi\n",
    )
    _make_executable(
        bin_dir / "setpriv",
        "#!/bin/sh\n"
        'if [ -n "$THERMOCTL_TEST_AUFRUFPROTOKOLL" ]; then\n'
        '    echo "setpriv $*" >> "$THERMOCTL_TEST_AUFRUFPROTOKOLL"\n'
        "fi\n"
        "while [ $# -gt 0 ]; do\n"
        '    case "$1" in\n'
        "        --reuid=*|--regid=*|--init-groups) shift ;;\n"
        "        *) break ;;\n"
        "    esac\n"
        "done\n"
        'exec sh "$@"\n',
    )
    return bin_dir


def _run_entrypoint(tmp_path: Path, extra_env: dict[str, str]) -> subprocess.CompletedProcess:
    bin_dir = _fake_bin(tmp_path)
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "THERMOCTL_ADDON_OPTIONS_SCRIPT": str(OPTIONEN_SKRIPT),
        "THERMOCTL_INGRESS_SCRIPT": str(INGRESS_SKRIPT),
        **extra_env,
    }
    return subprocess.run(  # noqa: S603
        ["/bin/sh", str(ENTRYPOINT)],
        env=env,
        capture_output=True,
        text=True,
    )


def test_without_an_options_file_nothing_changes(tmp_path: Path) -> None:
    """Der gewöhnliche docker-compose-Betrieb: kein /data/options.json, also keine
    Uebersetzung -- die Umgebungsvariablen, die schon gesetzt sind, kommen unverändert
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
        json.dumps({"secret_key": "y" * 40, "log_level": "DEBUG", "mqtt_enabled": True})
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
    """Kein `set -x` über dem Optionsblock, kein Echo der Werte (Grundsatz 2)."""
    options_file = tmp_path / "options.json"
    options_file.write_text(json.dumps({"secret_key": "s3hr-geheim-und-lang-genug"}))
    result = _run_entrypoint(tmp_path, {"THERMOCTL_ADDON_OPTIONS_FILE": str(options_file)})
    assert result.returncode == 0, result.stderr
    assert "s3hr-geheim-und-lang-genug" not in result.stderr


def test_a_broken_options_file_aborts_the_start_instead_of_starting_half_configured(
    tmp_path: Path,
) -> None:
    """`set -e` muss den fehlschlagenden Python-Aufruf tatsächlich abbrechen -- nicht nur
    das `eval` mit leerem Text erfolgreich aussehen lassen."""
    options_file = tmp_path / "options.json"
    options_file.write_text("das ist kein json")
    result = _run_entrypoint(tmp_path, {"THERMOCTL_ADDON_OPTIONS_FILE": str(options_file)})
    assert result.returncode != 0
    assert "THERMOCTL_" not in result.stdout


def test_an_unreadable_options_file_aborts_the_start_instead_of_starting_half_configured(
    tmp_path: Path,
) -> None:
    """Der aus dem Betrieb gemeldete Fehler: der Supervisor legt options.json als root an,
    nur für root lesbar. Solange der Container das nicht lesen kann, muss der Start
    abbrechen, mit einer verständlichen Meldung -- und `alembic`/`thermoctl` dürfen
    nicht laufen, auch nicht ohne eine einzige Einstellung."""
    options_file = tmp_path / "options.json"
    options_file.write_text(json.dumps({"secret_key": "x" * 32}))
    options_file.chmod(0o000)
    try:
        result = _run_entrypoint(tmp_path, {"THERMOCTL_ADDON_OPTIONS_FILE": str(options_file)})
    finally:
        options_file.chmod(0o600)  # sonst kann pytest die Datei am Ende nicht aufräumen
    assert result.returncode != 0
    assert "THERMOCTL_" not in result.stdout
    assert "nicht lesbar" in result.stderr
    assert "Permission denied" in result.stderr


def test_a_root_start_drops_to_the_unprivileged_user_before_alembic_and_the_service(
    tmp_path: Path,
) -> None:
    """Simuliert den Home-Assistant-Add-on-Betrieb: der Container startet als root (siehe
    Dockerfile), damit er /data/options.json und /data selbst lesen bzw. beschreibbar
    machen kann -- gibt die Rechte aber vor `alembic` und dem Dienst wieder ab. `id`,
    `chown` und `setpriv` sind hier Attrappen (siehe `_fake_bin`): das Verhalten von
    `setpriv` selbst zu prüfen ist nicht Aufgabe dieses Tests, wohl aber, dass
    entrypoint.sh es mit dem richtigen Zielbenutzer aufruft, `chown` auf das
    Datenverzeichnis anwendet, dabei nur einmal durchläuft (keine Endlosschleife durch
    den Wiedereinstieg via `exec ... "$0"`) und die aus der Optionsdatei gelesenen Werte
    danach immer noch beim Dienst ankommen.

    Prüft zusätzlich, dass die Optionsdatei dabei nur einmal gelesen wird -- als root,
    vor dem Rechteabgeben. Ein zweiter Versuch nach dem Wiedereinstieg wäre in der echten
    Anlage der aus dem Betrieb gemeldete Fehler: die Datei ist dann root:root 600, für
    den unprivilegierten Benutzer nicht mehr lesbar, und der zweite Versuch schlüge fehl,
    obwohl der erste (als root) schon erfolgreich war."""
    options_file = tmp_path / "options.json"
    options_file.write_text(json.dumps({"secret_key": "z" * 32, "log_level": "DEBUG"}))
    aufrufprotokoll = tmp_path / "aufrufe.log"
    result = _run_entrypoint(
        tmp_path,
        {
            "THERMOCTL_ADDON_OPTIONS_FILE": str(options_file),
            "THERMOCTL_TEST_ALS_ROOT": "1",
            "THERMOCTL_TEST_AUFRUFPROTOKOLL": str(aufrufprotokoll),
            "THERMOCTL_DATA_DIR": str(tmp_path / "data"),
        },
    )
    assert result.returncode == 0, result.stderr
    assert "THERMOCTL_SECRET_KEY=" + "z" * 32 in result.stdout
    assert "THERMOCTL_LOG_LEVEL=DEBUG" in result.stdout

    protokoll = aufrufprotokoll.read_text().splitlines()
    assert protokoll.count(f"chown thermoctl:thermoctl {tmp_path / 'data'}") == 1
    setpriv_aufrufe = [zeile for zeile in protokoll if zeile.startswith("setpriv ")]
    assert len(setpriv_aufrufe) == 1
    assert "--reuid=thermoctl" in setpriv_aufrufe[0]
    assert "--regid=thermoctl" in setpriv_aufrufe[0]
    assert "--init-groups" in setpriv_aufrufe[0]
    python3_aufrufe = [zeile for zeile in protokoll if zeile.startswith("python3 ")]
    assert python3_aufrufe.count(f"python3 {OPTIONEN_SKRIPT}") == 1
    assert python3_aufrufe.count(f"python3 {INGRESS_SKRIPT}") == 1


def test_the_script_that_ships_in_the_image_is_the_one_under_test() -> None:
    """Bindeglied zum Dockerfile: das Skript, das die Tests hier prüfen, ist dasselbe,
    das `docker/Dockerfile` in das Abbild kopiert -- unter demselben Pfad, den
    `docker/entrypoint.sh` aufruft."""
    dockerfile = (ROOT / "docker" / "Dockerfile").read_text()
    entrypoint = ENTRYPOINT.read_text()
    assert "docker/thermoctl_optionen.py /usr/local/bin/thermoctl_optionen.py" in dockerfile
    assert "/usr/local/bin/thermoctl_optionen.py" in entrypoint
    assert OPTIONEN_SKRIPT.exists()


def test_the_ingress_script_that_ships_in_the_image_is_the_one_under_test() -> None:
    """Dasselbe Bindeglied wie oben, für das Ingress-Skript."""
    dockerfile = (ROOT / "docker" / "Dockerfile").read_text()
    entrypoint = ENTRYPOINT.read_text()
    assert "docker/thermoctl_ingress.py /usr/local/bin/thermoctl_ingress.py" in dockerfile
    assert "/usr/local/bin/thermoctl_ingress.py" in entrypoint
    assert INGRESS_SKRIPT.exists()


def test_without_supervisor_token_ingress_root_path_stays_unset(tmp_path: Path) -> None:
    """Gewöhnlicher docker-compose-Betrieb ohne SUPERVISOR_TOKEN: keine Abfrage, kein
    THERMOCTL_ROOT_PATH in der Umgebung, mit der `thermoctl` gestartet wird."""
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
    assert "THERMOCTL_ROOT_PATH" not in result.stdout


def test_supervisor_token_but_operator_set_root_path_wins(tmp_path: Path) -> None:
    """Ein Betreiber, der THERMOCTL_ROOT_PATH selbst gesetzt hat, gewinnt -- auch wenn ein
    SUPERVISOR_TOKEN vorhanden wäre. Zeigt via eine garantiert unerreichbare
    Supervisor-Adresse: würde trotzdem abgefragt, liefe der Test in die
    Zeitüberschreitung statt sofort durchzulaufen."""
    missing = tmp_path / "options.json"
    result = _run_entrypoint(
        tmp_path,
        {
            "THERMOCTL_ADDON_OPTIONS_FILE": str(missing),
            "THERMOCTL_DATABASE_URL": "sqlite:///./eigene.db",
            "THERMOCTL_SECRET_KEY": "x" * 32,
            "THERMOCTL_ROOT_PATH": "/vom-betreiber-gesetzt",
            "SUPERVISOR_TOKEN": "mein-token",
            "THERMOCTL_INGRESS_SUPERVISOR_URL": "http://127.0.0.1:1/",
        },
    )
    assert result.returncode == 0, result.stderr
    assert "THERMOCTL_ROOT_PATH=/vom-betreiber-gesetzt" in result.stdout


def test_supervisor_token_with_valid_ingress_entry_sets_root_path(tmp_path: Path) -> None:
    """End-zu-Ende durch die echte Shell: ein Stellvertreter-Supervisor liefert einen
    gültigen ingress_entry, der Werte kommt bis zum (Fake-)`thermoctl`-Prozess durch."""
    import http.server
    import json as _json
    import threading

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                _json.dumps(
                    {"result": "ok", "data": {"ingress_entry": "/api/hassio_ingress/deadbeef"}}
                ).encode()
            )

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        missing = tmp_path / "options.json"
        result = _run_entrypoint(
            tmp_path,
            {
                "THERMOCTL_ADDON_OPTIONS_FILE": str(missing),
                "THERMOCTL_DATABASE_URL": "sqlite:///./eigene.db",
                "THERMOCTL_SECRET_KEY": "x" * 32,
                "SUPERVISOR_TOKEN": "mein-token",
                "THERMOCTL_INGRESS_SUPERVISOR_URL": f"http://127.0.0.1:{server.server_port}/",
            },
        )
    finally:
        server.shutdown()
        thread.join()
    assert result.returncode == 0, result.stderr
    assert "THERMOCTL_ROOT_PATH=/api/hassio_ingress/deadbeef" in result.stdout
    assert "mein-token" not in result.stdout
    assert "mein-token" not in result.stderr
