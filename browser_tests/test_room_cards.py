"""Raumüberschriften dürfen neben dem Statuschip nicht zerquetscht werden."""

import pytest
from playwright.sync_api import Page

from browser_tests import seed
from browser_tests.live_server import LiveServer
from browser_tests.test_tenant_ui import _login_as_tenant

pytestmark = pytest.mark.browser


def test_room_names_fit_the_card_at_390px(page: Page, live_server: LiveServer) -> None:
    names = (
        "Schlafzimmer",
        "Schlafzimmer mit Balkon und Gartenblick",
        "Schlafzimmermitbalkonundgartenblick",
    )
    password = "Raumkarten-Test-7"  # noqa: S105 -- ephemeral local test account
    with live_server.session() as session:
        zones = []
        for index, name in enumerate(names):
            zone = seed.create_schedule_zone(session, f"raumkarte-{index}")
            zone.display_name = name
            zones.append(zone)
        seed.create_login_tenant_user(
            session, "raumkarten-test", password, [("zone.read", zone.id) for zone in zones]
        )
        session.commit()

    page.set_viewport_size({"width": 390, "height": 844})
    _login_as_tenant(page, "raumkarten-test", password)
    page.evaluate("document.fonts.ready")
    for name in names:
        heading = page.get_by_role("heading", name=name, exact=True)
        layout = heading.evaluate("""el => {
            const card = el.closest('.tc-room').getBoundingClientRect();
            const range = document.createRange();
            range.selectNodeContents(el);
            const lines = Array.from(range.getClientRects());
            const chip = el.parentElement.querySelector('.tc-chip').getBoundingClientRect();
            return {
                lines: lines.length,
                contained: lines.every(r => r.left >= card.left && r.right <= card.right
                    && r.top >= card.top && r.bottom <= card.bottom),
                separate: lines.every(r => r.right <= chip.left || r.left >= chip.right
                    || r.bottom <= chip.top || r.top >= chip.bottom),
                overflow: el.scrollWidth > el.clientWidth,
            };
        }""")
        if name == "Schlafzimmer":
            assert layout["lines"] == 1, f"Schlafzimmer bricht in {layout['lines']} Zeilen um"
        assert layout["contained"], f"{name}: Text ragt aus der Karte"
        assert layout["separate"], f"{name}: Text überlappt den Statuschip"
        assert not layout["overflow"], f"{name}: Text wird abgeschnitten"
    assert page.evaluate("document.documentElement.scrollWidth") == 390
