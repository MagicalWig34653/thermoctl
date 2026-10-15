"""Zuordnungskarte (Entwurf): Regeln für Ausgang und Eingang, Graph und Seite."""
# ruff: noqa: E501

from collections.abc import Callable
from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.helpers import capability, create_device, create_settings, create_zone, role, source
from thermoctl.auth.csrf import CSRF_HEADER, csrf_token
from thermoctl.auth.sessions import COOKIE_NAME
from thermoctl.config import get_settings
from thermoctl.db.models.device import Device, DeviceCapabilityLink, DeviceProperty, ZoneDevice
from thermoctl.db.models.operations import AuditEvent
from thermoctl.domain.mapping_map import (
    SAME_SIDE,
    TECHNICAL_REASON,
    Connection,
    MappingMap,
    Port,
    build_mapping_map,
    can_connect,
    zone_port_id,
)

ClientAls = Callable[[list[tuple[str, int | None]]], TestClient]


# ---- Regeln, ohne Datenbank --------------------------------------------------------------------


def _dev(direction: str, kind: str | None, capability_code: str = "x", device_id: int = 1, **kw: Any) -> Port:
    return Port(
        id=f"dev-{device_id}-{capability_code}", node_id=f"dev-{device_id}", node_kind="device",
        node_name=f"Gerät{device_id}", direction=direction, kind=kind,  # type: ignore[arg-type]
        label=capability_code, tooltip="", capability=capability_code, device_id=device_id, **kw,
    )


def _zone(direction: str, kind: str, slot: str, accepts: set[str], zone_id: int = 1, **kw: Any) -> Port:
    return Port(
        id=zone_port_id(zone_id, slot), node_id=f"zone-{zone_id}", node_kind="zone",
        node_name=f"Zone{zone_id}", direction=direction, kind=kind,  # type: ignore[arg-type]
        label=slot, tooltip="", accepts=frozenset(accepts), accepts_text="X", zone_id=zone_id,
        slot=slot, **kw,
    )


def _stored(zone_port: Port, device_id: int, name: str = "Altgerät") -> Connection:
    return Connection(
        id="c", out_port_id=None, in_port_id=None, zone_port_id=zone_port.id, device_id=device_id,
        device_name=name, text="t", zone_id=1, slot=zone_port.slot or "", editable=True,
    )


TEMP_SOURCE = _zone("in", "temperature", "temperature_source", {"temperature"}, single=True)
WINDOW = _zone("in", "contact", "window_contact", {"contact"})
HEAT = _zone("out", "command", "actuator", {"switch", "thermostat"})


def test_a_measurement_flows_from_a_device_output_into_a_zone_input() -> None:
    sensor = _dev("out", "temperature", "temperature")
    verdict = can_connect(sensor, TEMP_SOURCE)
    assert verdict.ok and verdict.output is sensor and verdict.input is TEMP_SOURCE


def test_the_zone_commands_a_device_input_never_the_other_way_round() -> None:
    switch = _dev("in", "command", "switch")
    thermostat = _dev("in", "command", "thermostat", device_id=2)
    for device_input in (switch, thermostat):
        verdict = can_connect(HEAT, device_input)
        assert verdict.ok and verdict.output is HEAT and verdict.input is device_input
    # Der Fehler des ersten Entwurfs: ein Schaltaktor als „Quelle" für einen Zonen-Eingang.
    refused = can_connect(switch, TEMP_SOURCE)
    assert not refused.ok and refused.reason == SAME_SIDE["in"]


def test_the_drag_may_start_at_either_end_and_gives_the_same_answer() -> None:
    sensor = _dev("out", "temperature", "temperature")
    first, second = can_connect(sensor, TEMP_SOURCE), can_connect(TEMP_SOURCE, sensor)
    assert (first.ok, first.output, first.input) == (second.ok, second.output, second.input) == (True, sensor, TEMP_SOURCE)
    bad_a, bad_b = can_connect(sensor, HEAT), can_connect(HEAT, sensor)
    assert not bad_a.ok and bad_a.reason == bad_b.reason


def test_output_to_output_and_input_to_input_are_never_allowed() -> None:
    assert can_connect(_dev("out", "temperature", "temperature"), _dev("out", "temperature", "setpoint", device_id=2)).reason == SAME_SIDE["out"]
    assert can_connect(_dev("in", "command", "switch"), _dev("in", "command", "switch", device_id=2)).reason == SAME_SIDE["in"]
    assert can_connect(_dev("out", "command", "x"), HEAT).reason == SAME_SIDE["out"]
    assert can_connect(_dev("out", "temperature", "temperature"), TEMP_SOURCE).ok


