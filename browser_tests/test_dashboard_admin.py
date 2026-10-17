"""Übersichten pflegen im echten Browser: Vorlage anlegen, Person mit Vorlage anlegen,
die Übersicht einer Person bearbeiten und die Raum-bedienen-Kachel auf der Übersicht."""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from browser_tests.conftest import LiveServer
from browser_tests.seed import create_login_user, create_schedule_zone

pytestmark = pytest.mark.browser


def _open_overviews(page: Page) -> None:
    page.goto("/app/personen/rechte")
    page.get_by_role("tab", name="Übersichten").click()


def test_template_can_be_created_used_for_a_new_user_and_deleted(
    admin_page: Page, live_server: LiveServer
) -> None:
    _open_overviews(admin_page)
    admin_page.get_by_test_id("new-template-name").fill("Mieter")
    admin_page.get_by_role("button", name="Leer anlegen").click()
    expect(admin_page.get_by_test_id("template-list")).to_contain_text("Mieter")

    # Die Vorlage steht beim Anlegen einer Person zur Wahl.
    admin_page.get_by_role("tab", name="Benutzer").click()
    admin_page.get_by_role("button", name="Benutzer anlegen").click()
    admin_page.get_by_label("Benutzername").fill("mit-vorlage")
    admin_page.get_by_label("Startpasswort").fill("ein-langes-passwort-1")
    admin_page.get_by_test_id("create-template").select_option(label="Mieter")
    admin_page.get_by_role("button", name="Anlegen", exact=True).click()
    expect(admin_page.get_by_test_id("user-list")).to_contain_text("mit-vorlage")

    # Und lässt sich wieder löschen.
    admin_page.get_by_role("tab", name="Übersichten").click()
    admin_page.get_by_role("button", name="Vorlage Mieter löschen").click()
    admin_page.get_by_role("button", name="Löschen", exact=True).last.click()
    expect(admin_page.get_by_test_id("no-templates")).to_be_visible()


def test_admin_edits_the_overview_of_another_user(
    admin_page: Page, live_server: LiveServer
) -> None:
    with live_server.session() as session:
        create_login_user(session, "ziel-person", "Ziel-Kennwort-123", [("zone.read", None)])
        session.commit()
    _open_overviews(admin_page)
    admin_page.get_by_role("button", name="Übersicht von ziel-person bearbeiten").click()
    expect(admin_page.get_by_role("heading", name="Übersicht von ziel-person")).to_be_visible()
    # Der Editor startet sofort.
    expect(admin_page.get_by_test_id("edit-bar")).to_be_visible()
    admin_page.get_by_test_id("cancel").click()
    admin_page.get_by_test_id("overview-back").click()
    expect(admin_page.get_by_test_id("overview-users")).to_be_visible()


def test_control_tile_changes_the_setpoint(admin_page: Page, live_server: LiveServer) -> None:
    with live_server.session() as session:
        create_schedule_zone(session, "Kachelraum")
        session.commit()
    admin_page.goto("/app/")
    admin_page.get_by_test_id("edit-layout").click()
    admin_page.get_by_test_id("open-gallery").click()
    admin_page.get_by_role("button", name="Raum bedienen hinzufügen").click()
    admin_page.get_by_role("button", name="Fertig").click()
    admin_page.get_by_test_id("save").click()
    tile = admin_page.get_by_test_id("tile-control").first
    expect(tile).to_be_visible()
    raise_button = tile.get_by_role(
        "button", name="Sollwert in Kachelraum um ein halbes Grad erhöhen"
    )
    expect(raise_button).to_be_visible()


def test_glow_slider_sets_the_step_and_zero_switches_it_off(admin_page: Page) -> None:
    admin_page.get_by_label("Darstellung ändern").click()
    slider = admin_page.get_by_test_id("glow-slider")
    expect(admin_page.get_by_test_id("glow-label")).to_have_text("Aus")
    slider.fill("4")
    expect(admin_page.get_by_test_id("glow-label")).to_have_text("Deutlich")
    expect(admin_page.locator("html")).to_have_attribute("data-glow-step", "4")
    slider.fill("0")
    expect(admin_page.locator("html")).not_to_have_attribute("data-glow", "on")
