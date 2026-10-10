"""Anzeige am Mauszeiger im Verlaufsdiagramm: Marker, Wertebox, Lücken, Vollbild, Touch, Tastatur.

Grundsatz wie beim Diagramm: Die Box zeigt nur, was Zeilen belegen. In einer Lücke und nach
dem letzten Eintrag steht "keine Daten", nie ein geschätzter Wert.
"""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from playwright.sync_api import Browser, BrowserContext, Page, expect

from browser_tests import seed
from browser_tests.conftest import LiveServer
from tests.helpers import create_shadow_decision

pytestmark = pytest.mark.browser


@contextmanager
def _opened(
    browser: Browser, live_server: LiveServer, width: int, height: int, **options: Any
) -> Iterator[Page]:
    """Eine angemeldete Seite mit eigener Fenstergröße, Farbmodus und Browser-Zeitzone."""
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
    page.get_by_label("Benutzername").fill(live_server.admin_username)
    page.get_by_label("Passwort").fill(live_server.admin_password)
    page.get_by_role("button", name="Anmelden").click()
    page.locator(".tc-topbar").wait_for()
    try:
        yield page
    finally:
        context.close()
    assert not errors, "\n".join(errors)


def _seed(live_server: LiveServer, name: str, *, alternate: bool = False) -> int:
    """Eine Zone mit genau bekannten Zeilen (Minuten vor jetzt, je eine Zeile je Minute).

    - von -300 bis -200: Ist 18,0 (bei `alternate` im Wechsel 18,0 / 19,0), Soll 20,0, keine
      Absenkung, keine Heizanforderung
    - von -200 bis -100: keine Zeilen (Lücke)
    - von -100 bis -20: Ist 21,0, wirksamer Soll 18,5, Zeitplan-Soll 20,5, Absenkung 2,0 K,
      Heizanforderung
    - nach -20: nichts mehr
    """
    now = datetime.now(UTC).replace(tzinfo=None)
    with live_server.session() as session:
        zone = seed.create_schedule_zone(session, name)
        for minute in list(range(-300, -200)) + list(range(-100, -20)):
            late = minute >= -100
            row = create_shadow_decision(session, zone)
            row.decided_at = now + timedelta(minutes=minute)
            if late:
                row.temperature_c = Decimal("21.0")
                row.setpoint_c = Decimal("18.5")
                row.scheduled_setpoint_c = Decimal("20.5")
                row.solar_setback_k = Decimal("2.0")
                row.would_heat = True
            else:
                odd = alternate and minute % 2 != 0
                row.temperature_c = Decimal("19.0" if odd else "18.0")
                row.setpoint_c = Decimal("20.0")
                row.scheduled_setpoint_c = Decimal("20.0")
                row.would_heat = False
        session.commit()
        return zone.id


def _settled(page: Page) -> None:
    """Wartet, bis die sichtbare Zeichnung ihre Lage nicht mehr ändert (Vollbild)."""
    page.wait_for_function(
        """() => new Promise(resolve => {
            const read = () => JSON.stringify(
                document.querySelector('#zone-history svg:not([style*="none"])')
                    ? Array.from(document.querySelectorAll('#zone-history svg'))
                        .filter(s => s.getClientRects().length)
                        .map(s => s.getBoundingClientRect().toJSON()) : []);
            const first = read();
            setTimeout(() => resolve(first === read() && first !== '[]'), 250);
        })""",
        timeout=10000,
    )


_X_OF = """(svg, fraction) => {
    // Bildschirm-x einer Stelle der Zeichenfläche; die Bildschirmmatrix berücksichtigt auch den
    // freien Rand, wenn die Zeichnung im Vollbild kleiner als ihr Element ist (meet).
    const left = Number(svg.dataset.left), right = Number(svg.dataset.right);
    const point = svg.createSVGPoint();
    point.x = left + (right - left) * fraction;
    point.y = Number(svg.dataset.top) + 10;
    const screen = point.matrixTransform(svg.getScreenCTM());
    return {x: screen.x, y: screen.y};
}"""


def _x_of(page: Page, minutes_ago: float, window_minutes: int = 24 * 60) -> tuple[float, float]:
    drawing = page.locator("#zone-history svg:visible").first
    point: dict[str, float] = drawing.evaluate(_X_OF, 1 - minutes_ago / window_minutes)
    return point["x"], point["y"]