def test_a_node_is_never_connected_to_itself_and_zones_not_to_each_other() -> None:
    own_out = _dev("out", "temperature", "temperature", device_id=3)
    own_in = _dev("in", "temperature", "prop", device_id=3)
    verdict = can_connect(own_out, own_in)
    assert not verdict.ok and verdict.reason is not None and "mit sich selbst" in verdict.reason
    other_zone_in = _zone("in", "temperature", "temperature_source", {"temperature"}, zone_id=2)
    zone_zone = can_connect(HEAT, other_zone_in)
    assert not zone_zone.ok and zone_zone.reason == "Zonen werden nicht untereinander verbunden."
    own_zone = can_connect(HEAT, _zone("in", "temperature", "x", set()))
    assert not own_zone.ok and own_zone.reason is not None and "mit sich selbst" in own_zone.reason


def test_the_kind_of_value_has_to_match() -> None:
    humidity = _dev("out", "humidity", "humidity")
    verdict = can_connect(humidity, TEMP_SOURCE)
    assert not verdict.ok and verdict.reason is not None
    assert "Luftfeuchte" in verdict.reason and "Temperatur" in verdict.reason
    # Gleiche Wertart, anderes Ziel: Temperatur auf Luftfeuchte-Eigenschaft.
    prop = _dev("in", "humidity", "prop", device_id=2)
    assert not can_connect(_dev("out", "temperature", "temperature"), prop).ok
    assert can_connect(humidity, prop).ok  # Gerät → Gerät mit gleicher Wertart: Entwurf möglich


def test_a_command_never_meets_a_measurement() -> None:
    assert not can_connect(HEAT, TEMP_SOURCE).ok  # Zone-Ausgang an Zone-Eingang
    assert not can_connect(HEAT, _dev("in", "temperature", "prop", device_id=2)).ok
    assert not can_connect(_dev("out", "contact", "contact"), _dev("in", "command", "switch", device_id=2)).ok


def test_unknown_value_kinds_are_never_guessed() -> None:
    verdict = can_connect(_dev("out", None, "illuminance"), _dev("in", "temperature", "prop", device_id=2))
    assert not verdict.ok and verdict.reason is not None and "unbekannt" in verdict.reason


def test_the_required_capability_of_a_zone_slot_is_kept_even_for_the_same_kind() -> None:
    setpoint = _dev("out", "temperature", "setpoint")
    verdict = can_connect(setpoint, TEMP_SOURCE)
    assert not verdict.ok and verdict.reason is not None and "passt nicht" in verdict.reason
    assert not can_connect(_dev("out", "contact", "window_open"), WINDOW).ok
    assert can_connect(_dev("out", "contact", "contact"), WINDOW).ok
    assert not can_connect(HEAT, _dev("in", "command", "other", device_id=2)).ok


def test_technical_values_are_never_connectable() -> None:
    battery = _dev("out", None, "battery", technical=True)
    assert can_connect(battery, TEMP_SOURCE).reason == TECHNICAL_REASON
    assert can_connect(TEMP_SOURCE, battery).reason == TECHNICAL_REASON


def test_a_blocked_input_is_refused_with_its_own_reason() -> None:
    prop = _dev("in", "temperature", "prop", device_id=2, blocked_reason="gesperrt")
    assert can_connect(_dev("out", "temperature", "temperature"), prop).reason == "gesperrt"


def test_the_same_connection_cannot_be_made_twice() -> None:
    sensor = _dev("out", "temperature", "temperature", device_id=5)
    verdict = can_connect(sensor, TEMP_SOURCE, [_stored(TEMP_SOURCE, 5)])
    assert not verdict.ok and verdict.reason is not None and "bereits verbunden" in verdict.reason
    valve = _dev("in", "command", "thermostat", device_id=6)
    assert not can_connect(HEAT, valve, [_stored(HEAT, 6)]).ok


def test_the_temperature_source_takes_exactly_one_connection() -> None:
    other = _dev("out", "temperature", "temperature", device_id=7)
    verdict = can_connect(other, TEMP_SOURCE, [_stored(TEMP_SOURCE, 5, "Fühler Alt")])
    assert not verdict.ok and verdict.reason is not None
    assert "nur eine Verbindung" in verdict.reason and "Fühler Alt" in verdict.reason


