"""Zuordnungskarte im echten Browser: freie Knoten, folgende Linien, Zoom und Pan, klare Richtung."""
# ruff: noqa: E501

import json
from typing import Any

import pytest
from playwright.sync_api import Page, expect
from sqlalchemy import select

from browser_tests import seed
from browser_tests.live_server import LiveServer
from tests.helpers import capability, create_device, role
from thermoctl.db.models.device import DeviceCapabilityLink, DeviceProperty, ZoneDevice
from thermoctl.db.models.zone import Zone

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


# ---- Entfernen, Mehrfachzuordnung ----------------------------------------------------------------


def _seed_two_zones(live_server: LiveServer, name: str) -> tuple[int, int, int, int]:
    """Zwei Zonen, ein Fühler und ein Thermostatventil, nichts verbunden."""
    with live_server.session() as session:
        first = seed.create_schedule_zone(session, f"{name}-a")
        second = seed.create_schedule_zone(session, f"{name}-b")
        sensor = create_device(session, f"{name}-fuehler")
        valve = create_device(session, f"{name}-ventil")
        for device, codes in ((sensor, ("temperature",)), (valve, ("thermostat",))):
            for code in codes:
                session.add(DeviceCapabilityLink(device_id=device.id, capability_id=capability(session, code).id))
        session.commit()
        return first.id, second.id, sensor.id, valve.id


def _line_middle(page: Page, out_port: str, nth: int = 0) -> tuple[float, float]:
    """Mitte einer gezeichneten Linie in Bildschirmkoordinaten."""
    point: dict[str, float] = page.evaluate(
        """([id, nth]) => {
            const path = document.querySelectorAll('path[data-out="' + id + '"]')[nth];
            const p = path.getPointAtLength(path.getTotalLength() / 2), m = path.getScreenCTM();
            return { x: m.a * p.x + m.c * p.y + m.e, y: m.b * p.x + m.d * p.y + m.f };
        }""",
        [out_port, nth],
    )
    return point["x"], point["y"]


def _source_of(live_server: LiveServer, zone: int) -> int | None:
    with live_server.session() as session:
        value = session.get(Zone, zone)
        assert value is not None
        return value.temperature_source_device_id


def test_a_stored_line_is_marked_removed_with_the_button_and_stays_gone(admin_page: Page, live_server: LiveServer) -> None:
    zone, sensor, _ = _seed(live_server, "karte-entfernen", connected=True)
    _open(admin_page)
    out = f"dev-{sensor}-temperature"
    expect(admin_page.locator(f'path[data-out="{out}"]')).to_have_count(1)
    remover = admin_page.locator(".tc-line-remove")
    expect(remover).to_be_hidden()
    x, y = _line_middle(admin_page, out)
    admin_page.mouse.click(x, y)
    expect(admin_page.locator(f'path.tc-map-line-selected[data-out="{out}"]')).to_have_count(1)
    expect(remover).to_be_visible()
    expect(remover).to_have_text("× Entfernen")
    # Ein Klick auf den Hintergrund hebt die Markierung wieder auf.
    box = admin_page.locator("[data-map-viewport]").bounding_box()
    assert box is not None
    admin_page.mouse.click(box["x"] + 6, box["y"] + box["height"] - 6)
    expect(remover).to_be_hidden()
    x, y = _line_middle(admin_page, out)
    admin_page.mouse.click(x, y)
    remover.click()
    expect(admin_page.locator("[data-map-notice]")).to_contain_text("Verbindung gelöst", timeout=10000)
    expect(admin_page.locator(f'path[data-out="{out}"]')).to_have_count(0)
    expect(admin_page.locator(f'[data-connection-row="conn-{zone}-temperature_source-{sensor}"]')).to_have_count(0)
    assert _source_of(live_server, zone) is None
    admin_page.reload()
    admin_page.locator("[data-map][data-ready]").wait_for()
    expect(admin_page.locator(f'path[data-out="{out}"]')).to_have_count(0)


def test_a_line_is_reachable_and_removable_by_keyboard(admin_page: Page, live_server: LiveServer) -> None:
    zone, sensor, _ = _seed(live_server, "karte-tastatur", connected=True)
    _open(admin_page)
    hit = admin_page.locator(f'path[data-hit="conn-{zone}-temperature_source-{sensor}"]')
    expect(hit).to_have_count(1)
    expect(hit).to_have_attribute("role", "button")
    hit.focus()  # Fokus markiert
    expect(admin_page.locator(f'path.tc-map-line-selected[data-out="dev-{sensor}-temperature"]')).to_have_count(1)
    admin_page.keyboard.press("Delete")
    expect(admin_page.locator("[data-map-notice]")).to_contain_text("Verbindung gelöst", timeout=10000)
    admin_page.locator("[data-map][data-ready]").wait_for()
    expect(admin_page.locator(f'path[data-hit="conn-{zone}-temperature_source-{sensor}"]')).to_have_count(0)
    assert _source_of(live_server, zone) is None


