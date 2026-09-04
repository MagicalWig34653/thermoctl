from collections.abc import Callable
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.helpers import (
    create_mode,
    create_settings,
    create_zone,
    operating_mode,
    source,
    user_with_permissions,
)
from thermoctl.auth.csrf import CSRF_HEADER, csrf_token
from thermoctl.auth.sessions import COOKIE_NAME
from thermoctl.auth.tokens import issue_token
from thermoctl.config import get_settings
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.zone import Zone, ZoneSetpoint


@pytest.fixture
def api_token(session: Session) -> Callable[[list[tuple[str, int | None]]], dict[str, str]]:
    source(session, "web")
    counter = 0

    def create_entry(permissions: list[tuple[str, int | None]]) -> dict[str, str]:
        nonlocal counter
        counter += 1
        user_record = user_with_permissions(session, f"konfig-api-{counter}", permissions)
        _token, plaintext = issue_token(
            session, user_record, f"Konfiguration {counter}", permissions, None
        )
        return {"Authorization": f"Bearer {plaintext}"}

    return create_entry


def test_creating_updating_and_deleting_zones(
    client: TestClient, session: Session, api_token
) -> None:
    kind = operating_mode(session)
    head = api_token([("zone.manage", None), ("zone.read", None)])
    data = {
        "name": "api-zone",
        "display_name": "API-Zone",
        "operating_mode_id": kind.id,
        "sort_order": 4,
        "temperature_source_device_id": None,
    }

    angelegt = client.post("/api/v1/zones", headers=head, json=data)
    assert angelegt.status_code == 201
    zone_id = angelegt.json()["id"]
    data["display_name"] = "Geänderte API-Zone"
    assert (
        client.put(f"/api/v1/zones/{zone_id}", headers=head, json=data).json()["display_name"]
        == "Geänderte API-Zone"
    )
    assert client.delete(f"/api/v1/zones/{zone_id}", headers=head).status_code == 204
    assert session.get(Zone, zone_id) is None


def test_changing_a_zone_needs_the_permission_and_reports_a_duplicate_name(
    client: TestClient, session: Session, api_token
) -> None:
    zone = create_zone(session, "schon-da-api")
    data = {
        "name": zone.name,
        "display_name": "Doppelt",
        "operating_mode_id": zone.operating_mode_id,
    }
    without_permission = api_token([("zone.read", None)])
    assert client.post("/api/v1/zones", headers=without_permission, json=data).status_code == 403
    with_permission = api_token([("zone.read", None), ("zone.manage", None)])
    response = client.post("/api/v1/zones", headers=with_permission, json=data)
    assert response.status_code == 422
    assert "name" in response.json()["detail"]


def test_update_and_delete_each_check_their_own_permission(
    client: TestClient, session: Session, api_token
) -> None:
    zone = create_zone(session, "rechte-api-zone")
    mode = create_mode(session, "rechte-api-modus")
    point = SchedulePoint(zone_id=zone.id, weekday=1, minute_of_day=0, setpoint_mode_id=mode.id)
    session.add(point)
    session.flush()
    head = api_token([("zone.read", zone.id)])
    zone_data = {
        "name": zone.name,
        "display_name": zone.display_name,
        "operating_mode_id": zone.operating_mode_id,
    }

    assert client.put(f"/api/v1/zones/{zone.id}", headers=head, json=zone_data).status_code == 403
    assert client.delete(f"/api/v1/zones/{zone.id}", headers=head).status_code == 403
    assert (
        client.delete(f"/api/v1/zones/{zone.id}/schedule/{point.id}", headers=head).status_code
        == 403
    )


def test_reading_and_creating_modes(client: TestClient, session: Session, api_token) -> None:
    zone = create_zone(session, "modus-api-zone")
    head = api_token([("zone.read", zone.id), ("mode.manage", None)])
    assert client.get("/api/v1/modes", headers=head).status_code == 200
    response = client.post(
        "/api/v1/modes",
        headers=head,
        json={"code": "urlaub-api", "name": "Urlaub", "sort_order": 7},
    )
    assert response.status_code == 201
    assert response.json()["code"] == "urlaub-api"

    without_permission = api_token([("zone.read", zone.id)])
    assert (
        client.post(
            "/api/v1/modes", headers=without_permission, json={"code": "x", "name": "X"}
        ).status_code
        == 403
    )
    errors = client.post("/api/v1/modes", headers=head, json={"code": " ", "name": "X"})
    assert errors.status_code == 422
    assert "code" in errors.json()["detail"]