def _hover_at(page: Page, minutes_ago: float, window_minutes: int = 24 * 60) -> float:
    """Bewegt die Maus an die Stelle von `minutes_ago` und gibt die Pixel-x-Lage zurück."""
    _settled(page)
    x, y = _x_of(page, minutes_ago, window_minutes)
    page.mouse.move(x, y + 40)
    return x


def _tip(page: Page) -> str:
    tip = page.locator("#zone-history .tc-history-tip")
    expect(tip).to_be_visible()
    return tip.inner_text()


def test_hover_shows_the_values_the_rows_prove_at_that_place(
    browser: Browser, live_server: LiveServer
) -> None:
    zone_id = _seed(live_server, "marker-werte")
    with _opened(browser, live_server, 1280, 900) as page:
        page.goto(f"/zones/{zone_id}")
        page.locator("#zone-history svg:visible").first.wait_for()
        x = _hover_at(page, 250)
        early = _tip(page)
        assert "18,0 °C" in early
        assert "Zeitplan-Soll" in early and "Wirksamer Soll" in early and "20,0 °C" in early
        assert "keine Sonnenabsenkung" in early
        assert "Heizanforderung" in early and "nein" in early
        # Die senkrechte Linie steht unter der Maus.
        line = page.locator("#zone-history svg:visible .tc-history-marker-line").first
        box = line.bounding_box()
        assert box is not None and abs(box["x"] - x) <= 2
        # Zeit in Ortszeit der Anlage (Europe/Berlin): Minutenabweichung höchstens zwei.
        local = (datetime.now(UTC) - timedelta(minutes=250)).astimezone(ZoneInfo("Europe/Berlin"))
        shown = early.splitlines()[0].split()[-2]
        hours, minutes = (int(part) for part in shown.split(":"))
        delta = abs((hours * 60 + minutes) - (local.hour * 60 + local.minute))
        assert min(delta, 1440 - delta) <= 3, (shown, local)

        _hover_at(page, 60)
        late = _tip(page)
        assert "21,0 °C" in late and "18,5 °C" in late and "20,5 °C" in late
        assert "Sonnenabsenkung 2,0 K" in late
        assert "ja" in late


def test_hover_in_a_gap_and_after_the_last_row_says_no_data_and_draws_no_value(
    browser: Browser, live_server: LiveServer
) -> None:
    zone_id = _seed(live_server, "marker-luecke")
    with _opened(browser, live_server, 1280, 900) as page:
        page.goto(f"/zones/{zone_id}")
        page.locator("#zone-history svg:visible").first.wait_for()
        _hover_at(page, 150)
        gap = _tip(page)
        assert "keine Daten" in gap and "Kein Eintrag von" in gap
        assert "°C" not in gap.split("Ist", 1)[1].split("Kein Eintrag")[0]
        for kind in ("actual", "scheduled", "effective"):
            dot = page.locator(f"#zone-history svg:visible .tc-history-marker-dot-{kind}").first
            assert dot.get_attribute("display") == "none"
        _hover_at(page, 8)
        after = _tip(page)
        assert "keine Daten" in after and "Nach dem letzten Eintrag" in after
        assert "21,0" not in after


def test_a_dense_period_names_the_mean_with_its_section_length_and_the_extremes(
    browser: Browser, live_server: LiveServer
) -> None:
    zone_id = _seed(live_server, "marker-mittel", alternate=True)
    with _opened(browser, live_server, 1280, 900) as page:
        page.goto(f"/zones/{zone_id}")
        chart = page.locator("#zone-history")
        chart.get_by_role("button", name="7 Tage").click()
        page.locator('#zone-history .btn-primary:has-text("7 Tage")').wait_for()
        page.locator("#zone-history.htmx-settling").wait_for(state="detached")
        _hover_at(page, 250, 7 * 24 * 60)
        tip = _tip(page)
        assert "Mittelwert aus" in tip and "Abschnitt bis zu 17 Minuten" in tip
        assert "kleinster 18,0 °C" in tip and "größter 19,0 °C" in tip


