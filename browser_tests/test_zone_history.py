"""Zeitraumwechsel und Vollbild an der echten Oberfläche."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from playwright.sync_api import Page, expect

from browser_tests import seed
from browser_tests.conftest import LiveServer
from tests.helpers import create_shadow_decision

pytestmark = pytest.mark.browser


def test_history_period_and_fullscreen_controls(admin_page: Page, live_server: LiveServer) -> None:
    with live_server.session() as session:
        zone = seed.create_schedule_zone(session, "verlauf-browser")
        session.commit()
        zone_id = zone.id
    page = admin_page
    page.goto(f"/zones/{zone_id}")
    chart = page.locator("#zone-history")
    expect(chart).to_be_visible()
    chart.get_by_role("button", name="3 Tage").click()
    expect(chart.get_by_role("button", name="3 Tage")).to_have_class("btn btn-primary")
    page.locator("#zone-history.htmx-settling").wait_for(state="detached")
    chart.get_by_role("button", name="Vollbild").click()
    expect(chart).to_have_class("card tc-history tc-history-expanded")
    chart.get_by_role("button", name="7 Tage").click()
    page.locator("#zone-history.htmx-settling").wait_for(state="detached")
    expect(chart).to_have_class("card tc-history tc-history-expanded")
    expect(chart.get_by_role("button", name="7 Tage")).to_have_class("btn btn-primary")
    chart.get_by_role("button", name="Schließen").click()
    expect(chart).to_have_class("card tc-history")
    chart.get_by_role("button", name="Vollbild").click()
    page.keyboard.press("Escape")
    expect(chart).to_have_class("card tc-history")


def _seed_history_zone(live_server: LiveServer, name: str) -> int:
    """Eine Zone mit sechs Stunden Verlauf, davon die zweite Hälfte mit Sonnenabsenkung."""
    now = datetime.now(UTC).replace(tzinfo=None)
    with live_server.session() as session:
        zone = seed.create_schedule_zone(session, name)
        for index in range(360):
            solar = index >= 180
            row = create_shadow_decision(session, zone)
            row.decided_at = now - timedelta(minutes=360 - index)
            row.temperature_c = Decimal("19.5")
            row.setpoint_c = Decimal("18.5" if solar else "20.5")
            row.scheduled_setpoint_c = Decimal("20.5")
            row.solar_setback_k = Decimal("2.0") if solar else None
            row.would_heat = index % 4 == 0
        session.commit()
        return zone.id


@pytest.mark.parametrize(
    ("width", "height", "minimum_expanded"),
    [(1280, 800, 380), (390, 844, 560)],
)
def test_history_shows_one_drawing_above_the_form_and_fills_the_fullscreen(
    admin_page: Page, live_server: LiveServer, width: int, height: int, minimum_expanded: int
) -> None:
    zone_id = _seed_history_zone(live_server, f"verlauf-flaeche-{width}")
    page = admin_page
    page.set_viewport_size({"width": width, "height": height})
    page.goto(f"/zones/{zone_id}")
    chart = page.locator("#zone-history")
    expect(chart).to_be_visible()

    def visible_drawings() -> list[float]:
        return page.evaluate(
            """() => Array.from(document.querySelectorAll('#zone-history svg'))
                .map(s => s.getBoundingClientRect().height).filter(h => h > 0)"""
        )

    # Genau eine der vier Zeichnungen ist sichtbar, und die Karte liegt vor dem Formular
    # und beginnt im oberen Teil des ersten Bildschirms.
    assert len(visible_drawings()) == 1
    chart_top = chart.bounding_box()["y"]  # type: ignore[index]
    form_top = page.locator(f"form[action$='/zones/{zone_id}']").bounding_box()["y"]  # type: ignore[index]
    assert chart_top < form_top
    assert chart_top < height / 2
    normal_height = visible_drawings()[0]

    chart.get_by_role("button", name="Vollbild").click()
    expect(chart).to_have_class("card tc-history tc-history-expanded")
    expanded = visible_drawings()
    assert len(expanded) == 1
    # Im Vollbild wird eine höhere Zeichnung gewählt statt kleiner Zeichnung in Leerraum.
    assert expanded[0] >= minimum_expanded
    assert expanded[0] > normal_height * 1.2
    # Nichts ragt über den Schirm: Zeichnung samt Kopfzeile passt in die Höhe.
    assert chart.locator("svg:visible").bounding_box()["y"] + expanded[0] <= height  # type: ignore[index]
