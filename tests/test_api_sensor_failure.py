"""REST contract for Auftrag 8a: anlagenweites Notbetriebsprofil/-Notsollwert
(`/api/v1/control/sensor-failure-defaults`) and die Zonenüberschreibung inklusive
Ausgleichswerten (`/api/v1/zones/{id}/sensor-failure`).

CSRF ist hier nicht Thema -- REST authentifiziert über Bearer-Token, nicht über
die Sitzungs-Cookie-Formulare, für die `csrf_protection` greift (siehe
`thermoctl/web/control_views.py`'s eigener CSRF-Test für die HTML-Seite).
"""

from collections.abc import Callable
from decimal import Decimal as D

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.helpers import (
    capability,
    create_device,
    create_settings,
    create_zone,
    role,
    source,
    user_with_permissions,
)
from thermoctl.auth.tokens import issue_token
from thermoctl.db.models.device import DeviceCapabilityLink, ZoneDevice
from thermoctl.domain.sensor_failure_policy import ProfileValues, save_profile

POINTS = (
    {"outdoor_c": "-10", "on_seconds": 1200, "off_seconds": 600},
    {"outdoor_c": "0", "on_seconds": 600, "off_seconds": 1200},
    {"outdoor_c": "15", "on_seconds": 0, "off_seconds": 1800},
)
VALUES = ProfileValues(
    "Notbetrieb Vorgabe",
    600,
    1200,
    60,
    2,
    D("1"),
    (),
)


@pytest.fixture
def api_token(session: Session) -> Callable[[list[tuple[str, int | None]]], dict[str, str]]:
    source(session, "web")
    counter = 0

    def create_entry(permissions: list[tuple[str, int | None]]) -> dict[str, str]:
        nonlocal counter
        counter += 1
        user_record = user_with_permissions(session, f"notbetrieb-api-{counter}", permissions)
        _token, plaintext = issue_token(
            session, user_record, f"Notbetrieb {counter}", permissions, None
        )
        return {"Authorization": f"Bearer {plaintext}"}

    return create_entry


def _defaults_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "fixed_on_seconds": 600,
        "fixed_off_seconds": 1200,
        "recovery_seconds": 60,
        "recovery_samples": 2,
        "warm_restart_hysteresis_k": "1",
        "curve_points": list(POINTS),
        "emergency_setpoint_c": "16",
    }
    payload.update(overrides)
    return payload


def test_get_put_round_trip_for_the_plant_wide_profile(
    client: TestClient, session: Session, api_token
) -> None:
    create_settings(session)
    save_profile(session, VALUES)
    head = api_token([("zone.read", None), ("setting.manage", None)])

    before = client.get("/api/v1/control/sensor-failure-defaults", headers=head)
    assert before.status_code == 200
    assert before.json()["fixed_on_seconds"] == 600

    written = client.put(
        "/api/v1/control/sensor-failure-defaults",
        json=_defaults_payload(fixed_on_seconds=900, emergency_setpoint_c="17.5"),
        headers=head,
    )
    assert written.status_code == 200
    assert written.json()["fixed_on_seconds"] == 900
    assert written.json()["emergency_setpoint_c"] == "17.50"
    assert [p["outdoor_c"] for p in written.json()["curve_points"]] == ["-10.00", "0.00", "15.00"]

    reloaded = client.get("/api/v1/control/sensor-failure-defaults", headers=head)
    assert reloaded.json()["fixed_on_seconds"] == 900
    assert reloaded.json()["emergency_setpoint_c"] == "17.50"


def test_reading_only_needs_zone_read(client: TestClient, session: Session, api_token) -> None:
    create_settings(session)
    save_profile(session, VALUES)
    head = api_token([("zone.read", None)])
    assert client.get("/api/v1/control/sensor-failure-defaults", headers=head).status_code == 200


def test_writing_the_defaults_needs_setting_manage(
    client: TestClient, session: Session, api_token
) -> None:
    create_settings(session)
    save_profile(session, VALUES)
    head = api_token([("zone.read", None)])
    response = client.put(
        "/api/v1/control/sensor-failure-defaults", json=_defaults_payload(), headers=head
    )
    assert response.status_code == 403


def test_an_invalid_curve_point_rejects_the_whole_write(
    client: TestClient, session: Session, api_token
) -> None:
    create_settings(session)
    save_profile(session, VALUES)
    head = api_token([("zone.read", None), ("setting.manage", None)])

    bad_points = [
        {"outdoor_c": "-10", "on_seconds": 1200, "off_seconds": 600},
        {"outdoor_c": "0", "on_seconds": 600, "off_seconds": 1200},
        # kein Punkt mit on_seconds == 0: fehlender oberer Aus-Punkt.
    ]
    response = client.put(
        "/api/v1/control/sensor-failure-defaults",
        json=_defaults_payload(curve_points=bad_points, fixed_on_seconds=999),
        headers=head,
    )
    assert response.status_code == 422

    reloaded = client.get("/api/v1/control/sensor-failure-defaults", headers=head)
    assert reloaded.json()["fixed_on_seconds"] == 600


_ASSIGNMENT_COUNTER = {"n": 0}