def test_the_marker_leaves_the_drawing_untouched(browser: Browser, live_server: LiveServer) -> None:
    zone_id = _seed(live_server, "marker-unberuehrt")
    with _opened(browser, live_server, 1280, 900) as page:
        page.goto(f"/zones/{zone_id}")
        actual = page.locator("#zone-history svg:visible .tc-history-actual").first
        before = actual.get_attribute("d")
        _hover_at(page, 60)
        _tip(page)
        assert actual.get_attribute("d") == before
        # Linie dünn: ein Pixel, unabhängig vom Maßstab der Zeichnung.
        width = page.locator("#zone-history svg:visible .tc-history-marker-line").first.evaluate(
            "el => getComputedStyle(el).strokeWidth"
        )
        assert width == "1px"
        # Mit der Maus aus dem Diagramm verschwindet alles wieder.
        page.mouse.move(5, 5)
        expect(page.locator("#zone-history .tc-history-tip")).to_be_hidden()


def test_hover_works_in_the_fullscreen_drawing(browser: Browser, live_server: LiveServer) -> None:
    zone_id = _seed(live_server, "marker-voll")
    with _opened(browser, live_server, 1280, 800) as page:
        page.goto(f"/zones/{zone_id}")
        page.locator("#zone-history svg:visible").first.wait_for()
        page.locator("#zone-history").get_by_role("button", name="Vollbild").click()
        expect(page.locator("#zone-history")).to_have_class("card tc-history tc-history-expanded")
        x = _hover_at(page, 60)
        tip = _tip(page)
        assert "21,0 °C" in tip
        box = page.locator("#zone-history svg:visible .tc-history-marker-line").first.bounding_box()
        assert box is not None and abs(box["x"] - x) <= 2
        tip_box = page.locator("#zone-history .tc-history-tip").bounding_box()
        assert tip_box is not None and tip_box["x"] >= 0 and tip_box["x"] + tip_box["width"] <= 1280
        assert tip_box["y"] >= 0 and tip_box["y"] + tip_box["height"] <= 800
        # Der Wechsel zurück zur Karte räumt den Marker der großen Zeichnung weg.
        page.locator("#zone-history").get_by_role("button", name="Schließen").click()
        expect(page.locator("#zone-history .tc-history-tip")).to_be_hidden()


@pytest.mark.parametrize("tall", [False, True])
def test_a_tap_on_a_phone_shows_a_box_that_stays_inside_the_screen(
    browser: Browser, live_server: LiveServer, tall: bool
) -> None:
    zone_id = _seed(live_server, f"marker-telefon-{tall}")
    with _opened(browser, live_server, 390, 844, has_touch=True, is_mobile=True) as page:
        page.goto(f"/zones/{zone_id}")
        page.locator("#zone-history svg:visible").first.wait_for()
        if tall:
            page.locator("#zone-history").get_by_role("button", name="Vollbild").click()
        plot = page.locator("#zone-history .tc-history-plot")
        plot.scroll_into_view_if_needed()
        for minutes_ago in (60, 250, 10):  # auch ganz rechts, wo die Box nach links ausweichen muss
            box = plot.bounding_box()
            assert box is not None
            x, y = _x_of(page, minutes_ago)
            page.touchscreen.tap(x, y + 40)
            tip = page.locator("#zone-history .tc-history-tip")
            expect(tip).to_be_visible()
            tip_box = tip.bounding_box()
            assert tip_box is not None
            assert tip_box["x"] >= 0 and tip_box["x"] + tip_box["width"] <= 390, tip_box
            assert (
                tip_box["x"] >= box["x"] - 1
                and tip_box["x"] + tip_box["width"] <= box["x"] + box["width"] + 1
            )
        overflow = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
        assert overflow <= 1
        # Tippen neben das Diagramm blendet die Box wieder aus.
        page.touchscreen.tap(195, 20)
        expect(page.locator("#zone-history .tc-history-tip")).to_be_hidden()


