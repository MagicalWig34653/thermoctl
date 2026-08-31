import json
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from tests.helpers import (
    create_device,
    create_device_state,
    operating_mode,
    role,
    sensor_status_of,
    source,
    user_with_permissions,
)
from thermoctl.auth.tokens import issue_token
from thermoctl.db.models.device import DeviceCapabilityLink, ZoneDevice
from thermoctl.db.models.lookup import DeviceCapability
from thermoctl.db.models.state import ZoneState
from thermoctl.db.models.zone import Zone


@pytest.fixture
def token_fuer(session: Session) -> Callable[[list[tuple[str, str | None]]], dict[str, str]]:
    kind = operating_mode(session)
    bad = Zone(id=1, name="bad", display_name="Bad", operating_mode_id=kind.id)
    others = Zone(id=2, name="andere", display_name="Andere", operating_mode_id=kind.id)
    session.add_all([bad, others])
    session.flush()
    source(session, "api")

    counter = 0

    def _token_fuer(permissions: list[tuple[str, str | None]]) -> dict[str, str]:
        nonlocal counter
        counter += 1
        aufgeloest = [(code, bad.id if zone == "bad" else None) for code, zone in permissions]
        owner = user_with_permissions(session, f"api-{counter}", aufgeloest)
        _token, plaintext = issue_token(
            session, owner, f"test-{counter}", aufgeloest, None
        )
        return {"Authorization": f"Bearer {plaintext}"}

    return _token_fuer


def test_no_access_without_a_token(client) -> None:
    assert client.get("/api/v1/zones").status_code == 401


def test_an_invalid_token_is_refused(client) -> None:
    response = client.get("/api/v1/zones", headers={"Authorization": "Bearer tctl_x_y"})
    assert response.status_code == 401


def test_a_token_sees_only_the_zones_it_is_allowed(client, token_fuer) -> None:
    """visible_zones must work here too -- otherwise the API leaks what the UI hides."""
    head = token_fuer([("zone.read", "bad")])
    names = [z["name"] for z in client.get("/api/v1/zones", headers=head).json()]
    assert names == ["bad"]


def test_access_to_a_foreign_zone_yields_404(client, token_fuer) -> None:
    """404, not 403: a 403 would reveal that the zone exists."""
    head = token_fuer([("zone.read", "bad")])
    assert client.get("/api/v1/zones/2", headers=head).status_code == 404


def test_the_device_list_needs_device_read(client, token_fuer) -> None:
    head = token_fuer([("zone.read", "bad")])
    assert client.get("/api/v1/devices", headers=head).status_code == 403


def test_the_device_list_reports_signs_of_life(client, token_fuer, session: Session) -> None:
    beispiele = json.loads(
        (Path(__file__).parent / "daten/anlage-beispiele.json").read_text(encoding="utf-8")
    )
    device = create_device(session, beispiele["geraete"][2])
    session.get(Zone, 1).temperature_source_device_id = device.id
    session.add(
        ZoneDevice(
            zone_id=2,
            device_id=device.id,
            device_role_id=role(session, "controller").id,
        )
    )
    capability = DeviceCapability(code="temperature", label="Temperaturmessung")
    session.add(capability)
    session.flush()
    session.add(DeviceCapabilityLink(device_id=device.id, capability_id=capability.id))
    state = create_device_state(session, device)
    state.availability = "online"
    session.flush()
    head = token_fuer([("device.read", None)])

    response = client.get("/api/v1/devices", headers=head)

    assert response.status_code == 200
    assert response.json()[0]["external_id"] == beispiele["geraete"][2]
    assert response.json()[0]["availability"] == "online"
    assert response.json()[0]["capabilities"] == ["temperature"]
    assert response.json()[0]["zones"] == ["andere", "bad"]


