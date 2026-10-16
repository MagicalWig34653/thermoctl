"""Zuordnungskarte: Die Betriebsart eines Thermostatventils folgt aus den Linien.

Die Zone hat drei Ausgänge zum Ventil: Heizbefehl, Sollwert, Raumtemperatur. Welche Linien eine
Betriebsart hat, entscheidet `domain/thermostat_mode.py` (`switches`, `writes_setpoint`,
`writes_actual`); die Datenfluss-Seite (`domain/data_flow.py`) liest dieselben Fragen. Getestet
wird hier, dass Karte und Datenfluss dasselbe zeigen, dass eine gezogene Linie die richtige
Betriebsart speichert und dass die Karte nie eine Linie zeigt, die die Regelkette nicht ausführt.
"""
# ruff: noqa: E501

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.helpers import (
    capability,
    create_device,
    create_mode,
    create_settings,
    create_zone,
    create_zone_state,
    role,
)
from thermoctl.auth.csrf import CSRF_HEADER, csrf_token
from thermoctl.auth.sessions import COOKIE_NAME
from thermoctl.config import get_settings
from thermoctl.db.models.device import Device, DeviceCapabilityLink, DeviceProperty, ZoneDevice
from thermoctl.db.models.operations import Setting
from thermoctl.db.models.zone import Zone, ZoneSetpoint
from thermoctl.domain.data_flow import EDGE_ACTUAL, EDGE_SETPOINT, EDGE_SWITCH, build_data_flow
from thermoctl.domain.mapping_map import (
    NO_SOURCE_REASON,
    VALVE_SLOTS,
    VIA_ZONE_REASON,
    MappingMap,
    Port,
    build_mapping_map,
    can_connect,
    lines_for_mode,
    mode_for_lines,
    property_port_id,
    zone_port_id,
)
from thermoctl.domain.self_regulating import SETPOINT_PROPERTY, valve_commands
from thermoctl.domain.thermostat_mode import (
    MODE_CONTROLLED,
    MODE_READING_ONLY,
    MODE_SELF_REGULATING,
    MODES,
    writes_anything,
)

ClientAls = Callable[[list[tuple[str, int | None]]], TestClient]
JETZT = datetime(2026, 10, 15, 8, 0)
ACTUAL = "remote_temperature"


# ---- Aufbau ---------------------------------------------------------------------------------


def _prop(session: Session, device: Device, name: str, unit: str | None = "°C") -> None:
    session.add(
        DeviceProperty(
            device_id=device.id, name=name, value_type="numeric", unit=unit,
            min_value=Decimal("0"), max_value=Decimal("35"), is_readable=True, is_writable=True,
        )
    )
    session.flush()


def _valve(session: Session, name: str, *, setpoint: bool = True, actual: str | None = ACTUAL) -> Device:
    device = create_device(session, name)
    session.add(DeviceCapabilityLink(device_id=device.id, capability_id=capability(session, "thermostat").id))
    session.flush()
    if setpoint:
        _prop(session, device, SETPOINT_PROPERTY)
    if actual is not None:
        _prop(session, device, actual)
    return device


def _sensor(session: Session, name: str) -> Device:
    device = create_device(session, name)
    session.add(DeviceCapabilityLink(device_id=device.id, capability_id=capability(session, "temperature").id))
    session.flush()
    return device


def _zone(session: Session, name: str, *, with_source: bool = True) -> Zone:
    if session.get(Setting, 1) is None:
        create_settings(session)
    role(session, "actuator")
    zone = create_zone(session, name)
    if with_source:
        zone.temperature_source_device_id = _sensor(session, f"fuehler-{name}").id
        session.flush()
    return zone


def _assign(session: Session, zone: Zone, device: Device, mode: str) -> ZoneDevice:
    row = ZoneDevice(
        zone_id=zone.id, device_id=device.id,
        device_role_id=role(session, "actuator").id, thermostat_mode=mode,
    )
    session.add(row)
    session.flush()
    return row


def _graph(session: Session, *zones: Zone) -> MappingMap:
    return build_mapping_map(session, list(zones), {z.id for z in zones})


def _ports(graph: MappingMap) -> dict[str, Port]:
    return {p.id: p for p in graph.ports()}


