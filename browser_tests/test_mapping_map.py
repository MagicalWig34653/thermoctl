"""Zuordnungskarte im echten Browser: freie Knoten, folgende Linien, Zoom und Pan, klare Richtung."""
# ruff: noqa: E501

import json
from typing import Any

import pytest
from playwright.sync_api import Page, expect

from browser_tests import seed
from browser_tests.live_server import LiveServer
from tests.helpers import capability, create_device
from thermoctl.db.models.device import DeviceCapabilityLink

pytestmark = pytest.mark.browser


def _seed(live_server: LiveServer, name: str, *, connected: bool = False) -> tuple[int, int, int]:
    """Eine Zone, ein Fühler (Temperatur, Luftfeuchte, Batterie) und ein Thermostatventil."""
    with live_server.session() as session:
        zone = seed.create_schedule_zone(session, name)
        sensor = create_device(session, f"{name}-fuehler")
        valve = create_device(session, f"{name}-ventil")
        for device, codes in ((sensor, ("temperature", "humidity", "battery")), (valve, ("thermostat",))):
            for code in codes:
                session.add(
                    DeviceCapabilityLink(device_id=device.id, capability_id=capability(session, code).id)
                )
        if connected:
            zone.temperature_source_device_id = sensor.id
        session.commit()
        return zone.id, sensor.id, valve.id


def _open(page: Page) -> None:
    page.goto("/devices/map")
    page.locator("[data-map][data-ready]").wait_for()
    # Die Fläche gehört komplett ins Fenster, sonst liegen Anschlüsse außerhalb der Mauskoordinaten.
    page.locator("[data-map-viewport]").scroll_into_view_if_needed()


def _centre(page: Page, selector: str) -> tuple[float, float]:
    box = page.locator(selector).bounding_box()
    assert box is not None
    return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2


def _drag(page: Page, source: str, target: str) -> None:
    sx, sy = _centre(page, source)
    tx, ty = _centre(page, target)
    page.mouse.move(sx, sy)
    page.mouse.down()
    page.mouse.move(tx, ty, steps=8)
    page.mouse.up()


def _view(page: Page) -> dict[str, float]:
    view: dict[str, float] = json.loads(page.locator("[data-map]").get_attribute("data-view") or "{}")
    return view


def _line_start(page: Page, out_port: str) -> tuple[float, float]:
    """Wo die gezeichnete Linie beginnt, in Bildschirmkoordinaten (Weltkoordinate durch das SVG gerechnet)."""
    point: dict[str, float] = page.evaluate(
        """(id) => {
            const path = document.querySelector('path[data-out="' + id + '"]');
            const p = path.getPointAtLength(0), m = path.getScreenCTM();
            return { x: m.a * p.x + m.c * p.y + m.e, y: m.b * p.x + m.d * p.y + m.f };
        }""",
        out_port,
    )
    return point["x"], point["y"]


def _node_head(node: str) -> str:
    return f'[data-node="{node}"] [data-node-handle]'


def test_a_node_can_be_dragged_and_its_line_follows(admin_page: Page, live_server: LiveServer) -> None:
    _, sensor, _ = _seed(live_server, "karte-folgen", connected=True)
    _open(admin_page)
    out = f"dev-{sensor}-temperature"
    port = f'[data-port="{out}"]'
    expect(admin_page.locator(f'path[data-out="{out}"]')).to_have_count(1)
    port_before = _centre(admin_page, port)
    line_before = _line_start(admin_page, out)
    assert abs(line_before[0] - port_before[0]) < 2 and abs(line_before[1] - port_before[1]) < 2

    hx, hy = _centre(admin_page, _node_head(f"dev-{sensor}") + " .tc-node-title")
    admin_page.mouse.move(hx, hy)
    admin_page.mouse.down()
    admin_page.mouse.move(hx + 60, hy + 40, steps=6)
    admin_page.mouse.up()

    port_after = _centre(admin_page, port)
    assert abs((port_after[0] - port_before[0]) - 60) < 3 and abs((port_after[1] - port_before[1]) - 40) < 3
    line_after = _line_start(admin_page, out)
    assert abs(line_after[0] - port_after[0]) < 2 and abs(line_after[1] - port_after[1]) < 2
    assert line_after != line_before