def test_reading_and_writing_setpoints_like_the_domain(
    client: TestClient, client_als, session: Session, api_token
) -> None:
    zone = create_zone(session, "sollwert-api-zone")
    web_zone = create_zone(session, "sollwert-web-zone")
    mode = create_mode(session, "komfort-api", "Komfort")
    head = api_token([("zone.read", zone.id), ("setpoint.write", zone.id)])
    data = {"setpoints": [{"mode_id": mode.id, "temperature_c": "21.5"}]}
    response = client.put(f"/api/v1/zones/{zone.id}/setpoints", headers=head, json=data)
    assert response.status_code == 200
    row = session.get(ZoneSetpoint, (zone.id, mode.id))
    assert row is not None and row.temperature_c == Decimal("21.5")

    web_client = client_als([("setpoint.write", web_zone.id)])
    http_session = web_client.cookies[COOKIE_NAME]
    csrf = csrf_token(http_session, get_settings().secret_key.get_secret_value())
    assert (
        web_client.post(
            f"/zones/{web_zone.id}/setpoints",
            data={f"setpoint_{mode.id}": "21.5"},
            headers={CSRF_HEADER: csrf},
            follow_redirects=False,
        ).status_code
        == 303
    )
    web_row = session.get(ZoneSetpoint, (web_zone.id, mode.id))
    assert web_row is not None
    assert web_row.temperature_c == row.temperature_c
    assert client.get(f"/api/v1/zones/{zone.id}/setpoints", headers=head).status_code == 200

    without_permission = api_token([("zone.read", zone.id)])
    assert (
        client.put(
            f"/api/v1/zones/{zone.id}/setpoints", headers=without_permission, json=data
        ).status_code
        == 403
    )
    data["setpoints"][0]["temperature_c"] = "40.0"
    errors = client.put(f"/api/v1/zones/{zone.id}/setpoints", headers=head, json=data)
    assert errors.status_code == 422
    assert "temperature_c" in errors.json()["detail"]


def test_reading_creating_and_deleting_a_schedule(
    client: TestClient, session: Session, api_token
) -> None:
    zone = create_zone(session, "zeitplan-api-zone")
    mode = create_mode(session, "nacht-api", "Nacht")
    head = api_token([("zone.read", zone.id), ("schedule.manage", zone.id)])
    data = {"weekday": 1, "minute_of_day": 1320, "mode_id": mode.id}
    response = client.post(f"/api/v1/zones/{zone.id}/schedule", headers=head, json=data)
    assert response.status_code == 201
    point_id = response.json()["id"]
    assert (
        client.get(f"/api/v1/zones/{zone.id}/schedule", headers=head).json()[0]["mode_name"]
        == "Nacht"
    )
    assert (
        client.delete(f"/api/v1/zones/{zone.id}/schedule/{point_id}", headers=head).status_code
        == 204
    )
    assert session.get(SchedulePoint, point_id) is None

    without_permission = api_token([("zone.read", zone.id)])
    assert (
        client.post(
            f"/api/v1/zones/{zone.id}/schedule", headers=without_permission, json=data
        ).status_code
        == 403
    )
    errors = client.post(
        f"/api/v1/zones/{zone.id}/schedule", headers=head, json={**data, "weekday": 8}
    )
    assert errors.status_code == 422
    assert "weekday" in str(errors.json()["detail"])


