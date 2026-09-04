"""Infrastructure for the Playwright browser tests.

This suite is deliberately outside ``tests/`` and carries its own ``pytest.ini``
(``browser_tests/pytest.ini``): an ordinary ``pytest`` run must not even look in
here, let alone start a browser or a real server. See README.md, section
"Browsertests", for how to invoke it.

The fixtures below start the real application as a subprocess against its own,
freshly migrated SQLite database, wait for ``/healthz``, and tear everything down
afterwards. No test-suite trick (``TestClient``, rolled-back transactions) is used
here on purpose -- a real browser needs a real address, and the whole point of this
suite is to see what only a real browser can see.
"""

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
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode

import pytest
from playwright.sync_api import Browser, BrowserContext, ConsoleMessage, Page, sync_playwright
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from browser_tests._ingress_proxy import start_stripping_proxy

REPO_ROOT = Path(__file__).resolve().parent.parent

# Reused instead of duplicated: these already know the schema's foreign-key order
# and build the same rows the migrations seed in a real database. Importable because
# `tests/__init__.py` exists and the repo root is on `sys.path` (added below).
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Twelve is the minimum (thermoctl/auth/passwords.py); chosen well above it so a
# future change to the minimum does not quietly break this fixture.
ADMIN_USERNAME = "browsertest-admin"
ADMIN_PASSWORD = "Durchlauf-Kennwort-9"  # noqa: S105 -- local, ephemeral, throwaway DB


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
    raise TimeoutError(
        f"/healthz hat innerhalb von {timeout}s nicht geantwortet: {last_error}"
    )


@dataclass(frozen=True)
class LiveServer:
    """A running thermoctl instance, its database, and its first administrator."""

    base_url: str
    database_url: str
    admin_username: str
    admin_password: str

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