def test_zone_state_is_readable_only_for_a_visible_zone(
    client, token_fuer, session: Session
) -> None:
    moment = datetime(2026, 8, 29, 8, 0)
    session.add_all(
        [
            ZoneState(
                zone_id=1,
                temperature_c=Decimal("19.75"),
                measured_at=moment,
                sensor_status_id=sensor_status_of(session).id,
                updated_at=moment,
            ),
            ZoneState(
                zone_id=2,
                temperature_c=Decimal("21.00"),
                measured_at=moment,
                sensor_status_id=sensor_status_of(session).id,
                updated_at=moment,
            ),
        ]
    )
    session.flush()
    head = token_fuer([("zone.read", "bad")])

    response = client.get("/api/v1/zones/1/state", headers=head)

    assert response.status_code == 200
    assert response.json()["temperature_c"] == "19.75"
    assert response.json()["sensor_status"] == "ok"
    assert client.get("/api/v1/zones/2/state", headers=head).status_code == 404


def test_a_visible_zone_without_state_yields_404(client, token_fuer) -> None:
    head = token_fuer([("zone.read", "bad")])
    assert client.get("/api/v1/zones/1/state", headers=head).status_code == 404


def test_overriding_without_the_permission_is_refused(client, token_fuer) -> None:
    head = token_fuer([("zone.read", "bad")])
    response = client.post("/api/v1/zones/1/override", headers=head,
                          json={"temperature_c": "22.0", "duration_minutes": 30})
    assert response.status_code == 403


def test_overriding_with_the_permission_creates_an_entry(client, token_fuer, session) -> None:
    from thermoctl.db.models.override import ZoneOverride

    head = token_fuer([("zone.read", "bad"), ("override.create", "bad")])
    response = client.post("/api/v1/zones/1/override", headers=head,
                          json={"temperature_c": "22.0", "duration_minutes": 30})
    assert response.status_code == 201
    entry = session.query(ZoneOverride).one()
    assert entry.ends_at is not None  # duration is computed at creation time
    assert entry.created_by_token_id is not None


def test_the_api_needs_no_csrf_token(client, token_fuer) -> None:
    """Token requests send no cookie and are therefore not CSRF-vulnerable."""
    head = token_fuer([("zone.read", "bad"), ("override.create", "bad")])
    response = client.post("/api/v1/zones/1/override", headers=head,
                          json={"temperature_c": "22.0", "duration_minutes": 30})
    assert response.status_code == 201


def test_the_token_hash_appears_in_no_response(client, token_fuer) -> None:
    head = token_fuer([("zone.read", "bad"), ("token.self", None)])
    assert "token_hash" not in client.get("/api/v1/me", headers=head).text


def test_me_without_the_permission_is_refused(client, token_fuer) -> None:
    """token.self is deliberately absent here -- viewing even your own token is a permission."""
    head = token_fuer([("zone.read", "bad")])
    response = client.get("/api/v1/me", headers=head)
    assert response.status_code == 403


def test_overriding_until_the_next_switch_without_a_schedule(client, token_fuer) -> None:
    """Without schedule points in the zone, the override stays open-ended."""
    head = token_fuer([("zone.read", "bad"), ("override.create", "bad")])
    response = client.post(
        "/api/v1/zones/1/override", headers=head,
        json={"temperature_c": "22.0", "until_next_switch": True},
    )
    assert response.status_code == 201
    assert response.json()["ends_at"] is None


def test_overriding_until_the_next_switch_with_a_schedule(client, token_fuer, session) -> None:
    from thermoctl.db.models.operations import Setting
    from thermoctl.db.models.schedule import SchedulePoint
    from thermoctl.db.models.zone import SetpointMode

    mode = SetpointMode(code="tag", name="Tag")
    session.add(mode)
    session.flush()
    session.add(SchedulePoint(zone_id=1, weekday=1, minute_of_day=0, setpoint_mode_id=mode.id))
    session.add(Setting(id=1, timezone="Europe/Berlin", frost_protection_mode_id=mode.id))
    session.flush()

    head = token_fuer([("zone.read", "bad"), ("override.create", "bad")])
    response = client.post(
        "/api/v1/zones/1/override", headers=head,
        json={"temperature_c": "22.0", "until_next_switch": True},
    )
    assert response.status_code == 201
    assert response.json()["ends_at"] is not None


