"""Zuordnungskarte (Entwurf): Kompatibilitätsregeln, Graph und Seite."""
# ruff: noqa: E501

from collections.abc import Callable
from decimal import Decimal

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
    Endpoint,
    Target,
    build_mapping_map,
    can_connect,
    slot_target_id,
)

ClientAls = Callable[[list[tuple[str, int | None]]], TestClient]


def _endpoint(capability_code: str, direction: str, quantity: str | None, device_id: int = 1) -> Endpoint:
    return Endpoint(
        id=f"dev-{device_id}-{capability_code}",
        device_id=device_id,
        device_name="G",
        capability=capability_code,
        label=capability_code,
        direction=direction,  # type: ignore[arg-type]
        quantity=quantity,
    )


def _slot(accepts: set[str], direction: str, blocked: str | None = None) -> Target:
    return Target(
        id="zone-1-x", kind="zone_slot", group="Z", label="Stelle", direction=direction,  # type: ignore[arg-type]
        accepts=frozenset(accepts), accepts_text="X", quantity=None, zone_id=1, slot="x",
        blocked_reason=blocked,
    )


def _input(quantity: str | None, device_id: int = 2, blocked: str | None = None) -> Target:
    return Target(
        id="input-2-p", kind="device_input", group="G2", label="p", direction="read",
        accepts=frozenset(), accepts_text="", quantity=quantity, device_id=device_id,
        property_name="p", blocked_reason=blocked,
    )


def test_matching_capability_and_direction_is_accepted() -> None:
    assert can_connect(_endpoint("temperature", "read", "temperature"), _slot({"temperature"}, "read")).ok


def test_a_reading_endpoint_is_refused_by_a_writing_slot_with_a_reason() -> None:
    verdict = can_connect(_endpoint("temperature", "read", "temperature"), _slot({"switch"}, "write"))
    assert not verdict.ok and verdict.reason is not None and "Werte" in verdict.reason


def test_a_writing_endpoint_is_refused_by_a_reading_slot() -> None:
    verdict = can_connect(_endpoint("switch", "write", "binary"), _slot({"temperature"}, "read"))
    assert not verdict.ok and verdict.reason is not None and "Befehle" in verdict.reason


def test_same_direction_but_wrong_capability_is_refused() -> None:
    verdict = can_connect(_endpoint("humidity", "read", "humidity"), _slot({"temperature"}, "read"))
    assert not verdict.ok and verdict.reason is not None and "passt nicht" in verdict.reason


def test_either_capability_of_a_multi_capability_slot_is_accepted() -> None:
    slot = _slot({"switch", "thermostat"}, "write")
    assert can_connect(_endpoint("switch", "write", "binary"), slot).ok
    assert can_connect(_endpoint("thermostat", "write", "temperature"), slot).ok


def test_device_input_needs_the_same_quantity() -> None:
    assert can_connect(_endpoint("temperature", "read", "temperature"), _input("temperature")).ok
    verdict = can_connect(_endpoint("humidity", "read", "humidity"), _input("temperature"))
    assert not verdict.ok and verdict.reason is not None and "Luftfeuchte" in verdict.reason


def test_unknown_quantity_is_never_guessed() -> None:
    assert not can_connect(_endpoint("illuminance", "read", None), _input("temperature")).ok
    assert not can_connect(_endpoint("temperature", "read", "temperature"), _input(None)).ok


def test_a_device_cannot_feed_its_own_input() -> None:
    verdict = can_connect(_endpoint("temperature", "read", "temperature", device_id=2), _input("temperature", 2))
    assert not verdict.ok and verdict.reason is not None and "eigenen" in verdict.reason


def test_a_blocked_target_is_refused_with_its_own_reason() -> None:
    verdict = can_connect(
        _endpoint("temperature", "read", "temperature"), _input("temperature", blocked="gesperrt")
    )
    assert verdict == type(verdict)(False, "gesperrt")


def test_a_writing_endpoint_cannot_feed_a_device_input() -> None:
    assert not can_connect(_endpoint("switch", "write", "binary"), _input("binary")).ok


