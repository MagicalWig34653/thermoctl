"""Temperaturverlauf der Wohnungssicht (`/temperature-history`) im echten Browser.

Der Live-Server ist über alle Browsertests hinweg geteilt. Diese Tests prüfen deshalb nur Kacheln
der Räume, die sie selbst angelegt haben -- und das ergibt sich hier ohnehin aus der Sache: ein
Mieter sieht nur, was seine Rechte freigeben, andere Tests legen ihm nichts dazu.
"""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from playwright.sync_api import Browser, BrowserContext, Page, expect

from browser_tests import seed
from browser_tests.conftest import LiveServer
from browser_tests.history_tiles import scroll_until_loaded
from tests.helpers import create_shadow_decision

pytestmark = pytest.mark.browser

_PASSWORD = "Mieter-Verlauf-Kennwort-9"  # noqa: S105 -- local, ephemeral, throwaway DB


def _seed_room(live_server: LiveServer, name: str, display: str, ist: str) -> int:
    """Ein Raum mit genau bekannten Zeilen (je eine Zeile je Minute, in Minuten vor jetzt).

    - von -300 bis -200: Ist `ist`, Zielwert 20,0 ohne Absenkung, keine Heizanforderung
    - von -200 bis -100: keine Zeilen (Lücke)
    - von -100 bis -20: Ist `ist`, tatsächlicher Zielwert 18,5, ohne Absenkung 20,5, Sonnenabsenkung
      2,0 K, Heizanforderung
    """
    now = datetime.now(UTC).replace(tzinfo=None)
    with live_server.session() as session:
        zone = seed.create_schedule_zone(session, name)
        zone.display_name = display
        for minute in list(range(-300, -200)) + list(range(-100, -20)):
            late = minute >= -100
            row = create_shadow_decision(session, zone)
            row.decided_at = now + timedelta(minutes=minute)
            row.temperature_c = Decimal(ist)
            if late:
                row.setpoint_c = Decimal("18.5")
                row.scheduled_setpoint_c = Decimal("20.5")
                row.solar_setback_k = Decimal("2.0")
                row.would_heat = True
            else:
                row.setpoint_c = Decimal("20.0")
                row.scheduled_setpoint_c = Decimal("20.0")
                row.would_heat = False
        session.commit()
        return zone.id


def _tenant(live_server: LiveServer, username: str, zone_ids: list[int]) -> str:
    with live_server.session() as session:
        seed.create_login_tenant_user(
            session, username, _PASSWORD, [("zone.read", zone_id) for zone_id in zone_ids]
        )
        session.commit()
    return username


@contextmanager
def _opened_as(
    browser: Browser, live_server: LiveServer, username: str, width: int, height: int,
    **options: Any,
) -> Iterator[Page]:
    context: BrowserContext = browser.new_context(
        base_url=live_server.base_url,
        viewport={"width": width, "height": height},
        locale="de-DE",
        **{"color_scheme": "light", **options},
    )
    errors: list[str] = []
    page = context.new_page()
    page.on("pageerror", lambda exc: errors.append(f"[pageerror] {exc}"))
    page.goto("/login")
    page.get_by_label("Benutzername").fill(username)
    page.get_by_label("Passwort").fill(_PASSWORD)
    page.get_by_role("button", name="Anmelden").click()
    page.locator(".tc-theader").wait_for()
    try:
        yield page
    finally:
        context.close()
    assert not errors, "\n".join(errors)


def _watch_requests(page: Page) -> list[tuple[str, int]]:
    seen: list[tuple[str, int]] = []
    page.on(
        "response",
        lambda r: seen.append((r.url, r.status)) if "/temperature-history/" in r.url else None,
    )
    return seen


def _loaded(page: Page) -> None:
    """Rollt zum jeweils nächsten Platzhalter, bis alle Kacheln geladen sind (nach Zeit und
    Fortschritt begrenzt, nicht nach Schritten: `browser_tests/history_tiles.py`)."""
    scroll_until_loaded(page)
    page.wait_for_function(
        "document.querySelectorAll('.tc-history-placeholder').length === 0", timeout=30000
    )
    page.evaluate("window.scrollTo(0, 0)")


def _hover_on(page: Page, scope: Any, fraction: float) -> None:
    """Bewegt die Maus an eine Stelle der sichtbaren Zeichnung (Anteil von links)."""
    drawing = scope.locator("svg:visible").first
    point = drawing.evaluate(
        """(svg, fraction) => {
            const left = Number(svg.dataset.left), right = Number(svg.dataset.right);
            const p = svg.createSVGPoint(); p.x = left + (right - left) * fraction;
            p.y = Number(svg.dataset.top) + 30;
            const s = p.matrixTransform(svg.getScreenCTM()); return {x: s.x, y: s.y};
        }""",
        fraction,
    )
    page.mouse.move(point["x"], point["y"])