def test_reading_and_writing_control_parameters(
    client: TestClient, session: Session, api_token
) -> None:
    create_settings(session)
    source(session, "web")
    zone = create_zone(session, "parameter-api-zone")
    head = api_token([("zone.read", zone.id), ("zone.manage", zone.id)])
    data = {
        "hysteresis_k": "0.45",
        "min_on_seconds": 120,
        "min_off_seconds": None,
        "sensor_timeout_seconds": None,
        "temperature_offset_k": "-0.20",
        "window_resume_delay_seconds": None,
    }
    response = client.put(f"/api/v1/zones/{zone.id}/parameters", headers=head, json=data)
    assert response.status_code == 200
    assert response.json()["hysteresis_k"] == "0.45"
    assert client.get(f"/api/v1/zones/{zone.id}/parameters", headers=head).status_code == 200

    without_permission = api_token([("zone.read", zone.id)])
    assert (
        client.put(
            f"/api/v1/zones/{zone.id}/parameters", headers=without_permission, json=data
        ).status_code
        == 403
    )
    errors = client.put(
        f"/api/v1/zones/{zone.id}/parameters", headers=head, json={**data, "min_on_seconds": -1}
    )
    assert errors.status_code == 422
    assert "min_on_seconds" in str(errors.json()["detail"])


@pytest.mark.parametrize("pfad", ["setpoints", "schedule", "parameters"])
def test_a_foreign_zone_stays_hidden_from_the_new_routes(
    pfad: str, client: TestClient, session: Session, api_token
) -> None:
    eigene = create_zone(session, f"eigene-{pfad}")
    fremde = create_zone(session, f"fremde-{pfad}")
    head = api_token([("zone.read", eigene.id)])
    assert client.get(f"/api/v1/zones/{fremde.id}/{pfad}", headers=head).status_code == 404


def test_reading_modes_without_a_visible_zone_is_denied(client, api_token, session) -> None:
    """Anyone who may not see a single zone has no business in the mode list either --
    otherwise it would disclose information about the installation to someone with
    no zone permission at all."""
    create_zone(session, "unsichtbare-zone")
    head = api_token([("token.self", None)])
    assert client.get("/api/v1/modes", headers=head).status_code == 403


def test_renaming_to_a_taken_name_yields_422(client, api_token, session) -> None:
    source(session, "api")
    kind = operating_mode(session, "auto")
    create_zone(session, "belegt")
    others = create_zone(session, "wird-umbenannt")
    head = api_token([("zone.manage", None), ("zone.read", None)])
    response = client.put(
        f"/api/v1/zones/{others.id}",
        json={
            "name": "belegt", "display_name": "Andere",
            "operating_mode_id": kind.id, "sort_order": 0,
            "temperature_source_device_id": None,
        },
        headers=head,
    )
    assert response.status_code == 422
    assert "bereits vergeben" in response.text
    assert others.name == "wird-umbenannt"


def test_a_duplicate_schedule_point_yields_422_with_a_message(client, api_token, session) -> None:
    """A domain-level business error becomes a 422 with field names, not a 500.

    Deliberately a case that the schema check lets through: two points at the same
    moment are formally valid and only fail on the rule.
    """
    source(session, "api")
    zone = create_zone(session, "zone-api-zeitplan")
    mode = create_mode(session, "api-tag", "Tag")
    head = api_token([("schedule.manage", None), ("zone.read", None)])
    payload = {"weekday": 1, "minute_of_day": 360, "mode_id": mode.id}
    assert client.post(
        f"/api/v1/zones/{zone.id}/schedule", json=payload, headers=head
    ).status_code in (200, 201)
    response = client.post(
        f"/api/v1/zones/{zone.id}/schedule", json=payload, headers=head
    )
    assert response.status_code == 422
    assert "500" not in str(response.status_code)


def test_a_foreign_schedule_point_yields_404(client, api_token, session) -> None:
    source(session, "api")
    eigene = create_zone(session, "eigene-api")
    fremde = create_zone(session, "fremde-api")
    mode = create_mode(session, "api-fremd", "Tag")
    point = SchedulePoint(
        zone_id=fremde.id, weekday=1, minute_of_day=360, setpoint_mode_id=mode.id
    )
    session.add(point)
    session.flush()
    head = api_token([("schedule.manage", None), ("zone.read", None)])
    response = client.delete(
        f"/api/v1/zones/{eigene.id}/schedule/{point.id}", headers=head
    )
    assert response.status_code == 404
    assert session.get(SchedulePoint, point.id) is not None