def _thermostat(session: Session, name: str) -> Device:
    device = create_device(session, name)
    session.add(DeviceCapabilityLink(device_id=device.id, capability_id=capability(session, "thermostat").id))
    session.flush()
    return device


def _sensor(session: Session, name: str) -> Device:
    device = create_device(session, name)
    for code in ("temperature", "humidity", "battery"):
        session.add(DeviceCapabilityLink(device_id=device.id, capability_id=capability(session, code).id))
    session.flush()
    return device


def test_graph_uses_the_domain_slot_table_and_skips_diagnostic_endpoints(session: Session) -> None:
    zone = create_zone(session, "karte")
    sensor = _sensor(session, "fuehler")
    graph = build_mapping_map(session, [zone], {zone.id})
    endpoints = {e.capability for _i, _n, eps in graph.devices for e in eps}
    assert endpoints == {"temperature", "humidity"}
    slots = {t.slot for t in graph.targets if t.kind == "zone_slot"}
    assert slots == {"temperature_source", "actuator", "window_contact"}
    rejected = graph.rejections[f"dev-{sensor.id}-temperature"]
    assert slot_target_id(zone.id, "temperature_source") not in rejected
    assert slot_target_id(zone.id, "actuator") in rejected
    assert slot_target_id(zone.id, "window_contact") in rejected


def test_graph_shows_existing_connections_and_writable_inputs(session: Session) -> None:
    zone = create_zone(session, "karte2")
    sensor = _sensor(session, "fuehler2")
    valve = _thermostat(session, "ventil2")
    zone.temperature_source_device_id = sensor.id
    session.add(ZoneDevice(zone_id=zone.id, device_id=valve.id, device_role_id=role(session, "actuator").id))
    session.add(DeviceProperty(device_id=valve.id, name="external_temperature_input", value_type="numeric",
                               unit="°C", min_value=Decimal("0"), max_value=Decimal("35"),
                               is_readable=True, is_writable=True))
    session.add(DeviceProperty(device_id=valve.id, name="child_lock", value_type="binary",
                               unit=None, is_readable=True, is_writable=True))
    session.flush()
    graph = build_mapping_map(session, [zone], set())
    assert {(c.slot, c.device_id, c.editable) for c in graph.connections} == {
        ("temperature_source", sensor.id, False), ("actuator", valve.id, False)}
    inputs = [t for t in graph.targets if t.kind == "device_input"]
    assert [t.property_name for t in inputs] == ["external_temperature_input"]
    # Ein Ventil, das irgendwo Aktor ist, darf keinen Schreibkanal bekommen (wie bei den Kanälen).
    assert inputs[0].blocked_reason is not None


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


def test_page_needs_a_right_and_is_closed_to_tenants_and_anonymous(
    client_als: ClientAls, tenant_client: ClientAls, client: TestClient, session: Session
) -> None:
    create_settings(session)
    zone = create_zone(session, "zu")
    assert client.get("/devices/map", follow_redirects=False).status_code in (303, 401)
    assert tenant_client([("device.read", zone.id)]).get("/devices/map").status_code == 403
    page = client_als([("zone.read", zone.id)]).get("/devices/map")
    assert "Zu</div>" not in page.text


def test_connect_stores_a_temperature_source_through_the_domain_function(client_als: ClientAls, session: Session) -> None:
    source(session)
    zone = create_zone(session, "speichern")
    sensor = _sensor(session, "fuehler4")
    client = client_als([("device.manage", zone.id), ("device.read", zone.id)])
    response = client.post(
        "/devices/map/connect",
        data={"endpoint_id": f"dev-{sensor.id}-temperature",
              "target_id": slot_target_id(zone.id, "temperature_source")},
        headers=_csrf(client),
    )
    assert response.status_code == 200 and response.json() == {"ok": True}
    session.refresh(zone)
    assert zone.temperature_source_device_id == sensor.id
    assert session.scalar(select(AuditEvent.id).where(AuditEvent.object_type == "zone_temperature_source"))