# -- Isolation ----------------------------------------------------------------------------


def test_a_tenant_gets_tiles_for_their_own_rooms_only(
    browser: Browser, live_server: LiveServer
) -> None:
    mine = _seed_room(live_server, "vt-eigen-1", "Eigenes Wohnzimmer", "19.5")
    mine_two = _seed_room(live_server, "vt-eigen-2", "Eigenes Bad", "16.5")
    theirs = _seed_room(live_server, "vt-fremd-1", "Fremdes Schlafzimmer", "24.5")
    other = _seed_room(live_server, "vt-fremd-2", "Fremde Küche", "25.5")
    first = _tenant(live_server, "vt-mieter-a", [mine, mine_two])
    second = _tenant(live_server, "vt-mieter-b", [theirs])
    with _opened_as(browser, live_server, first, 1280, 900) as page:
        seen = _watch_requests(page)
        page.goto("/temperature-history")
        expect(page.get_by_role("heading", name="Wie war die Temperatur?", level=1)).to_be_visible()
        _loaded(page)
        names = page.locator(".tc-history-tile h2").all_inner_texts()
        assert sorted(names) == ["Eigenes Bad", "Eigenes Wohnzimmer"]
        for tile in page.locator(".tc-history-tile").all():
            expect(tile.locator("svg:visible")).to_have_count(1)
        html = page.content()
        for foreign, display in ((theirs, "Fremdes Schlafzimmer"), (other, "Fremde Küche")):
            assert display not in html and f"zone-history-{foreign}" not in html
        assert seen and all(status == 200 for _, status in seen), seen
        assert {url.split("/temperature-history/")[1].split("/")[0] for url, _ in seen} == {
            str(mine), str(mine_two)
        }
        # Hover-Daten und Texte der Kacheln gehören nur den eigenen Räumen: 19,5 und 16,5 °C
        # (Zehntel: 195 / 165), nie 24,5 oder 25,5.
        means = {
            json.loads(plot.get_attribute("data-history-data") or "{}")["c"][3]
            for plot in page.locator("[data-history-plot]").all()
        }
        assert means == {195, 165}
        # Die Kachel-Adresse eines fremden Raums gibt es für diesen Mieter nicht.
        assert page.request.get(f"/temperature-history/{theirs}/tile").status == 404
        assert page.request.get(f"/temperature-history/{theirs}/tile?period=kaputt").status == 404
    with _opened_as(browser, live_server, second, 1280, 900) as page:
        page.goto("/temperature-history")
        _loaded(page)
        assert page.locator(".tc-history-tile h2").all_inner_texts() == ["Fremdes Schlafzimmer"]
        assert "Eigenes" not in page.content()


def test_a_tenant_without_rooms_sees_an_empty_page(
    browser: Browser, live_server: LiveServer
) -> None:
    username = _tenant(live_server, "vt-mieter-leer", [])
    with _opened_as(browser, live_server, username, 390, 844) as page:
        page.goto("/temperature-history")
        expect(page.get_by_text("noch kein Raum freigegeben")).to_be_visible()
        expect(page.locator(".tc-history-tile")).to_have_count(0)
        assert page.evaluate("document.documentElement.scrollWidth - window.innerWidth") <= 1


# -- Zeitraum, Vollbild, Hover ----------------------------------------------------------------


def test_the_period_applies_to_every_tile_and_stays_in_the_address(
    browser: Browser, live_server: LiveServer
) -> None:
    rooms = [
        _seed_room(live_server, f"vt-zeit-{i}", f"Zeitraum Raum {i}", "19.5") for i in (1, 2)
    ]
    username = _tenant(live_server, "vt-mieter-zeit", rooms)
    with _opened_as(browser, live_server, username, 1280, 900) as page:
        seen = _watch_requests(page)
        page.goto("/temperature-history")
        _loaded(page)
        expect(page.locator(".tc-history-tile")).to_have_count(2)
        expect(page.locator(".tc-segment.tc-here")).to_have_text("24 Stunden")
        page.get_by_role("link", name="7 Tage").click()
        expect(page.locator(".tc-segment.tc-here")).to_have_text("7 Tage")
        page.wait_for_function(
            "document.querySelectorAll('.tc-history-tile').length === 2 && "
            "Array.from(document.querySelectorAll('.tc-history-tile')).every(t => "
            "t.classList.contains('tc-history-placeholder') ? "
            "(t.getAttribute('hx-get') || '').includes('period=7d') : "
            "t.textContent.includes('17 Minuten'))",
            timeout=15000,
        )
        _loaded(page)
        assert "period=7d" in page.url
        for tile in page.locator(".tc-history-tile").all():
            expect(tile).to_contain_text("bis zu 17 Minuten")
        assert all(status == 200 for _, status in seen), seen
        page.reload()
        _loaded(page)
        expect(page.locator(".tc-segment.tc-here")).to_have_text("7 Tage")