def _thermostat_assignment(session: Session, zone_id: int) -> int:
    _ASSIGNMENT_COUNTER["n"] += 1
    device = create_device(session, f"notbetrieb-rest-thermostat-{_ASSIGNMENT_COUNTER['n']}")
    thermostat_capability_id = capability(session, "thermostat").id
    session.add(
        DeviceCapabilityLink(device_id=device.id, capability_id=thermostat_capability_id)
    )
    assignment = ZoneDevice(
        zone_id=zone_id,
        device_id=device.id,
        device_role_id=role(session, "actuator").id,
        self_regulating=True,
    )
    session.add(assignment)
    session.flush()
    return assignment.id


def test_zone_get_put_round_trip_including_switching_back_to_inherit(
    client: TestClient, session: Session, api_token
) -> None:
    create_settings(session)
    save_profile(session, VALUES)
    zone = create_zone(session, "notbetrieb-rest-zone")
    head = api_token([("zone.read", None), ("zone.manage", zone.id)])

    before = client.get(f"/api/v1/zones/{zone.id}/sensor-failure", headers=head)
    assert before.status_code == 200
    assert before.json()["enabled"] is False
    assert before.json()["profile_source"] == "Vorgabe"

    written = client.put(
        f"/api/v1/zones/{zone.id}/sensor-failure",
        json={"enabled": True, "emergency_setpoint_c": "19.0"},
        headers=head,
    )
    assert written.status_code == 200
    assert written.json()["enabled"] is True
    assert written.json()["emergency_setpoint_c"] == "19.0"
    assert written.json()["setpoint_source"] == "Zone"

    back_to_inherit = client.put(
        f"/api/v1/zones/{zone.id}/sensor-failure",
        json={"enabled": True, "emergency_setpoint_c": None},
        headers=head,
    )
    assert back_to_inherit.status_code == 200
    assert back_to_inherit.json()["emergency_setpoint_c"] is None
    assert back_to_inherit.json()["setpoint_source"] == "Anlage"


def test_zone_isolation_hides_a_foreign_zone(
    client: TestClient, session: Session, api_token
) -> None:
    create_settings(session)
    save_profile(session, VALUES)
    own = create_zone(session, "eigene-notbetrieb-zone")
    foreign = create_zone(session, "fremde-notbetrieb-zone")
    head = api_token([("zone.read", own.id), ("zone.manage", own.id)])

    response = client.get(f"/api/v1/zones/{foreign.id}/sensor-failure", headers=head)
    assert response.status_code == 404


def test_backup_offset_round_trip_needs_device_manage(
    client: TestClient, session: Session, api_token
) -> None:
    create_settings(session)
    save_profile(session, VALUES)
    zone = create_zone(session, "notbetrieb-ausgleich-zone")
    assignment_id = _thermostat_assignment(session, zone.id)

    without_device_manage = api_token([("zone.read", None), ("zone.manage", zone.id)])
    refused = client.put(
        f"/api/v1/zones/{zone.id}/sensor-failure",
        json={"enabled": False, "backup_offsets": {str(assignment_id): "1.5"}},
        headers=without_device_manage,
    )
    assert refused.status_code == 403

    with_device_manage = api_token(
        [("zone.read", None), ("zone.manage", zone.id), ("device.manage", zone.id)]
    )
    accepted = client.put(
        f"/api/v1/zones/{zone.id}/sensor-failure",
        json={"enabled": False, "backup_offsets": {str(assignment_id): "1.5"}},
        headers=with_device_manage,
    )
    assert accepted.status_code == 200
    offsets = {o["zone_device_id"]: o["temperature_backup_offset_k"] for o in accepted.json()[
        "backup_offsets"
    ]}
    assert offsets[assignment_id] == "1.50"

    reloaded = client.get(f"/api/v1/zones/{zone.id}/sensor-failure", headers=with_device_manage)
    offsets = {o["zone_device_id"]: o["temperature_backup_offset_k"] for o in reloaded.json()[
        "backup_offsets"
    ]}
    assert offsets[assignment_id] == "1.50"


def test_an_unknown_backup_offset_assignment_is_rejected(
    client: TestClient, session: Session, api_token
) -> None:
    create_settings(session)
    save_profile(session, VALUES)
    zone = create_zone(session, "notbetrieb-fremdausgleich-zone")
    other_zone = create_zone(session, "andere-zone")
    foreign_assignment_id = _thermostat_assignment(session, other_zone.id)
    head = api_token(
        [("zone.read", None), ("zone.manage", zone.id), ("device.manage", zone.id)]
    )

    response = client.put(
        f"/api/v1/zones/{zone.id}/sensor-failure",
        json={"enabled": False, "backup_offsets": {str(foreign_assignment_id): "1"}},
        headers=head,
    )
    assert response.status_code == 404


def test_an_invalid_zone_sensor_failure_write_is_rejected(
    client: TestClient, session: Session, api_token
) -> None:
    create_settings(session)
    save_profile(session, VALUES)
    zone = create_zone(session, "notbetrieb-rest-ungueltig-zone")
    head = api_token([("zone.read", None), ("zone.manage", zone.id)])

    response = client.put(
        f"/api/v1/zones/{zone.id}/sensor-failure",
        json={"enabled": True, "emergency_setpoint_c": "3"},
        headers=head,
    )
    assert response.status_code == 422
