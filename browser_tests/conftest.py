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
from collections.abc import Iterator

import pytest
from playwright.sync_api import Browser, BrowserContext, ConsoleMessage, Page, sync_playwright

from browser_tests._ingress_proxy import start_stripping_proxy
from browser_tests.live_server import (
    INGRESS_PREFIX,
    LiveServer,
    _live_server,
)


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
            # `backend.base_url` -- the real server's own port, with no proxy and no
            # `X-Ingress-Path` header in front of it -- reached directly, exactly as a
            # reverse proxy pointed straight at the exposed container port would.
            # `THERMOCTL_ROOT_PATH` is still set for this same process (see
            # `_live_server(INGRESS_PREFIX)` above); what makes this "direct" is only
            # the absence of the header, same as `client_direct_with_ingress_
            # configured` in `tests/conftest.py`.
            direct_base_url=f"{backend.base_url}/",
        )
    finally:
        server.shutdown()
        server.server_close()
        # Exhausts the generator so its own `finally` (process teardown) runs.
        next(generator, None)


@pytest.fixture(scope="session")
def browser() -> Iterator[Browser]:
    """Chromium, unsichtbar -- außer jemand will zusehen.

    `THERMOCTL_BROWSER_HEADED=1` öffnet ein echtes Fenster und verlangsamt jede
    Geste um `THERMOCTL_BROWSER_SLOWMO` Millisekunden (Vorgabe 300). Das ist der
    einzige Weg, einen Browsertest zu verstehen, der fehlschlägt: Zusehen, statt
    aus einer Fehlermeldung zu raten. Über eine Umgebungsvariable und nicht über
    einen Schalter auf der Kommandozeile, weil diese Vorrichtung Playwright
    unmittelbar startet und nicht über das Zusatzpaket `pytest-playwright`, dessen
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
    # Fixed light scheme: a test asserting a specific computed colour must not depend
    # on which theme the host happens to prefer.
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
    """
    if message.type != "error":
        return
    if message.text.startswith("Failed to load resource:"):
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


def sign_in(page: Page, login_path: str, username: str, password: str) -> None:
    """Meldet in der Svelte-Oberfläche an. `/login` leitet auf `/app/login` um; als
    Beweis, dass die Anmeldung durch ist, gilt der Rumpf der Oberfläche mit der
    Hauptnavigation (`#tc-main` gibt es nur angemeldet)."""
    page.goto(login_path)
    page.get_by_label("Benutzername").fill(username)
    page.get_by_label("Passwort").fill(password)
    page.get_by_role("button", name="Anmelden", exact=True).click()
    page.get_by_role("button", name="Abmelden").or_(page.get_by_text("Abmelden")).first.wait_for()


@pytest.fixture
def admin_page(page: Page, live_server: LiveServer) -> Page:
    """A page already logged in as the administrator created at server start.

    Not itself the login test (see test_login_logout.py) -- most other tests need
    an authenticated page as a *precondition*, and repeating the login dance in
    each of them would make every one of them a login test by accident.
    """
    sign_in(page, "/login", live_server.admin_username, live_server.admin_password)
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
    sign_in(
        page_with_prefix, "login",
        live_server_with_prefix.admin_username, live_server_with_prefix.admin_password,
    )
    return page_with_prefix


# --- the same running instance, reached directly instead of through the proxy ---
#
# `live_server_with_prefix` fronts one running thermoctl process with two addresses:
# `base_url` (through the stripping proxy, `X-Ingress-Path` included -- the fixtures
# above) and `direct_base_url` (the process's own port, nothing in front). Both
# fixtures below open a *second*, independent browser context against the very same
# `direct_base_url` -- this is the actual proof this task asks for: not that direct
# access works in isolation (that is already what the plain `page`/`admin_page`
# fixtures against `live_server` show), but that it keeps working *at the same time*
# as Ingress access against the identical configured, running process.


@pytest.fixture
def context_direct_via_prefixed_instance(
    browser: Browser, live_server_with_prefix: LiveServer
) -> Iterator[BrowserContext]:
    assert live_server_with_prefix.direct_base_url is not None
    ctx = browser.new_context(
        base_url=live_server_with_prefix.direct_base_url, color_scheme="light"
    )
    yield ctx
    ctx.close()


@pytest.fixture
def page_direct_via_prefixed_instance(
    context_direct_via_prefixed_instance: BrowserContext, console_errors: list[str]
) -> Iterator[Page]:
    new_page = context_direct_via_prefixed_instance.new_page()
    new_page.on("console", lambda message: _record_console_error(console_errors, message))
    new_page.on("pageerror", lambda exc: console_errors.append(f"[pageerror] {exc}"))
    yield new_page
    new_page.close()
    assert not console_errors, "Browserkonsole meldete Fehler:\n" + "\n".join(console_errors)


@pytest.fixture
def admin_page_direct_via_prefixed_instance(
    page_direct_via_prefixed_instance: Page, live_server_with_prefix: LiveServer
) -> Page:
    sign_in(
        page_direct_via_prefixed_instance, "login",
        live_server_with_prefix.admin_username, live_server_with_prefix.admin_password,
    )
    return page_direct_via_prefixed_instance