def test_actuators_and_window_contacts_take_several_devices() -> None:
    assert can_connect(HEAT, _dev("in", "command", "thermostat", device_id=8), [_stored(HEAT, 6)]).ok
    assert can_connect(_dev("out", "contact", "contact", device_id=9), WINDOW, [_stored(WINDOW, 4)]).ok


def test_a_connection_in_another_zone_does_not_block() -> None:
    other_zone = _zone("in", "temperature", "temperature_source", {"temperature"}, zone_id=2, single=True)
    sensor = _dev("out", "temperature", "temperature", device_id=5)
    assert can_connect(sensor, TEMP_SOURCE, [_stored(other_zone, 5)]).ok


# ---- Graph aus der Datenbank -------------------------------------------------------------------


def _caps(session: Session, device: Device, *codes: str) -> Device:
    for code in codes:
        session.add(DeviceCapabilityLink(device_id=device.id, capability_id=capability(session, code).id))
    session.flush()
    return device


def _thermostat(session: Session, name: str) -> Device:
    return _caps(session, create_device(session, name), "thermostat")


def _sensor(session: Session, name: str) -> Device:
    return _caps(session, create_device(session, name), "temperature", "humidity", "battery")


def _ports(graph: MappingMap) -> dict[str, Port]:
    return {p.id: p for p in graph.ports()}


def test_graph_gives_every_node_its_outputs_and_inputs(session: Session) -> None:
    zone = create_zone(session, "karte")
    sensor = _sensor(session, "fuehler")
    valve = _thermostat(session, "ventil")
    switch = _caps(session, create_device(session, "steckdose"), "switch")
    graph = build_mapping_map(session, [zone], {zone.id})
    ports = _ports(graph)
    assert {(p.label, p.direction) for p in graph.nodes[-1].ports} == {
        ("Messquelle", "in"), ("Fensterkontakt", "in"), ("Heizbefehl", "out")}
    assert ports[f"dev-{sensor.id}-temperature"].direction == "out"
    assert ports[f"dev-{sensor.id}-humidity"].direction == "out"
    assert ports[f"dev-{valve.id}-thermostat"].direction == "in"
    assert ports[f"dev-{switch.id}-switch"].direction == "in"
    assert ports[f"dev-{sensor.id}-temperature"].tooltip == "Ausgang: liefert Temperatur"
    assert ports[f"dev-{switch.id}-switch"].tooltip == "Eingang: nimmt Schaltbefehle an"
    assert ports[zone_port_id(zone.id, "actuator")].tooltip == "Ausgang: liefert Heizbefehle"


def test_graph_keeps_technical_values_apart_and_unconnectable(session: Session) -> None:
    zone = create_zone(session, "technik")
    sensor = _sensor(session, "fuehler-t")
    graph = build_mapping_map(session, [zone], {zone.id})
    battery = _ports(graph)[f"dev-{sensor.id}-battery"]
    assert battery.technical and battery.kind is None
    assert battery.id not in graph.rejections  # kein Anschluss, von dem aus verbunden wird
    assert all(battery.id not in rej for rej in graph.rejections.values())
    assert can_connect(battery, _ports(graph)[zone_port_id(zone.id, "temperature_source")]).reason == TECHNICAL_REASON


def test_graph_rejections_resolve_to_reasons_and_leave_the_fitting_pairs_out(session: Session) -> None:
    zone = create_zone(session, "regeln")
    sensor = _sensor(session, "fuehler-r")
    valve = _thermostat(session, "ventil-r")
    graph = build_mapping_map(session, [zone], {zone.id})
    temp, hum = f"dev-{sensor.id}-temperature", f"dev-{sensor.id}-humidity"
    source_id, window, heat = zone_port_id(zone.id, "temperature_source"), zone_port_id(zone.id, "window_contact"), zone_port_id(zone.id, "actuator")
    assert source_id not in graph.rejections[temp]          # Temperatur → Messquelle passt
    assert source_id in graph.rejections[hum]               # Luftfeuchte → Messquelle nicht
    assert window in graph.rejections[temp]
    valve_in = f"dev-{valve.id}-thermostat"
    assert valve_in not in graph.rejections[heat]           # Heizbefehl → Thermostat passt
    assert heat not in graph.rejections[valve_in]           # ... auch von der anderen Seite
    assert valve_in not in graph.rejections[source_id]      # Eingang ↔ Eingang steht nicht in der Tabelle (SAME_SIDE)
    assert heat not in graph.rejections[temp]               # Ausgang → Ausgang steht nicht in der Tabelle (SAME_SIDE)
    assert all(0 <= index < len(graph.reasons) for rej in graph.rejections.values() for index in rej.values())


