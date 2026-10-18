"""Geräte unter `/ui-api`: Überblick, Anlagenbild, Karte, Zuordnung, Datenfluss, Bediengeräte.

Der Adapter ist dünn; getestet wird, dass er dieselben Domänenwege geht wie zuvor die HTML-Oberfläche
(Wirkung in der Datenbank), dass Rechte und Zonenfilter je Endpunkt greifen (401/403/404,
CSRF, Profil) und dass Gründe der Domäne als `{detail, code}` ankommen.
"""

# ruff: noqa: E501

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.helpers import (
    capability,
    create_all_command_outcomes,
    create_device,
    create_device_command,
    create_settings,
    create_zone,
    role,
    sensor_status_of,
    source,
)
from thermoctl.auth.csrf import CSRF_HEADER, csrf_token
from thermoctl.auth.sessions import COOKIE_NAME
from thermoctl.config import get_settings
from thermoctl.db.models.device import (
    ControllerBinding,
    ControllerChannel,
    Device,
    DeviceCapabilityLink,
    DeviceProperty,
    ZoneDevice,
)
from thermoctl.db.models.lookup import (
    CHANNEL_KINDS,
    CONTROLLER_COMMANDS,
    ChannelKind,
    ControllerCommand,
)
from thermoctl.db.models.measurement import Measurement
from thermoctl.db.models.operations import AuditEvent
from thermoctl.db.models.state import ZoneState
from thermoctl.db.models.zone import Zone
from thermoctl.domain.mapping_map import property_port_id, zone_port_id
from thermoctl.domain.self_regulating import SETPOINT_PROPERTY
from thermoctl.domain.thermostat_mode import (
    MODE_CONTROLLED,
    MODE_READING_ONLY,
    MODE_SELF_REGULATING,
)
from thermoctl.services.ingest import advance_zone_state

Als = Callable[[list[tuple[str, int | None]]], TestClient]
ACTUAL = "remote_temperature"
ALLES: list[tuple[str, int | None]] = [
    (code, None)
    for code in ("device.read", "device.manage", "zone.read", "zone.manage", "audit.read")
]


def _csrf(client: TestClient) -> dict[str, str]:
    cookie = client.cookies.get(COOKIE_NAME)
    assert cookie is not None
    return {CSRF_HEADER: csrf_token(cookie, get_settings().secret_key.get_secret_value())}


def _call(
    client: TestClient,
    method: str,
    url: str,
    body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
) -> Any:
    return client.request(method, url, json=body, params=params, headers=_csrf(client))


def _fehler(response: Any, status: int, code: str) -> dict[str, Any]:
    assert response.status_code == status, response.text
    body: dict[str, Any] = response.json()
    assert body["code"] == code
    assert isinstance(body["detail"], str) and body["detail"]
    return body


# -- Aufbau -----------------------------------------------------------------------------


def _prop(
    session: Session, device: Device, name: str, *, writable: bool = True, readable: bool = True
) -> DeviceProperty:
    prop = DeviceProperty(
        device_id=device.id,
        name=name,
        value_type="numeric",
        unit="°C",
        min_value=Decimal("0"),
        max_value=Decimal("35"),
        is_readable=readable,
        is_writable=writable,
    )
    session.add(prop)
    session.flush()
    return prop


def _with(session: Session, device: Device, *codes: str) -> Device:
    for code in codes:
        session.add(DeviceCapabilityLink(device_id=device.id, capability_id=capability(session, code).id))
    session.flush()
    return device


def _valve(session: Session, name: str) -> Device:
    device = _with(session, create_device(session, name), "thermostat")
    _prop(session, device, SETPOINT_PROPERTY)
    _prop(session, device, ACTUAL)
    return device


def _assign(session: Session, zone: Zone, device: Device, role_code: str, mode: str = MODE_CONTROLLED) -> ZoneDevice:
    row = ZoneDevice(
        zone_id=zone.id,
        device_id=device.id,
        device_role_id=role(session, role_code).id,
        thermostat_mode=mode,
    )
    session.add(row)
    session.flush()
    return row


@dataclass
class World:
    a: Zone
    b: Zone
    sensor: Device
    sensor2: Device
    plug: Device
    contact: Device
    ctrl: Device


@pytest.fixture
def world(session: Session) -> World:
    create_settings(session)
    source(session, "web")
    for code in ("actuator", "window_contact", "controller"):
        role(session, code)
    for code, label in CHANNEL_KINDS:
        session.add(ChannelKind(code=code, label=label))
    for code, label in CONTROLLER_COMMANDS:
        session.add(ControllerCommand(code=code, label=label))
    session.flush()
    a, b = create_zone(session, "Wohnen"), create_zone(session, "Bad")
    sensor = _with(session, create_device(session, "fuehler-a"), "temperature")
    sensor2 = _with(session, create_device(session, "fuehler-b"), "temperature")
    a.temperature_source_device_id = sensor.id
    plug = _with(session, create_device(session, "steckdose"), "switch")
    contact = _with(session, create_device(session, "fenster"), "contact")
    ctrl = _with(session, create_device(session, "drehknopf"), "action")
    _prop(session, ctrl, "external_temperature", readable=True)
    _assign(session, a, ctrl, "controller")
    session.flush()
    return World(a, b, sensor, sensor2, plug, contact, ctrl)


def _manage(client_als: Als, *zones: Zone) -> TestClient:
    perms: list[tuple[str, int | None]] = [("zone.read", z.id) for z in zones]
    for zone in zones:
        perms += [("device.read", zone.id), ("device.manage", zone.id), ("zone.manage", zone.id)]
    return client_als(perms)


def _rows(session: Session, zone: Zone, device: Device) -> list[ZoneDevice]:
    session.expire_all()
    return list(
        session.scalars(
            select(ZoneDevice).where(ZoneDevice.zone_id == zone.id, ZoneDevice.device_id == device.id)
        )
    )


def _endpoints(w: World) -> list[tuple[str, str, dict[str, Any] | None]]:
    """Jeder Endpunkt, der nicht an eine Zone gebunden ist, mit gültiger Anfrage."""
    return [
        ("GET", "/ui-api/devices", None),
        ("GET", "/ui-api/plant", None),
        ("GET", "/ui-api/devices/map", None),
        ("GET", "/ui-api/devices/map/check?from_id=x&to_id=y", None),
        ("POST", "/ui-api/devices/map/connect", {"from_id": "x", "to_id": "y"}),
        ("POST", "/ui-api/devices/map/disconnect", {"connection_id": "x"}),
        ("GET", "/ui-api/zones/data-flow", None),
        ("GET", "/ui-api/controllers", None),
        ("POST", "/ui-api/controllers/channel", {"device_id": w.ctrl.id, "property_name": "p", "direction": "read", "kind": "fixed"}),
        ("POST", "/ui-api/controllers/button", {"device_id": w.ctrl.id, "action_code": "x"}),
        ("GET", "/ui-api/device-commands", None),
    ]


def _zone_endpoints(w: World) -> list[tuple[str, str, dict[str, Any] | None, bool]]:
    """An eine Zone gebunden; `True`: schreibend (`device.manage`)."""
    base = f"/ui-api/zones/{w.a.id}"
    return [
        ("GET", f"{base}/devices", None, False),
        ("GET", f"{base}/data-flow", None, False),
        ("POST", f"{base}/devices/assign", {"device_id": w.plug.id, "role_code": "actuator"}, True),
        ("POST", f"{base}/devices/detach", {"assignment_id": 1}, True),
        ("POST", f"{base}/devices/regulation", {"assignment_id": 1, "thermostat_mode": MODE_READING_ONLY}, True),
        ("POST", f"{base}/devices/source", {"device_id": w.sensor.id}, True),
        ("POST", f"{base}/devices/swap", {"old_device_id": w.sensor.id, "new_device_id": w.sensor2.id}, True),
        ("POST", f"{base}/devices/button", {"device_id": w.ctrl.id, "action_code": "x"}, True),
    ]


