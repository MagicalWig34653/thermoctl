"""Touch-Ziele am Handy: sichtbare interaktive Elemente sind mindestens 44 px hoch.

Gemessen wird im echten Browser mit Handy-Viewport (390 x 844, `has_touch`, damit
`@media (pointer: coarse)` greift) auf den Seiten Räume, Wochenpläne, Raum
(Übersteuerung) und Konto. Gemessen wird die Box des Elements; die Mindesthöhe wird
im Code über `min-height` bzw. Padding unter `pointer: coarse` hergestellt
(`frontend/src/styles/touch.css`), nicht über unsichtbare Pseudoelemente.

Ausgenommen sind ausschließlich Links innerhalb von Fließtext (`p`, inline): Dort ist
eine Trefferfläche von Zeilenhöhe typografisch gewollt.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from playwright.sync_api import Browser, BrowserContext, Page, ViewportSize

from browser_tests.live_server import LiveServer
from browser_tests.seed import create_schedule_zone

pytestmark = pytest.mark.browser

MIN_PX = 44
TOLERANCE = 0.5

_MEASURE = """
() => {
  const sel = 'a[href], button, input:not([type=hidden]), select, textarea, ' +
    '[role=switch], [role=checkbox], [role=tab], [role=button], [role=menuitem]';
  const out = [];
  for (const el of document.querySelectorAll(sel)) {
    const style = getComputedStyle(el);
    if (style.visibility === 'hidden' || style.display === 'none') continue;
    if (el.closest('[hidden], [aria-hidden=true], [inert]')) continue;
    let r = el.getBoundingClientRect();
    if (r.width <= 1 || r.height <= 1) continue;
    let h = r.height;
    // Natives Kontrollkästchen / Optionsfeld: Trefferfläche ist die Beschriftung drumherum.
    if (el.tagName === 'INPUT' && (el.type === 'checkbox' || el.type === 'radio')) {
      const label = el.closest('label');
      if (label) h = Math.max(h, label.getBoundingClientRect().height);
    }
    // Schalter / Kontrollkästchen (Bits UI): Trefferfläche ragt über ein ::after hinaus.
    const after = getComputedStyle(el, '::after');
    if (after.position === 'absolute' && after.content !== 'none') {
      h = Math.max(h, parseFloat(after.height) || 0);
    }
    const inProse = el.tagName === 'A' && !!el.closest('p') && style.display === 'inline';
    out.push({
      tag: el.tagName.toLowerCase(),
      role: el.getAttribute('role') || '',
      text: (el.getAttribute('aria-label') || el.textContent || el.getAttribute('name') || '')
        .trim().replace(/\\s+/g, ' ').slice(0, 40),
      w: Math.round(r.width * 10) / 10,
      h: Math.round(h * 10) / 10,
      inProse,
    });
  }
  return out;
}
"""

VIEWPORT: ViewportSize = {"width": 390, "height": 844}


@pytest.fixture
def touch_context(browser: Browser, live_server: LiveServer) -> Iterator[BrowserContext]:
    ctx = browser.new_context(
        base_url=live_server.base_url,
        color_scheme="light",
        viewport=VIEWPORT,
        has_touch=True,
        is_mobile=True,
    )
    yield ctx
    ctx.close()


@pytest.fixture
def touch_page(
    touch_context: BrowserContext, live_server: LiveServer, console_errors: list[str]
) -> Iterator[Page]:
    page = touch_context.new_page()
    page.on("pageerror", lambda exc: console_errors.append(f"[pageerror] {exc}"))
    # Am Handy liegt „Abmelden“ im eingeklappten Menü (unsichtbar); `sign_in` wartet darauf.
    page.goto("/login")
    page.get_by_label("Benutzername").fill(live_server.admin_username)
    page.get_by_label("Passwort").fill(live_server.admin_password)
    page.get_by_role("button", name="Anmelden", exact=True).click()
    page.wait_for_url(lambda url: "/login" not in url)
    yield page
    page.close()


def _too_small(page: Page) -> list[str]:
    page.wait_for_load_state("networkidle")
    # Übergänge (`transition-all`) würden Maße mitten in der Animation liefern.
    page.add_style_tag(content="*, *::before, *::after { transition: none !important; }")
    found = page.evaluate(_MEASURE)
    return [
        f"{e['tag']}[{e['role']}] '{e['text']}' {e['w']}x{e['h']}"
        for e in found
        if not e["inProse"] and e["h"] < MIN_PX - TOLERANCE
    ]


@pytest.fixture(scope="module")
def room_id(live_server: LiveServer) -> int:
    with live_server.session() as session:
        zone = create_schedule_zone(session, "Touchraum")
        session.commit()
        return int(zone.id)


@pytest.mark.parametrize("path", ["uebersicht/raeume", "zeit/wochenplaene", "konto"])
def test_touch_targets_on_list_pages(touch_page: Page, room_id: int, path: str) -> None:
    touch_page.goto(f"/app/{path}")
    assert _too_small(touch_page) == []


def test_touch_targets_on_room_page(touch_page: Page, room_id: int) -> None:
    touch_page.goto(f"/app/raeume/{room_id}")
    assert _too_small(touch_page) == []
