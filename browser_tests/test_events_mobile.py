"""Schaltprotokoll am Handy (UX-Befund 2026-11-22): Karten statt Tabelle, Erläuterung eingeklappt.

Gemessen im echten Browser mit Handy-Viewport (390 x 844, `has_touch`) und Desktop (1280 x 900),
mit Zeilen aller drei Arten (Befehl, Entscheidung, Absenkung) aus `seed.create_command_log`:
- die Erläuterung ist am Handy eingeklappt und lässt sich aufklappen, am Desktop steht
  sie offen,
- die Seite scrollt nicht seitwärts und kein Bereich scrollt in sich seitwärts,
- Ergebnis und Begründung jeder Karte liegen im Sichtbereich, die lange Begründung ist
  nicht gekürzt,
- die Liste hat Listenrollen mit Namen, der Aufklapper und die Seitenknöpfe sind
  mindestens 44 px hoch,
- am Desktop bleibt die Tabelle mit allen Spalten.
Mit `THERMOCTL_SHOTS_DIR` werden Bildschirmfotos der ganzen Seite abgelegt.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from playwright.sync_api import Browser, Page, expect

from browser_tests.conftest import _record_console_error
from browser_tests.live_server import LiveServer
from browser_tests.seed import LONG_REASON, create_command_log

pytestmark = pytest.mark.browser

ZONE = "Protokollraum"
KINDS = {"befehl", "entscheidung", "absenkung"}
COLUMNS = ["Art", "Zeit", "Raum", "Gerät", "Befehl", "Quelle", "Ergebnis"]


@pytest.fixture(scope="module", autouse=True)
def command_log(live_server: LiveServer) -> None:
    with live_server.session() as session:
        create_command_log(session, ZONE, "Heizkoerper-Protokoll")
        session.commit()


@contextmanager
def _open_log(
    browser: Browser, live_server: LiveServer, width: int, console_errors: list[str]
) -> Iterator[Page]:
    phone = width < 600
    ctx = browser.new_context(
        base_url=live_server.base_url,
        color_scheme="light",
        viewport={"width": width, "height": 844 if phone else 900},
        has_touch=phone,
        is_mobile=phone,
    )
    page = ctx.new_page()
    page.on("console", lambda message: _record_console_error(console_errors, message))
    page.on("pageerror", lambda exc: console_errors.append(f"[pageerror] {exc}"))
    page.goto("/login")
    page.get_by_label("Benutzername").fill(live_server.admin_username)
    page.get_by_label("Passwort").fill(live_server.admin_password)
    page.get_by_role("button", name="Anmelden", exact=True).click()
    page.wait_for_url(lambda url: "/login" not in url)
    page.goto("/app/auswertung/ereignisse")
    page.get_by_role("button", name="Schaltprotokoll").click()
    page.wait_for_load_state("networkidle")
    # Karten (Handy) oder Tabelle (Desktop): beides ist ein Beleg, dass die Liste geladen ist.
    page.locator("[data-testid=commands-list], [data-testid=commands-table]").first.wait_for()
    try:
        yield page
    finally:
        ctx.close()
        assert not console_errors, "Browserkonsole meldete Fehler:\n" + "\n".join(console_errors)


def _shot(page: Page, name: str) -> None:
    shots = os.environ.get("THERMOCTL_SHOTS_DIR")
    if shots:
        os.makedirs(shots, exist_ok=True)
        page.screenshot(path=f"{shots}/{name}.png", full_page=True)


@pytest.fixture
def phone(
    browser: Browser, live_server: LiveServer, console_errors: list[str]
) -> Iterator[Page]:
    with _open_log(browser, live_server, 390, console_errors) as page:
        yield page


def test_explainer_is_collapsed_on_phone_and_expandable(phone: Page) -> None:
    explainer = phone.get_by_test_id("commands-explainer")
    assert explainer.evaluate("el => el.tagName") == "DETAILS"
    assert explainer.evaluate("el => el.open") is False
    summary = explainer.locator("summary")
    expect(summary).to_be_visible()
    text = explainer.locator("p")
    expect(text).to_be_hidden()
    assert summary.bounding_box()["height"] >= 44 - 0.5  # type: ignore[index]
    summary.click()
    assert explainer.evaluate("el => el.open") is True
    expect(text).to_be_visible()
    assert "Notbetriebsentscheidungen" in text.inner_text()
    _shot(phone, "ereignisse-handy-aufgeklappt")


def test_explainer_is_open_on_desktop(
    browser: Browser, live_server: LiveServer, console_errors: list[str]
) -> None:
    with _open_log(browser, live_server, 1280, console_errors) as page:
        explainer = page.get_by_test_id("commands-explainer")
        assert explainer.evaluate("el => el.tagName") == "P"
        expect(explainer).to_be_visible()
        assert "Notbetriebsentscheidungen" in explainer.inner_text()


def test_phone_page_does_not_scroll_sideways(phone: Page) -> None:
    _shot(phone, "ereignisse-handy")
    sizes = phone.evaluate(
        "({ doc: document.documentElement.scrollWidth, body: document.body.scrollWidth,"
        " inner: innerWidth })"
    )
    assert sizes["doc"] <= sizes["inner"], sizes
    assert sizes["body"] <= sizes["inner"], sizes


def test_phone_has_no_inner_horizontal_scroll_area(phone: Page) -> None:
    scrolling = phone.evaluate(
        """
        () => [...document.querySelectorAll('main *, #tc-main *')].filter((el) => {
          const x = getComputedStyle(el).overflowX;
          return (x === 'auto' || x === 'scroll') && el.scrollWidth > el.clientWidth + 1;
        }).map((el) => el.tagName + '.' + el.className)
        """
    )
    assert scrolling == []
    # Auch eine Tabelle darf es am Handy nicht mehr geben.
    expect(phone.get_by_test_id("commands-table")).to_have_count(0)


def test_every_kind_is_a_card_with_result_and_reason_in_view(phone: Page) -> None:
    cards = phone.get_by_test_id("command-card")
    assert cards.count() >= 7  # 4 Befehle, 1 Entscheidung, 2 Absenkungen
    kinds = set(cards.evaluate_all("els => els.map((el) => el.dataset.kind)"))
    assert kinds == KINDS
    width = phone.evaluate("innerWidth")
    for index in range(cards.count()):
        card = cards.nth(index)
        for field in ("ergebnis", "begruendung"):
            cell = card.locator(f'[data-field="{field}"]')
            assert cell.count() == 1, (index, field)
            cell.scroll_into_view_if_needed()
            box = cell.bounding_box()
            assert box is not None, (index, field)
            assert box["x"] >= 0 and box["x"] + box["width"] <= width + 0.5, (index, field, box)
            assert cell.inner_text().strip() not in ("", "—"), (index, field)
            fits = cell.evaluate("el => el.scrollWidth <= el.clientWidth + 1")
            assert fits, (index, field)


def test_long_reason_is_not_truncated(phone: Page) -> None:
    cell = phone.get_by_test_id("command-card").locator('[data-field="begruendung"]').filter(
        has_text="Begründungsende"
    )
    expect(cell).to_have_count(1)
    assert cell.inner_text().strip() == LONG_REASON
    # Kein Abschneiden per Höhe oder Auslassungspunkte: der Inhalt füllt die Zelle.
    assert cell.evaluate(
        "el => el.scrollHeight <= el.clientHeight + 1"
        " && getComputedStyle(el).textOverflow !== 'ellipsis'"
    )


def test_failed_command_shows_error_text_in_card(phone: Page) -> None:
    card = phone.get_by_test_id("command-card").filter(has_text="Gescheitert")
    expect(card).to_have_count(1)
    error = card.locator('[data-field="fehler"]')
    expect(error).to_contain_text("nicht innerhalb von 10 Sekunden")
    assert card.locator('[data-field="ergebnis"]').inner_text().strip() == "Gescheitert"


def test_cards_are_a_labelled_list_with_terms(phone: Page) -> None:
    listing = phone.get_by_role("list", name="Schaltprotokoll, Seite 1")
    expect(listing).to_be_visible()
    items = listing.get_by_role("listitem")
    assert items.count() >= 7
    first = items.first
    labels = first.locator("dt").all_inner_texts()
    assert labels[:5] == ["Raum", "Gerät", "Befehl", "Quelle", "Ergebnis"]
    assert first.locator("time").count() == 1
    assert first.locator("time").get_attribute("datetime")


def test_phone_touch_targets_on_the_page(phone: Page) -> None:
    from browser_tests.test_touch_targets import _too_small

    assert _too_small(phone) == []
    for name in ("Neuere", "Ältere"):
        box = phone.get_by_role("button", name=name).bounding_box()
        assert box is not None and box["height"] >= 44 - 0.5, (name, box)


def test_desktop_keeps_the_table_with_all_columns(
    browser: Browser, live_server: LiveServer, console_errors: list[str]
) -> None:
    with _open_log(browser, live_server, 1280, console_errors) as page:
        _shot(page, "ereignisse-desktop")
        table = page.get_by_test_id("commands-table")
        expect(table).to_be_visible()
        assert table.get_by_role("columnheader").all_inner_texts() == COLUMNS
        assert table.locator("tbody tr").count() >= 7
        expect(page.get_by_test_id("commands-list")).to_have_count(0)
        expect(page.get_by_test_id("command-card")).to_have_count(0)