def test_arrow_keys_move_the_marker_when_the_chart_has_focus(
    browser: Browser, live_server: LiveServer
) -> None:
    zone_id = _seed(live_server, "marker-tasten")
    with _opened(browser, live_server, 1280, 900) as page:
        page.goto(f"/zones/{zone_id}")
        plot = page.locator("#zone-history .tc-history-plot")
        expect(plot).to_have_attribute("tabindex", "0")
        plot.focus()
        page.keyboard.press("ArrowRight")
        first = _tip(page)
        assert first.splitlines()[0] != ""
        at = float(plot.get_attribute("data-history-at") or "nan")
        page.keyboard.press("ArrowRight")
        assert float(plot.get_attribute("data-history-at") or "nan") > at
        page.keyboard.press("End")
        assert "keine Daten" in _tip(page)  # nach dem letzten Eintrag
        page.keyboard.press("ArrowLeft")
        page.keyboard.press("Escape")
        expect(page.locator("#zone-history .tc-history-tip")).to_be_hidden()
        # Nur das Ablesen wird angesagt (role=status), die Box ist kein Dauerrauschen.
        assert page.locator("#zone-history .tc-history-tip").get_attribute("role") == "status"


def test_without_javascript_the_chart_is_still_there_and_readable(
    browser: Browser, live_server: LiveServer
) -> None:
    zone_id = _seed(live_server, "marker-ohnejs")
    with _opened(browser, live_server, 1280, 900, java_script_enabled=False) as page:
        page.goto(f"/zones/{zone_id}")
        chart = page.locator("#zone-history")
        expect(chart.locator("svg:visible")).to_have_count(1)
        assert "Ist:" in chart.locator(".tc-history-legend").inner_text()
        assert "Heizanforderung" in chart.locator(".tc-history-summary").inner_text()
        assert chart.locator(".tc-history-tip").count() == 0
        assert chart.locator("[data-history-plot]").get_attribute("tabindex") is None


def test_the_box_is_readable_in_dark_mode(browser: Browser, live_server: LiveServer) -> None:
    zone_id = _seed(live_server, "marker-dunkel")
    with _opened(browser, live_server, 1280, 900, color_scheme="dark") as page:
        page.goto(f"/zones/{zone_id}")
        page.locator("#zone-history svg:visible").first.wait_for()
        _hover_at(page, 60)
        _tip(page)
        colors = page.locator("#zone-history .tc-history-tip").evaluate(
            "el => [getComputedStyle(el).backgroundColor, getComputedStyle(el).color]"
        )

        def luminance(color: str) -> float:
            red, green, blue = (
                int(part) for part in color[color.index("(") + 1 : -1].split(",")[:3]
            )
            return (0.2126 * red + 0.7152 * green + 0.0722 * blue) / 255

        assert luminance(colors[0]) < 0.3 < luminance(colors[1])


def test_times_follow_the_configured_zone_across_the_end_of_summer_time_not_the_browser(
    browser: Browser, live_server: LiveServer
) -> None:
    """25.10.2026: um 03:00 Sommerzeit wird auf 02:00 Winterzeit zurückgestellt.

    Zwei Zeitpunkte eine Stunde auseinander zeigen beide 02:30 Uhr -- die Stunde kommt zweimal.
    Eine Anzeige mit festem Versatz zeigte 03:30; der Browser steht hier absichtlich auf einer
    anderen Zeitzone als die Anlage.
    """
    zone_id = _seed(live_server, "marker-umstellung")
    start = int(datetime(2026, 10, 25, 0, 30, tzinfo=UTC).timestamp())
    crafted = {
        "v": 1, "tz": "Europe/Berlin", "s": start, "e": 7200, "b": "3 Minuten",
        "c": [0, 7200, 5, 200, -1, 1, 0],
        "ef": [[1, 200, 200, 0]], "sc": [[1, 200, 200, 0]], "so": [[1, 0, 0, 0]],
    }  # fmt: skip
    with _opened(browser, live_server, 1280, 900, timezone_id="America/New_York") as page:
        page.goto(f"/zones/{zone_id}")
        page.locator("#zone-history svg:visible").first.wait_for()
        page.evaluate(
            "data => document.querySelector('[data-history-plot]').dataset.historyData = data",
            json.dumps(crafted),
        )
        plot = page.locator("#zone-history .tc-history-plot")
        plot.focus()
        page.keyboard.press("Home")
        head_first = _tip(page).splitlines()[0]
        assert head_first.endswith("25.10. 02:30 Uhr"), head_first
        # +3600 s: End-Taste ist +7200; die Mitte per Pfeiltasten (je 1 % = 72 s, 50 Schritte).
        for _ in range(50):
            page.keyboard.press("ArrowRight")
        head_second = _tip(page).splitlines()[0]
        assert head_second.endswith("25.10. 02:30 Uhr"), head_second
