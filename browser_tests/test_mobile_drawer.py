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