def test_an_override_through_the_api_holds_the_same_limit(client, api_token, session) -> None:
    """The limit lives in the domain, not in each adapter's schema."""
    source(session, "api")
    create_settings(session)
    zone = create_zone(session, "zone-api-grenze")
    head = api_token([("override.create", None), ("zone.read", None)])
    response = client.post(
        f"/api/v1/zones/{zone.id}/override",
        json={"temperature_c": "99.0"},
        headers=head,
    )
    assert response.status_code == 422


# --- Control via REST -------------------------------------------------------


def _defaults(**abweichungen: object) -> dict[str, object]:
    values: dict[str, object] = {
        "timezone": "Europe/Berlin",
        "polling_interval_seconds": 30,
        "shadow_interval_seconds": 60,
        "default_hysteresis_k": "0.3",
        "default_min_on_seconds": 300,
        "default_min_off_seconds": 300,
        "default_sensor_timeout_seconds": 1800,
        "default_window_resume_delay_seconds": 120,
        "measurement_retention_days": 30,
        "shadow_decision_retention_days": 365,
        "session_lifetime_seconds": 1209600,
        "default_solar_setback_max_k": "2.0",
        "solar_setback_lookahead_hours": 3,
        "assumed_relay_lifetime_operations": 500_000,
    }
    values.update(abweichungen)
    return values


def test_steuerung_lesen(client: TestClient, session: Session, api_token) -> None:
    create_settings(session)
    head = api_token([("zone.read", None)])
    response = client.get("/api/v1/control", headers=head)
    assert response.status_code == 200
    assert response.json()["control_armed"] is False
    # The default a fresh installation starts from -- the project owner's explicit
    # request, not the old 100,000 the constant used to carry.
    assert response.json()["assumed_relay_lifetime_operations"] == 500_000


def test_arming_and_taking_it_back(
    client: TestClient, session: Session, api_token
) -> None:
    create_settings(session)
    head = api_token([("zone.read", None), ("control.arm", None)])

    armed = client.put(
        "/api/v1/control/armed",
        json={"armed": True, "reason": "Vergleich abgeschlossen"},
        headers=head,
    )
    assert armed.status_code == 200
    assert armed.json()["control_armed"] is True

    zurück = client.put("/api/v1/control/armed", json={"armed": False}, headers=head)
    assert zurück.status_code == 200
    assert zurück.json()["control_armed"] is False


def test_arming_without_a_reason_is_refused(
    client: TestClient, session: Session, api_token
) -> None:
    """The same check as in the interface -- it lives in the domain, not in the
    schema, so it is the same for every adapter."""
    create_settings(session)
    head = api_token([("zone.read", None), ("control.arm", None)])
    response = client.put("/api/v1/control/armed", json={"armed": True}, headers=head)
    assert response.status_code == 422


def test_arming_needs_its_own_permission(
    client: TestClient, session: Session, api_token
) -> None:
    create_settings(session)
    head = api_token([("zone.read", None), ("setting.manage", None)])
    response = client.put(
        "/api/v1/control/armed", json={"armed": True, "reason": "x"}, headers=head
    )
    assert response.status_code == 403


def test_vorgaben_schreiben(client: TestClient, session: Session, api_token) -> None:
    create_settings(session)
    head = api_token([("zone.read", None), ("setting.manage", None)])
    response = client.put(
        "/api/v1/control/defaults",
        json=_defaults(shadow_interval_seconds=90, shadow_decision_retention_days=730),
        headers=head,
    )
    assert response.status_code == 200
    assert response.json()["shadow_interval_seconds"] == 90
    assert response.json()["shadow_decision_retention_days"] == 730


def test_an_unusable_default_is_refused(
    client: TestClient, session: Session, api_token
) -> None:
    create_settings(session)
    head = api_token([("zone.read", None), ("setting.manage", None)])
    response = client.put(
        "/api/v1/control/defaults",
        json=_defaults(default_min_on_seconds=0),
        headers=head,
    )
    assert response.status_code == 422