def test_graph_reasons_tell_what_the_output_delivers_and_the_input_accepts(session: Session) -> None:
    zone = create_zone(session, "begruendung")
    sensor = _sensor(session, "fuehler-b")
    graph = build_mapping_map(session, [zone], {zone.id})
    reason = graph.reasons[graph.rejections[f"dev-{sensor.id}-humidity"][zone_port_id(zone.id, "temperature_source")]]
    assert "liefert Luftfeuchte" in reason and "nimmt Temperatur an" in reason


def test_graph_orients_stored_connections_from_output_to_input(session: Session) -> None:
    zone = create_zone(session, "karte2")
    sensor = _sensor(session, "fuehler2")
    valve = _thermostat(session, "ventil2")
    zone.temperature_source_device_id = sensor.id
    session.add(ZoneDevice(zone_id=zone.id, device_id=valve.id, device_role_id=role(session, "actuator").id))
    session.flush()
    graph = build_mapping_map(session, [zone], set())
    by_slot = {c.slot: c for c in graph.connections}
    temp = by_slot["temperature_source"]
    assert (temp.out_port_id, temp.in_port_id) == (f"dev-{sensor.id}-temperature", zone_port_id(zone.id, "temperature_source"))
    heat = by_slot["actuator"]
    assert (heat.out_port_id, heat.in_port_id) == (zone_port_id(zone.id, "actuator"), f"dev-{valve.id}-thermostat")
    assert not temp.editable and not heat.editable


def test_a_stored_connection_blocks_a_second_source_and_a_duplicate(session: Session) -> None:
    zone = create_zone(session, "belegt")
    first, second = _sensor(session, "erster"), _sensor(session, "zweiter")
    zone.temperature_source_device_id = first.id
    session.flush()
    graph = build_mapping_map(session, [zone], {zone.id})
    target = zone_port_id(zone.id, "temperature_source")
    assert graph.reasons[graph.rejections[f"dev-{second.id}-temperature"][target]].count("nur eine Verbindung") == 1
    assert "bereits verbunden" in graph.reasons[graph.rejections[f"dev-{first.id}-temperature"][target]]


def test_a_connection_without_a_matching_port_is_listed_without_a_line(session: Session) -> None:
    zone = create_zone(session, "ohne")
    device = _caps(session, create_device(session, "ohne-faehigkeit"), "battery")
    zone.temperature_source_device_id = device.id
    session.flush()
    (connection,) = build_mapping_map(session, [zone], {zone.id}).connections
    assert connection.out_port_id is None and connection.in_port_id == zone_port_id(zone.id, "temperature_source")


def test_writable_properties_are_device_inputs_with_a_kind(session: Session) -> None:
    zone = create_zone(session, "eigenschaften")
    valve = _thermostat(session, "ventil3")
    session.add(DeviceProperty(device_id=valve.id, name="external_temperature_input", value_type="numeric",
                               unit="°C", min_value=Decimal("0"), max_value=Decimal("35"),
                               is_readable=True, is_writable=True))
    session.add(DeviceProperty(device_id=valve.id, name="child_lock", value_type="binary",
                               unit=None, is_readable=True, is_writable=True))
    session.flush()
    graph = build_mapping_map(session, [zone], {zone.id})
    props = [p for p in graph.ports() if p.property_name is not None]
    assert [(p.property_name, p.direction, p.kind, p.single) for p in props] == [
        ("external_temperature_input", "in", "temperature", True)]
    # Ein Ventil, das irgendwo Aktor ist, darf keinen Schreibkanal bekommen (wie bei den Kanälen).
    assert props[0].blocked_reason is not None


# ---- Seite und Speichern ------------------------------------------------------------------------


def _csrf(client: TestClient) -> dict[str, str]:
    cookie = client.cookies.get(COOKIE_NAME)
    assert cookie is not None
    return {CSRF_HEADER: csrf_token(cookie, get_settings().secret_key.get_secret_value())}