def test_cancelling_an_override_without_the_permission_is_refused(client, token_fuer) -> None:
    head = token_fuer([("zone.read", "bad")])
    response = client.delete("/api/v1/zones/1/override", headers=head)
    assert response.status_code == 403


def test_cancelling_an_override_ends_the_active_one(client, token_fuer, session) -> None:
    from thermoctl.db.models.override import ZoneOverride

    head = token_fuer([("zone.read", "bad"), ("override.create", "bad"),
                       ("override.cancel", "bad")])
    client.post("/api/v1/zones/1/override", headers=head,
               json={"temperature_c": "22.0", "duration_minutes": 30})
    response = client.delete("/api/v1/zones/1/override", headers=head)
    assert response.status_code == 204
    entry = session.query(ZoneOverride).one()
    assert entry.cancelled_at is not None


def _zone_with_plan(session: Session) -> None:
    """Zone 1 gets day starting at 00:00 and night starting at 22:00, both with a setpoint."""
    from thermoctl.db.models.operations import Setting
    from thermoctl.db.models.schedule import SchedulePoint
    from thermoctl.db.models.zone import SetpointMode, ZoneSetpoint

    day = SetpointMode(code="tag", name="Tag")
    night = SetpointMode(code="nacht", name="Nacht")
    session.add_all([day, night])
    session.flush()
    session.add_all(
        [
            Setting(id=1, timezone="UTC", frost_protection_mode_id=day.id),
            SchedulePoint(zone_id=1, weekday=1, minute_of_day=0, setpoint_mode_id=day.id),
            SchedulePoint(zone_id=1, weekday=1, minute_of_day=1320, setpoint_mode_id=night.id),
            ZoneSetpoint(zone_id=1, setpoint_mode_id=day.id, temperature_c=Decimal("21.0")),
            ZoneSetpoint(zone_id=1, setpoint_mode_id=night.id, temperature_c=Decimal("18.0")),
        ]
    )
    session.flush()


def test_boost_needs_the_permission_to_override(client, token_fuer) -> None:
    """It *is* an override -- just one whose value the schedule determines."""
    head = token_fuer([("zone.read", "bad")])
    assert client.post("/api/v1/zones/1/boost", headers=head).status_code == 403


def test_boost_brings_the_next_switch_forward(client, token_fuer, session) -> None:
    from thermoctl.db.models.override import ZoneOverride

    _zone_with_plan(session)
    head = token_fuer([("zone.read", "bad"), ("override.create", "bad")])

    response = client.post("/api/v1/zones/1/boost", headers=head)

    assert response.status_code == 201
    data = response.json()
    # The mode is included: "18.0 °C until 22:00" does not say why.
    assert data["mode_code"] in ("tag", "nacht")
    assert data["gilt_bis"] is not None
    entry = session.query(ZoneOverride).one()
    # It ends at the switch it is bringing forward -- not at some arbitrary time.
    assert entry.ends_at is not None
    assert entry.ends_at.isoformat() == data["gilt_bis"]


def test_boost_without_a_schedule_says_why(client, token_fuer, session) -> None:
    """Counter-check: without a schedule there is nothing to bring forward, and that is
    not a server error."""
    from thermoctl.db.models.operations import Setting
    from thermoctl.db.models.zone import SetpointMode

    mode = SetpointMode(code="frost", name="Frost")
    session.add(mode)
    session.flush()
    session.add(Setting(id=1, timezone="UTC", frost_protection_mode_id=mode.id))
    session.flush()

    head = token_fuer([("zone.read", "bad"), ("override.create", "bad")])
    response = client.post("/api/v1/zones/1/boost", headers=head)

    assert response.status_code == 409
    assert "Zeitplan" in response.json()["detail"]


def _defaults(session: Session) -> None:
    """The global defaults that every zone inherits from."""
    from thermoctl.db.models.operations import Setting
    from thermoctl.db.models.zone import SetpointMode

    mode = SetpointMode(code="frost", name="Frost")
    session.add(mode)
    session.flush()
    session.add(Setting(id=1, timezone="UTC", frost_protection_mode_id=mode.id))
    session.flush()