def test_position_and_view_survive_a_reload(admin_page: Page, live_server: LiveServer) -> None:
    _, sensor, _ = _seed(live_server, "karte-merken")
    _open(admin_page)
    hx, hy = _centre(admin_page, _node_head(f"dev-{sensor}") + " .tc-node-title")
    admin_page.mouse.move(hx, hy)
    admin_page.mouse.down()
    admin_page.mouse.move(hx + 90, hy + 70, steps=6)
    admin_page.mouse.up()
    admin_page.locator("[data-map-zoom=in]").click()
    moved = _centre(admin_page, f'[data-port="dev-{sensor}-temperature"]')
    view = _view(admin_page)
    _open(admin_page)
    again = _centre(admin_page, f'[data-port="dev-{sensor}-temperature"]')
    assert abs(again[0] - moved[0]) < 2 and abs(again[1] - moved[1]) < 2
    assert _view(admin_page) == view


def test_zoom_and_pan_move_the_whole_surface(admin_page: Page, live_server: LiveServer) -> None:
    _, sensor, _ = _seed(live_server, "karte-zoom")
    _open(admin_page)
    port = f'[data-port="dev-{sensor}-temperature"]'
    before_view = _view(admin_page)

    admin_page.locator("[data-map-zoom=in]").click()
    assert _view(admin_page)["z"] > before_view["z"]
    admin_page.locator("[data-map-zoom=out]").click()
    admin_page.locator("[data-map-zoom=out]").click()
    assert _view(admin_page)["z"] < before_view["z"]

    admin_page.locator("[data-map-fit]").click()
    fitted, at_fit = _view(admin_page), _centre(admin_page, port)
    box = admin_page.locator("[data-map-viewport]").bounding_box()
    assert box is not None
    admin_page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    admin_page.mouse.wheel(0, -400)
    assert _view(admin_page)["z"] > fitted["z"]

    admin_page.locator("[data-map-fit]").click()
    admin_page.mouse.move(box["x"] + 6, box["y"] + 6)  # leere Ecke der Fläche
    admin_page.mouse.down()
    admin_page.mouse.move(box["x"] + 76, box["y"] + 46, steps=5)
    admin_page.mouse.up()
    panned = _centre(admin_page, port)
    assert abs((panned[0] - at_fit[0]) - 70) < 3 and abs((panned[1] - at_fit[1]) - 40) < 3


def test_ports_say_which_side_they_are_and_what_flows(admin_page: Page, live_server: LiveServer) -> None:
    zone, sensor, valve = _seed(live_server, "karte-richtung")
    _open(admin_page)
    temp = admin_page.locator(f'[data-port="dev-{sensor}-temperature"]')
    expect(temp).to_have_attribute("title", "Ausgang: liefert Temperatur")
    expect(admin_page.locator(f'[data-port="dev-{valve}-thermostat"]')).to_have_attribute(
        "title", "Eingang: nimmt Schaltbefehle an"
    )
    expect(admin_page.locator(f'[data-port="zone-{zone}-actuator"]')).to_have_attribute("data-dir", "out")
    # Ausgänge sitzen rechts, Eingänge links vom Knoten.
    sensor_node = admin_page.locator(f'[data-node="dev-{sensor}"]').bounding_box()
    out_box = temp.bounding_box()
    zone_node = admin_page.locator(f'[data-node="zone-{zone}"]').bounding_box()
    in_box = admin_page.locator(f'[data-port="zone-{zone}-temperature_source"]').bounding_box()
    assert sensor_node and out_box and zone_node and in_box
    assert out_box["x"] > sensor_node["x"] + sensor_node["width"] / 2
    assert in_box["x"] < zone_node["x"] + zone_node["width"] / 2
    # Die Legende erklärt es auch in Worten.
    expect(admin_page.locator(".tc-map-legend")).to_contain_text("Ausgang")
    expect(admin_page.locator(".tc-map-legend")).to_contain_text("Eingang")


def test_a_valid_connection_is_stored_from_either_end(admin_page: Page, live_server: LiveServer) -> None:
    zone, sensor, valve = _seed(live_server, "karte-gueltig")
    _open(admin_page)
    # Vom Ausgang zum Eingang: Messwert auf die Messquelle.
    _drag(admin_page, f'[data-port="dev-{sensor}-temperature"]', f'[data-port="zone-{zone}-temperature_source"]')
    row = admin_page.locator(f'[data-connection-row="conn-{zone}-temperature_source-{sensor}"]')
    expect(row).to_have_count(1, timeout=10000)
    admin_page.locator("[data-map][data-ready]").wait_for()
    expect(admin_page.locator(f'path[data-in="zone-{zone}-temperature_source"]')).to_have_count(1)
    # Vom Eingang zum Ausgang gezogen: Heizbefehl der Zone an den Ventil-Eingang.
    _drag(admin_page, f'[data-port="dev-{valve}-thermostat"]', f'[data-port="zone-{zone}-actuator"]')
    expect(admin_page.locator(f'[data-connection-row="conn-{zone}-actuator-{valve}"]')).to_have_count(1, timeout=10000)
    admin_page.locator("[data-map][data-ready]").wait_for()
    expect(admin_page.locator(f'path[data-out="zone-{zone}-actuator"]')).to_have_count(1)


