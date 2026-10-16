"""Anmelden und Abmelden in der Svelte-Oberfläche, mit einem echten Browser."""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page, expect

from browser_tests.conftest import LiveServer

pytestmark = pytest.mark.browser


def test_login_with_correct_credentials_reaches_the_app(
    page: Page, live_server: LiveServer
) -> None:
    page.goto("/login")
    # Der Altpfad leitet auf die Oberfläche um.
    expect(page).to_have_url(re.compile(r"/app/login"))
    page.get_by_label("Benutzername").fill(live_server.admin_username)
    page.get_by_label("Passwort").fill(live_server.admin_password)
    page.get_by_role("button", name="Anmelden", exact=True).click()
    expect(page.get_by_text("Abmelden").first).to_be_visible()
    expect(page).not_to_have_url(re.compile(r"/login"))


def test_login_with_wrong_password_shows_an_error_and_stays_on_the_login_page(
    page: Page, live_server: LiveServer
) -> None:
    page.goto("/app/login")
    page.get_by_label("Benutzername").fill(live_server.admin_username)
    page.get_by_label("Passwort").fill("ganz-offensichtlich-falsch")
    page.get_by_role("button", name="Anmelden", exact=True).click()
    expect(page.get_by_role("alert")).to_contain_text("Benutzername oder Passwort falsch")
    expect(page.get_by_label("Benutzername")).to_be_visible()


def test_logout_leads_back_to_the_login_page(admin_page: Page) -> None:
    admin_page.get_by_text("Abmelden").first.click()
    expect(admin_page.get_by_label("Benutzername")).to_be_visible()
    expect(admin_page.get_by_label("Passwort")).to_be_visible()
    # Die Oberfläche zeigt die Anmeldung, ohne in der Sitzung angemeldet zu bleiben.
    expect(admin_page.get_by_text("Abmelden")).to_have_count(0)
