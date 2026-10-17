"""Räume anlegen über das Menü und Nachladen gescheiterter Seiten-Chunks, im echten Browser."""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page, expect

from browser_tests.conftest import LiveServer
from browser_tests.seed import create_schedule_zone

pytestmark = pytest.mark.browser


def _open_overview_group(page: Page) -> None:
    group = page.get_by_role("button", name="Übersicht", exact=True)
    if group.get_attribute("aria-expanded") != "true":
        group.click()


def test_rooms_page_stays_reachable_from_the_menu_once_a_room_exists(
    admin_page: Page, live_server: LiveServer
) -> None:
    with live_server.session() as session:
        create_schedule_zone(session, "Vorhanden")
        session.commit()
    admin_page.goto("/app/")
    # „Übersicht“ ist eine Gruppe mit Startseite und Räumen; der Link „Erste Zone anlegen“ gibt es
    # nur bei leerer Anlage, das Menü muss den Weg also immer bieten.
    _open_overview_group(admin_page)
    admin_page.get_by_role("link", name="Räume", exact=True).click()
    expect(admin_page).to_have_url(re.compile(r"/app/uebersicht/raeume$"))
    admin_page.get_by_test_id("room-create").click()
    admin_page.get_by_label("Technischer Name").fill("neuer-raum")
    admin_page.get_by_label("Anzeigename").fill("Neuer Raum")
    admin_page.get_by_role("dialog").get_by_role("button", name="Raum anlegen").click()
    expect(admin_page.get_by_text("Neuer Raum").first).to_be_visible()


def test_a_failed_page_chunk_is_retried_by_itself(admin_page: Page) -> None:
    failed: list[str] = []

    def flaky(route):  # type: ignore[no-untyped-def]
        if not failed:
            failed.append(route.request.url)
            route.abort()
        else:
            route.continue_()

    admin_page.route(re.compile(r"/static/app/assets/RoomsPage-.*\.js"), flaky)
    admin_page.goto("/app/")
    _open_overview_group(admin_page)
    admin_page.get_by_role("link", name="Räume", exact=True).click()
    expect(admin_page.get_by_test_id("room-create")).to_be_visible()
    assert failed, "der Chunk wurde nie angefordert"
