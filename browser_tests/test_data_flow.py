"""Datenfluss-Seite im echten Browser: das Diagramm wird tatsächlich gezeichnet."""

from decimal import Decimal

import pytest
from playwright.sync_api import Page, expect

from browser_tests import seed
from browser_tests.conftest import LiveServer
from tests.helpers import capability, create_device, role
from thermoctl.db.models.device import DeviceCapabilityLink, DeviceProperty, ZoneDevice

pytestmark = pytest.mark.browser


def test_diagram_is_rendered_as_svg_without_third_party_requests(
    admin_page: Page, live_server: LiveServer
) -> None:
    with live_server.session() as session:
        zone = seed.create_schedule_zone(session, "datenfluss-browser")
        session.commit()
        zone_id = zone.id
    requested: list[str] = []
    admin_page.on("request", lambda request: requested.append(request.url))
    admin_page.goto(f"/zones/{zone_id}/data-flow")

    diagram = admin_page.locator("[data-flow-diagram]")
    expect(diagram.locator("svg")).to_have_count(1, timeout=20000)
    expect(diagram).to_have_attribute("data-flow-done", "yes")
    expect(diagram.locator("[data-flow-source]")).to_be_hidden()
    own = admin_page.url.split("/zones/")[0]
    assert [url for url in requested if not url.startswith(own)] == []


def test_full_zone_with_a_hostile_name_is_drawn_and_nothing_runs(
    admin_page: Page, live_server: LiveServer
) -> None:
    hostile = 'a"]<img src=x onerror="window.pwned=1"><script>window.pwned=2</script>%% n9["x'
    with live_server.session() as session:
        zone = seed.create_schedule_zone(session, "datenfluss-voll")
        sensor = create_device(session, "voll-sensor")
        sensor.display_name = hostile
        zone.temperature_source_device_id = sensor.id
        window = create_device(session, "voll-fenster")
        session.add(
            ZoneDevice(
                zone_id=zone.id,
                device_id=window.id,
                device_role_id=role(session, "window_contact").id,
            )
        )
        for mode in ("controlled", "self_regulating", "reading_only"):
            valve = create_device(session, f"voll-{mode}")
            valve.display_name = hostile if mode == "reading_only" else f"Kopf {mode}"
            session.add(
                DeviceCapabilityLink(
                    device_id=valve.id, capability_id=capability(session, "thermostat").id
                )
            )
            session.add(
                ZoneDevice(
                    zone_id=zone.id,
                    device_id=valve.id,
                    device_role_id=role(session, "actuator").id,
                    thermostat_mode=mode,
                )
            )
            for name in ("occupied_heating_setpoint", "external_temperature_input"):
                session.add(
                    DeviceProperty(
                        device_id=valve.id,
                        name=name,
                        value_type="numeric",
                        min_value=Decimal("5"),
                        max_value=Decimal("30"),
                        is_readable=True,
                        is_writable=True,
                    )
                )
        session.commit()
        zone_id = zone.id
    admin_page.goto(f"/zones/{zone_id}/data-flow")
    diagram = admin_page.locator("[data-flow-diagram]")
    expect(diagram).to_have_attribute("data-flow-done", "yes", timeout=20000)
    expect(diagram.locator("svg")).to_have_count(1)
    assert admin_page.evaluate("window.pwned") is None
    assert "Syntax error" not in diagram.inner_text()
    assert diagram.locator("svg img, svg script").count() == 0
