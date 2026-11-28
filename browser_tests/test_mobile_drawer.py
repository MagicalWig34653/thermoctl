"""Mobile Schublade: Fokusfalle, Fokus beim Öffnen/Schließen, Esc (echter Browser, Touch)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from playwright.sync_api import Browser, Page

from browser_tests.conftest import LiveServer

pytestmark = pytest.mark.browser

# Wo steht der Fokus? "drawer", "overlay", "trigger" oder "content" (alles andere ist ein Fehler).
_WHERE = """() => {
  const a = document.activeElement;
  if (!a) return 'none';
  if (a.closest('#app-sidebar')) return 'drawer';
  if (a.closest('header')) return 'trigger';
  if (a.getAttribute('aria-label') === 'Navigation schließen') return 'overlay';
  if (a.closest('#content')) return 'content';
  return 'other:' + a.tagName;
}"""

_CLOSED = "document.querySelector('#app-sidebar').dataset.open === 'false'"
_OPEN = "document.querySelector('#app-sidebar').dataset.open === 'true'"


@pytest.fixture
def phone(browser: Browser, live_server: LiveServer) -> Iterator[Page]:
    ctx = browser.new_context(
        base_url=live_server.base_url,
        viewport={"width": 390, "height": 844},
        has_touch=True,
        is_mobile=True,
        color_scheme="light",
    )
    page = ctx.new_page()
    page.goto("/login")
    page.get_by_label("Benutzername").fill(live_server.admin_username)
    page.get_by_label("Passwort").fill(live_server.admin_password)
    page.get_by_role("button", name="Anmelden", exact=True).click()
    page.get_by_role("button", name="Navigation öffnen").wait_for()
    yield page
    ctx.close()


def _open_drawer(page: Page) -> None:
    page.get_by_role("button", name="Navigation öffnen").tap()
    page.wait_for_function(_OPEN)


def test_open_drawer_traps_focus_and_returns_it_to_the_trigger(phone: Page) -> None:
    page = phone
    _open_drawer(page)
    assert page.evaluate(_WHERE) == "drawer"
    assert page.evaluate("document.querySelector('#content').closest('[inert]') !== null")
    seen: set[str] = set()
    for key in ("Tab", "Shift+Tab"):
        for _ in range(40):
            page.keyboard.press(key)
            seen.add(page.evaluate(_WHERE))
    # "other:BODY" = Fokus verlässt die Seite Richtung Browserleiste, kein Seiteninhalt.
    assert seen <= {"drawer", "overlay", "other:BODY"}, seen

    page.keyboard.press("Escape")
    page.wait_for_function(_CLOSED)
    assert page.evaluate(_WHERE) == "trigger"
    assert page.evaluate("document.querySelector('#content').closest('[inert]') === null")


def test_closing_via_overlay_returns_focus_to_the_trigger(phone: Page) -> None:
    _open_drawer(phone)
    phone.mouse.click(350, 400)  # rechts neben der Schublade: der Überzug
    phone.wait_for_function(_CLOSED)
    assert phone.evaluate(_WHERE) == "trigger"


# Alles in der Schublade, was Tab erreichen soll: sichtbar und nicht aus der Reihenfolge genommen.
_TAB_STOPS = """() => [...document.querySelectorAll('#app-sidebar :is(a[href], button, input)')]
  .filter((e) => e.checkVisibility() && e.tabIndex >= 0)
  .map((e) => e.getAttribute('aria-label') || e.textContent.trim())"""

_FOCUSED = """() => {
  const a = document.activeElement;
  return a.getAttribute('aria-label') || a.textContent.trim();
}"""

_FOCUS_IS_CONTENT = "document.activeElement === document.querySelector('#content')"


def _open_drawer_by_keyboard(page: Page) -> None:
    page.get_by_role("button", name="Navigation öffnen").focus()
    page.keyboard.press("Enter")
    page.wait_for_function(_OPEN)
    page.wait_for_function("document.activeElement.closest('#app-sidebar') !== null")


def test_keyboard_order_runs_through_the_drawer_then_the_overlay_in_both_directions(
    phone: Page,
) -> None:
    page = phone
    _open_drawer_by_keyboard(page)
    stops = page.evaluate(_TAB_STOPS)
    assert stops[:2] == ["thermoctl", "Einstellung suchen"], stops
    assert stops[-1] == "Abmelden", stops
    expected = [*stops, "Navigation schließen"]

    forward = [page.evaluate(_FOCUSED)]
    for _ in expected[1:]:
        page.keyboard.press("Tab")
        forward.append(page.evaluate(_FOCUSED))
    assert forward == expected

    backward = [forward[-1]]
    for _ in expected[1:]:
        page.keyboard.press("Shift+Tab")
        backward.append(page.evaluate(_FOCUSED))
    assert backward == expected[::-1]


def test_closing_via_the_overlay_by_keyboard_returns_focus_to_the_trigger(phone: Page) -> None:
    page = phone
    _open_drawer_by_keyboard(page)
    # Der Überzug heißt wie der (jetzt inerte) Auslöser; er steht außerhalb der Kopfzeile.
    page.locator("button[aria-label='Navigation schließen']:not([aria-controls])").focus()
    page.keyboard.press("Enter")
    page.wait_for_function(_CLOSED)
    page.wait_for_function("document.activeElement.closest('header') !== null")


def test_following_a_drawer_link_moves_focus_to_the_new_content(phone: Page) -> None:
    page = phone
    _open_drawer_by_keyboard(page)
    page.locator("#app-sidebar").get_by_role("link", name="Konto & Sicherheit").focus()
    page.keyboard.press("Enter")
    page.wait_for_url("**/app/konto")
    page.wait_for_function(_CLOSED)
    page.wait_for_timeout(100)  # der nachgelagerte Fokuswechsel (tick) ist dann durch
    assert page.evaluate(_WHERE) == "content"
    assert page.evaluate(_FOCUS_IS_CONTENT)


def test_picking_a_search_result_moves_focus_to_the_new_content(phone: Page) -> None:
    page = phone
    _open_drawer_by_keyboard(page)
    search = page.get_by_label("Einstellung suchen")
    search.fill("Abwesenheit")
    page.get_by_text("Treffer").wait_for()
    search.press("Enter")
    page.wait_for_function(_CLOSED)
    page.wait_for_timeout(100)
    assert page.evaluate(_FOCUS_IS_CONTENT), page.evaluate(_WHERE)
