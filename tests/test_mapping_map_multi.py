"""Zuordnungskarte: Mehrfachzuordnung (Matrix a. bis h.) und Entfernen von Verbindungen.

Jede Zeile der Kombinationsmatrix hat hier ihren Test mit dem Ergebnis (erlaubt / abgelehnt samt
Begründung). Wo behauptet wird, die Regelkette vertrage eine Mehrfachzuordnung, läuft der Test durch
`advance_zone_state`, nicht nur durch die Karte.
"""
# ruff: noqa: E501

from collections.abc import Callable
from datetime import datetime
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.helpers import (
    capability,
    create_device,
    create_zone,
    role,
)
from thermoctl.auth.csrf import CSRF_HEADER, csrf_token
from thermoctl.auth.sessions import COOKIE_NAME
from thermoctl.config import get_settings
from thermoctl.db.models.device import Device, DeviceCapabilityLink, ZoneDevice
from thermoctl.db.models.zone import Zone
from thermoctl.domain.mapping_map import (
    MappingMap,
    Port,
    build_mapping_map,
    can_connect,
    zone_port_id,
)
from thermoctl.web.shared.mapping_map import connection_title

ClientAls = Callable[[list[tuple[str, int | None]]], TestClient]
JETZT = datetime(2026, 8, 29, 7, 0)


def _caps(session: Session, device: Device, *codes: str) -> Device:
    for code in codes:
        session.add(DeviceCapabilityLink(device_id=device.id, capability_id=capability(session, code).id))
    session.flush()
    return device


def _sensor(session: Session, name: str) -> Device:
    return _caps(session, create_device(session, name), "temperature", "humidity", "battery")


def _valve(session: Session, name: str) -> Device:
    return _caps(session, create_device(session, name), "thermostat")


def _csrf(client: TestClient) -> dict[str, str]:
    cookie = client.cookies.get(COOKIE_NAME)
    assert cookie is not None
    return {CSRF_HEADER: csrf_token(cookie, get_settings().secret_key.get_secret_value())}


def _connect(client: TestClient, first: str, second: str) -> Any:
    return client.post("/devices/map/connect", headers=_csrf(client), data={"from_id": first, "to_id": second})


def _disconnect(client: TestClient, connection_id: str) -> Any:
    return client.post("/devices/map/disconnect", headers=_csrf(client), data={"connection_id": connection_id})


def _graph(session: Session, *zones: Zone) -> MappingMap:
    return build_mapping_map(session, list(zones), {z.id for z in zones})


def _ports(graph: MappingMap) -> dict[str, Port]:
    return {p.id: p for p in graph.ports()}


# ---- a. Ein Fühler, mehrere Zonen -----------------------------------------------------------------






# ---- b./c. Kontakt in mehreren Zonen, mehrere Kontakte und Aktoren je Zone ---------------------






# ---- d. Ein Aktor, eine Zone ----------------------------------------------------------------------








def test_d_a_switch_is_locked_like_a_thermostat(session: Session) -> None:
    role(session, "actuator")
    a, b = create_zone(session, "s-a"), create_zone(session, "s-b")
    plug = _caps(session, create_device(session, "steckdose"), "switch")
    session.add(ZoneDevice(zone_id=a.id, device_id=plug.id, device_role_id=role(session, "actuator").id))
    session.flush()
    ports = _ports(_graph(session, a, b))
    verdict = can_connect(ports[zone_port_id(b.id, "actuator")], ports[f"dev-{plug.id}-switch"], [])
    assert not verdict.ok and verdict.reason is not None and "nur eine Zone heizen" in verdict.reason


# ---- e. Mehrere Fühler, eine Messquelle -----------------------------------------------------------




# ---- f. Gerät → Gerät: nur Entwurf; die Regeln gelten trotzdem -------------------------------------




# ---- g. Werte ohne Zonen-Eingang -----------------------------------------------------------------


def test_g_humidity_presence_and_unknown_values_have_no_zone_target(session: Session) -> None:
    zone = create_zone(session, "ohne-ziel")
    sensor = _caps(session, create_device(session, "vielfuehler"), "temperature", "humidity", "occupancy", "illuminance", "battery")
    graph = _graph(session, zone)
    ports = _ports(graph)
    zone_ports = [p for p in ports.values() if p.node_kind == "zone"]
    assert {p.slot for p in zone_ports} == {"temperature_source", "window_contact", "actuator", "setpoint", "room_temperature"}
    for code in ("humidity", "occupancy", "illuminance"):
        port = ports[f"dev-{sensor.id}-{code}"]
        for target in zone_ports:
            assert not can_connect(port, target, []).ok, (code, target.slot)
    # Keine Zonenstelle nimmt Luftfeuchte oder Anwesenheit an: Nur Temperatur, Kontakt, Heizbefehl (Sollwert und Raumtemperatur sind Ausgänge).
    assert {p.kind for p in zone_ports} == {"temperature", "contact", "command"}




# ---- h. Gruppen und Bediengeräte ------------------------------------------------------------------




# ---- Entfernen -----------------------------------------------------------------------------------










def test_every_line_is_named_in_the_direction_of_the_data_flow(session: Session) -> None:
    role(session, "actuator")
    zone = create_zone(session, "titel")
    sensor, valve = _sensor(session, "titelfuehler"), _valve(session, "titelventil")
    zone.temperature_source_device_id = sensor.id
    session.add(ZoneDevice(zone_id=zone.id, device_id=valve.id, device_role_id=role(session, "actuator").id))
    session.flush()
    connections = _graph(session, zone).connections
    assert sorted(connection_title(c) for c in connections) == ["Titel: Heizbefehl → titelventil", "titelfuehler → Titel: Messquelle"]