def test_assumed_relay_lifetime_is_writable_and_bounded(
    client: TestClient, session: Session, api_token
) -> None:
    """The same domain limit as everywhere else -- neither adapter invents its own."""
    create_settings(session)
    head = api_token([("zone.read", None), ("setting.manage", None)])

    ok = client.put(
        "/api/v1/control/defaults",
        json=_defaults(assumed_relay_lifetime_operations=250_000),
        headers=head,
    )
    assert ok.status_code == 200
    assert ok.json()["assumed_relay_lifetime_operations"] == 250_000

    too_small = client.put(
        "/api/v1/control/defaults",
        json=_defaults(assumed_relay_lifetime_operations=100),
        headers=head,
    )
    assert too_small.status_code == 422

    too_large = client.put(
        "/api/v1/control/defaults",
        json=_defaults(assumed_relay_lifetime_operations=1_000_000_000),
        headers=head,
    )
    assert too_large.status_code == 422


def test_moving_a_schedule_point(
    client: TestClient, session: Session, api_token
) -> None:
    zone = create_zone(session, "api-verschiebezone")
    mode = create_mode(session, "api-verschiebemodus")
    point = SchedulePoint(
        zone_id=zone.id, weekday=1, minute_of_day=360, setpoint_mode_id=mode.id
    )
    session.add(point)
    session.flush()
    head = api_token([("zone.read", None), ("schedule.manage", None)])

    response = client.put(
        f"/api/v1/zones/{zone.id}/schedule/{point.id}",
        json={"weekday": 4, "minute_of_day": 480},
        headers=head,
    )
    assert response.status_code == 200
    # The identifier stays: a caller should be able to keep tracking the same point.
    assert response.json()["id"] == point.id
    assert response.json()["weekday"] == 4


def test_moving_onto_an_occupied_moment(
    client: TestClient, session: Session, api_token
) -> None:
    zone = create_zone(session, "api-kollision")
    mode = create_mode(session, "api-kollisionsmodus")
    beweglich = SchedulePoint(
        zone_id=zone.id, weekday=1, minute_of_day=360, setpoint_mode_id=mode.id
    )
    session.add(beweglich)
    session.add(
        SchedulePoint(
            zone_id=zone.id, weekday=2, minute_of_day=480, setpoint_mode_id=mode.id
        )
    )
    session.flush()
    head = api_token([("zone.read", None), ("schedule.manage", None)])

    response = client.put(
        f"/api/v1/zones/{zone.id}/schedule/{beweglich.id}",
        json={"weekday": 2, "minute_of_day": 480},
        headers=head,
    )
    assert response.status_code == 422


def test_moving_a_foreign_point_is_not_found(
    client: TestClient, session: Session, api_token
) -> None:
    zone = create_zone(session, "api-eigen")
    fremde = create_zone(session, "api-fremd")
    mode = create_mode(session, "api-fremdmodus")
    fremder = SchedulePoint(
        zone_id=fremde.id, weekday=1, minute_of_day=360, setpoint_mode_id=mode.id
    )
    session.add(fremder)
    session.flush()
    head = api_token([("zone.read", None), ("schedule.manage", None)])

    response = client.put(
        f"/api/v1/zones/{zone.id}/schedule/{fremder.id}",
        json={"weekday": 2, "minute_of_day": 480},
        headers=head,
    )
    assert response.status_code == 404


def test_a_rest_change_is_logged_with_source_api(
    client: TestClient, session: Session, api_token
) -> None:
    """Every domain function used to hard-code `source="web"`. That made the audit
    log claim that every REST and MCP change had come through the interface --
    answering, wrongly, exactly the question it exists to answer."""
    from thermoctl.db.models.lookup import ActorSource
    from thermoctl.db.models.operations import AuditEvent

    zone = create_zone(session, "protokollzone")
    mode = create_mode(session, "protokollmodus")
    head = api_token([("zone.read", None), ("schedule.manage", None)])
    client.post(
        f"/api/v1/zones/{zone.id}/schedule",
        json={"weekday": 3, "minute_of_day": 420, "mode_id": mode.id},
        headers=head,
    )
    entry = session.scalars(
        select(AuditEvent).where(AuditEvent.object_type == "schedule_point")
    ).one()
    source_code = session.get(ActorSource, entry.source_id).code
    assert source_code == "api"