def _drawn_slots(graph: MappingMap, device: Device) -> dict[str, str]:
    """Welche Linien zum Gerät tatsächlich gezeichnet werden: Zonenstelle -> Name des Geräte-Eingangs."""
    ports = _ports(graph)
    drawn: dict[str, str] = {}
    for c in graph.connections:
        if c.device_id != device.id or c.slot not in VALVE_SLOTS or c.in_port_id is None:
            continue
        assert c.out_port_id == zone_port_id(c.zone_id, c.slot)
        target = ports[c.in_port_id]
        drawn[c.slot] = target.property_name or target.capability or ""
    return drawn


# ---- Betriebsart <-> Linien (Domäne, ohne Datenbank) ------------------------------------------


def test_every_mode_has_its_own_lines_and_the_mapping_is_reversible() -> None:
    assert lines_for_mode(MODE_CONTROLLED) == {"actuator"}
    assert lines_for_mode(MODE_SELF_REGULATING) == {"setpoint", "room_temperature"}
    assert lines_for_mode(MODE_READING_ONLY) == {"room_temperature"}
    for mode in MODES:
        assert mode_for_lines(lines_for_mode(mode), has_actual_port=True) == mode


def test_a_device_without_an_actual_input_has_no_room_temperature_line() -> None:
    # Die Regelkette schreibt dort den Sollwert und sonst nichts -- also genau diese Linie.
    assert lines_for_mode(MODE_SELF_REGULATING, has_actual_port=False) == {"setpoint"}
    assert mode_for_lines({"setpoint"}, has_actual_port=False) == MODE_SELF_REGULATING
    # Ohne Eingang für den Istwert hat "nur Raumtemperatur" gar keine Linie.
    assert lines_for_mode(MODE_READING_ONLY, has_actual_port=False) == frozenset()


def test_lines_that_no_mode_carries_have_no_mode() -> None:
    assert mode_for_lines(set(), has_actual_port=True) is None
    # Die Regelkette schreibt den Istwert mit, wo das Gerät ihn annimmt: Sollwert allein stimmt nicht.
    assert mode_for_lines({"setpoint"}, has_actual_port=True) is None
    assert mode_for_lines({"actuator", "room_temperature"}, has_actual_port=True) is None
    assert mode_for_lines({"actuator", "setpoint"}, has_actual_port=True) is None


# ---- Karte und Datenfluss zeigen dasselbe ------------------------------------------------------


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("with_source", [True, False])
@pytest.mark.parametrize("actual", [ACTUAL, None])
def test_map_and_data_flow_show_the_same_lines(
    session: Session, mode: str, with_source: bool, actual: str | None
) -> None:
    zone = _zone(session, "konsistent", with_source=with_source)
    valve = _valve(session, "ventil", actual=actual)
    _assign(session, zone, valve, mode)

    drawn = _drawn_slots(_graph(session, zone), valve)

    flow = build_data_flow(session, zone, JETZT)
    actuator = next(n for n in flow.nodes if n.device_id == valve.id)
    solid = {e.kind: e for e in flow.edges if e.target == actuator.id and not e.dashed}
    expected: dict[str, str] = {}
    if EDGE_SWITCH in solid:
        expected["actuator"] = "thermostat"
    if EDGE_SETPOINT in solid:
        expected["setpoint"] = solid[EDGE_SETPOINT].property_name or ""
    if EDGE_ACTUAL in solid:
        expected["room_temperature"] = solid[EDGE_ACTUAL].property_name or ""
    assert drawn == expected, (mode, with_source, actual)


def test_a_setpoint_mode_without_a_setpoint_input_writes_and_shows_nothing() -> None:
    # `valve_commands` überspringt ein Ventil ohne Sollwert-Eingang ganz -- auch den Istwert.
    assert not writes_anything(MODE_SELF_REGULATING, has_setpoint_property=False)
    assert writes_anything(MODE_SELF_REGULATING, has_setpoint_property=True)
    assert writes_anything(MODE_READING_ONLY, has_setpoint_property=False)
    assert writes_anything(MODE_CONTROLLED, has_setpoint_property=False)
    assert lines_for_mode(MODE_SELF_REGULATING, has_setpoint_port=False, has_actual_port=True) == frozenset()
    assert lines_for_mode(MODE_READING_ONLY, has_setpoint_port=False, has_actual_port=True) == {"room_temperature"}


