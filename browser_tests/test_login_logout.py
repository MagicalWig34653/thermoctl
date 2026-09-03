"""Login and logout, end to end through a real browser.

Both broke in exactly this way once before (see the task description this suite
was commissioned from): an outdated login page locked users out, and logging out
did not actually land on the login page because `hx-boost` followed the redirect
invisibly. Neither failure shows up in an HTTP test that only checks a status code
and a `Location` header — it needs a browser that actually runs `hx-boost`,
executes htmx's redirect handling, and updates the address bar (or doesn't).
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page, expect

from browser_tests.conftest import LiveServer

pytestmark = pytest.mark.browser


def test_login_with_correct_credentials_reaches_the_dashboard(
    page: Page, live_server: LiveServer
) -> None:
    page.goto("/login")
    page.get_by_label("Benutzername").fill(live_server.admin_username)
    page.get_by_label("Passwort").fill(live_server.admin_password)
    page.get_by_role("button", name="Anmelden").click()

    # The navigation bar exists only on `base.html` (logged in), never on the
    # login page's `base_plain.html` — an HTTP test can check the redirect target
    # in isolation, but not that htmx's boosted submit actually arrives there with
    # a working page behind it.
    expect(page.locator(".tc-head")).to_be_visible()
    expect(page).to_have_url(f"{live_server.base_url}/")


def test_login_with_wrong_password_shows_an_error_and_stays_on_the_login_page(
    page: Page, live_server: LiveServer
) -> None:
    page.goto("/login")
    page.get_by_label("Benutzername").fill(live_server.admin_username)
    page.get_by_label("Passwort").fill("ganz-offensichtlich-falsch")
    page.get_by_role("button", name="Anmelden").click()

    expect(page.get_by_role("alert")).to_contain_text("Benutzername oder Passwort falsch")
    # Still on the login page, not silently let through.
    expect(page.get_by_label("Benutzername")).to_be_visible()


def test_logout_leads_back_to_a_working_login_page(admin_page: Page) -> None:
    # The account menu, not a direct `goto("/logout")`: the real defect sat in the
    # button's own `hx-post`/`hx-boost` interaction, which only fires from an
    # actual click on the actual element.
    admin_page.get_by_role("button", name="Browsertest-Verwaltung").click()
    admin_page.get_by_role("button", name="Abmelden").click()

    expect(admin_page.get_by_label("Benutzername")).to_be_visible()
    expect(admin_page.get_by_label("Passwort")).to_be_visible()
    expect(admin_page).to_have_url(re.compile(r"/login$"))
