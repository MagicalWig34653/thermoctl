"""The confirmed web revert works in a real browser."""

from decimal import Decimal

import pytest
from playwright.sync_api import Page, expect

from browser_tests.live_server import LiveServer
from tests.helpers import create_zone
from thermoctl.db.models.zone import Zone
from thermoctl.domain.change_log import Actor, set_parameter

pytestmark = pytest.mark.browser


def test_confirmed_revert(admin_page: Page, live_server: LiveServer) -> None:
    with live_server.session() as session:
        zone = create_zone(session, "Browser-Rücknahme")
        change = set_parameter(
            session, Actor(None, None, "web"), zone, "hysteresis_k", Decimal("0.7")
        )
        session.commit()
        zone_id = zone.id
        change_id = change.change_id
    assert change_id is not None

    admin_page.goto("/changes")
    expect(admin_page.get_by_role("heading", name="Änderungsprotokoll")).to_be_visible()
    admin_page.locator(f"#change-{change_id}").get_by_role("link", name="Zurücknehmen").click()
    expect(admin_page.get_by_text("hysteresis_k")).to_be_visible()
    admin_page.get_by_role("button", name="Zurücknahme bestätigen").click()
    expect(admin_page.locator(f"#change-{change_id}")).to_contain_text("Zurückgenommen am")
    with live_server.session() as session:
        restored = session.get(Zone, zone_id)
        assert restored is not None and restored.hysteresis_k is None