@pytest.mark.parametrize("mode", [MODE_SELF_REGULATING, MODE_READING_ONLY])
@pytest.mark.parametrize("setpoint", [True, False])
@pytest.mark.parametrize("actual", [ACTUAL, None])
def test_map_and_data_flow_show_what_valve_commands_actually_writes(
    session: Session, mode: str, setpoint: bool, actual: str | None
) -> None:
    """Gegen die Regelkette selbst, nicht nur gegeneinander: gezeichnet wird, was geschrieben wird."""
    zone = _zone(session, "regelkette")
    session.add(
        ZoneSetpoint(
            zone_id=zone.id, setpoint_mode_id=create_mode(session, "tag-regelkette").id,
            temperature_c=Decimal("21.0"),
        )
    )
    create_zone_state(session, zone).temperature_c = Decimal("19.5")
    valve = _valve(session, "ventil-kette", setpoint=setpoint, actual=actual)
    _assign(session, zone, valve, mode)
    session.flush()

    written: set[str] = set()
    for command in valve_commands(session, zone, JETZT):
        if command.setpoint_c is not None:
            written.add("setpoint")
        if command.temperature_property is not None:
            written.add("room_temperature")

    assert set(_drawn_slots(_graph(session, zone), valve)) == written, (mode, setpoint, actual)

    flow = build_data_flow(session, zone, JETZT)
    node = next(n for n in flow.nodes if n.device_id == valve.id)
    kinds = {e.kind for e in flow.edges if e.target == node.id and not e.dashed}
    assert kinds == ({EDGE_SETPOINT} if "setpoint" in written else set()) | (
        {EDGE_ACTUAL} if "room_temperature" in written else set()
    ), (mode, setpoint, actual)


def test_reading_only_shows_no_heating_command_line(session: Session) -> None:
    zone = _zone(session, "nurlesen")
    valve = _valve(session, "lesen")
    _assign(session, zone, valve, MODE_READING_ONLY)
    graph = _graph(session, zone)
    assert _drawn_slots(graph, valve) == {"room_temperature": ACTUAL}
    assert [c.slot for c in graph.connections if c.device_id == valve.id] == ["room_temperature"]


def test_self_regulating_shows_setpoint_and_room_temperature_and_controlled_only_the_command(
    session: Session,
) -> None:
    zone = _zone(session, "modi")
    regulating, controlled = _valve(session, "regelt"), _valve(session, "geschaltet")
    _assign(session, zone, regulating, MODE_SELF_REGULATING)
    _assign(session, zone, controlled, MODE_CONTROLLED)
    graph = _graph(session, zone)
    assert _drawn_slots(graph, regulating) == {"setpoint": SETPOINT_PROPERTY, "room_temperature": ACTUAL}
    assert _drawn_slots(graph, controlled) == {"actuator": "thermostat"}


def test_without_a_source_the_room_temperature_line_is_listed_but_not_drawn(session: Session) -> None:
    zone = _zone(session, "ohnequelle", with_source=False)
    valve = _valve(session, "ventil-ohne")
    _assign(session, zone, valve, MODE_READING_ONLY)
    graph = _graph(session, zone)
    (connection,) = [c for c in graph.connections if c.device_id == valve.id]
    assert connection.in_port_id is None  # keine Linie
    assert "ohne Messquelle" in connection.text


def test_a_mode_without_a_matching_input_stays_listed_so_it_can_be_released(session: Session) -> None:
    zone = _zone(session, "ohneeingang")
    valve = _valve(session, "ventil-ohne-istwert", actual=None)
    _assign(session, zone, valve, MODE_READING_ONLY)
    (connection,) = [c for c in _graph(session, zone).connections if c.device_id == valve.id]
    assert connection.in_port_id is None
    assert "ohne Linie" in connection.text


# ---- Regeln der Ports --------------------------------------------------------------------------


