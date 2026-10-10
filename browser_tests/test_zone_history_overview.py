"""Seite „Verläufe aller Zonen": Kacheln, Zeitraumwechsel, Vollbild je Kachel, Nachladen."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from playwright.sync_api import Browser, Locator, Page, expect

from browser_tests import seed
from browser_tests.conftest import LiveServer
from browser_tests.test_zone_history_marker import _opened
from tests.helpers import create_shadow_decision

pytestmark = pytest.mark.browser


def _seed_zones(live_server: LiveServer, prefix: str, count: int) -> list[str]:
    """Zonen mit je 90 Minuten Verlauf; gibt die Anzeigenamen zurück."""
    now = datetime.now(UTC).replace(tzinfo=None)
    names: list[str] = []
    with live_server.session() as session:
        for index in range(count):
            zone = seed.create_schedule_zone(session, f"{prefix}-{index}")
            names.append(zone.display_name)
            for minute in range(90):
                row = create_shadow_decision(session, zone)
                row.decided_at = now - timedelta(minutes=90 - minute)
                row.temperature_c = Decimal("19.5") + Decimal(index) / 10
                row.setpoint_c = Decimal("20.0")
                row.scheduled_setpoint_c = Decimal("20.0")
        session.commit()
    return names


def _watch_tiles(page: Page) -> list[tuple[str, int]]:
    """Merkt sich Adresse und Status jeder Kachel-Anfrage: ein 500 bliebe sonst unbemerkt, weil
    die Browserkonsole ihn als gewöhnliche Fehlermeldung übergeht."""
    seen: list[tuple[str, int]] = []
    page.on("response", lambda r: seen.append((r.url, r.status)) if "tile=1" in r.url else None)
    return seen


def _two_rects(page: Page) -> tuple[dict[str, float], dict[str, float]]:
    """Beide ersten Kacheln in einem Zug gemessen, damit kein Nachrutschen dazwischenkommt."""
    rects = page.evaluate(
        """() => Array.from(document.querySelectorAll('.tc-history-grid > .tc-history'))
            .slice(0, 2).map(e => e.getBoundingClientRect().toJSON())"""
    )
    return rects[0], rects[1]


def _all_loaded(page: Page) -> None:
    page.wait_for_function(
        "document.querySelectorAll('.tc-history-placeholder').length === 0", timeout=30000
    )


def _tile(page: Page, name: str) -> Locator:
    """Die Kachel genau dieser Zone. Der Live-Server ist für die ganze Sitzung geteilt, die Seite
    zeigt also auch die Zonen früherer Tests (teils ohne Verlaufsdaten): Zusicherungen über
    „jede Kachel" oder „die erste Kachel" gelten nur für die eigenen Zonen, nie für die Seite."""
    return page.locator(".tc-history-tile", has=page.get_by_role("heading", name=name, exact=True))


def _period_applied(page: Page, period: str, names: list[str]) -> None:
    """Wartet, bis die Seite fertig getauscht ist: jede Kachel der eigenen Zonen (`names`) ist
    entweder ein Platzhalter für den neuen Zeitraum oder schon seine geladene Karte. Der neue
    Zeitraum-Knopf allein beweist das nicht -- htmx setzt ihn einen Augenblick vor den Kacheln.
    Fremde Zonen bleiben außen vor: Sie haben womöglich keine Daten, also keinen Text."""
    marker = "17 Minuten" if period == "7d" else "3 Minuten"
    page.wait_for_function(
        """([period, marker, names]) => {
            const tiles = Array.from(document.querySelectorAll('.tc-history-tile')).filter(
                tile => names.includes(tile.querySelector('h2')?.textContent.trim()));
            return tiles.length === names.length && tiles.every(tile =>
                tile.classList.contains('tc-history-placeholder')
                    ? (tile.getAttribute('hx-get') || '').includes('period=' + period)
                    : tile.textContent.includes(marker));
        }""",
        arg=[period, marker, names],
        timeout=15000,
    )


def _scroll_through(page: Page) -> None:
    """Rollt zum jeweils nächsten Platzhalter, bis keiner mehr da ist (Kacheln tauschen sich
    beim Laden aus, ein fester Satz Fundstellen wäre danach veraltet)."""
    page.evaluate(
        """async () => {
            for (let i = 0; i < 200; i++) {
                const next = document.querySelector('.tc-history-placeholder');
                if (!next) return;
                next.scrollIntoView({block: 'center'});
                await new Promise(resolve => setTimeout(resolve, 150));
            }
        }"""
    )


def test_every_visible_zone_gets_a_tile_with_the_drawing_and_a_link(
    browser: Browser, live_server: LiveServer
) -> None:
    names = _seed_zones(live_server, "uebersicht", 3)
    with _opened(browser, live_server, 1280, 900) as page:
        seen = _watch_tiles(page)
        page.goto("/zones/history")
        expect(page.get_by_role("heading", name="Verläufe aller Zonen", level=1)).to_be_visible()
        _scroll_through(page)
        _all_loaded(page)
        for name in names:
            tile = _tile(page, name)
            expect(tile.locator("svg:visible")).to_have_count(1)
            link = tile.get_by_role("link", name=name, exact=True)
            assert (link.get_attribute("href") or "").startswith("/zones/")
        assert seen and all(status == 200 for _, status in seen), seen
        # Jede Kennung im Dokument kommt genau einmal vor (Muster, Beschreibungen, Kacheln).
        duplicates = page.evaluate(
            """() => { const ids = Array.from(document.querySelectorAll('[id]')).map(e => e.id);
                       return ids.filter((id, i) => ids.indexOf(id) !== i); }"""
        )
        assert duplicates == []


def test_the_period_applies_to_all_tiles_and_stays_in_the_address(
    browser: Browser, live_server: LiveServer
) -> None:
    names = _seed_zones(live_server, "zeitraum", 2)
    with _opened(browser, live_server, 1280, 900) as page:
        seen = _watch_tiles(page)
        page.goto("/zones/history")
        _scroll_through(page)
        _all_loaded(page)
        page.get_by_role("button", name="7 Tage").click()
        page.locator('#zones-history-page .btn-primary:has-text("7 Tage")').wait_for()
        _period_applied(page, "7d", names)
        _scroll_through(page)
        _all_loaded(page)
        page.wait_for_load_state("networkidle")
        # Jede Kachel der eigenen Zonen zeigt den gewählten Zeitraum (verdichtet: Abschnitte von
        # bis zu 17 Minuten), und keine Anfrage -- auch keine fremder Zonen -- ist gescheitert.
        for name in names:
            tile = _tile(page, name)
            expect(tile).to_have_count(1)
            expect(tile).to_contain_text("bis zu 17 Minuten", timeout=15000)
        assert all(status == 200 for _, status in seen), seen
        assert "period=7d" in page.url
        page.reload()
        _scroll_through(page)
        _all_loaded(page)
        expect(page.locator("#zones-history-page .btn-primary")).to_have_text("7 Tage")


def test_each_tile_has_its_own_fullscreen_and_hover(
    browser: Browser, live_server: LiveServer
) -> None:
    names = _seed_zones(live_server, "voll", 2)
    with _opened(browser, live_server, 1280, 800) as page:
        page.goto("/zones/history")
        _scroll_through(page)
        _all_loaded(page)
        tiles = _tile(page, names[1])
        tiles.get_by_role("button", name="Vollbild").click()
        expanded = page.locator(".tc-history-expanded")
        expect(expanded).to_have_count(1)
        expect(expanded.get_by_role("heading", name=names[1], exact=True)).to_be_visible()
        # Der Zeitraumwechsel gehört der Seite, nicht der Kachel: im Vollbild gibt es ihn nicht.
        assert expanded.get_by_role("button", name="7 Tage").count() == 0
        page.wait_for_timeout(400)
        point = expanded.locator("svg:visible").first.evaluate(
            """svg => { const l = Number(svg.dataset.left), r = Number(svg.dataset.right);
                const p = svg.createSVGPoint(); p.x = l + (r - l) * 0.9;
                p.y = Number(svg.dataset.top) + 30;
                const s = p.matrixTransform(svg.getScreenCTM()); return {x: s.x, y: s.y}; }"""
        )
        page.mouse.move(point["x"], point["y"])
        expect(expanded.locator(".tc-history-tip")).to_be_visible()
        expanded.get_by_role("button", name="Schließen").click()
        expect(page.locator(".tc-history-expanded")).to_have_count(0)
        # Auch nach dem Schließen zeigt die Kachel ihre Werte beim Darüberfahren.
        first = _tile(page, names[0])
        first.scroll_into_view_if_needed()
        box = first.locator(".tc-history-plot").bounding_box()
        assert box is not None
        page.mouse.move(box["x"] + box["width"] * 0.7, box["y"] + box["height"] * 0.4)
        expect(first.locator(".tc-history-tip")).to_be_visible()


def test_tiles_load_only_when_they_come_into_view_and_one_at_a_time(
    browser: Browser, live_server: LiveServer
) -> None:
    _seed_zones(live_server, "nachladen", 8)
    with _opened(browser, live_server, 390, 600) as page:
        seen = _watch_tiles(page)
        page.goto("/zones/history", wait_until="networkidle")
        total = page.locator(".tc-history-grid > .tc-history").count()
        assert total >= 8
        loaded_at_start = len(seen)
        # Nur, was im Bild ist, wurde schon geholt.
        assert 1 <= loaded_at_start < total, (loaded_at_start, total)
        assert page.locator(".tc-history-placeholder").count() >= total - loaded_at_start
        _scroll_through(page)
        _all_loaded(page)
        assert len(seen) == total and all(status == 200 for _, status in seen), seen


def test_the_grid_is_two_columns_on_a_desktop_and_one_on_a_phone_without_overflow(
    browser: Browser, live_server: LiveServer
) -> None:
    names = _seed_zones(live_server, "raster", 2)
    with _opened(browser, live_server, 1280, 900) as page:
        page.goto("/zones/history")
        _scroll_through(page)
        _all_loaded(page)
        first, second = _two_rects(page)
        assert abs(first["y"] - second["y"]) < 2 and second["x"] > first["x"] + first["width"] - 1
        # Eine Kachel nimmt die Zeichnung nach ihrer Breite: in der halben Spalte die schmale.
        assert (
            _tile(page, names[0])
            .locator("svg:visible")
            .first.evaluate("s => s.viewBox.baseVal.width")
            == 360
        )
    with _opened(browser, live_server, 390, 844) as page:
        page.goto("/zones/history")
        _scroll_through(page)
        _all_loaded(page)
        first, second = _two_rects(page)
        assert second["y"] > first["y"] + first["height"] - 1 and abs(first["x"] - second["x"]) < 2
        assert page.evaluate("document.documentElement.scrollWidth - window.innerWidth") <= 1


def test_the_overview_is_reachable_from_the_navigation_and_the_zone_list(
    browser: Browser, live_server: LiveServer
) -> None:
    with _opened(browser, live_server, 1280, 900) as page:
        page.goto("/zones")
        page.get_by_role("link", name="Alle Verläufe").click()
        expect(page.get_by_role("heading", name="Verläufe aller Zonen", level=1)).to_be_visible()
        current = page.locator('.tc-sidenav [aria-current="page"]')
        expect(current).to_have_count(1)
        expect(current).to_have_text("Zonenverläufe")
