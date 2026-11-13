"""Wochenplan am Handy: Der Link „Zeit per Formular anlegen“ steht direkt unter dem Plan."""

from __future__ import annotations

import os
import re

import pytest
from playwright.sync_api import Page, expect

from browser_tests.conftest import LiveServer
from browser_tests.seed import create_schedule_zone

pytestmark = pytest.mark.browser

PHONE = {"width": 390, "height": 844}
SHOTS = os.environ.get("THERMOCTL_SHOT_DIR")


def _shot(page: Page, name: str) -> None:
    if SHOTS:
        os.makedirs(SHOTS, exist_ok=True)
        page.screenshot(path=os.path.join(SHOTS, name), full_page=False)


def _open_plan(page: Page, live_server: LiveServer, name: str) -> None:
    with live_server.session() as session:
        create_schedule_zone(session, name)
        session.commit()
    page.goto("/app/zeit/wochenplaene")
    expect(page.get_by_test_id("plan-card")).to_be_visible()


def test_form_link_is_visible_on_a_phone_without_scrolling_far_and_focuses_the_form(
    admin_page: Page, live_server: LiveServer
) -> None:
    admin_page.set_viewport_size(PHONE)
    _open_plan(admin_page, live_server, "Handy-Raum")
    link = admin_page.get_by_test_id("new-time-jump")
    expect(link).to_be_visible()
    # Sichtbar innerhalb von höchstens anderthalb Bildschirmhöhen, nicht erst ~1500 px tiefer.
    top = link.evaluate("el => el.getBoundingClientRect().top + window.scrollY")
    assert top < 1.5 * PHONE["height"], f"Link liegt bei {top}px"
    _shot(admin_page, "handy-plan.png")
    link.scroll_into_view_if_needed()
    _shot(admin_page, "handy-link.png")

    link.click()
    heading = admin_page.get_by_role("heading", name="Neue Zeit anlegen")
    expect(heading).to_be_focused()
    expect(heading).to_be_in_viewport()
    expect(admin_page.get_by_test_id("new-point")).to_be_in_viewport()
    _shot(admin_page, "handy-formular.png")


def test_form_link_on_desktop(admin_page: Page, live_server: LiveServer) -> None:
    admin_page.set_viewport_size({"width": 1280, "height": 900})
    _open_plan(admin_page, live_server, "Desktop-Raum")
    expect(admin_page.get_by_test_id("new-time-jump")).to_be_visible()
    _shot(admin_page, "desktop-plan.png")
    admin_page.get_by_test_id("new-time-jump").click()
    expect(admin_page.get_by_role("heading", name="Neue Zeit anlegen")).to_be_focused()
    _shot(admin_page, "desktop-formular.png")
    assert re.search(r"/app/", admin_page.url)