def test_each_tile_has_its_own_fullscreen_and_the_hover_speaks_plainly(
    browser: Browser, live_server: LiveServer
) -> None:
    rooms = [
        _seed_room(live_server, f"vt-voll-{i}", f"Vollbild Raum {i}", "19.5") for i in (1, 2)
    ]
    username = _tenant(live_server, "vt-mieter-voll", rooms)
    with _opened_as(browser, live_server, username, 1280, 800) as page:
        page.goto("/temperature-history")
        _loaded(page)
        tile = page.locator(
            ".tc-history-tile", has=page.get_by_role("heading", name="Vollbild Raum 2")
        )
        tile.get_by_role("button", name="Vollbild").click()
        expanded = page.locator(".tc-history-expanded")
        expect(expanded).to_have_count(1)
        expect(expanded.get_by_role("heading", name="Vollbild Raum 2")).to_be_visible()
        page.wait_for_timeout(400)
        # Im Zeitraum von 24 Stunden liegt die Sonnenabsenkung rund 100 bis 20 Minuten zurück.
        _hover_on(page, expanded, 1 - 60 / (24 * 60))
        tip = expanded.locator(".tc-history-tip")
        expect(tip).to_be_visible()
        text = tip.inner_text()
        for expected in ("Gemessen", "19,5 °C", "Zielwert ohne Sonnenabsenkung", "20,5 °C",
                         "Tatsächlicher Zielwert", "18,5 °C", "Heizanforderung", "ja",
                         "Sonnenabsenkung 2,0 Grad"):
            assert expected in text, (expected, text)
        for technical in ("Zeitplan-Soll", "Sollwert vor Sonnenabsenkung", "Wirksamer Soll", " K"):
            assert technical not in text, (technical, text)
        # Eine senkrechte Linie hat keine Breite und gilt Playwright deshalb nie als sichtbar:
        # geprüft wird, dass die Markergruppe eingeblendet ist und die Punkte eine Lage haben.
        marker = expanded.locator("svg:visible .tc-history-marker")
        expect(marker).not_to_have_attribute("display", "none")
        expect(expanded.locator("svg:visible .tc-history-marker-dot-actual")).to_be_visible()
        # In der Lücke gibt es keine Zeile -- und die Anzeige sagt das.
        _hover_on(page, expanded, 1 - 150 / (24 * 60))
        expect(tip).to_contain_text("keine Daten")
        expanded.get_by_role("button", name="Schließen").click()
        expect(page.locator(".tc-history-expanded")).to_have_count(0)
        # Auch nach dem Schließen zeigt die Kachel ihre Werte beim Darüberfahren.
        first = page.locator(".tc-history-tile").first
        first.scroll_into_view_if_needed()
        _hover_on(page, first, 1 - 60 / (24 * 60))
        expect(first.locator(".tc-history-tip")).to_be_visible()


def test_the_legend_and_summary_use_the_words_of_the_dwelling(
    browser: Browser, live_server: LiveServer
) -> None:
    room = _seed_room(live_server, "vt-text", "Textraum", "19.5")
    username = _tenant(live_server, "vt-mieter-text", [room])
    with _opened_as(browser, live_server, username, 1280, 900) as page:
        page.goto("/temperature-history")
        _loaded(page)
        legend = page.locator(".tc-history-legend").inner_text()
        for expected in ("Gemessen:", "Zielwert ohne Sonnenabsenkung:", "Tatsächlicher Zielwert:",
                         "Heizanforderung (Spur unten): Anteil der Zeit",
                         "Wenn Sonne erwartet wird, kann die Regelung den Zielwert absenken"):
            assert expected in legend, (expected, legend)
        for technical in (
            "Zeitplan-Soll", "Sollwert vor Sonnenabsenkung", "Wirksamer Soll", "untere Spur"
        ):
            assert technical not in legend
        # Das Fachwort führt ins Glossar (neuer Tab), ohne dass die Seite verlassen wird.
        link = page.locator('a.tc-help[href$="/glossar#sonnenabsenkung"]')
        expect(link).to_have_attribute("target", "_blank")


# -- Telefon, Dunkelmodus, Navigation ---------------------------------------------------------