def test_a_single_parameter_leaves_the_others_inherited(client, token_fuer, session) -> None:
    """Whoever wants to change only the hysteresis should not have to send all six.

    Doing so would pin down every inherited value as a zone-specific deviation --
    and a later change to the global default would then bypass this zone.
    """
    _defaults(session)
    head = token_fuer([("zone.read", "bad"), ("zone.manage", "bad")])

    response = client.put(
        "/api/v1/zones/1/parameters/hysteresis_k", headers=head, json={"value": "0.40"}
    )

    assert response.status_code == 200
    assert response.json()["hysteresis_k"] == "0.40"
    zone = session.get(Zone, 1)
    assert zone is not None
    assert zone.hysteresis_k == Decimal("0.40")
    assert zone.min_on_seconds is None, "an inherited value was pinned down"


def test_rest_reads_and_writes_valve_protection_parameters(client, token_fuer, session) -> None:
    _defaults(session)
    head = token_fuer([("zone.read", "bad"), ("zone.manage", "bad")])
    response = client.put(
        "/api/v1/zones/1/parameters", headers=head,
        json={"valve_protection_enabled": True,
              "valve_protection_interval_days": 14,
              "valve_protection_duration_minutes": 7},
    )
    assert response.status_code == 200
    assert response.json()["valve_protection_enabled"] is True
    assert response.json()["valve_protection_interval_days"] == 14
    assert response.json()["valve_protection_duration_minutes"] == 7

    invalid = client.put(
        "/api/v1/zones/1/parameters", headers=head,
        json={"valve_protection_enabled": True,
              "valve_protection_interval_days": 1,
              "valve_protection_duration_minutes": 1441},
    )
    assert invalid.status_code == 422
    assert "nicht länger" in invalid.json()["detail"]


def test_rest_schema_rejects_valve_protection_values_above_the_domain_limits(
    client, token_fuer, session
) -> None:
    _defaults(session)
    head = token_fuer([("zone.read", "bad"), ("zone.manage", "bad")])

    for field, value in (
        ("valve_protection_interval_days", 3651),
        ("valve_protection_duration_minutes", 5_256_001),
    ):
        response = client.put(
            "/api/v1/zones/1/parameters", headers=head, json={field: value}
        )
        assert response.status_code == 422, field

    schema = client.get("/openapi.json").json()["components"]["schemas"][
        "WriteControlParameters"
    ]["properties"]
    assert schema["valve_protection_interval_days"]["maximum"] == 3650
    assert schema["valve_protection_duration_minutes"]["maximum"] == 5_256_000


def test_an_unknown_parameter_name_lists_the_valid_ones(client, token_fuer, session) -> None:
    _defaults(session)
    head = token_fuer([("zone.read", "bad"), ("zone.manage", "bad")])
    response = client.put(
        "/api/v1/zones/1/parameters/farbe", headers=head, json={"value": "1"}
    )
    assert response.status_code == 404
    assert "hysteresis_k" in response.json()["detail"]


def test_a_parameter_outside_the_limits_is_refused(client, token_fuer, session) -> None:
    """The limits live in the domain and apply the same way for every path."""
    _defaults(session)
    head = token_fuer([("zone.read", "bad"), ("zone.manage", "bad")])
    response = client.put(
        "/api/v1/zones/1/parameters/hysteresis_k", headers=head, json={"value": "99"}
    )
    assert response.status_code == 422
    zone = session.get(Zone, 1)
    assert zone is not None
    assert zone.hysteresis_k is None


def test_a_single_parameter_needs_zone_manage(client, token_fuer, session) -> None:
    _defaults(session)
    head = token_fuer([("zone.read", "bad")])
    response = client.put(
        "/api/v1/zones/1/parameters/hysteresis_k", headers=head, json={"value": "0.4"}
    )
    assert response.status_code == 403
