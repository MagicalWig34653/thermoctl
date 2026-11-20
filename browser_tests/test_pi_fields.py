"""Raumtechnik, Gruppe „PI-Regler (Beta)“: neue Felder im echten Browser.

Geprüft werden die Warnung bei einer Verstärkung über 0,5 (mit Empfehlung zur Nachstellzeit ab
1,0) und das Ausgrauen der Vorsteuerungsfelder, solange die Vorsteuerung aus ist.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Browser, Page, expect

from browser_tests.live_server import LiveServer
from browser_tests.seed import create_schedule_zone

pytestmark = pytest.mark.browser


@pytest.fixture(scope="module")
def room_id(live_server: LiveServer) -> int:
    with live_server.session() as session:
        zone = create_schedule_zone(session, "PI-Feldraum")
        session.commit()
        return int(zone.id)


@pytest.fixture()
def page(browser: Browser, live_server: LiveServer) -> Page:
    ctx = browser.new_context(
        base_url=live_server.base_url, viewport={"width": 1280, "height": 900}
    )
    page = ctx.new_page()
    page.goto("/login")
    page.get_by_label("Benutzername").fill(live_server.admin_username)
    page.get_by_label("Passwort").fill(live_server.admin_password)
    page.get_by_role("button", name="Anmelden", exact=True).click()
    page.wait_for_url(lambda url: "/login" not in url)
    yield page
    ctx.close()


def _open_pi_group(page: Page, room: int) -> None:
    page.goto(f"/app/raeume/{room}")
    page.wait_for_load_state("networkidle")
    page.locator("#rt-title").wait_for(state="attached")
    page.locator("summary", has_text="PI-Regler (Beta)").click()


def test_gain_above_half_warns_and_from_one_recommends_a_longer_integral_time(
    page: Page, room_id: int
) -> None:
    _open_pi_group(page, room_id)
    gain = page.locator("#rt-pi_gain_per_k")
    warning = page.get_by_test_id("pi-gain-warning")

    gain.fill("0,5")
    expect(warning).to_have_count(0)

    gain.fill("0,75")
    expect(warning).to_be_visible()
    expect(warning).to_contain_text("Schwingneigung")
    expect(warning).not_to_contain_text("180 Minuten")

    gain.fill("1,5")
    expect(warning).to_contain_text("mindestens 180 Minuten")


def test_feedforward_fields_are_greyed_out_until_the_switch_is_on(
    page: Page, room_id: int
) -> None:
    _open_pi_group(page, room_id)
    gain = page.locator("#rt-pi_feedforward_gain")
    smoothing = page.locator("#rt-pi_feedforward_smoothing_hours")

    expect(page.locator("#rt-pi_feedforward_enabled")).to_have_attribute(
        "aria-checked", "false"
    )
    expect(gain).to_be_disabled()
    expect(smoothing).to_be_disabled()

    page.locator("#rt-pi_feedforward_enabled").click()
    expect(gain).to_be_enabled()
    expect(smoothing).to_be_enabled()
