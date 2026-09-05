"""The Homebridge copy button on the Schnittstellen page.

Worth a browser test for one reason a rendered-HTML test in ``tests/`` cannot see:
`navigator.clipboard` only exists in a "secure context" (HTTPS, or `localhost`/
`127.0.0.1`) -- and thermoctl typically runs on the home network over plain HTTP,
where it is `undefined`. The two things that only a real browser can prove: the
button actually places the configuration on the clipboard when the modern API is
available, and it still gives an honest, visible result instead of doing nothing
when that API is forced unavailable (simulating the plain-HTTP case Chromium itself
won't reproduce, since `127.0.0.1` is always a secure context to it).
"""

from __future__ import annotations

import pytest
from playwright.sync_api import BrowserContext, Page, expect

from browser_tests import seed
from browser_tests.conftest import LiveServer

pytestmark = pytest.mark.browser


def test_the_copy_button_places_the_zones_configuration_on_the_clipboard(
    admin_page: Page, live_server: LiveServer, context: BrowserContext
) -> None:
    with live_server.session() as session:
        seed.create_bare_zone(session, "Bad")
        session.commit()

    context.grant_permissions(["clipboard-read", "clipboard-write"])
    admin_page.goto("/interfaces")

    entry = admin_page.locator("details", has_text="Bad")
    entry.locator("summary").click()
    config_text = entry.locator("pre").inner_text()
    assert '"accessory": "mqttthing"' in config_text

    entry.get_by_role("button", name="Konfiguration kopieren").click()

    status = entry.locator("span").last
    expect(status).to_contain_text("In die Zwischenablage kopiert.")

    clipboard_text = admin_page.evaluate("() => navigator.clipboard.readText()")
    assert clipboard_text == config_text


def test_the_copy_button_still_gives_an_honest_result_without_the_clipboard_api(
    admin_page: Page, live_server: LiveServer
) -> None:
    """Simulates the plain-HTTP case: `window.isSecureContext` forced to `false`,
    exactly what strips `navigator.clipboard` away in a real browser on an
    unencrypted connection. The button must not stay silently inert -- either the
    `execCommand` fallback succeeds, or the honest failure message appears; either
    way something visible happens, and the page fixture's console-error guard
    (browser_tests/conftest.py) still proves no uncaught exception occurred.
    """
    with live_server.session() as session:
        seed.create_bare_zone(session, "Kueche")
        session.commit()

    admin_page.add_init_script(
        "Object.defineProperty(window, 'isSecureContext', { get: () => false });"
    )
    admin_page.goto("/interfaces")

    entry = admin_page.locator("details", has_text="Kueche")
    entry.locator("summary").click()
    entry.get_by_role("button", name="Konfiguration kopieren").click()

    status = entry.locator("span").last
    expect(status).to_be_visible()
    text = status.inner_text()
    assert text in (
        "In die Zwischenablage kopiert.",
        "Kopieren nicht möglich — Text bitte markieren und selbst kopieren.",
    ), text