def test_page_answers_lists_connections_and_is_visible_zone_filtered(client_als: ClientAls, session: Session) -> None:
    create_settings(session)
    mine = create_zone(session, "meine")
    other = create_zone(session, "fremde")
    sensor = _sensor(session, "fuehler3")
    mine.temperature_source_device_id = sensor.id
    session.flush()
    page = client_als([("device.read", mine.id)]).get("/devices/map")
    assert page.status_code == 200
    assert "Entwurf" in page.text and "Meine" in page.text and "Fremde" not in page.text
    assert f'data-connection-row="conn-{mine.id}-temperature_source-{sensor.id}"' in page.text
    assert other.display_name not in page.text
    # Jeder Anschluss sagt, ob er Ausgang oder Eingang ist, und trägt den Hinweis dazu.
    assert 'data-dir="out"' in page.text and 'data-dir="in"' in page.text
    assert "Ausgang: liefert Temperatur" in page.text and "Eingang: nimmt Temperatur an" in page.text
    assert "technische Werte zeigen" in page.text and "Automatisch anordnen" in page.text


def test_page_marks_technical_ports_and_stored_zone_ports(client_als: ClientAls, session: Session) -> None:
    create_settings(session)
    zone = create_zone(session, "markiert")
    sensor = _sensor(session, "fuehler-m")
    page = client_als([("device.read", zone.id)]).get("/devices/map").text
    def tag(port_id: str) -> str:
        start = page.index(f'data-port="{port_id}"')
        return page[page.rindex("<button", 0, start) : page.index("</button>", start)]

    assert "data-technical" in tag(f"dev-{sensor.id}-battery") and "tc-prow-technical" in page
    assert "data-technical" not in tag(f"dev-{sensor.id}-temperature")
    assert "data-stored" in tag(zone_port_id(zone.id, "actuator"))
    assert "data-stored" not in tag(f"dev-{sensor.id}-temperature")


def test_page_needs_a_right_and_is_closed_to_tenants_and_anonymous(
    client_als: ClientAls, tenant_client: ClientAls, client: TestClient, session: Session
) -> None:
    create_settings(session)
    zone = create_zone(session, "zu")
    assert client.get("/devices/map", follow_redirects=False).status_code in (303, 401)
    assert tenant_client([("device.read", zone.id)]).get("/devices/map").status_code == 403
    page = client_als([("zone.read", zone.id)]).get("/devices/map")
    assert "Zu</div>" not in page.text


def _connect(client: TestClient, first: str, second: str) -> Any:
    return client.post("/devices/map/connect", headers=_csrf(client), data={"from_id": first, "to_id": second})


def test_connect_stores_a_temperature_source_in_either_drag_direction(client_als: ClientAls, session: Session) -> None:
    source(session)
    zone = create_zone(session, "speichern")
    sensor = _sensor(session, "fuehler4")
    client = client_als([("device.manage", zone.id), ("device.read", zone.id)])
    target = zone_port_id(zone.id, "temperature_source")
    response = _connect(client, target, f"dev-{sensor.id}-temperature")  # vom Eingang aus gezogen
    assert response.status_code == 200 and response.json() == {"ok": True}
    session.refresh(zone)
    assert zone.temperature_source_device_id == sensor.id
    assert session.scalar(select(AuditEvent.id).where(AuditEvent.object_type == "zone_temperature_source"))
    second = _sensor(session, "fuehler4b")
    refused = _connect(client, f"dev-{second.id}-temperature", target)
    assert refused.status_code == 422 and "nur eine Verbindung" in refused.json()["reason"]
    session.refresh(zone)
    assert zone.temperature_source_device_id == sensor.id


def test_connect_assigns_an_actuator_from_the_zone_output_and_disconnect_removes_it(client_als: ClientAls, session: Session) -> None:
    source(session)
    zone = create_zone(session, "aktor")
    valve = _thermostat(session, "ventil4")
    role(session, "actuator")
    client = client_als([("device.manage", zone.id), ("device.read", zone.id)])
    heat = zone_port_id(zone.id, "actuator")
    ok = _connect(client, heat, f"dev-{valve.id}-thermostat")
    assert ok.status_code == 200
    assert session.scalar(select(ZoneDevice.id).where(ZoneDevice.zone_id == zone.id)) is not None
    again = _connect(client, f"dev-{valve.id}-thermostat", heat)  # andere Ziehrichtung, gleiche Verbindung
    assert again.status_code == 422 and "bereits verbunden" in again.json()["reason"]
    gone = client.post("/devices/map/disconnect", headers=_csrf(client),
                       data={"connection_id": f"conn-{zone.id}-actuator-{valve.id}"})
    assert gone.status_code == 200
    assert session.scalar(select(ZoneDevice.id).where(ZoneDevice.zone_id == zone.id)) is None