def test_on_a_phone_the_tiles_stack_without_overflow_and_the_navigation_reaches_the_page(
    browser: Browser, live_server: LiveServer
) -> None:
    rooms = [
        _seed_room(live_server, f"vt-tel-{i}", f"Telefon Raum {i}", "19.5") for i in (1, 2)
    ]
    username = _tenant(live_server, "vt-mieter-tel", rooms)
    with _opened_as(browser, live_server, username, 390, 844) as page:
        bottom = page.locator(".tc-tbottomnav")
        link = bottom.get_by_role("link", name="Verlauf", exact=True)
        expect(link).to_be_visible()
        # Fünf Einträge nebeneinander: jeder bleibt groß genug zum Antippen.
        widths = bottom.locator("a").evaluate_all(
            "els => els.map(e => e.getBoundingClientRect().width)"
        )
        assert len(widths) == 5 and min(widths) >= 60, widths
        link.click()
        expect(page.get_by_role("heading", name="Wie war die Temperatur?", level=1)).to_be_visible()
        expect(bottom.locator('[aria-current="page"]')).to_have_text("Verlauf")
        _loaded(page)
        rects = page.evaluate(
            """() => Array.from(document.querySelectorAll('.tc-history-tile'))
                .map(e => e.getBoundingClientRect().toJSON())"""
        )
        assert len(rects) == 2
        assert rects[1]["y"] > rects[0]["y"] + rects[0]["height"] - 1
        assert abs(rects[0]["x"] - rects[1]["x"]) < 2
        assert page.evaluate("document.documentElement.scrollWidth - window.innerWidth") <= 1
        # Hover per Finger: antippen zeigt die Werte.
        first = page.locator(".tc-history-tile").first
        first.scroll_into_view_if_needed()
        box = first.locator(".tc-history-plot").bounding_box()
        assert box is not None
        page.mouse.click(box["x"] + box["width"] * 0.8, box["y"] + box["height"] * 0.4)
        expect(first.locator(".tc-history-tip")).to_be_visible()
        # Auch geöffnet im Vollbild läuft nichts über.
        first.get_by_role("button", name="Vollbild").click()
        page.wait_for_timeout(400)
        assert page.evaluate("document.documentElement.scrollWidth - window.innerWidth") <= 1
        expanded = page.locator(".tc-history-expanded")
        expect(expanded).to_have_count(1)
        # Die Wertebox mit den langen Bezeichnungen der Wohnungssicht: jeder Wert bleibt in einer
        # Zeile (früher brach "20,3 °C" bei langer Bezeichnung zu einer Zeichensäule um).
        _hover_on(page, expanded, 1 - 60 / (24 * 60))
        expect(expanded.locator(".tc-history-tip")).to_be_visible()
        values = expanded.locator(".tc-history-tip-grid dd:not(.tc-history-tip-wide)")
        lines = values.evaluate_all(
            """els => els.map(e => {
                const range = document.createRange(); range.selectNodeContents(e);
                return new Set(Array.from(range.getClientRects()).map(r => Math.round(r.top))).size;
            })"""
        )
        assert lines and set(lines) == {1}, lines


def test_the_page_is_readable_in_dark_mode(browser: Browser, live_server: LiveServer) -> None:
    room = _seed_room(live_server, "vt-dunkel", "Dunkelraum", "19.5")
    username = _tenant(live_server, "vt-mieter-dunkel", [room])
    with _opened_as(browser, live_server, username, 1280, 900, color_scheme="dark") as page:
        page.goto("/temperature-history")
        _loaded(page)

        def luminance(color: str) -> float:
            red, green, blue = (
                int(part) for part in color[color.index("(") + 1 : -1].split(",")[:3]
            )
            return (0.2126 * red + 0.7152 * green + 0.0722 * blue) / 255

        tile = page.locator(".tc-history-tile").first
        body = page.evaluate("getComputedStyle(document.body).backgroundColor")
        card = tile.evaluate("el => getComputedStyle(el).backgroundColor")
        text = tile.locator(".tc-history-legend li").first.evaluate(
            "el => getComputedStyle(el).color"
        )
        assert luminance(body) < 0.3 and luminance(card) < 0.4
        assert luminance(text) > 0.5
        intro = page.locator(".tc-history-help").evaluate("el => getComputedStyle(el).color")
        assert luminance(intro) > 0.4
        _hover_on(page, tile, 1 - 60 / (24 * 60))
        tip = tile.locator(".tc-history-tip")
        expect(tip).to_be_visible()
        colors = tip.evaluate(
            "el => [getComputedStyle(el).backgroundColor, getComputedStyle(el).color]"
        )
        assert luminance(colors[0]) < 0.3 < luminance(colors[1])


def test_the_plant_pages_stay_closed_to_the_tenant_in_the_browser(
    browser: Browser, live_server: LiveServer
) -> None:
    room = _seed_room(live_server, "vt-zu", "Geschlossen", "19.5")
    username = _tenant(live_server, "vt-mieter-zu", [room])
    with _opened_as(browser, live_server, username, 1280, 900) as page:
        assert page.request.get("/zones/history").status == 403
        assert page.request.get(f"/zones/{room}/history?tile=1").status == 403
        assert page.request.get(f"/temperature-history/{room}/tile").status == 200