def _live_server(root_path: str) -> Iterator[LiveServer]:
    """Shared implementation behind `live_server` and `live_server_with_prefix`.

    `root_path` becomes `THERMOCTL_ROOT_PATH` for the subprocess -- empty for the
    plain fixture, `INGRESS_PREFIX` for the prefixed one. Everything else (database,
    admin setup, teardown) is identical; only the environment and, in the prefixed
    case, what fronts the server (see `live_server_with_prefix`) differ.
    """
    workdir = Path(tempfile.mkdtemp(prefix="thermoctl-browsertests-"))
    db_path = workdir / "browsertests.db"
    database_url = f"sqlite:///{db_path}"
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"

    env = {
        **os.environ,
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
            sys.executable, "-m", "uvicorn", "thermoctl.app:create_app", "--factory",
            "--host", "127.0.0.1", "--port", str(port),
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
                match = re.search(r"Einmal-Token \(gueltig \d+ Minuten\): (\S+)", line)
                if match:
                    token = match.group(1)
                    break
            if token is None:
                time.sleep(0.05)
        if token is None:
            raise RuntimeError(
                "Kein Einrichtungs-Token in der Serverausgabe gefunden:\n"
                + "".join(log_lines)
            )

        payload = urlencode({
            "username": ADMIN_USERNAME,
            "display_name": "Browsertest-Verwaltung",
            "password": ADMIN_PASSWORD,
            "timezone": "Europe/Berlin",
            "setup_token": token,
        }).encode()
        request = urllib.request.Request(  # noqa: S310
            f"{base_url}/setup", data=payload, method="POST",
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
            admin_username=ADMIN_USERNAME,
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


@pytest.fixture(scope="session")
def live_server() -> Iterator[LiveServer]:
    yield from _live_server("")


@pytest.fixture(scope="session")
def live_server_with_prefix() -> Iterator[LiveServer]:
    """A `LiveServer` fronted by a small proxy that strips `INGRESS_PREFIX`.

    `_live_server(INGRESS_PREFIX)` starts the real application with
    `THERMOCTL_ROOT_PATH` set -- it still only ever *receives* bare paths on its own
    port, exactly like the container would behind real Ingress. `start_stripping_proxy`
    (`browser_tests/_ingress_proxy.py`) is what actually plays Home Assistant's part:
    it listens on its own port, strips `INGRESS_PREFIX` from every incoming request
    before forwarding it to the real server, and passes the response back unchanged.
    `LiveServer.base_url` here points at the *proxy*, prefix included -- so
    `page.goto("/login")` against this fixture's context resolves to
    `.../api/hassio_ingress/A1b2C3d4e5/login`, precisely what a browser sees behind
    real Ingress.
    """
    generator = _live_server(INGRESS_PREFIX)
    backend = next(generator)
    server, port = start_stripping_proxy(INGRESS_PREFIX, backend.base_url)
    try:
        yield LiveServer(
            # Trailing slash, deliberately: Playwright's `base_url` context option
            # resolves a relative `goto()` argument by ordinary URL-reference rules
            # (RFC 3986) -- a value starting with "/" replaces the whole path
            # (`goto("/login")` against ".../A1b2C3d4e5" would land on ".../login",
            # losing the prefix entirely), and a value without a leading "/" only
            # appends correctly if the base itself ends in "/". Every `goto()` call
            # against this fixture must therefore use a *relative*, non-leading-slash
            # path ("login", not "/login").
            base_url=f"http://127.0.0.1:{port}{INGRESS_PREFIX}/",
            database_url=backend.database_url,
            admin_username=backend.admin_username,
            admin_password=backend.admin_password,
        )
    finally:
        server.shutdown()
        server.server_close()
        # Exhausts the generator so its own `finally` (process teardown) runs.
        next(generator, None)


@pytest.fixture(scope="session")
def browser() -> Iterator[Browser]:
    """Chromium, unsichtbar -- ausser jemand will zusehen.

    `THERMOCTL_BROWSER_HEADED=1` oeffnet ein echtes Fenster und verlangsamt jede
    Geste um `THERMOCTL_BROWSER_SLOWMO` Millisekunden (Vorgabe 300). Das ist der
    einzige Weg, einen Browsertest zu verstehen, der fehlschlaegt: Zusehen, statt
    aus einer Fehlermeldung zu raten. Ueber eine Umgebungsvariable und nicht ueber
    einen Schalter auf der Kommandozeile, weil diese Vorrichtung Playwright
    unmittelbar startet und nicht ueber das Zusatzpaket `pytest-playwright`, dessen
    `--headed` es hier also gar nicht gibt.
    """
    sichtbar = bool(os.environ.get("THERMOCTL_BROWSER_HEADED"))
    langsam = int(os.environ.get("THERMOCTL_BROWSER_SLOWMO", "300")) if sichtbar else 0
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch(headless=not sichtbar, slow_mo=langsam)
        yield instance
        instance.close()


@pytest.fixture
def context(browser: Browser, live_server: LiveServer) -> Iterator[BrowserContext]:
    # Fixed light scheme: the CSS deliberately swaps colours under
    # `[data-bs-theme="dark"]` (thermoctl.css), and a test asserting a specific
    # computed colour must not depend on which theme the host happens to prefer.
    ctx = browser.new_context(base_url=live_server.base_url, color_scheme="light")
    yield ctx
    ctx.close()


@pytest.fixture
def console_errors() -> list[str]:
    return []


def _record_console_error(sink: list[str], message: ConsoleMessage) -> None:
    """Filters the browser's console feed down to genuine application errors.

    Chromium reports a plain failed HTTP request (a 401 on a deliberately wrong
    login, a 403 our own navigation tests provoke on purpose, ...) through the same
    console channel, with the same ``type == "error"``, as an uncaught exception or
    an actual ``console.error()`` call. The former is expected application
    behaviour this suite triggers on purpose in several tests; the latter is
    exactly the class of bug this fixture exists to catch. Without this filter,
    every test that visits an intentionally-rejected page would fail on a "console
    error" that was never a defect.

    htmx itself adds a second, equally unavoidable source of the same kind: any
    event it fires with an ``error`` detail (``htmx:responseError``,
    ``htmx:sendError``, ...) goes through ``console.error`` unconditionally, inside
    htmx's own trigger function, before any application code sees the event --
    there is no way to opt out of it from outside htmx.min.js. A boosted request a
    test deliberately fails (`browser_tests/test_loading_indicator.py`, the loading
    bar must disappear again after a failure) always produces exactly this line, on
    purpose, and it is filtered here for the same reason as the line above.
    """
    if message.type != "error":
        return
    if message.text.startswith("Failed to load resource:"):
        return
    if message.text.startswith("Response Status Error Code "):
        return
    sink.append(f"[console] {message.text}")


@pytest.fixture
def page(context: BrowserContext, console_errors: list[str]) -> Iterator[Page]:
    """A page that fails the test if the browser console logs an error, or an
    uncaught exception happens on any page it visits.

    This is deliberately the single most valuable check in this whole suite (see
    the task description) and deliberately wired here, once, instead of repeated in
    every test: a missing stylesheet, a JavaScript exception in schedule.js, a
    rejected fetch -- all of them show up here on every page a test happens to
    visit, without that test having to know to look.
    """
    new_page = context.new_page()
    new_page.on("console", lambda message: _record_console_error(console_errors, message))
    new_page.on("pageerror", lambda exc: console_errors.append(f"[pageerror] {exc}"))
    yield new_page
    new_page.close()
    assert not console_errors, "Browserkonsole meldete Fehler:\n" + "\n".join(console_errors)


@pytest.fixture
def admin_page(page: Page, live_server: LiveServer) -> Page:
    """A page already logged in as the administrator created at server start.

    Not itself the login test (see test_login_logout.py) -- most other tests need
    an authenticated page as a *precondition*, and repeating the login dance in
    each of them would make every one of them a login test by accident.
    """
    page.goto("/login")
    page.get_by_label("Benutzername").fill(live_server.admin_username)
    page.get_by_label("Passwort").fill(live_server.admin_password)
    page.get_by_role("button", name="Anmelden").click()
    # Not `wait_for_url`: the login form has no `hx-post` of its own, but `hx-boost`
    # on <body> still upgrades it to a fetch that follows the redirect and swaps the
    # page in via `pushState` -- indistinguishable from a real navigation by URL
    # alone. The navigation bar only exists on `base.html`, never on the login
    # page's `base_plain.html`, so its presence is the actual proof of being past
    # the login.
    page.locator(".tc-head").wait_for()
    return page


# --- the same three fixtures, against `live_server_with_prefix` -----------------
#
# Playwright's `base_url` context option is what makes a bare `page.goto("/login")`
# resolve against a particular server -- there is no way to parametrize a single
# `context`/`page` fixture by which `live_server` a given test wants without every
# other test in this suite (which never mentions a prefix) having to say so too.
# Duplicating the three fixtures below against `live_server_with_prefix` keeps every
# existing test and fixture in this file untouched.


@pytest.fixture
def context_with_prefix(
    browser: Browser, live_server_with_prefix: LiveServer
) -> Iterator[BrowserContext]:
    ctx = browser.new_context(base_url=live_server_with_prefix.base_url, color_scheme="light")
    yield ctx
    ctx.close()


@pytest.fixture
def page_with_prefix(
    context_with_prefix: BrowserContext, console_errors: list[str]
) -> Iterator[Page]:
    new_page = context_with_prefix.new_page()
    new_page.on("console", lambda message: _record_console_error(console_errors, message))
    new_page.on("pageerror", lambda exc: console_errors.append(f"[pageerror] {exc}"))
    yield new_page
    new_page.close()
    assert not console_errors, "Browserkonsole meldete Fehler:\n" + "\n".join(console_errors)


@pytest.fixture
def admin_page_with_prefix(page_with_prefix: Page, live_server_with_prefix: LiveServer) -> Page:
    page_with_prefix.goto("login")
    page_with_prefix.get_by_label("Benutzername").fill(live_server_with_prefix.admin_username)
    page_with_prefix.get_by_label("Passwort").fill(live_server_with_prefix.admin_password)
    page_with_prefix.get_by_role("button", name="Anmelden").click()
    page_with_prefix.locator(".tc-head").wait_for()
    return page_with_prefix