# -- Zugriff ----------------------------------------------------------------------------


def test_every_endpoint_without_session_is_401(client: TestClient, world: World) -> None:
    for method, url, body in _endpoints(world):
        assert client.request(method, url, json=body).status_code == 401, url
    for method, url, body, _write in _zone_endpoints(world):
        assert client.request(method, url, json=body).status_code == 401, url


def test_endpoints_without_rights_are_403(client_als: Als, world: World, session: Session) -> None:
    client = client_als([])
    for method, url, body in _endpoints(world):
        _fehler(_call(client, method, url, body), 403, "forbidden")
    assert session.scalar(select(AuditEvent.id)) is None


def test_zone_endpoints_without_any_right_are_the_same_404_as_an_unknown_zone(
    client_als: Als, world: World
) -> None:
    client = client_als([])
    for method, url, body, _write in _zone_endpoints(world):
        known = _fehler(_call(client, method, url, body), 404, "not_found")
        unknown = _fehler(_call(client, method, url.replace(f"/{world.a.id}/", "/99999/"), body), 404, "not_found")
        assert known == unknown


def test_zone_of_another_principal_is_404_and_read_only_is_403(
    client_als: Als, world: World, session: Session
) -> None:
    other = client_als([("device.read", world.b.id), ("zone.read", world.b.id), ("zone.manage", world.b.id)])
    for method, url, body, _write in _zone_endpoints(world):
        _fehler(_call(other, method, url, body), 404, "not_found")
    reader = client_als([("device.read", world.a.id), ("zone.read", world.a.id)])
    before = session.query(ZoneDevice).count()
    for method, url, body, write in _zone_endpoints(world):
        if write:
            _fehler(_call(reader, method, url, body), 403, "forbidden")
    assert session.query(ZoneDevice).count() == before
    assert _call(reader, "GET", f"/ui-api/zones/{world.a.id}/devices").json()["may_edit"] is False


def test_writes_without_csrf_are_403_stale(client_als: Als, world: World) -> None:
    client = client_als(ALLES)
    for method, url, body in _endpoints(world):
        if method == "POST":
            response = client.request(method, url, json=body, headers={CSRF_HEADER: "falsch"})
            assert response.status_code == 403 and response.json()["stale"] is True, url
    for method, url, body, write in _zone_endpoints(world):
        if write:
            response = client.request(method, url, json=body, headers={CSRF_HEADER: "falsch"})
            assert response.status_code == 403 and response.json()["stale"] is True, url


def test_tenant_profile_does_not_block_the_rights(tenant_client: Als, world: World) -> None:
    """Das Profil sperrt nichts: mit den Rechten kommt auch ein Mieterprofil durch."""
    client = tenant_client(ALLES)
    for method, url, body in _endpoints(world):
        response = _call(client, method, url, body)
        assert response.status_code != 403, (method, url, response.text)
    for method, url, body, _write in _zone_endpoints(world):
        response = _call(client, method, url, body)
        assert response.status_code != 403, (method, url, response.text)


def test_tenant_profile_without_rights_is_forbidden(tenant_client: Als, world: World) -> None:
    client = tenant_client([])
    for method, url, body in _endpoints(world):
        response = _call(client, method, url, body)
        assert response.status_code == 403 and response.json()["code"] != "wrong_profile", url


def test_malformed_bodies_are_422(client_als: Als, world: World) -> None:
    client = client_als(ALLES)
    for method, url, _body in _endpoints(world):
        if method == "POST":
            _fehler(_call(client, method, url, {"unerwartet": 1}), 422, "validation")
    for method, url, _body, write in _zone_endpoints(world):
        if write:
            bad = {"device_id": "abc"} if url.endswith("/source") else {"unerwartet": 1}
            _fehler(_call(client, method, url, bad), 422, "validation")


def test_router_is_hidden_and_csrf_protected() -> None:
    from thermoctl.auth.dependencies import csrf_protection
    from thermoctl.web.ui_api_devices import router

    assert csrf_protection in [d.dependency for d in router.dependencies]
    assert not router.include_in_schema


# -- Geräteüberblick und Anlagenbild (R076, R077) ---------------------------------------


def test_device_overview_lists_findings_and_zones(client_als: Als, world: World, session: Session) -> None:
    world.plug.is_enabled = False
    session.flush()
    body = _call(client_als(ALLES), "GET", "/ui-api/devices").json()
    by_name = {d["name"]: d for d in body["devices"]}
    assert set(by_name) == {"fuehler-a", "fuehler-b", "steckdose", "fenster", "drehknopf"}
    assert by_name["fuehler-a"]["zones"] == ["Wohnen"] and by_name["drehknopf"]["zones"] == ["Wohnen"]
    assert body["without_zone"] == 3
    plug = by_name["steckdose"]
    assert plug["is_fine"] is False and plug["findings"][0]["kind"] == "disabled"
    assert plug["integration"] == "zigbee2mqtt" and plug["last_heard"] is None
    # Auffälliges zuerst: aufsteigend nach Dringlichkeit
    severities = [d["severity"] for d in body["devices"]]
    assert severities == sorted(severities)
    assert body["timezone"] is not None


def test_device_overview_without_settings_has_no_timezone(client_als: Als, session: Session) -> None:
    create_device(session, "einzeln")
    from thermoctl.db.models.operations import Setting

    setting = session.get(Setting, 1)
    if setting is not None:
        session.delete(setting)
        session.flush()
    body = _call(client_als(ALLES), "GET", "/ui-api/devices").json()
    assert body["timezone"] is None and body["devices"][0]["name"] == "einzeln"


def test_plant_shows_wiring_and_defects_per_visible_zone(client_als: Als, world: World, session: Session) -> None:
    _assign(session, world.a, world.plug, "actuator")
    body = _call(client_als(ALLES), "GET", "/ui-api/plant").json()
    assert body["bridge"] is None
    zones = {z["name"]: z for z in body["zones"]}
    wohnen = zones["Wohnen"]
    assert wohnen["temperature_source"]["name"] == "fuehler-a"
    assert [d["name"] for d in wohnen["actuators"]] == ["steckdose"]
    assert wohnen["actuators"][0]["assignment_id"] is not None
    assert wohnen["defects"] == []
    assert any("keine Messquelle" in d for d in zones["Bad"]["defects"])
    assert {d["name"] for d in body["without_zone"]} == {"fuehler-b", "fenster"}
    # Zonenfilter: nur sichtbare Zonen
    only = _call(client_als([("device.read", None), ("zone.read", world.b.id)]), "GET", "/ui-api/plant").json()
    assert [z["name"] for z in only["zones"]] == ["Bad"]


# -- Zuordnungskarte (R078-R080) --------------------------------------------------------


