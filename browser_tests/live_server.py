"""Isolated live server shared by browser tests and screenshot generation."""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Generator
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

REPO_ROOT = Path(__file__).resolve().parent.parent
ADMIN_USERNAME = "browsertest-admin"
ADMIN_PASSWORD = "Durchlauf-Kennwort-9"  # noqa: S105 -- ephemeral local database only


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_healthz(base_url: str, process: subprocess.Popen[str], timeout: float) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f"Server-Prozess hat sich vorzeitig beendet (exit={process.returncode}). "
                "Siehe die mitgeschnittene Ausgabe im Testfehler."
            )
        try:
            with urllib.request.urlopen(f"{base_url}/healthz", timeout=1) as response:  # noqa: S310
                if response.status == 200:
                    return
        except (urllib.error.URLError, ConnectionError, TimeoutError) as exc:
            last_error = exc
        time.sleep(0.1)
    raise TimeoutError(f"/healthz hat innerhalb von {timeout}s nicht geantwortet: {last_error}")


@dataclass(frozen=True)
class LiveServer:
    """A running thermoctl instance, its database, and its first administrator."""

    base_url: str
    database_url: str
    admin_username: str
    admin_password: str
    # The bare, un-proxied address of the same running process -- `None` for `live_server`
    # (there is no separate "direct" address there; `base_url` already is one). Set for
    # `live_server_with_prefix` so a test can reach the *same* instance the Ingress-shaped
    # `base_url` above goes through, but without the stripping proxy in front and without
    # an `X-Ingress-Path` header -- exactly the reverse-proxy-on-the-exposed-port case this
    # task is about, proven against the identical process rather than a second one.
    direct_base_url: str | None = None

    def session(self) -> Session:
        """A short-lived SQLAlchemy session against the server's own database.

        For seeding fixtures the browser cannot conveniently create itself (a zone
        with a schedule, a second, less-privileged user) -- the same shortcut
        ``tests/helpers.py`` takes for the HTTP test suite, just against a real
        file instead of a rolled-back transaction. ``timeout`` gives the SQLite
        driver room to wait out a lock instead of failing immediately if a request
        to the running server happens to hold one at the same moment.
        """
        engine = create_engine(self.database_url, connect_args={"timeout": 30})
        factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        return factory()


def _read_stream(stream: object, sink: list[str]) -> None:
    for line in stream:  # type: ignore[attr-defined]
        sink.append(line)


# The prefix `live_server_with_prefix` (below) serves the interface under -- deliberately
# not `/app` or another plausible-looking real path (the task asked for something
# Ingress-like): Home Assistant's own prefix is `/api/hassio_ingress/<random-token>`.
INGRESS_PREFIX = "/api/hassio_ingress/A1b2C3d4e5"


def _live_server(
    root_path: str,
    *,
    before_setup: Callable[[LiveServer], None] | None = None,
    admin_username: str = ADMIN_USERNAME,
) -> Generator[LiveServer]:
    """Shared implementation behind `live_server` and `live_server_with_prefix`.

    `root_path` becomes `THERMOCTL_ROOT_PATH` for the subprocess -- empty for the
    plain fixture, `INGRESS_PREFIX` for the prefixed one. Everything else (database,
    admin setup, teardown) is identical; only the environment and, in the prefixed
    case, what fronts the server (see `live_server_with_prefix`) differ.

    `admin_username` defaults to the shared browser-test constant; `tools/screenshots.py`
    passes its own demo-schema name so the recorded username never becomes browser-test
    plumbing leaking into documentation images.
    """
    workdir = Path(tempfile.mkdtemp(prefix="thermoctl-browsertests-"))
    db_path = workdir / "browsertests.db"
    database_url = f"sqlite:///{db_path}"
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"

    env = {
        **{key: value for key, value in os.environ.items() if not key.startswith("THERMOCTL_")},
        "THERMOCTL_DATABASE_URL": database_url,
        "THERMOCTL_SECRET_KEY": "b" * 32,
        "THERMOCTL_ROOT_PATH": root_path,
        # Cuts off a developer's own `.env`, exactly like `tests/conftest.py` does --
        # otherwise real MQTT or Meross credentials sitting there for local, manual
        # testing would reach this subprocess and it would reach out to the network.
        "THERMOCTL_ENV_FILE": "",
        "THERMOCTL_BIND_HOST": "127.0.0.1",
        "THERMOCTL_BIND_PORT": str(port),
        "THERMOCTL_SECURE_COOKIES": "false",
        "THERMOCTL_LOG_FORMAT": "text",
        "THERMOCTL_LOG_LEVEL": "INFO",
        # Explicitly unset (not merely defaulted) so a developer's real environment
        # cannot accidentally arm MQTT or Meross for this throwaway instance either.
        "THERMOCTL_MQTT_ENABLED": "false",
        "THERMOCTL_MEROSS_EMAIL": "",
        "THERMOCTL_MEROSS_PASSWORD": "",
    }

    migration = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if migration.returncode != 0:
        shutil.rmtree(workdir, ignore_errors=True)
        raise RuntimeError(
            "`alembic upgrade head` gegen die frische Browsertest-Datenbank ist "
            f"gescheitert:\nSTDOUT:\n{migration.stdout}\nSTDERR:\n{migration.stderr}"
        )

    # Same reasoning as tests/test_migrations.py's own subprocess call: a fixed
    # argument list built from `sys.executable` and constants, not from anything
    # a caller controls.
    process = subprocess.Popen(  # noqa: S603
        [
            sys.executable,
            "-m",
            "uvicorn",
            "thermoctl.app:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    log_lines: list[str] = []
    reader = threading.Thread(target=_read_stream, args=(process.stdout, log_lines), daemon=True)
    reader.start()

    try:
        _wait_for_healthz(base_url, process, timeout=20.0)

        # The setup token is the one secret this project deliberately writes to the
        # log (thermoctl/logging.py, thermoctl/setup.py) -- reading it from here
        # exercises exactly the channel an operator would use, instead of reaching
        # into the database for a shortcut that only proves the database works.
        deadline = time.monotonic() + 5.0
        token = None
        while time.monotonic() < deadline and token is None:
            for line in log_lines:
                match = re.search(r"Einmal-Token \(gültig \d+ Minuten\): (\S+)", line)
                if match:
                    token = match.group(1)
                    break
            if token is None:
                time.sleep(0.05)
        if token is None:
            raise RuntimeError(
                "Kein Einrichtungs-Token in der Serverausgabe gefunden:\n" + "".join(log_lines)
            )

        if before_setup is not None:
            before_setup(LiveServer(base_url, database_url, admin_username, ADMIN_PASSWORD))

        payload = urlencode(
            {
                "username": admin_username,
                "display_name": "Browsertest-Verwaltung",
                "password": ADMIN_PASSWORD,
                "timezone": "Europe/Berlin",
                "setup_token": token,
            }
        ).encode()
        request = urllib.request.Request(  # noqa: S310
            f"{base_url}/setup",
            data=payload,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
                status = response.status
        except urllib.error.HTTPError as exc:
            status = exc.code
        if status not in (200, 303):
            raise RuntimeError(f"/setup hat mit Status {status} geantwortet, erwartet 303.")

        yield LiveServer(
            base_url=base_url,
            database_url=database_url,
            admin_username=admin_username,
            admin_password=ADMIN_PASSWORD,
        )
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
        reader.join(timeout=2)
        shutil.rmtree(workdir, ignore_errors=True)
