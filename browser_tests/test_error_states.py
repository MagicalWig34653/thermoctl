"""Fehler- und Leerzustände im echten Browser: verständliche Texte statt technischer Sätze,
kein doppeltes „nicht gefunden“, ein gestalteter Verbindungsfehler, ehrliche Leermeldungen."""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from browser_tests.conftest import LiveServer, sign_in
from browser_tests.seed import create_login_user

pytestmark = pytest.mark.browser

PASSWORD = "Klein-Kennwort-123"  # noqa: S105 -- ephemeral local database only


def test_page_without_permission_explains_what_to_do(
    page: Page, live_server: LiveServer
) -> None:
    with live_server.session() as session:
        create_login_user(session, "nur-lesen", PASSWORD, [("zone.read", None)])
        session.commit()
    sign_in(page, "/login", "nur-lesen", PASSWORD)
    page.goto("/app/personen/rechte")
    main = page.locator("main")
    expect(main.get_by_role("heading", name="Nicht verfügbar")).to_be_visible()
    expect(main).to_contain_text("nicht freigegeben")
    expect(main).to_contain_text("Person, die die Anlage verwaltet")
    expect(main).not_to_contain_text("entscheidet der Server")
    expect(main.get_by_role("link", name="Zur Übersicht")).to_be_visible()


def test_unknown_room_says_not_found_only_once(admin_page: Page) -> None:
    admin_page.goto("/app/raeume/99999")
    expect(admin_page.get_by_role("heading", name="Raum nicht gefunden")).to_be_visible()
    expect(admin_page.get_by_test_id("room-not-found")).to_contain_text("nicht (mehr)")
    assert admin_page.get_by_text("Raum nicht gefunden").count() == 1


def test_lost_server_on_start_shows_a_styled_card_with_retry(page: Page) -> None:
    page.route("**/ui-api/bootstrap", lambda route: route.abort())
    page.goto("/app/")
    card = page.get_by_test_id("session-error")
    expect(card.get_by_role("heading", name="Keine Verbindung")).to_be_visible()
    expect(card).to_contain_text("Es wurde nichts geändert")
    # Gestaltet: die Karte hat einen Rand, der Knopf ist erreichbar.
    inner = card.locator("div").first
    assert inner.evaluate("el => getComputedStyle(el).borderTopWidth") != "0px"
    page.unroute("**/ui-api/bootstrap")
    card.get_by_role("button", name="Erneut versuchen").click()
    expect(page.get_by_test_id("session-error")).to_have_count(0)


def test_empty_event_lists_only_mention_filters_when_one_is_set(admin_page: Page) -> None:
    admin_page.goto("/app/auswertung/ereignisse")
    empty = admin_page.get_by_test_id("changes-empty")
    expect(empty).to_be_visible()
    expect(empty).not_to_contain_text("für diese Filter")
    admin_page.get_by_label("Von").fill("2001-01-01")
    admin_page.get_by_label("Bis").fill("2001-01-02")
    expect(empty).to_contain_text("Keine Änderungen für diese Filter")
    admin_page.get_by_role("button", name="Audit-Protokoll").click()
    expect(admin_page.get_by_test_id("audit-empty")).to_contain_text("für diese Filter")
    admin_page.get_by_role("button", name="Schaltprotokoll", exact=True).click()
    # Eigene Filter je Ansicht: hier ist noch keiner gesetzt.
    commands = admin_page.get_by_test_id("commands-empty")
    expect(commands).not_to_contain_text("für diese Filter")
    admin_page.get_by_label("Von").fill("2001-01-01")
    expect(commands).to_contain_text("Keine Befehle für diese Filter")