def test_map_is_a_real_model_with_ports_directions_and_reasons(client_als: Als, world: World, session: Session) -> None:
    valve = _valve(session, "ventil")
    _assign(session, world.a, valve, "actuator", MODE_SELF_REGULATING)
    body = _call(client_als(ALLES), "GET", "/ui-api/devices/map").json()
    assert body["may_edit"] is True
    nodes = {n["id"]: n for n in body["nodes"]}
    zone_node = nodes[f"zone-{world.a.id}"]
    slots = {p["slot"]: p for p in zone_node["ports"]}
    assert {"temperature_source", "window_contact", "actuator", "setpoint", "room_temperature"} <= set(slots)
    assert slots["setpoint"]["direction"] == "out" and slots["temperature_source"]["direction"] == "in"
    assert slots["setpoint"]["editable"] is True
    device_ports = {p["id"]: p for p in nodes[f"dev-{valve.id}"]["ports"]}
    assert device_ports[property_port_id(valve.id, SETPOINT_PROPERTY)]["valve_channel"] == "setpoint"
    assert device_ports[property_port_id(valve.id, ACTUAL)]["valve_channel"] == "actual"
    assert device_ports[f"dev-{valve.id}-thermostat"]["direction"] == "in"
    # Mehrfachsensoren / Fensterkontakte: der Eingang der Zone nimmt mehrere Linien, die Messquelle nur eine
    assert slots["temperature_source"]["single"] is True and slots["window_contact"]["single"] is False
    # Linien: Messquelle und die beiden Ventillinien
    drawn = {(c["slot"], c["device_id"]) for c in body["connections"]}
    assert ("temperature_source", world.sensor.id) in drawn
    assert ("setpoint", valve.id) in drawn and ("room_temperature", valve.id) in drawn
    titles = [c["title"] for c in body["connections"] if c["slot"] == "setpoint"]
    assert titles and "→" in titles[0]
    # Ventilbetriebsarten und Zustand
    modes = {m["code"]: m for m in body["valve_modes"]}
    assert set(modes) == {MODE_CONTROLLED, MODE_SELF_REGULATING, MODE_READING_ONLY}
    assert modes[MODE_SELF_REGULATING]["lines"] == ["room_temperature", "setpoint"]
    assert modes[MODE_READING_ONLY]["lines"] == ["room_temperature"]
    assert modes[MODE_CONTROLLED]["lines"] == ["actuator"]
    assert [(v["device_id"], v["mode"]) for v in body["valve_assignments"]] == [(valve.id, MODE_SELF_REGULATING)]
    assert body["reasons"] and isinstance(body["rejections"], dict) and body["same_side"]
    assert body["technical_reason"] and body["single_reason"] and body["kind_labels"]


def test_map_marks_zones_without_manage_as_read_only(client_als: Als, world: World) -> None:
    client = client_als([("device.read", world.a.id), ("device.manage", world.b.id), ("zone.read", world.a.id)])
    body = _call(client, "GET", "/ui-api/devices/map").json()
    zone_ports = [p for n in body["nodes"] if n["kind"] == "zone" for p in n["ports"]]
    assert zone_ports and all(p["editable"] is False for p in zone_ports)
    assert body["may_edit"] is False
    # Fremde Zone kommt nicht vor.
    assert all(n["id"] != f"zone-{world.b.id}" for n in body["nodes"])


def test_map_hides_the_names_of_invisible_zones_for_exclusive_actuators(client_als: Als, world: World, session: Session) -> None:
    _assign(session, world.b, world.plug, "actuator")
    _assign(session, world.a, world.plug, "window_contact")  # sichtbar nur, weil auch in A
    client = client_als([("device.read", world.a.id), ("zone.read", world.a.id)])
    body = _call(client, "GET", "/ui-api/devices/map").json()
    assert "Bad" not in str(body)
    plug_ports = next(n for n in body["nodes"] if n["id"] == f"dev-{world.plug.id}")["ports"]
    assert plug_ports[0]["actuator_elsewhere"] == ["einer anderen Zone"]


def _only_in_b(session: Session, world: World) -> Device:
    """Geräte, die nur in Raum B hängen: Steckdose (Aktor), Fenster, ein Bediengerät, dazu
    eine Messquelle für B. Raum A bekommt nichts davon."""
    _assign(session, world.b, world.plug, "actuator")
    _assign(session, world.b, world.contact, "window_contact")
    world.b.temperature_source_device_id = world.sensor2.id
    ctrl_b = _with(session, create_device(session, "schalter-bad"), "action")
    _prop(session, ctrl_b, "external_temperature", readable=True)
    _assign(session, world.b, ctrl_b, "controller")
    session.flush()
    return ctrl_b


SECRETS_OF_B = ("steckdose", "fenster", "fuehler-b", "schalter-bad", "Bad")


def test_a_room_a_reader_finds_no_device_of_room_b_anywhere(client_als: Als, world: World, session: Session) -> None:
    ctrl_b = _only_in_b(session, world)
    client = _manage(client_als, world.a)
    for url in ("/ui-api/devices/map", f"/ui-api/zones/{world.a.id}/devices", "/ui-api/controllers"):
        text = str(_call(client, "GET", url).json())
        for secret in SECRETS_OF_B:
            assert secret not in text, (url, secret)
    # Was in Raum A hängt oder nirgends, bleibt sichtbar.
    body = _call(client, "GET", "/ui-api/devices/map").json()
    ids = {n["id"] for n in body["nodes"]}
    assert f"dev-{world.ctrl.id}" in ids and f"dev-{world.sensor.id}" in ids
    assert f"dev-{ctrl_b.id}" not in ids
    kept = {p["id"] for n in body["nodes"] for p in n["ports"]}
    assert set(body["rejections"]) <= kept
    assert all(set(rejected) <= kept for rejected in body["rejections"].values())
    assert all(0 <= i < len(body["reasons"]) for r in body["rejections"].values() for i in r.values())
    # Ein verborgener Anschluss lässt sich auch nicht über die Prüfung erfragen.
    hidden = _call(client, "GET", "/ui-api/devices/map/check", params={"from_id": f"dev-{world.sensor2.id}-temperature", "to_id": zone_port_id(world.a.id, "temperature_source")})
    _fehler(hidden, 404, "not_found")


def test_plant_wide_device_read_still_sees_everything(client_als: Als, world: World, session: Session) -> None:
    _only_in_b(session, world)
    text = str(_call(client_als(ALLES), "GET", "/ui-api/devices/map").json())
    for secret in SECRETS_OF_B:
        assert secret in text
    assert "schalter-bad" in str(_call(client_als(ALLES), "GET", "/ui-api/controllers").json())


def test_check_returns_the_reason_of_the_domain(client_als: Als, world: World, session: Session) -> None:
    valve = _valve(session, "ventil")
    client = client_als(ALLES)
    ok = _call(client, "GET", "/ui-api/devices/map/check", params={"from_id": f"dev-{world.sensor2.id}-temperature", "to_id": zone_port_id(world.b.id, "temperature_source")}).json()
    assert ok["ok"] is True and ok["reason"] is None
    assert ok["out_port"] == f"dev-{world.sensor2.id}-temperature"
    assert ok["in_port"] == zone_port_id(world.b.id, "temperature_source")
    # Sollwert allein ergibt keine Betriebsart: Grund kommt aus der Domäne.
    no = _call(client, "GET", "/ui-api/devices/map/check", params={"from_id": zone_port_id(world.a.id, "setpoint"), "to_id": property_port_id(valve.id, SETPOINT_PROPERTY)}).json()
    assert no["ok"] is False and "Raumtemperatur" in no["reason"]
    assert no["out_port"] is None and no["in_port"] is None
    _fehler(_call(client, "GET", "/ui-api/devices/map/check", params={"from_id": "nope", "to_id": "nada"}), 404, "not_found")


