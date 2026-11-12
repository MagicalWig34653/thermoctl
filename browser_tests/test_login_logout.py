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


def test_slow_login_explains_the_wait_and_removes_the_note_with_the_answer(
    page: Page, live_server: LiveServer
) -> None:
    """Der Server bremst Fehlversuche absichtlich aus (1, 2, 4 ... bis 5 s); die Seite
    erklärt nach ca. 1 s, warum es dauert, und nimmt die Meldung mit der Antwort zurück."""
    page.goto("/app/login")
    page.get_by_label("Benutzername").fill("wartezeit-pruefung")
    button = page.get_by_role("button", name="Anmelden", exact=True)
    hint = page.get_by_test_id("slow-login-hint")

    def attempt() -> None:
        page.get_by_label("Passwort").fill("ganz-offensichtlich-falsch")
        button.click()

    # Zwei Fehlversuche vorab, damit der dritte sicher länger als 1 s wartet (4 s).
    for _ in range(2):
        attempt()
        expect(page.get_by_role("alert")).to_contain_text(
            "Benutzername oder Passwort falsch", timeout=15_000
        )
    attempt()
    expect(page.get_by_role("button", name="Anmelden …")).to_be_visible()
    expect(hint).to_be_visible(timeout=3_000)
    expect(hint).to_contain_text("Die Prüfung dauert einen Moment")
    page.screenshot(path="/tmp/shots/hint-wartend.png")
    expect(page.get_by_role("alert")).to_contain_text(
        "Benutzername oder Passwort falsch", timeout=15_000
    )
    expect(hint).to_have_count(0)


def test_logout_leads_back_to_the_login_page(admin_page: Page) -> None:
    # Gezielt die Schaltfläche (nicht irgendein Text „Abmelden") und erst klicken, wenn die
    # Oberfläche geladen ist; der Wechsel zur Anmeldung geht über eine Serveranfrage und
    # bekam unter Last (parallele Läufe) die Standardfrist von 5 s manchmal nicht ein.
    admin_page.wait_for_load_state("networkidle")
    admin_page.get_by_role("button", name="Abmelden").click()
    expect(admin_page.get_by_label("Benutzername")).to_be_visible(timeout=15_000)
    expect(admin_page.get_by_label("Passwort")).to_be_visible()
    # Die Oberfläche zeigt die Anmeldung, ohne in der Sitzung angemeldet zu bleiben.
    expect(admin_page.get_by_text("Abmelden")).to_have_count(0)
