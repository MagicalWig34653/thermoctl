"""Zeitraumwechsel und Vollbild an der echten Oberfläche."""

import pytest
from playwright.sync_api import Page, expect

from browser_tests import seed
from browser_tests.conftest import LiveServer

pytestmark = pytest.mark.browser


def test_history_period_and_fullscreen_controls(admin_page: Page, live_server: LiveServer) -> None:
    with live_server.session() as session:
        zone = seed.create_schedule_zone(session, "verlauf-browser")
        session.commit()
        zone_id = zone.id
    page = admin_page
    page.goto(f"/zones/{zone_id}")
    chart = page.locator("#zone-history")
    expect(chart).to_be_visible()
    chart.get_by_role("button", name="3 Tage").click()
    expect(chart.get_by_role("button", name="3 Tage")).to_have_class("btn btn-primary")
    page.locator("#zone-history.htmx-settling").wait_for(state="detached")
    chart.get_by_role("button", name="Vollbild").click()
    expect(chart).to_have_class("card tc-history tc-history-expanded")
    chart.get_by_role("button", name="7 Tage").click()
    page.locator("#zone-history.htmx-settling").wait_for(state="detached")
    expect(chart).to_have_class("card tc-history tc-history-expanded")
    expect(chart.get_by_role("button", name="7 Tage")).to_have_class("btn btn-primary")
    chart.get_by_role("button", name="Schließen").click()
    expect(chart).to_have_class("card tc-history")
    chart.get_by_role("button", name="Vollbild").click()
    page.keyboard.press("Escape")
    expect(chart).to_have_class("card tc-history")