def test_connect_sets_source_window_and_actuator_and_disconnect_removes_them(client_als: Als, world: World, session: Session) -> None:
    client = _manage(client_als, world.a, world.b)

    def connect(first: str, second: str) -> Any:
        return _call(client, "POST", "/ui-api/devices/map/connect", {"from_id": first, "to_id": second})

    # Mehrfachsensoren: Messquelle der Zone b (eine Linie) ...
    assert connect(f"dev-{world.sensor2.id}-temperature", zone_port_id(world.b.id, "temperature_source")).json() == {"ok": True, "mode": None}
    session.expire_all()
    assert session.get(Zone, world.b.id).temperature_source_device_id == world.sensor2.id  # type: ignore[union-attr]
    # ... Fensterkontakt (Zone zuerst, Reihenfolge egal) und Heizbefehl
    assert connect(zone_port_id(world.b.id, "window_contact"), f"dev-{world.contact.id}-contact").status_code == 200
    assert connect(zone_port_id(world.b.id, "actuator"), f"dev-{world.plug.id}-switch").json()["mode"] is None
    assert len(_rows(session, world.b, world.contact)) == 1 and len(_rows(session, world.b, world.plug)) == 1
    audit = session.scalars(select(AuditEvent.object_type)).all()
    assert audit.count("zone_device") >= 2
    # Doppelt: abgelehnt, nichts doppelt gespeichert
    again = _fehler(connect(zone_port_id(world.b.id, "window_contact"), f"dev-{world.contact.id}-contact"), 422, "connection_rejected")
    assert again["detail"]
    assert len(_rows(session, world.b, world.contact)) == 1

    def disconnect(connection_id: str) -> Any:
        return _call(client, "POST", "/ui-api/devices/map/disconnect", {"connection_id": connection_id})

    gone = disconnect(f"conn-{world.b.id}-window_contact-{world.contact.id}")
    assert gone.status_code == 200 and "Verbindung gelöst" in gone.json()["message"]
    assert _rows(session, world.b, world.contact) == []
    assert disconnect(f"conn-{world.b.id}-temperature_source-{world.sensor2.id}").json()["ok"] is True
    session.expire_all()
    assert session.get(Zone, world.b.id).temperature_source_device_id is None  # type: ignore[union-attr]
    plug_gone = disconnect(f"conn-{world.b.id}-actuator-{world.plug.id}").json()
    assert "behält seinen letzten Zustand" in plug_gone["message"] and _rows(session, world.b, world.plug) == []


def test_valve_lines_decide_the_mode_and_invalid_combinations_say_why(client_als: Als, world: World, session: Session) -> None:
    valve = _valve(session, "ventil")
    client = _manage(client_als, world.a)
    z = lambda slot: zone_port_id(world.a.id, slot)  # noqa: E731

    def connect(first: str, second: str) -> Any:
        return _call(client, "POST", "/ui-api/devices/map/connect", {"from_id": first, "to_id": second})

    def mode() -> str | None:
        rows = _rows(session, world.a, valve)
        return rows[0].thermostat_mode if rows else None

    # Nur Sollwert: keine Betriebsart, nichts gespeichert, Grund maschinenlesbar
    refused = _fehler(connect(property_port_id(valve.id, SETPOINT_PROPERTY), z("setpoint")), 422, "connection_rejected")
    assert "zusammen mit der Raumtemperatur" in refused["detail"] and mode() is None
    # Raumtemperatur -> reading_only; plus Sollwert -> self_regulating
    assert connect(z("room_temperature"), property_port_id(valve.id, ACTUAL)).json() == {"ok": True, "mode": MODE_READING_ONLY}
    assert mode() == MODE_READING_ONLY
    assert connect(z("setpoint"), property_port_id(valve.id, SETPOINT_PROPERTY)).json()["mode"] == MODE_SELF_REGULATING
    assert mode() == MODE_SELF_REGULATING
    # Heizbefehl neben Regelung schließt sich aus
    both = _fehler(connect(z("actuator"), f"dev-{valve.id}-thermostat"), 422, "connection_rejected")
    assert both["detail"]
    # Raumtemperatur lösen, solange Sollwert steht: keine Betriebsart
    cut = _call(client, "POST", "/ui-api/devices/map/disconnect", {"connection_id": f"conn-{world.a.id}-room_temperature-{valve.id}"})
    assert _fehler(cut, 422, "no_valve_mode")["detail"].startswith("Der Sollwert")
    assert mode() == MODE_SELF_REGULATING
    # Sollwert lösen: zurück auf reading_only, mit Hinweis
    step = _call(client, "POST", "/ui-api/devices/map/disconnect", {"connection_id": f"conn-{world.a.id}-setpoint-{valve.id}"}).json()
    assert step["mode"] == MODE_READING_ONLY and "Sollwert am Gerät bleibt" in step["message"]
    assert mode() == MODE_READING_ONLY


def test_connect_unknown_port_foreign_zone_and_two_devices(client_als: Als, world: World, session: Session) -> None:
    client = _manage(client_als, world.a)
    url = "/ui-api/devices/map/connect"
    _fehler(_call(client, "POST", url, {"from_id": "x", "to_id": "y"}), 404, "not_found")
    # Zone b ist für diesen Principal nicht bearbeitbar: Anschluss unbekannt
    _fehler(_call(client, "POST", url, {"from_id": zone_port_id(world.b.id, "actuator"), "to_id": f"dev-{world.plug.id}-switch"}), 404, "not_found")
    two = _fehler(_call(client, "POST", url, {"from_id": f"dev-{world.sensor.id}-temperature", "to_id": f"dev-{world.plug.id}-switch"}), 422, "connection_rejected")
    assert two["detail"]
    _fehler(_call(client, "POST", "/ui-api/devices/map/disconnect", {"connection_id": f"conn-{world.b.id}-actuator-{world.plug.id}"}), 404, "not_found")
    assert session.query(ZoneDevice).count() == 1  # nur das Bediengerät aus dem Aufbau


def test_second_zone_for_an_actuator_is_refused(client_als: Als, world: World, session: Session) -> None:
    _assign(session, world.a, world.plug, "actuator")
    client = _manage(client_als, world.a, world.b)
    refused = _fehler(
        _call(client, "POST", "/ui-api/devices/map/connect", {"from_id": zone_port_id(world.b.id, "actuator"), "to_id": f"dev-{world.plug.id}-switch"}),
        422, "connection_rejected",
    )
    assert "nur eine Zone heizen" in refused["detail"]
    assert _rows(session, world.b, world.plug) == []