def test_the_old_wrong_direction_is_refused_and_changes_nothing(client_als: ClientAls, session: Session) -> None:
    source(session)
    zone = create_zone(session, "abgelehnt")
    sensor = _sensor(session, "fuehler5")
    valve = _thermostat(session, "ventil5")
    role(session, "actuator")
    client = client_als([("device.manage", zone.id)])
    # Messwert auf den Heizbefehl-Ausgang, Ventil auf die Messquelle, Ausgang auf Ausgang.
    for first, second in (
        (f"dev-{sensor.id}-temperature", zone_port_id(zone.id, "actuator")),
        (f"dev-{valve.id}-thermostat", zone_port_id(zone.id, "temperature_source")),
        (f"dev-{sensor.id}-humidity", zone_port_id(zone.id, "temperature_source")),
        (f"dev-{sensor.id}-battery", zone_port_id(zone.id, "temperature_source")),
    ):
        response = _connect(client, first, second)
        assert response.status_code == 422 and response.json()["ok"] is False and response.json()["reason"], (first, second)
    assert session.scalar(select(ZoneDevice.id)) is None
    session.refresh(zone)
    assert zone.temperature_source_device_id is None


def test_device_to_device_is_judged_by_the_rules_and_then_not_stored(client_als: ClientAls, session: Session) -> None:
    source(session)
    zone = create_zone(session, "eingang")
    sensor = _sensor(session, "fuehler7")
    display = create_device(session, "anzeige")
    session.add(ZoneDevice(zone_id=zone.id, device_id=display.id, device_role_id=role(session, "controller").id))
    session.add(DeviceProperty(device_id=display.id, name="ext", value_type="numeric", unit="°C",
                               is_readable=True, is_writable=True))
    session.flush()
    client = client_als([("device.manage", zone.id)])
    prop = f"dev-{display.id}-prop-ext"
    ok_kind = _connect(client, f"dev-{sensor.id}-temperature", prop)
    assert ok_kind.status_code == 422 and "nicht gespeichert" in ok_kind.json()["reason"]
    wrong_kind = _connect(client, f"dev-{sensor.id}-humidity", prop)
    assert wrong_kind.status_code == 422 and "liefert Luftfeuchte" in wrong_kind.json()["reason"]


def test_foreign_zones_unknown_ports_and_missing_csrf_are_refused(client_als: ClientAls, session: Session) -> None:
    source(session)
    mine = create_zone(session, "meine2")
    foreign = create_zone(session, "fremde2")
    sensor = _sensor(session, "fuehler6")
    device = f"dev-{sensor.id}-temperature"
    reader = client_als([("device.read", mine.id)])
    assert _connect(reader, device, zone_port_id(mine.id, "temperature_source")).status_code == 404
    manager = client_als([("device.manage", mine.id)])
    assert _connect(manager, device, zone_port_id(foreign.id, "temperature_source")).status_code == 404
    assert _connect(manager, device, "input-1-x").status_code == 404
    assert manager.post("/devices/map/connect", data={"from_id": device, "to_id": zone_port_id(mine.id, "temperature_source")}).status_code in (400, 403)
    session.refresh(mine)
    assert mine.temperature_source_device_id is None


def test_disconnect_of_a_foreign_or_unknown_connection_is_refused(client_als: ClientAls, session: Session) -> None:
    source(session)
    zone = create_zone(session, "loesen")
    sensor = _sensor(session, "fuehler8")
    zone.temperature_source_device_id = sensor.id
    session.flush()
    client = client_als([("device.manage", zone.id)])
    assert client.post("/devices/map/disconnect", headers=_csrf(client), data={"connection_id": "conn-0-x-0"}).status_code == 404
    done = client.post("/devices/map/disconnect", headers=_csrf(client),
                       data={"connection_id": f"conn-{zone.id}-temperature_source-{sensor.id}"})
    assert done.status_code == 200
    session.refresh(zone)
    assert zone.temperature_source_device_id is None


def test_script_is_served_locally_and_registered(client: TestClient) -> None:
    assert client.get("/static/mapping_map.js").status_code == 200
    assert "mapping_map.js" in client.get("/static/page_scripts.js").text