def test_the_valve_inputs_are_the_properties_the_control_chain_writes(session: Session) -> None:
    zone = _zone(session, "eingaenge")
    valve = _valve(session, "ventil-ports")
    # Die Regelkette wählt den ersten Namen der Liste (`TEMPERATURE_PROPERTIES`), den das Gerät hat.
    _prop(session, valve, "external_temperature", unit=None)
    _prop(session, valve, "min_temperature")  # ein anderer beschreibbarer Wert in °C
    ports = _ports(_graph(session, zone))
    assert ports[property_port_id(valve.id, SETPOINT_PROPERTY)].valve_channel == "setpoint"
    assert ports[property_port_id(valve.id, SETPOINT_PROPERTY)].requires_actual is True
    assert ports[property_port_id(valve.id, "external_temperature")].valve_channel == "actual"
    assert ports[property_port_id(valve.id, "min_temperature")].valve_channel is None
    # `remote_temperature` kommt später in der Liste: Die Regelkette beschreibt es nicht, also ist es
    # kein Raumtemperatur-Eingang, und eine Linie dorthin würde etwas anderes zeigen als geschieht.
    assert ports[property_port_id(valve.id, ACTUAL)].valve_channel is None


def test_the_room_temperature_tooltip_explains_where_the_value_comes_from(session: Session) -> None:
    zone = _zone(session, "tooltip")
    ports = _ports(_graph(session, zone))
    assert ports[zone_port_id(zone.id, "room_temperature")].tooltip == (
        "Messwert der Messquelle dieser Zone, wird als Istwert ans Ventil geschrieben."
    )


def test_a_sensor_on_the_actual_input_is_refused_with_the_way_via_the_zone(session: Session) -> None:
    zone = _zone(session, "sensor-direkt")
    sensor = _sensor(session, "multisensor")
    valve = _valve(session, "ventil-direkt")
    _assign(session, zone, valve, MODE_CONTROLLED)  # Aktor: der Eingang ist gesperrt
    ports = _ports(_graph(session, zone))
    verdict = can_connect(ports[f"dev-{sensor.id}-temperature"], ports[property_port_id(valve.id, ACTUAL)])
    assert not verdict.ok and verdict.reason == VIA_ZONE_REASON
    assert "Messquelle der Zone" in VIA_ZONE_REASON and "Raumtemperatur" in VIA_ZONE_REASON


def test_other_refusals_on_an_actuator_property_keep_their_wording(session: Session) -> None:
    zone = _zone(session, "andere-sperre")
    sensor = _sensor(session, "fuehler-sperre")
    valve = _valve(session, "ventil-sperre")
    _prop(session, valve, "min_temperature")  # kein Kanal der Regelkette
    _assign(session, zone, valve, MODE_CONTROLLED)
    ports = _ports(_graph(session, zone))
    verdict = can_connect(ports[f"dev-{sensor.id}-temperature"], ports[property_port_id(valve.id, "min_temperature")])
    assert not verdict.ok
    assert verdict.reason == "Schreibkanäle sind nur auf Bediengeräten erlaubt, die nirgends Aktor sind."


def test_the_zone_outputs_only_fit_their_own_valve_input(session: Session) -> None:
    zone = _zone(session, "falscher-kanal")
    valve = _valve(session, "ventil-kanal")
    _prop(session, valve, "min_temperature")
    ports = _ports(_graph(session, zone))
    setpoint, room = ports[zone_port_id(zone.id, "setpoint")], ports[zone_port_id(zone.id, "room_temperature")]
    assert can_connect(room, ports[property_port_id(valve.id, ACTUAL)]).ok
    crossed = can_connect(room, ports[property_port_id(valve.id, SETPOINT_PROPERTY)])
    assert not crossed.ok and crossed.reason is not None and "kein Eingang für 'Raumtemperatur'" in crossed.reason
    # Mit der Raumtemperatur-Linie ist der Sollwert danach erlaubt (Betriebsart: Ventil regelt selbst).
    _assign(session, zone, valve, MODE_READING_ONLY)
    graph = _graph(session, zone)
    ports = _ports(graph)
    assert can_connect(setpoint, ports[property_port_id(valve.id, SETPOINT_PROPERTY)], graph.connections).ok
    other = can_connect(setpoint, ports[property_port_id(valve.id, "min_temperature")])
    assert not other.ok and other.reason is not None and "kein Eingang für 'Sollwert'" in other.reason
    # Der Heizbefehl ist ein Schaltbefehl und gehört nur an den Schalt-Eingang.
    assert not can_connect(ports[zone_port_id(zone.id, "actuator")], ports[property_port_id(valve.id, ACTUAL)]).ok