def test_concurrent_mode_change_is_a_409_conflict(client_als: Als, world: World, session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    from thermoctl.web.shared import mapping_map as mapping_map_shared

    valve = _valve(session, "ventil")
    row = _assign(session, world.a, valve, "actuator", MODE_READING_ONLY)
    session.commit()
    client = _manage(client_als, world.a)
    original = mapping_map_shared.editable_graph

    def graph_then_change(*args: Any, **kwargs: Any) -> Any:
        result = original(*args, **kwargs)
        concurrent = session.get(ZoneDevice, row.id)
        assert concurrent is not None
        concurrent.thermostat_mode = MODE_CONTROLLED
        session.commit()
        return result

    monkeypatch.setattr(mapping_map_shared, "editable_graph", graph_then_change)
    refused = _fehler(
        _call(client, "POST", "/ui-api/devices/map/connect", {"from_id": zone_port_id(world.a.id, "setpoint"), "to_id": property_port_id(valve.id, SETPOINT_PROPERTY)}),
        409, "stale",
    )
    assert "inzwischen geändert" in refused["detail"]
    assert _rows(session, world.a, valve)[0].thermostat_mode == MODE_CONTROLLED


# -- Zuordnung je Raum (R081-R087) ------------------------------------------------------


def test_zone_devices_read_model(client_als: Als, world: World, session: Session) -> None:
    valve = _valve(session, "ventil")
    _assign(session, world.a, valve, "actuator", MODE_READING_ONLY)
    body = _call(_manage(client_als, world.a), "GET", f"/ui-api/zones/{world.a.id}/devices").json()
    assert body["zone"] == {"id": world.a.id, "name": "Wohnen"} and body["may_edit"] is True
    assert body["temperature_source"] == {"id": world.sensor.id, "name": "fuehler-a"}
    actuator = next(a for a in body["assignments"] if a["role"]["code"] == "actuator")
    assert actuator["thermostat_mode"] == MODE_READING_ONLY and actuator["is_thermostat"] is True
    assert actuator["thermostat_mode_label"] == "Nur Raumtemperatur melden"
    assert {r["code"] for r in body["roles"]} == {"actuator", "window_contact", "controller"}
    assert sorted(body["required_capabilities"]["actuator"]) == ["switch", "thermostat"]
    assert [m["code"] for m in body["thermostat_modes"]] == [MODE_CONTROLLED, MODE_SELF_REGULATING, MODE_READING_ONLY]
    assert [c["name"] for c in body["picture"]["actuators"]] == ["ventil"]
    assert body["picture"]["defects"] == []
    assert body["controllers"][0]["device"]["name"] == "drehknopf"
    assert {c["code"] for c in body["commands"]} == {c for c, _ in CONTROLLER_COMMANDS}
    assert {d["name"] for d in body["devices"]} >= {"steckdose", "ventil"}


def test_assign_several_sensors_and_windows_but_one_actuator_zone(client_als: Als, world: World, session: Session) -> None:
    client = _manage(client_als, world.a, world.b)
    url = f"/ui-api/zones/{world.a.id}/devices/assign"
    second_window = _with(session, create_device(session, "fenster2"), "contact")
    for window in (world.contact, second_window):
        out = _call(client, "POST", url, {"device_id": window.id, "role_code": "window_contact"})
        assert out.status_code == 200, out.text
    assert out.json()["picture"]["window_contacts"].__len__() == 2
    assert _call(client, "POST", url, {"device_id": world.plug.id, "role_code": "actuator"}).status_code == 200
    assert len(_rows(session, world.a, world.plug)) == 1
    assert session.scalar(select(AuditEvent.id).where(AuditEvent.object_type == "zone_device")) is not None
    # dieselbe Rolle nochmal -> 409
    _fehler(_call(client, "POST", url, {"device_id": world.plug.id, "role_code": "actuator"}), 409, "already_assigned")
    # zweite Zone -> 409, Name der sichtbaren Zone steht drin
    elsewhere = _fehler(
        _call(client, "POST", f"/ui-api/zones/{world.b.id}/devices/assign", {"device_id": world.plug.id, "role_code": "actuator"}),
        409, "actuator_in_other_zone",
    )
    assert "Wohnen" in elsewhere["detail"]
    assert _rows(session, world.b, world.plug) == []


def test_actuator_conflict_hides_the_name_of_an_invisible_zone(client_als: Als, world: World, session: Session) -> None:
    _assign(session, world.a, world.plug, "actuator")
    client = _manage(client_als, world.b)
    body = _fehler(
        _call(client, "POST", f"/ui-api/zones/{world.b.id}/devices/assign", {"device_id": world.plug.id, "role_code": "actuator"}),
        409, "actuator_in_other_zone",
    )
    assert "Wohnen" not in body["detail"] and "einer anderen Zone" in body["detail"]


def test_assign_rejects_unknown_role_device_and_missing_capability(client_als: Als, world: World) -> None:
    client = _manage(client_als, world.a)
    url = f"/ui-api/zones/{world.a.id}/devices/assign"
    bad_role = _fehler(_call(client, "POST", url, {"device_id": world.plug.id, "role_code": "quatsch"}), 422, "unknown_role")
    assert bad_role["field"] == "role_code"
    _fehler(_call(client, "POST", url, {"device_id": 99999, "role_code": "actuator"}), 404, "not_found")
    _fehler(_call(client, "POST", url, {"device_id": world.sensor.id, "role_code": "actuator"}), 422, "capability_missing")


def test_detach_removes_the_assignment_only_of_this_zone(client_als: Als, world: World, session: Session) -> None:
    row = _assign(session, world.a, world.plug, "actuator")
    foreign = _assign(session, world.b, world.contact, "window_contact")
    client = _manage(client_als, world.a, world.b)
    url = f"/ui-api/zones/{world.a.id}/devices/detach"
    _fehler(_call(client, "POST", url, {"assignment_id": foreign.id}), 404, "not_found")
    _fehler(_call(client, "POST", url, {"assignment_id": 99999}), 404, "not_found")
    assert len(_rows(session, world.b, world.contact)) == 1
    out = _call(client, "POST", url, {"assignment_id": row.id})
    assert out.status_code == 200 and _rows(session, world.a, world.plug) == []
    assert out.json()["picture"]["actuators"] == []


def test_regulation_switches_the_valve_mode_and_is_audited(client_als: Als, world: World, session: Session) -> None:
    valve = _valve(session, "ventil")
    row = _assign(session, world.a, valve, "actuator")
    plain = _assign(session, world.a, world.plug, "actuator")
    client = _manage(client_als, world.a)
    url = f"/ui-api/zones/{world.a.id}/devices/regulation"
    out = _call(client, "POST", url, {"assignment_id": row.id, "thermostat_mode": MODE_READING_ONLY})
    assert out.status_code == 200
    assert _rows(session, world.a, valve)[0].thermostat_mode == MODE_READING_ONLY
    summaries = session.scalars(select(AuditEvent.summary).where(AuditEvent.object_type == "zone_device")).all()
    assert any("Betriebsart" in s for s in summaries)
    _fehler(_call(client, "POST", url, {"assignment_id": row.id, "thermostat_mode": "quer"}), 422, "unknown_mode")
    _fehler(_call(client, "POST", url, {"assignment_id": 99999, "thermostat_mode": MODE_CONTROLLED}), 404, "not_found")
    _fehler(_call(client, "POST", url, {"assignment_id": plain.id, "thermostat_mode": MODE_SELF_REGULATING}), 422, "capability_missing")
    assert _rows(session, world.a, valve)[0].thermostat_mode == MODE_READING_ONLY


def test_source_can_be_set_replaced_and_cleared(client_als: Als, world: World, session: Session) -> None:
    client = _manage(client_als, world.a)
    url = f"/ui-api/zones/{world.a.id}/devices/source"
    out = _call(client, "POST", url, {"device_id": world.sensor2.id})
    assert out.json()["temperature_source"]["id"] == world.sensor2.id
    session.expire_all()
    assert session.get(Zone, world.a.id).temperature_source_device_id == world.sensor2.id  # type: ignore[union-attr]
    _fehler(_call(client, "POST", url, {"device_id": world.plug.id}), 422, "capability_missing")
    _fehler(_call(client, "POST", url, {"device_id": 99999}), 404, "not_found")
    cleared = _call(client, "POST", url, {"device_id": None})
    assert cleared.json()["temperature_source"] is None
    session.expire_all()
    assert session.get(Zone, world.a.id).temperature_source_device_id is None  # type: ignore[union-attr]


def test_swap_moves_all_roles_and_resets_the_valve_mode(client_als: Als, world: World, session: Session) -> None:
    old = _valve(session, "alt")
    new = _valve(session, "neu")
    _assign(session, world.a, old, "actuator", MODE_READING_ONLY)
    client = _manage(client_als, world.a)
    url = f"/ui-api/zones/{world.a.id}/devices/swap"
    assert _call(client, "POST", url, {"old_device_id": old.id, "new_device_id": new.id}).status_code == 200
    assert _rows(session, world.a, old) == []
    assert _rows(session, world.a, new)[0].thermostat_mode == MODE_CONTROLLED
    # Messquelle tauscht mit
    assert _call(client, "POST", url, {"old_device_id": world.sensor.id, "new_device_id": world.sensor2.id}).status_code == 200
    session.expire_all()
    assert session.get(Zone, world.a.id).temperature_source_device_id == world.sensor2.id  # type: ignore[union-attr]


def test_swap_errors(client_als: Als, world: World, session: Session) -> None:
    other_valve = _valve(session, "woanders")
    _assign(session, world.b, other_valve, "actuator")
    mine = _valve(session, "meins")
    _assign(session, world.a, mine, "actuator")
    client = _manage(client_als, world.a)
    url = f"/ui-api/zones/{world.a.id}/devices/swap"
    same = _fehler(_call(client, "POST", url, {"old_device_id": mine.id, "new_device_id": mine.id}), 422, "swap_invalid")
    assert "verschieden" in same["detail"]
    _fehler(_call(client, "POST", url, {"old_device_id": world.plug.id, "new_device_id": mine.id}), 422, "swap_invalid")
    _fehler(_call(client, "POST", url, {"old_device_id": mine.id, "new_device_id": world.sensor.id}), 422, "capability_missing")
    elsewhere = _fehler(_call(client, "POST", url, {"old_device_id": mine.id, "new_device_id": other_valve.id}), 409, "actuator_in_other_zone")
    assert "Bad" not in elsewhere["detail"] and "einer anderen Zone" in elsewhere["detail"]
    _fehler(_call(client, "POST", url, {"old_device_id": 99999, "new_device_id": mine.id}), 404, "not_found")
    _fehler(_call(client, "POST", url, {"old_device_id": mine.id, "new_device_id": 99999}), 404, "not_found")
    assert len(_rows(session, world.a, mine)) == 1


def test_zone_button_binds_and_deletes_a_binding(client_als: Als, world: World, session: Session) -> None:
    client = _manage(client_als, world.a)
    url = f"/ui-api/zones/{world.a.id}/devices/button"
    out = _call(client, "POST", url, {"device_id": world.ctrl.id, "action_code": "single_plus", "command": "setpoint_up", "step_k": "0,5"})
    assert out.status_code == 200
    binding = out.json()["controllers"][0]["bindings"][0]
    assert binding["action"] == "single_plus" and binding["command_code"] == "setpoint_up" and binding["step_k"] == "0.5"
    stored = session.scalars(select(ControllerBinding)).one()
    assert stored.step_k == Decimal("0.5")
    cleared = _call(client, "POST", url, {"device_id": world.ctrl.id, "action_code": "single_plus"})
    assert cleared.status_code == 200 and session.scalars(select(ControllerBinding)).all() == []


def test_zone_button_is_stricter_than_the_html_view(client_als: Als, world: World, session: Session) -> None:
    other_ctrl = _with(session, create_device(session, "fremder-knopf"), "action")
    _assign(session, world.b, other_ctrl, "controller")
    shared = _with(session, create_device(session, "geteilt"), "action")
    _assign(session, world.a, shared, "controller")
    _assign(session, world.b, shared, "controller")
    client = _manage(client_als, world.a)
    url = f"/ui-api/zones/{world.a.id}/devices/button"
    body = {"action_code": "x", "command": "boost"}
    # kein Bediengerät dieser Zone (auch kein beliebiges Gerät)
    _fehler(_call(client, "POST", url, {"device_id": other_ctrl.id, **body}), 404, "not_found")
    _fehler(_call(client, "POST", url, {"device_id": world.plug.id, **body}), 404, "not_found")
    _fehler(_call(client, "POST", url, {"device_id": 99999, **body}), 404, "not_found")
    # hängt auch in Zone b ohne Recht dort: die Belegung gilt dort ebenfalls
    _fehler(_call(client, "POST", url, {"device_id": shared.id, **body}), 403, "forbidden")
    assert session.scalars(select(ControllerBinding)).all() == []


def test_zone_button_validation(client_als: Als, world: World) -> None:
    client = _manage(client_als, world.a)
    url = f"/ui-api/zones/{world.a.id}/devices/button"
    empty = _fehler(_call(client, "POST", url, {"device_id": world.ctrl.id, "action_code": "  "}), 422, "validation")
    assert empty["field"] == "action_code"
    nan = _fehler(_call(client, "POST", url, {"device_id": world.ctrl.id, "action_code": "x", "command": "boost", "step_k": "viel"}), 422, "validation")
    assert nan["field"] == "step_k"
    _fehler(_call(client, "POST", url, {"device_id": world.ctrl.id, "action_code": "x", "command": "boost", "step_k": "NaN"}), 422, "validation")
    _fehler(_call(client, "POST", url, {"device_id": world.ctrl.id, "action_code": "x", "command": "gibtsnicht"}), 422, "binding_invalid")
    _fehler(_call(client, "POST", url, {"device_id": world.ctrl.id, "action_code": "x", "command": "boost", "step_k": "0.25"}), 422, "binding_invalid")
    _fehler(_call(client, "POST", url, {"device_id": world.ctrl.id, "action_code": "x", "command": "boost", "step_k": "-1"}), 422, "binding_invalid")


# -- Datenfluss (R091, R092) ------------------------------------------------------------


def test_data_flow_is_structured_and_filtered_by_zone_manage(client_als: Als, world: World, session: Session) -> None:
    valve = _valve(session, "ventil")
    _assign(session, world.a, valve, "actuator", MODE_SELF_REGULATING)
    _assign(session, world.a, world.contact, "window_contact")
    one = _call(_manage(client_als, world.a), "GET", f"/ui-api/zones/{world.a.id}/data-flow").json()
    ids = {n["id"] for n in one["nodes"]}
    assert {"zone"} <= {n["kind"] for n in one["nodes"]}
    assert one["edges"] and all(e["source"] in ids and e["target"] in ids for e in one["edges"])
    assert {e["kind"] for e in one["edges"]} >= {"actual", "setpoint"}
    assert all(e["text"] and isinstance(e["dashed"], bool) for e in one["edges"])
    assert one["mermaid"].strip() and one["without_devices"] is False
    assert one["zone"] == {"id": world.a.id, "name": "Wohnen"}
    overview = _call(_manage(client_als, world.a), "GET", "/ui-api/zones/data-flow").json()
    assert [z["zone"]["name"] for z in overview["zones"]] == ["Wohnen"]
    bare = _call(client_als(ALLES), "GET", "/ui-api/zones/data-flow").json()
    by_name = {z["zone"]["name"]: z for z in bare["zones"]}
    assert by_name["Bad"]["without_devices"] is True
    # device.read allein genügt nicht (wie die HTML-Seite: zone.manage)
    _fehler(_call(client_als([("device.read", world.a.id), ("zone.read", world.a.id)]), "GET", f"/ui-api/zones/{world.a.id}/data-flow"), 404, "not_found")


# -- Bediengeräte (R088-R090) -----------------------------------------------------------


def test_controllers_read_model_with_properties_channels_and_seen_actions(client_als: Als, world: World, session: Session) -> None:
    prop = session.scalars(select(DeviceProperty).where(DeviceProperty.device_id == world.ctrl.id)).one()
    prop.value_type = "enum"
    session.add(ControllerBinding(device_id=world.ctrl.id, action_code="hold", command_id=session.scalars(select(ControllerCommand)).first().id))  # type: ignore[union-attr]
    from thermoctl.db.models.device import DevicePropertyValue

    session.add(DevicePropertyValue(property_id=prop.id, value="a", sort_order=0))
    kind = session.scalars(select(ChannelKind).where(ChannelKind.code == "zone_temperature")).one()
    session.add(ControllerChannel(device_id=world.ctrl.id, property_name=prop.name, direction="read", kind_id=kind.id, zone_id=world.a.id))
    session.flush()
    body = _call(_manage(client_als, world.a), "GET", "/ui-api/controllers").json()
    ctrl = body["controllers"][0]
    assert ctrl["name"] == "drehknopf" and ctrl["manageable"] is True
    assert ctrl["zones"] == [{"id": world.a.id, "name": "Wohnen"}]
    assert ctrl["properties"][0]["name"] == "external_temperature" and ctrl["properties"][0]["values"] == ["a"]
    assert ctrl["properties"][0]["min_value"] == "0.0000" or ctrl["properties"][0]["min_value"] == "0"
    assert ctrl["channels"][0]["kind"] == "zone_temperature" and ctrl["channels"][0]["zone_id"] == world.a.id
    assert ctrl["channels"][0]["fixed_number"] is None
    assert ctrl["bindings"][0]["action"] == "hold" and ctrl["bindings"][0]["last_seen"] is None
    assert body["zones"] == [{"id": world.a.id, "name": "Wohnen", "manageable": True}]
    assert "fuehler-a" in [d["name"] for d in body["source_devices"]]
    assert {k["code"] for k in body["kinds"]} == {c for c, _ in CHANNEL_KINDS}
    assert body["commands"]


def test_controllers_report_the_last_value_with_its_age(client_als: Als, world: World, session: Session) -> None:
    from datetime import timedelta

    from thermoctl.db.base import utcnow

    number = session.scalars(select(DeviceProperty).where(DeviceProperty.device_id == world.ctrl.id)).one()
    number.last_value_number = Decimal("21.5000")
    number.last_value_at = utcnow() - timedelta(minutes=5)
    text = _prop(session, world.ctrl, "action", writable=False)
    text.last_value_text = "hold"
    text.last_value_at = utcnow() + timedelta(minutes=3)  # Gerätezeit geht vor: Alter nie negativ
    silent = _prop(session, world.ctrl, "linkquality", writable=False)
    whole = _prop(session, world.ctrl, "battery", writable=False)
    whole.last_value_number = Decimal("100.0000")
    whole.last_value_at = utcnow()
    session.flush()
    body = _call(_manage(client_als, world.a), "GET", "/ui-api/controllers").json()
    by_name = {p["name"]: p for p in body["controllers"][0]["properties"]}
    assert body["timezone"] == "Europe/Berlin"
    assert by_name["external_temperature"]["last_value"] == "21.5"
    assert 299 <= by_name["external_temperature"]["last_value_age_seconds"] <= 330
    assert by_name["external_temperature"]["last_value_at"].endswith("+00:00")
    assert by_name["action"]["last_value"] == "hold" and by_name["action"]["last_value_age_seconds"] == 0
    assert by_name["battery"]["last_value"] == "100"
    assert by_name[silent.name]["last_value"] is None
    assert by_name[silent.name]["last_value_at"] is None and by_name[silent.name]["last_value_age_seconds"] is None


def test_controllers_in_hidden_zones_show_no_values(client_als: Als, world: World, session: Session) -> None:
    other = create_device(session, "knopf-bad")
    prop = _prop(session, other, "secret", writable=False)
    prop.last_value_text = "geheim"
    _assign(session, world.b, other, "controller")
    session.flush()
    body = _call(_manage(client_als, world.a), "GET", "/ui-api/controllers").json()
    assert [c["name"] for c in body["controllers"]] == ["drehknopf"]
    assert "geheim" not in str(body) and "knopf-bad" not in str(body)


def test_controllers_hide_zones_the_viewer_cannot_read(client_als: Als, world: World, session: Session) -> None:
    _assign(session, world.b, world.ctrl, "controller")
    read_only = client_als([("device.read", world.a.id), ("zone.read", world.a.id)])
    ctrl = _call(read_only, "GET", "/ui-api/controllers").json()["controllers"][0]
    assert ctrl["manageable"] is False and ctrl["zones"] == [{"id": world.a.id, "name": "Wohnen"}]


def test_channel_writes_and_validates(client_als: Als, world: World, session: Session) -> None:
    client = _manage(client_als, world.a)
    url = "/ui-api/controllers/channel"
    base = {"device_id": world.ctrl.id, "property_name": "external_temperature"}
    out = _call(client, "POST", url, {**base, "direction": "write", "kind": "zone_temperature", "zone_id": world.a.id})
    assert out.status_code == 200
    channel = session.scalars(select(ControllerChannel)).one()
    assert (channel.direction, channel.zone_id) == ("write", world.a.id)
    assert out.json()["controllers"][0]["channels"][0]["direction"] == "write"
    fixed = _call(client, "POST", url, {**base, "direction": "write", "kind": "fixed", "fixed_number": "21,5", "zone_id": world.a.id, "fixed_text": " "})
    assert fixed.status_code == 200
    session.expire_all()
    assert session.scalars(select(ControllerChannel)).one().fixed_number == Decimal("21.5")
    sourced = _call(client, "POST", url, {**base, "direction": "write", "kind": "sensor_temperature", "source_device_id": world.sensor.id})
    assert sourced.status_code == 200
    _fehler(_call(client, "POST", url, {**base, "direction": "quer", "kind": "fixed"}), 422, "channel_invalid")
    _fehler(_call(client, "POST", url, {**base, "direction": "write", "kind": "fixed", "fixed_number": "99"}), 422, "channel_invalid")
    bad = _fehler(_call(client, "POST", url, {**base, "direction": "write", "kind": "fixed", "fixed_number": "viel"}), 422, "validation")
    assert bad["field"] == "fixed_number"
    _fehler(_call(client, "POST", url, {"device_id": 99999, "property_name": "p", "direction": "read", "kind": "fixed"}), 404, "not_found")
    assert session.query(ControllerChannel).count() == 1


def test_channel_cannot_target_a_zone_or_device_beyond_the_principals_rights(client_als: Als, world: World, session: Session) -> None:
    base = {"device_id": world.ctrl.id, "property_name": "external_temperature", "direction": "write"}
    url = "/ui-api/controllers/channel"
    # device.manage nur für Zone a: Ziel Zone b wäre fremde Steuerung
    client = client_als([("device.read", world.a.id), ("device.manage", world.a.id), ("zone.read", world.a.id)])
    _fehler(_call(client, "POST", url, {**base, "kind": "zone_setpoint", "zone_id": world.b.id}), 403, "forbidden")
    # Quellgerät aus nicht lesbarer Zone: derselbe 404 wie unbekannt
    hidden = _fehler(_call(client, "POST", url, {**base, "direction": "read", "kind": "sensor_temperature", "source_device_id": world.sensor2.id}), 404, "not_found")
    unknown = _fehler(_call(client, "POST", url, {**base, "direction": "read", "kind": "sensor_temperature", "source_device_id": 99999}), 404, "not_found")
    assert hidden == unknown
    # Bediengerät in einer Zone ohne device.manage: 404
    reader = client_als([("device.read", world.a.id), ("zone.read", world.a.id)])
    _fehler(_call(reader, "POST", url, {**base, "kind": "zone_temperature", "zone_id": world.a.id}), 404, "not_found")
    assert session.query(ControllerChannel).count() == 0


def test_controller_button_checks_every_zone_the_device_hangs_in(client_als: Als, world: World, session: Session) -> None:
    url = "/ui-api/controllers/button"
    body = {"device_id": world.ctrl.id, "action_code": "single_plus", "command": "setpoint_up", "step_k": "1"}
    out = _call(_manage(client_als, world.a), "POST", url, body)
    assert out.status_code == 200 and out.json()["controllers"][0]["bindings"][0]["step_k"] in ("1", "1.0")
    assert session.scalars(select(ControllerBinding)).one().action_code == "single_plus"
    _assign(session, world.b, world.ctrl, "controller")
    before = session.scalars(select(ControllerBinding)).one().command_id
    _fehler(_call(_manage(client_als, world.a), "POST", url, {**body, "command": "boost"}), 403, "forbidden")
    session.expire_all()
    assert session.scalars(select(ControllerBinding)).one().command_id == before
    _fehler(_call(_manage(client_als, world.a, world.b), "POST", url, {**body, "action_code": ""}), 422, "validation")
    _fehler(_call(_manage(client_als, world.a, world.b), "POST", url, {**body, "command": "nix"}), 422, "binding_invalid")
    _fehler(_call(_manage(client_als, world.a, world.b), "POST", url, {**body, "device_id": 99999}), 404, "not_found")


# -- Schaltprotokoll (R072) -------------------------------------------------------------


def test_device_commands_list_filter_and_page(client_als: Als, world: World, session: Session) -> None:
    create_all_command_outcomes(session)
    valve = _valve(session, "ventil")
    for index in range(52):
        create_device_command(session, world.a, valve, at=datetime(2026, 8, 29, 8, index % 60), outcome_code="executed")
    create_device_command(session, world.b, world.plug, at=datetime(2026, 8, 30, 9, 0), source_code="web")
    client = client_als(ALLES)
    first = _call(client, "GET", "/ui-api/device-commands").json()
    assert first["page"] == 1 and first["page_size"] == 50 and first["has_more"] is True
    assert len(first["entries"]) == 50
    assert first["entries"][0]["zone_name"] == "Bad" and first["entries"][0]["sent_at"].endswith("+00:00")
    assert first["entries"][0]["kind"] == "befehl"
    assert set(first["zone_names"]) >= {"Wohnen", "Bad"} and first["outcomes"]
    assert any(k.startswith("absenkung_") for k in first["outcome_labels"]) and first["source_labels"]["regelung"]
    second = _call(client, "GET", "/ui-api/device-commands", params={"page": "2"}).json()
    assert second["has_more"] is False and len(second["entries"]) == 3
    only_bad = _call(client, "GET", "/ui-api/device-commands", params={"zone": "Bad"}).json()
    assert [e["zone_name"] for e in only_bad["entries"]] == ["Bad"]
    day = _call(client, "GET", "/ui-api/device-commands", params={"from_date": "2026-08-30", "to_date": "2026-08-30"}).json()
    assert [e["zone_name"] for e in day["entries"]] == ["Bad"]
    none = _call(client, "GET", "/ui-api/device-commands", params={"outcome": "gibtsnicht"}).json()
    assert none["entries"] == []


def test_device_commands_validate_the_filters_and_need_audit_read(client_als: Als, world: World) -> None:
    client = client_als(ALLES)
    url = "/ui-api/device-commands"
    assert _fehler(_call(client, "GET", url, params={"from_date": "morgen"}), 422, "validation")["field"] == "from_date"
    assert _fehler(_call(client, "GET", url, params={"from_date": "2026-09-02", "to_date": "2026-09-01"}), 422, "validation")["field"] == "to_date"
    assert _fehler(_call(client, "GET", url, params={"page": "x"}), 422, "validation")["field"] == "page"
    no_audit = client_als([("device.read", None), ("zone.read", None)])
    _fehler(_call(no_audit, "GET", url), 403, "forbidden")


# -- Mehrfachzuordnung: ein Fühler, zwei Zonen; Gerät-zu-Gerät (Lücken der Altprüfung) ----------


def test_one_sensor_is_the_temperature_source_of_two_zones_and_both_zones_read_it(
    client_als: Als, world: World, session: Session
) -> None:
    for code in ("ok", "veraltet", "keine_quelle"):
        sensor_status_of(session, code)
    shared = _with(session, create_device(session, "gemeinsamer-fuehler"), "temperature")
    world.a.temperature_source_device_id = None  # beide Zonen beginnen ohne Messquelle
    session.flush()
    client = _manage(client_als, world.a, world.b)
    out = f"dev-{shared.id}-temperature"
    for zone in (world.a, world.b):
        done = _call(client, "POST", "/ui-api/devices/map/connect", {"from_id": out, "to_id": zone_port_id(zone.id, "temperature_source")})
        assert done.status_code == 200 and done.json() == {"ok": True, "mode": None}
    session.expire_all()
    assert session.get(Zone, world.a.id).temperature_source_device_id == shared.id  # type: ignore[union-attr]
    assert session.get(Zone, world.b.id).temperature_source_device_id == shared.id  # type: ignore[union-attr]

    # Zwei Linien vom selben Ausgang, jede einzeln geführt und lösbar.
    graph = _call(client, "GET", "/ui-api/devices/map").json()
    lines = [c for c in graph["connections"] if c["out_port"] == out]
    assert sorted(c["zone_id"] for c in lines) == sorted([world.a.id, world.b.id])
    assert len({c["id"] for c in lines}) == 2

    # Die Regelkette liest den Messwert für beide Zonen.
    moment = datetime(2026, 8, 29, 7, 0)
    session.add(Measurement(device_id=shared.id, capability_id=capability(session, "temperature").id, value_numeric=Decimal("21.50"), measured_at=moment, received_at=moment))
    session.flush()
    advance_zone_state(session, moment)
    for zone in (world.a, world.b):
        state = session.get(ZoneState, zone.id)
        assert state is not None and state.temperature_c == Decimal("21.50")


def test_device_to_device_fan_out_is_judged_fit_but_never_stored(
    client_als: Als, world: World, session: Session
) -> None:
    displays = []
    for index in range(2):
        display = create_device(session, f"anzeige-{index}")
        _prop(session, display, "ext")
        _assign(session, world.a, display, "controller")
        displays.append(display)
    client = _manage(client_als, world.a)
    out = f"dev-{world.sensor.id}-temperature"
    body = _call(client, "GET", "/ui-api/devices/map").json()
    ports = {p["id"]: p for n in body["nodes"] for p in n["ports"]}
    for display in displays:
        target = property_port_id(display.id, "ext")
        # Ein Ausgang, mehrere Eingänge gleicher Wertart: nicht abgelehnt ...
        assert target not in body["rejections"].get(out, {})
        # ... aber jeder Geräteeingang nimmt nur eine Verbindung an.
        assert ports[target]["single"] is True
    assert body["single_reason"] == "Dieser Eingang nimmt nur eine Verbindung an."
    before = session.query(ZoneDevice).count()
    refused = _fehler(
        _call(client, "POST", "/ui-api/devices/map/connect", {"from_id": out, "to_id": property_port_id(displays[0].id, "ext")}),
        422, "connection_rejected",
    )
    assert "nicht gespeichert" in refused["detail"]
    assert session.query(ZoneDevice).count() == before