def test_invalid_drops_are_refused_with_a_reason_and_store_nothing(admin_page: Page, live_server: LiveServer) -> None:
    zone, sensor, valve = _seed(live_server, "karte-ungueltig")
    _open(admin_page)
    error = admin_page.locator("[data-map-error]")
    cases: list[tuple[str, str, str]] = [
        (f"dev-{sensor}-temperature", f"zone-{zone}-actuator", "Ausgänge"),
        (f"dev-{valve}-thermostat", f"zone-{zone}-temperature_source", "Eingänge"),
        (f"dev-{sensor}-humidity", f"zone-{zone}-temperature_source", "liefert Luftfeuchte"),
        (f"dev-{sensor}-temperature", f"zone-{zone}-window_contact", "liefert Temperatur"),
    ]
    for first, second, expected in cases:
        _drag(admin_page, f'[data-port="{first}"]', f'[data-port="{second}"]')
        expect(error, f"{first} -> {second}").to_be_visible()
        expect(error).to_contain_text(expected)
        admin_page.keyboard.press("Escape")
    expect(admin_page.locator(f'[data-connection-row$="-{sensor}"], [data-connection-row$="-{valve}"]')).to_have_count(0)
    admin_page.reload()
    expect(admin_page.locator(f'[data-connection-row$="-{sensor}"], [data-connection-row$="-{valve}"]')).to_have_count(0)


def test_technical_values_are_hidden_until_asked_for_and_never_connectable(admin_page: Page, live_server: LiveServer) -> None:
    zone, sensor, _ = _seed(live_server, "karte-technik")
    _open(admin_page)
    battery = admin_page.locator(f'[data-port="dev-{sensor}-battery"]')
    expect(battery).to_be_hidden()
    admin_page.locator("[data-map-technical]").check()
    expect(battery).to_be_visible()
    _drag(admin_page, f'[data-port="dev-{sensor}-battery"]', f'[data-port="zone-{zone}-temperature_source"]')
    expect(admin_page.locator("[data-map-error]")).to_contain_text("nicht verbindbar")


def test_nodes_can_be_folded_and_filtered(admin_page: Page, live_server: LiveServer) -> None:
    _, sensor, valve = _seed(live_server, "karte-filter", connected=True)
    _open(admin_page)
    node = admin_page.locator(f'[data-node="dev-{sensor}"]')
    node.locator("[data-fold]").click()
    expect(admin_page.locator(f'[data-port="dev-{sensor}-temperature"]')).to_be_hidden()
    expect(admin_page.locator("path[data-out]").first).to_be_attached()  # die Linie endet an der Kopfzeile
    node.locator("[data-fold]").click()
    expect(admin_page.locator(f'[data-port="dev-{sensor}-temperature"]')).to_be_visible()
    admin_page.locator("[data-map-filter]").fill("karte-filter-ventil")
    expect(admin_page.locator(f'[data-node="dev-{valve}"]')).to_be_visible()
    expect(node).to_be_hidden()
    admin_page.locator("[data-map-filter]").fill("")
    expect(node).to_be_visible()


def test_automatic_layout_separates_the_nodes_and_keeps_the_flow_direction(admin_page: Page, live_server: LiveServer) -> None:
    zone, sensor, valve = _seed(live_server, "karte-anordnen")
    _open(admin_page)
    admin_page.locator("[data-map-layout]").click()
    boxes: dict[str, Any] = {
        name: admin_page.locator(f'[data-node="{name}"]').bounding_box()
        for name in (f"dev-{sensor}", f"zone-{zone}", f"dev-{valve}")
    }
    assert all(boxes.values())
    # Fühler links der Zone, Ventil rechts der Zone, ohne Überlappung.
    assert boxes[f"dev-{sensor}"]["x"] + boxes[f"dev-{sensor}"]["width"] <= boxes[f"zone-{zone}"]["x"]
    assert boxes[f"zone-{zone}"]["x"] + boxes[f"zone-{zone}"]["width"] <= boxes[f"dev-{valve}"]["x"]