def test_a_failed_removal_keeps_the_line_and_says_why(admin_page: Page, live_server: LiveServer) -> None:
    zone, sensor, _ = _seed(live_server, "karte-fehlschlag", connected=True)
    _open(admin_page)
    admin_page.route(
        "**/devices/map/disconnect",
        lambda route: route.fulfill(status=422, content_type="application/json", body='{"ok": false, "reason": "Verbindung nicht gefunden."}'),
    )
    admin_page.locator(f'path[data-hit="conn-{zone}-temperature_source-{sensor}"]').focus()
    admin_page.locator(".tc-line-remove").click()
    expect(admin_page.locator("[data-map-error]")).to_contain_text("Verbindung nicht gefunden.")
    expect(admin_page.locator(f'path[data-out="dev-{sensor}-temperature"]')).to_have_count(1)
    assert _source_of(live_server, zone) == sensor


def test_a_draft_line_is_removed_locally_without_a_request(admin_page: Page, live_server: LiveServer) -> None:
    with live_server.session() as session:
        sensor = create_device(session, "entwurf-fuehler")
        display = create_device(session, "entwurf-anzeige")
        session.add(DeviceCapabilityLink(device_id=sensor.id, capability_id=capability(session, "temperature").id))
        zone = seed.create_schedule_zone(session, "entwurf-zone")
        session.add(ZoneDevice(zone_id=zone.id, device_id=display.id, device_role_id=role(session, "controller").id))
        session.add(DeviceProperty(device_id=display.id, name="ext", value_type="numeric", unit="°C", is_readable=True, is_writable=True))
        session.commit()
        sensor_id, display_id = sensor.id, display.id
    _open(admin_page)
    requests: list[str] = []
    admin_page.on("request", lambda request: requests.append(request.url) if request.method == "POST" else None)
    admin_page.evaluate("window.__marker = 42")
    out = f"dev-{sensor_id}-temperature"
    _drag(admin_page, f'[data-port="{out}"]', f'[data-port="dev-{display_id}-prop-ext"]')
    expect(admin_page.locator(f'path.tc-map-line-draft[data-out="{out}"]')).to_have_count(1)
    expect(admin_page.locator("[data-draft-list] li:not([data-draft-empty])")).to_have_count(1)
    # Die Mitte dieser Linie liegt hinter der Zonenkarte, also per Tastatur anwählen (Linien laufen unter den Karten).
    admin_page.locator(f'path[data-hit="draft:{out}>dev-{display_id}-prop-ext"]').focus()
    admin_page.locator(".tc-line-remove").click()
    expect(admin_page.locator(f'path[data-out="{out}"]')).to_have_count(0)
    expect(admin_page.locator("[data-draft-list] li:not([data-draft-empty])")).to_have_count(0)
    expect(admin_page.locator("[data-map-notice]")).to_contain_text("Entwurf entfernt")
    assert admin_page.evaluate("window.__marker") == 42  # kein Neuladen
    assert requests == []


def test_one_sensor_feeds_two_zones_and_removing_one_line_keeps_the_other(admin_page: Page, live_server: LiveServer) -> None:
    first, second, sensor, _ = _seed_two_zones(live_server, "karte-fan")
    _open(admin_page)
    out = f"dev-{sensor}-temperature"
    rows = admin_page.locator(f'[data-connection-row$="-{sensor}"]')
    _drag(admin_page, f'[data-port="{out}"]', f'[data-port="zone-{first}-temperature_source"]')
    expect(rows).to_have_count(1, timeout=10000)
    admin_page.locator("[data-map][data-ready]").wait_for()
    _drag(admin_page, f'[data-port="{out}"]', f'[data-port="zone-{second}-temperature_source"]')
    expect(rows).to_have_count(2, timeout=10000)
    admin_page.locator("[data-map][data-ready]").wait_for()
    expect(admin_page.locator(f'path.tc-map-line[data-out="{out}"]')).to_have_count(2)
    assert _source_of(live_server, first) == sensor and _source_of(live_server, second) == sensor
    # Eine der beiden Linien entfernen: die andere bleibt.
    expect(admin_page.locator(f'path[data-in="zone-{second}-temperature_source"]')).to_have_count(1)
    admin_page.locator(f'path[data-in="zone-{second}-temperature_source"] ~ path[data-hit]').first.focus()
    admin_page.keyboard.press("Delete")
    expect(admin_page.locator("[data-map-notice]")).to_contain_text("Verbindung gelöst", timeout=10000)
    admin_page.locator("[data-map][data-ready]").wait_for()
    expect(admin_page.locator(f'path.tc-map-line[data-out="{out}"]')).to_have_count(1)
    assert _source_of(live_server, first) == sensor and _source_of(live_server, second) is None