def test_the_solar_location_can_be_set_and_read_back_over_rest(
    client: TestClient, session: Session, api_token
) -> None:
    """The forecast location was reachable only through the web interface.

    Every other plant setting is readable and writable through all three adapters;
    a location that only the browser can set would make the REST view of the plant
    lie by omission -- an assistant reading `/control` would see the setback's cap
    and lookahead but never learn whether it is switched on at all.
    """
    create_settings(session)
    source(session, "web")
    head = api_token([("zone.read", None), ("setting.manage", None)])

    before = client.get("/api/v1/control", headers=head)
    assert before.status_code == 200
    assert before.json()["solar_forecast_enabled"] is False
    assert before.json()["solar_forecast_latitude"] is None

    response = client.put(
        "/api/v1/control/solar-location",
        headers=head,
        json={"enabled": True, "latitude": "52.520", "longitude": "13.405"},
    )
    assert response.status_code == 200
    assert response.json()["solar_forecast_enabled"] is True
    assert Decimal(response.json()["solar_forecast_latitude"]) == Decimal("52.520")

    # Empty is a valid answer and means "no location" -- there is no default one.
    cleared = client.put(
        "/api/v1/control/solar-location",
        headers=head,
        json={"enabled": False, "latitude": "", "longitude": ""},
    )
    assert cleared.status_code == 200
    assert cleared.json()["solar_forecast_latitude"] is None


def test_an_impossible_coordinate_is_refused_by_the_domain_not_the_schema(
    client: TestClient, session: Session, api_token
) -> None:
    """The limits live in one place. A `Field(ge=-90, le=90)` on the schema would be
    a second copy of them, and two copies drift apart."""
    create_settings(session)
    source(session, "web")
    head = api_token([("zone.read", None), ("setting.manage", None)])
    response = client.put(
        "/api/v1/control/solar-location",
        headers=head,
        json={"enabled": True, "latitude": "91", "longitude": "0"},
    )
    assert response.status_code == 422
    assert "solar_forecast_latitude" in str(response.json()["detail"])


def test_setting_the_solar_location_needs_setting_manage(
    client: TestClient, session: Session, api_token
) -> None:
    create_settings(session)
    source(session, "web")
    head = api_token([("zone.read", None)])
    assert (
        client.put(
            "/api/v1/control/solar-location",
            headers=head,
            json={"enabled": True, "latitude": "52.5", "longitude": "13.4"},
        ).status_code
        == 403
    )


def test_a_zone_carries_its_solar_profile_through_rest(
    client: TestClient, session: Session, api_token
) -> None:
    """A caller that does not know about the setback must switch it off, not on.

    Hence the default of 0 on `WriteZone`: leaving the field out of a zone update
    written against the old schema turns the setback off for that zone rather than
    silently enabling it at full strength.
    """
    create_settings(session)
    source(session, "web")
    head = api_token([("zone.read", None), ("zone.manage", None)])
    mode = operating_mode(session)
    created = client.post(
        "/api/v1/zones",
        headers=head,
        json={
            "name": "sonnenzone-api",
            "display_name": "Sonnenzone",
            "operating_mode_id": mode.id,
            "solar_gain_factor": "0.75",
        },
    )
    assert created.status_code == 201
    assert Decimal(created.json()["solar_gain_factor"]) == Decimal("0.75")

    zone_id = created.json()["id"]
    unaware = client.put(
        f"/api/v1/zones/{zone_id}",
        headers=head,
        json={
            "name": "sonnenzone-api",
            "display_name": "Sonnenzone",
            "operating_mode_id": mode.id,
        },
    )
    assert unaware.status_code == 200
    assert Decimal(unaware.json()["solar_gain_factor"]) == Decimal("0")

    beyond_one = client.post(
        "/api/v1/zones",
        headers=head,
        json={
            "name": "zu-viel-sonne",
            "display_name": "Zu viel",
            "operating_mode_id": mode.id,
            "solar_gain_factor": "1.5",
        },
    )
    assert beyond_one.status_code == 422
