"""Deaktivierte Primärschaltflächen bleiben lesbar und als Knöpfe erkennbar.

Gemessen wird im echten Browser auf der Raumseite (Speichern, Übersteuerung, Notbetrieb
usw.), hell und dunkel, Desktop und Handy. Der Kontrast ist der tatsächlich sichtbare:
Beschriftung und Knopffläche werden mit der Deckkraft des Elements gegen den Seiten-
hintergrund gemischt (nicht nur die berechnete Farbe gelesen). Verlangt wird
Text/Fläche >= 3:1, ein sichtbarer Umriss (Knopffläche oder Rand gegen die Seite
>= 1,5:1) und ein klarer Unterschied zum aktiven Zustand.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from playwright.sync_api import Browser, Page

from browser_tests.live_server import LiveServer
from browser_tests.seed import create_schedule_zone

pytestmark = pytest.mark.browser

MIN_TEXT_CONTRAST = 3.0
MIN_OUTLINE_CONTRAST = 1.5

_MEASURE = """
() => {
  const cv = document.createElement('canvas'); cv.width = cv.height = 1;
  const cx = cv.getContext('2d', { willReadFrequently: true });
  const rgba = (css) => {           // beliebige CSS-Farbe (auch oklch) -> [r,g,b,a]
    cx.clearRect(0, 0, 1, 1); cx.fillStyle = '#000'; cx.fillStyle = css;
    cx.fillRect(0, 0, 1, 1); const d = cx.getImageData(0, 0, 1, 1).data;
    return [d[0], d[1], d[2], d[3] / 255];
  };
  const over = (top, a, bottom) => top.map((c, i) => c * a + bottom[i] * (1 - a));
  const lum = (c) => { const f = c.map((v) => { v /= 255;
    return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); });
    return 0.2126 * f[0] + 0.7152 * f[1] + 0.0722 * f[2]; };
  const ratio = (a, b) => { const x = lum(a), y = lum(b);
    return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
  const pageBg = (el) => {          // erster deckender Vorfahre
    for (let n = el.parentElement; n; n = n.parentElement) {
      const c = rgba(getComputedStyle(n).backgroundColor);
      if (c[3] >= 0.99) return c.slice(0, 3);
    }
    return [255, 255, 255];
  };
  const out = [];
  for (const el of document.querySelectorAll("button[data-slot=button]")) {
    const s = getComputedStyle(el);
    if (s.visibility === 'hidden' || s.display === 'none') continue;
    if (el.closest('[hidden], [aria-hidden=true], [inert]')) continue;
    if (el.getBoundingClientRect().width <= 1) continue;
    if (!el.classList.contains('bg-primary')) continue;
    const page = pageBg(el);
    const alpha = parseFloat(s.opacity);
    const bgc = rgba(s.backgroundColor);
    const fill = over(bgc.slice(0, 3), bgc[3], page);
    const txt = rgba(s.color);
    const bc = rgba(s.borderTopColor);
    const bw = parseFloat(s.borderTopWidth);
    const seenFill = over(fill, alpha, page);
    const seenText = over(over(txt.slice(0, 3), txt[3], fill), alpha, page);
    const seenBorder = bw > 0 ? over(over(bc.slice(0, 3), bc[3], fill), alpha, page) : null;
    out.push({
      text: (el.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 40),
      disabled: el.disabled,
      opacity: alpha,
      textContrast: Math.round(ratio(seenText, seenFill) * 100) / 100,
      fillVsPage: Math.round(ratio(seenFill, page) * 100) / 100,
      borderVsPage: seenBorder ? Math.round(ratio(seenBorder, page) * 100) / 100 : 0,
      fillKey: seenFill.map(Math.round).join(','),
      textKey: seenText.map(Math.round).join(','),
    });
  }
  return out;
}
"""


@pytest.fixture(scope="module")
def room_id(live_server: LiveServer) -> int:
    with live_server.session() as session:
        zone = create_schedule_zone(session, "Kontrastraum")
        session.commit()
        return int(zone.id)


@contextmanager
def _open_room(
    browser: Browser, live_server: LiveServer, scheme: str, width: int, height: int, room: int
) -> Iterator[Page]:
    ctx = browser.new_context(
        base_url=live_server.base_url,
        color_scheme=scheme,  # type: ignore[arg-type]
        viewport={"width": width, "height": height},
        has_touch=width < 600,
        is_mobile=width < 600,
    )
    page = ctx.new_page()
    page.goto("/login")
    page.get_by_label("Benutzername").fill(live_server.admin_username)
    page.get_by_label("Passwort").fill(live_server.admin_password)
    page.get_by_role("button", name="Anmelden", exact=True).click()
    page.wait_for_url(lambda url: "/login" not in url)
    page.goto(f"/app/raeume/{room}")
    page.wait_for_load_state("networkidle")
    page.add_style_tag(content="*, *::before, *::after { transition: none !important; }")
    # Eingeklappte Abschnitte aufklappen, damit auch ihre Speichern-Knöpfe gemessen werden.
    page.evaluate("document.querySelectorAll('details').forEach((d) => { d.open = true; })")
    try:
        yield page
    finally:
        ctx.close()


@pytest.mark.parametrize(
    ("scheme", "width", "height"),
    [("light", 1280, 900), ("dark", 1280, 900), ("light", 390, 844), ("dark", 390, 844)],
)
def test_disabled_primary_buttons_are_legible(
    browser: Browser,
    live_server: LiveServer,
    room_id: int,
    scheme: str,
    width: int,
    height: int,
) -> None:
    with _open_room(browser, live_server, scheme, width, height, room_id) as page:
        shots = os.environ.get("THERMOCTL_SHOTS_DIR")
        if shots:  # Sichtprüfung von Hand: Bildschirmfoto der ganzen Seite ablegen
            page.screenshot(path=f"{shots}/raum-{scheme}-{width}.png", full_page=True)
        found = page.evaluate(_MEASURE)
        # Am Handy liegt die Raumtechnik hinter dem Reiter „Technik“.
        technik = page.get_by_role("tab", name="Technik")
        if technik.count() and technik.is_visible():
            technik.click()
            page.evaluate("document.querySelectorAll('details').forEach((d) => { d.open = true; })")
            page.wait_for_timeout(300)
            if shots:
                page.screenshot(path=f"{shots}/raum-{scheme}-{width}-technik.png", full_page=True)
            found += page.evaluate(_MEASURE)
        if shots:  # Ausschnitt um die Speichern-Knöpfe der Raumtechnik
            knopf = page.get_by_role("button", name="Raumtechnik speichern")
            knopf.scroll_into_view_if_needed()
            knopf.locator("xpath=..").screenshot(path=f"{shots}/knoepfe-{scheme}-{width}.png")
        disabled = [b for b in found if b["disabled"]]
        active = [b for b in found if not b["disabled"]]
        print(f"\n[{scheme} {width}] deaktivierte Primärknöpfe:")
        for b in disabled:
            print(f"   {b}")
        for b in active:
            print(f"   (aktiv) {b}")
        assert disabled, "Die Raumseite zeigt keinen deaktivierten Primärknopf -- Test misst nichts"
        for b in disabled:
            assert b["textContrast"] >= MIN_TEXT_CONTRAST, b
            assert max(b["fillVsPage"], b["borderVsPage"]) >= MIN_OUTLINE_CONTRAST, b
            for a in active:
                assert (b["fillKey"], b["textKey"]) != (a["fillKey"], a["textKey"]), (b, a)