def test_a_valve_cannot_be_the_actuator_of_a_second_zone(admin_page: Page, live_server: LiveServer) -> None:
    first, second, _, valve = _seed_two_zones(live_server, "karte-zwei-aktoren")
    _open(admin_page)
    rows = admin_page.locator(f'[data-connection-row$="-actuator-{valve}"]')
    _drag(admin_page, f'[data-port="zone-{first}-actuator"]', f'[data-port="dev-{valve}-thermostat"]')
    expect(rows).to_have_count(1, timeout=10000)
    admin_page.locator("[data-map][data-ready]").wait_for()
    _drag(admin_page, f'[data-port="zone-{second}-actuator"]', f'[data-port="dev-{valve}-thermostat"]')
    error = admin_page.locator("[data-map-error]")
    expect(error).to_be_visible()
    expect(error).to_contain_text("ist bereits Aktor von Zone")
    expect(error).to_contain_text("ein Gerät kann nur eine Zone heizen")
    admin_page.reload()
    admin_page.locator("[data-map][data-ready]").wait_for()
    expect(rows).to_have_count(1)


# ---- Betriebsart eines Thermostatventils folgt aus den Linien ---------------------------------


def _seed_valve_properties(live_server: LiveServer, valve: int) -> None:
    with live_server.session() as session:
        for name in ("occupied_heating_setpoint", "remote_temperature"):
            session.add(
                DeviceProperty(
                    device_id=valve, name=name, value_type="numeric", unit="°C",
                    is_readable=True, is_writable=True,
                )
            )
        session.commit()


def _mode_of(live_server: LiveServer, zone: int, valve: int) -> str | None:
    with live_server.session() as session:
        return session.scalar(
            select(ZoneDevice.thermostat_mode).where(
                ZoneDevice.zone_id == zone, ZoneDevice.device_id == valve
            )
        )


def test_the_mode_of_a_valve_follows_the_lines_drawn_and_removed(admin_page: Page, live_server: LiveServer) -> None:
    zone, _, valve = _seed(live_server, "karte-betriebsart", connected=True)
    _seed_valve_properties(live_server, valve)
    with live_server.session() as session:
        role(session, "actuator")
        session.commit()
    _open(admin_page)
    room = f'[data-port="zone-{zone}-room_temperature"]'
    expect(admin_page.locator(room)).to_have_attribute(
        "title", "Messwert der Messquelle dieser Zone, wird als Istwert ans Ventil geschrieben."
    )
    _drag(admin_page, room, f'[data-port="dev-{valve}-prop-remote_temperature"]')
    expect(admin_page.locator(f'[data-connection-row="conn-{zone}-room_temperature-{valve}"]')).to_have_count(1, timeout=10000)
    admin_page.locator("[data-map][data-ready]").wait_for()
    assert _mode_of(live_server, zone, valve) == "reading_only"
    # Nur Raumtemperatur: keine Heizbefehl-Linie.
    expect(admin_page.locator(f'path[data-out="zone-{zone}-actuator"]')).to_have_count(0)
    expect(admin_page.locator(f'path[data-out="zone-{zone}-room_temperature"]')).to_have_count(1)

    _drag(admin_page, f'[data-port="zone-{zone}-setpoint"]', f'[data-port="dev-{valve}-prop-occupied_heating_setpoint"]')
    expect(admin_page.locator(f'[data-connection-row="conn-{zone}-setpoint-{valve}"]')).to_have_count(1, timeout=10000)
    admin_page.locator("[data-map][data-ready]").wait_for()
    assert _mode_of(live_server, zone, valve) == "self_regulating"
    expect(admin_page.locator(f'path[data-out="zone-{zone}-actuator"]')).to_have_count(0)

    # Linie Sollwert entfernen: zurück auf "nur Raumtemperatur".
    admin_page.locator(f'path[data-hit="conn-{zone}-setpoint-{valve}"]').focus()
    admin_page.keyboard.press("Delete")
    expect(admin_page.locator("[data-map-notice]")).to_contain_text("Nur Raumtemperatur melden", timeout=10000)
    admin_page.locator("[data-map][data-ready]").wait_for()
    assert _mode_of(live_server, zone, valve) == "reading_only"
    expect(admin_page.locator(f'[data-connection-row="conn-{zone}-setpoint-{valve}"]')).to_have_count(0)


def test_a_sensor_on_the_valve_input_is_explained_not_just_refused(admin_page: Page, live_server: LiveServer) -> None:
    zone, sensor, valve = _seed(live_server, "karte-sensor-direkt", connected=True)
    _seed_valve_properties(live_server, valve)
    _open(admin_page)
    _drag(admin_page, f'[data-port="dev-{sensor}-temperature"]', f'[data-port="dev-{valve}-prop-remote_temperature"]')
    error = admin_page.locator("[data-map-error]")
    expect(error).to_be_visible()
    expect(error).to_contain_text("Messquelle der Zone")
    expect(error).to_contain_text("Raumtemperatur")
    assert _mode_of(live_server, zone, valve) is None