def test_setpoint_comes_only_together_with_room_temperature(session: Session) -> None:
    zone = _zone(session, "sollwert-zuerst")
    valve = _valve(session, "ventil-sw")
    graph = _graph(session, zone)
    ports = _ports(graph)
    first = can_connect(ports[zone_port_id(zone.id, "setpoint")], ports[property_port_id(valve.id, SETPOINT_PROPERTY)], graph.connections)
    assert not first.ok and first.reason is not None and "zusammen mit der Raumtemperatur" in first.reason
    # Ohne Istwert-Eingang am Gerät ist der Sollwert allein genau das, was die Regelkette tut.
    bare = _valve(session, "ventil-nur-sw", actual=None)
    graph = _graph(session, zone)
    ports = _ports(graph)
    assert can_connect(ports[zone_port_id(zone.id, "setpoint")], ports[property_port_id(bare.id, SETPOINT_PROPERTY)], graph.connections).ok


def test_command_and_valve_regulation_exclude_each_other(session: Session) -> None:
    zone = _zone(session, "ausschluss")
    valve = _valve(session, "ventil-aus")
    _assign(session, zone, valve, MODE_READING_ONLY)
    graph = _graph(session, zone)
    ports = _ports(graph)
    verdict = can_connect(ports[zone_port_id(zone.id, "actuator")], ports[f"dev-{valve.id}-thermostat"], graph.connections)
    assert not verdict.ok and verdict.reason is not None
    assert "'Raumtemperatur'" in verdict.reason and "schließt" in verdict.reason
    _assign_other = _valve(session, "ventil-heiz")
    _assign(session, zone, _assign_other, MODE_CONTROLLED)
    graph = _graph(session, zone)
    ports = _ports(graph)
    blocked = can_connect(ports[zone_port_id(zone.id, "room_temperature")], ports[property_port_id(_assign_other.id, ACTUAL)], graph.connections)
    assert not blocked.ok and blocked.reason is not None and "'Heizbefehl'" in blocked.reason


def test_room_temperature_needs_a_source_in_the_zone(session: Session) -> None:
    zone = _zone(session, "keine-quelle", with_source=False)
    valve = _valve(session, "ventil-kq")
    ports = _ports(_graph(session, zone))
    verdict = can_connect(ports[zone_port_id(zone.id, "room_temperature")], ports[property_port_id(valve.id, ACTUAL)])
    assert not verdict.ok and verdict.reason == NO_SOURCE_REASON


def test_one_valve_serves_one_zone_for_every_line(session: Session) -> None:
    zone_a, zone_b = _zone(session, "zone-a"), _zone(session, "zone-b")
    valve = _valve(session, "ventil-zwei")
    _assign(session, zone_a, valve, MODE_READING_ONLY)
    graph = _graph(session, zone_a, zone_b)
    ports = _ports(graph)
    verdict = can_connect(ports[zone_port_id(zone_b.id, "room_temperature")], ports[property_port_id(valve.id, ACTUAL)], graph.connections)
    assert not verdict.ok and verdict.reason is not None and "nur eine Zone heizen" in verdict.reason


# ---- Speichern über die Seite ------------------------------------------------------------------


def _csrf(client: TestClient) -> dict[str, str]:
    cookie = client.cookies.get(COOKIE_NAME)
    assert cookie is not None
    return {CSRF_HEADER: csrf_token(cookie, get_settings().secret_key.get_secret_value())}


def _connect(client: TestClient, first: str, second: str) -> Any:
    return client.post("/devices/map/connect", headers=_csrf(client), data={"from_id": first, "to_id": second})


def _disconnect(client: TestClient, zone: Zone, slot: str, device: Device) -> Any:
    return client.post(
        "/devices/map/disconnect", headers=_csrf(client),
        data={"connection_id": f"conn-{zone.id}-{slot}-{device.id}"},
    )


def _mode(session: Session, zone: Zone, device: Device) -> str | None:
    session.expire_all()
    return session.scalar(
        select(ZoneDevice.thermostat_mode).where(ZoneDevice.zone_id == zone.id, ZoneDevice.device_id == device.id)
    )


def _manager(client_als: ClientAls, zone: Zone) -> TestClient:
    return client_als([("device.manage", zone.id), ("device.read", zone.id)])


















