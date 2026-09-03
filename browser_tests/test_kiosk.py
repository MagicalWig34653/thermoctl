"""The kiosk dashboard: the one page nobody watches over anyone's shoulder.

It runs on a wall tablet, authenticates via its own single-use-link cookie instead
of a login, and auto-refreshes itself with `hx-trigger="every 20s"` -- none of
which an HTTP test exercises the way a real browser does. This test issues a real
kiosk token through the admin UI (the same way an operator would), opens it in a
fresh, unauthenticated browser context (nothing shared with the admin session, as
on the tablet it is meant for), and drives the setpoint buttons that are only
present because the token was issued with "auch bedienen".
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Browser, Page, expect

from browser_tests import seed
from browser_tests.conftest import LiveServer, _record_console_error

pytestmark = pytest.mark.browser


def _issue_kiosk_token(admin_page: Page, zone_name: str) -> str:
    admin_page.goto("/kiosk-tokens")
    admin_page.get_by_label("Name").fill("Browsertest-Tablet")
    admin_page.get_by_text(zone_name, exact=False).click()
    admin_page.get_by_label("Auch bedienen (Sollwert und Boost)").check()
    admin_page.get_by_role("button", name="Ausstellen").click()

    entry = admin_page.locator("#new-kiosk-token")
    expect(entry).to_be_visible()
    text = entry.inner_text()
    match = re.search(r"/kiosk/(\S+)", text)
    assert match, f"Kein Kiosk-Token in {text!r} gefunden."
    return match.group(1)


def test_the_kiosk_dashboard_shows_the_zone_and_lets_the_setpoint_be_adjusted(
    admin_page: Page, live_server: LiveServer, browser: Browser
) -> None:
    zone_name = "Kiosk-Wohnzimmer"
    with live_server.session() as session:
        zone = seed.create_schedule_zone(session, zone_name)
        session.commit()
        zone_display_name = zone.display_name

    plaintext = _issue_kiosk_token(admin_page, zone_display_name)

    # A separate, unauthenticated context: the kiosk cookie is its own credential,
    # unrelated to the admin's session cookie -- exactly what a wall tablet has.
    kiosk_context = browser.new_context(
        base_url=live_server.base_url, color_scheme="light"
    )
    kiosk_errors: list[str] = []
    kiosk_page = kiosk_context.new_page()
    kiosk_page.on("console", lambda message: _record_console_error(kiosk_errors, message))
    try:
        kiosk_page.goto(f"/kiosk/{plaintext}")
        expect(kiosk_page).to_have_url(re.compile(r"/kiosk$"))
        expect(kiosk_page.get_by_text(zone_display_name)).to_be_visible()
        # The auto-refresh htmx wires up -- the one behaviour unique to this page
        # among everything else in this suite.
        expect(kiosk_page.locator("#kiosk-body")).to_have_attribute(
            "hx-trigger", "every 20s"
        )

        tile = kiosk_page.locator(".kiosk-tile", has_text=zone_display_name)
        setpoint_value = tile.locator(".kiosk-setpoint .t-value")
        before = setpoint_value.inner_text()
        tile.get_by_label("Sollwert anheben").click()
        expect(setpoint_value).not_to_have_text(before)
    finally:
        assert not kiosk_errors, "Kiosk-Konsole meldete Fehler:\n" + "\n".join(kiosk_errors)
        kiosk_context.close()