def test_connect_assigns_an_actuator_and_disconnect_removes_it(client_als: ClientAls, session: Session) -> None:
    source(session)
    zone = create_zone(session, "aktor")
    valve = _thermostat(session, "ventil4")
    role(session, "actuator")
    client = client_als([("device.manage", zone.id), ("device.read", zone.id)])
    head = _csrf(client)
    ok = client.post("/devices/map/connect", headers=head, data={
        "endpoint_id": f"dev-{valve.id}-thermostat", "target_id": slot_target_id(zone.id, "actuator")})
    assert ok.status_code == 200
    assert session.scalar(select(ZoneDevice.id).where(ZoneDevice.zone_id == zone.id)) is not None
    again = client.post("/devices/map/connect", headers=head, data={
        "endpoint_id": f"dev-{valve.id}-thermostat", "target_id": slot_target_id(zone.id, "actuator")})
    assert again.status_code == 409
    gone = client.post("/devices/map/disconnect", headers=head,
                       data={"connection_id": f"conn-{zone.id}-actuator-{valve.id}"})
    assert gone.status_code == 200
    assert session.scalar(select(ZoneDevice.id).where(ZoneDevice.zone_id == zone.id)) is None


def test_an_incompatible_connection_is_refused_and_changes_nothing(client_als: ClientAls, session: Session) -> None:
    source(session)
    zone = create_zone(session, "abgelehnt")
    sensor = _sensor(session, "fuehler5")
    client = client_als([("device.manage", zone.id)])
    response = client.post("/devices/map/connect", headers=_csrf(client), data={
        "endpoint_id": f"dev-{sensor.id}-temperature", "target_id": slot_target_id(zone.id, "actuator")})
    assert response.status_code == 422 and response.json()["ok"] is False and response.json()["reason"]
    assert session.scalar(select(ZoneDevice.id)) is None


def test_device_inputs_and_foreign_zones_and_missing_right_are_refused(client_als: ClientAls, session: Session) -> None:
    source(session)
    mine = create_zone(session, "meine2")
    foreign = create_zone(session, "fremde2")
    sensor = _sensor(session, "fuehler6")
    reader = client_als([("device.read", mine.id)])
    data = {"endpoint_id": f"dev-{sensor.id}-temperature", "target_id": slot_target_id(mine.id, "temperature_source")}
    assert reader.post("/devices/map/connect", headers=_csrf(reader), data=data).status_code == 404
    manager = client_als([("device.manage", mine.id)])
    head = _csrf(manager)
    foreign_data = dict(data, target_id=slot_target_id(foreign.id, "temperature_source"))
    assert manager.post("/devices/map/connect", headers=head, data=foreign_data).status_code == 404
    unknown = dict(data, target_id="input-1-x")
    assert manager.post("/devices/map/connect", headers=head, data=unknown).status_code == 404
    assert manager.post("/devices/map/connect", data=data).status_code in (400, 403)
    session.refresh(mine)
    assert mine.temperature_source_device_id is None


def test_connecting_to_a_device_input_is_not_stored(client_als: ClientAls, session: Session) -> None:
    source(session)
    zone = create_zone(session, "eingang")
    sensor = _sensor(session, "fuehler7")
    display = create_device(session, "anzeige")
    session.add(DeviceProperty(device_id=display.id, name="ext", value_type="numeric", unit="°C",
                               is_readable=True, is_writable=True))
    session.flush()
    client = client_als([("device.manage", zone.id)])
    response = client.post("/devices/map/connect", headers=_csrf(client), data={
        "endpoint_id": f"dev-{sensor.id}-temperature", "target_id": f"input-{display.id}-ext"})
    assert response.status_code == 422 and "noch nicht gespeichert" in response.json()["reason"]


def test_script_is_served_locally_and_registered(client: TestClient) -> None:
    assert client.get("/static/mapping_map.js").status_code == 200
    assert "mapping_map.js" in client.get("/static/page_scripts.js").text
