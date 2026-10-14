"""Zuordnungskarte im echten Browser: Ziehen verbindet, Unpassendes nicht."""

import pytest
from playwright.sync_api import Page, expect

from browser_tests import seed
from browser_tests.conftest import LiveServer
from tests.helpers import capability, create_device
from thermoctl.db.models.device import DeviceCapabilityLink

pytestmark = pytest.mark.browser


def _seed(live_server: LiveServer, name: str) -> tuple[int, int]:
    with live_server.session() as session:
        zone = seed.create_schedule_zone(session, name)
        sensor = create_device(session, f"{name}-fuehler")
        for code in ("temperature", "humidity"):
            session.add(
                DeviceCapabilityLink(
                    device_id=sensor.id, capability_id=capability(session, code).id
                )
            )
        session.commit()
        return zone.id, sensor.id


def _drag(page: Page, source: str, target: str) -> None:
    start = page.locator(source).bounding_box()
    end = page.locator(target).bounding_box()
    assert start is not None and end is not None
    page.mouse.move(start["x"] + 5, start["y"] + 5)
    page.mouse.down()
    page.mouse.move(end["x"] + 5, end["y"] + 5, steps=8)
    page.mouse.up()


def test_dragging_to_a_fitting_target_stores_it_and_a_misfit_does_nothing(
    admin_page: Page, live_server: LiveServer
) -> None:
    zone_id, sensor_id = _seed(live_server, "karte-browser")
    admin_page.goto("/devices/map")
    source = f'[data-endpoint="dev-{sensor_id}-temperature"]'
    good = f'[data-target="zone-{zone_id}-temperature_source"]'
    bad = f'[data-target="zone-{zone_id}-actuator"]'

    _drag(admin_page, source, bad)
    expect(admin_page.locator("[data-map-error]")).to_be_visible()
    expect(admin_page.locator("[data-connection-row]")).to_have_count(0)

    _drag(admin_page, source, good)
    expect(admin_page.locator("[data-connection-row]")).to_have_count(1, timeout=10000)
    expect(admin_page.locator("path.tc-map-line")).to_have_count(1)
