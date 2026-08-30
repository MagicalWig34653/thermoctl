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
from thermoctl.auth.tokens import token_ausstellen
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
        nutzer = user_with_permissions(session, f"konfig-api-{counter}", permissions)
        _token, plaintext = token_ausstellen(
            session, nutzer, f"Konfiguration {counter}", permissions, None
        )
        return {"Authorization": f"Bearer {plaintext}"}

    return create_entry


def test_creating_updating_and_deleting_zones(
    client: TestClient, session: Session, api_token
) -> None:
    kind = operating_mode(session)
    kopf = api_token([("zone.manage", None), ("zone.read", None)])
    data = {
        "name": "api-zone",
        "display_name": "API-Zone",
        "operating_mode_id": kind.id,
        "sort_order": 4,
        "temperature_source_device_id": None,
    }

    angelegt = client.post("/api/v1/zones", headers=kopf, json=data)
    assert angelegt.status_code == 201
    zone_id = angelegt.json()["id"]
    data["display_name"] = "Geänderte API-Zone"
    assert (
        client.put(f"/api/v1/zones/{zone_id}", headers=kopf, json=data).json()["display_name"]
        == "Geänderte API-Zone"
    )
    assert client.delete(f"/api/v1/zones/{zone_id}", headers=kopf).status_code == 204
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
    kopf = api_token([("zone.read", zone.id)])
    zone_data = {
        "name": zone.name,
        "display_name": zone.display_name,
        "operating_mode_id": zone.operating_mode_id,
    }

    assert client.put(f"/api/v1/zones/{zone.id}", headers=kopf, json=zone_data).status_code == 403
    assert client.delete(f"/api/v1/zones/{zone.id}", headers=kopf).status_code == 403
    assert (
        client.delete(f"/api/v1/zones/{zone.id}/schedule/{point.id}", headers=kopf).status_code
        == 403
    )


def test_reading_and_creating_modes(client: TestClient, session: Session, api_token) -> None:
    zone = create_zone(session, "modus-api-zone")
    kopf = api_token([("zone.read", zone.id), ("mode.manage", None)])
    assert client.get("/api/v1/modes", headers=kopf).status_code == 200
    response = client.post(
        "/api/v1/modes",
        headers=kopf,
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
    errors = client.post("/api/v1/modes", headers=kopf, json={"code": " ", "name": "X"})
    assert errors.status_code == 422
    assert "code" in errors.json()["detail"]


def test_reading_and_writing_setpoints_like_the_domain(
    client: TestClient, client_als, session: Session, api_token
) -> None:
    zone = create_zone(session, "sollwert-api-zone")
    web_zone = create_zone(session, "sollwert-web-zone")
    mode = create_mode(session, "komfort-api", "Komfort")
    kopf = api_token([("zone.read", zone.id), ("setpoint.write", zone.id)])
    data = {"setpoints": [{"mode_id": mode.id, "temperature_c": "21.5"}]}
    response = client.put(f"/api/v1/zones/{zone.id}/setpoints", headers=kopf, json=data)
    assert response.status_code == 200
    row = session.get(ZoneSetpoint, (zone.id, mode.id))
    assert row is not None and row.temperature_c == Decimal("21.5")

    web_client = client_als([("setpoint.write", web_zone.id)])
    http_session = web_client.cookies[COOKIE_NAME]
    csrf = csrf_token(http_session, get_settings().secret_key.get_secret_value())
    assert (
        web_client.post(
            f"/zones/{web_zone.id}/setpoints",
            data={f"sollwert_{mode.id}": "21.5"},
            headers={CSRF_HEADER: csrf},
            follow_redirects=False,
        ).status_code
        == 303
    )
    web_row = session.get(ZoneSetpoint, (web_zone.id, mode.id))
    assert web_row is not None
    assert web_row.temperature_c == row.temperature_c
    assert client.get(f"/api/v1/zones/{zone.id}/setpoints", headers=kopf).status_code == 200

    without_permission = api_token([("zone.read", zone.id)])
    assert (
        client.put(
            f"/api/v1/zones/{zone.id}/setpoints", headers=without_permission, json=data
        ).status_code
        == 403
    )
    data["setpoints"][0]["temperature_c"] = "40.0"
    errors = client.put(f"/api/v1/zones/{zone.id}/setpoints", headers=kopf, json=data)
    assert errors.status_code == 422
    assert "temperature_c" in errors.json()["detail"]


def test_reading_creating_and_deleting_a_schedule(
    client: TestClient, session: Session, api_token
) -> None:
    zone = create_zone(session, "zeitplan-api-zone")
    mode = create_mode(session, "nacht-api", "Nacht")
    kopf = api_token([("zone.read", zone.id), ("schedule.manage", zone.id)])
    data = {"weekday": 1, "minute_of_day": 1320, "mode_id": mode.id}
    response = client.post(f"/api/v1/zones/{zone.id}/schedule", headers=kopf, json=data)
    assert response.status_code == 201
    point_id = response.json()["id"]
    assert (
        client.get(f"/api/v1/zones/{zone.id}/schedule", headers=kopf).json()[0]["mode_name"]
        == "Nacht"
    )
    assert (
        client.delete(f"/api/v1/zones/{zone.id}/schedule/{point_id}", headers=kopf).status_code
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
        f"/api/v1/zones/{zone.id}/schedule", headers=kopf, json={**data, "weekday": 8}
    )
    assert errors.status_code == 422
    assert "weekday" in str(errors.json()["detail"])


def test_reading_and_writing_control_parameters(
    client: TestClient, session: Session, api_token
) -> None:
    create_settings(session)
    source(session, "web")
    zone = create_zone(session, "parameter-api-zone")
    kopf = api_token([("zone.read", zone.id), ("zone.manage", zone.id)])
    data = {
        "hysteresis_k": "0.45",
        "min_on_seconds": 120,
        "min_off_seconds": None,
        "sensor_timeout_seconds": None,
        "temperature_offset_k": "-0.20",
        "window_resume_delay_seconds": None,
    }
    response = client.put(f"/api/v1/zones/{zone.id}/parameters", headers=kopf, json=data)
    assert response.status_code == 200
    assert response.json()["hysteresis_k"] == "0.45"
    assert client.get(f"/api/v1/zones/{zone.id}/parameters", headers=kopf).status_code == 200

    without_permission = api_token([("zone.read", zone.id)])
    assert (
        client.put(
            f"/api/v1/zones/{zone.id}/parameters", headers=without_permission, json=data
        ).status_code
        == 403
    )
    errors = client.put(
        f"/api/v1/zones/{zone.id}/parameters", headers=kopf, json={**data, "min_on_seconds": -1}
    )
    assert errors.status_code == 422
    assert "min_on_seconds" in str(errors.json()["detail"])


@pytest.mark.parametrize("pfad", ["setpoints", "schedule", "parameters"])
def test_a_foreign_zone_stays_hidden_from_the_new_routes(
    pfad: str, client: TestClient, session: Session, api_token
) -> None:
    eigene = create_zone(session, f"eigene-{pfad}")
    fremde = create_zone(session, f"fremde-{pfad}")
    kopf = api_token([("zone.read", eigene.id)])
    assert client.get(f"/api/v1/zones/{fremde.id}/{pfad}", headers=kopf).status_code == 404


def test_reading_modes_without_a_visible_zone_is_denied(client, api_token, session) -> None:
    """Anyone who may not see a single zone has no business in the mode list either --
    otherwise it would disclose information about the installation to someone with
    no zone permission at all."""
    create_zone(session, "unsichtbare-zone")
    kopf = api_token([("token.self", None)])
    assert client.get("/api/v1/modes", headers=kopf).status_code == 403


def test_renaming_to_a_taken_name_yields_422(client, api_token, session) -> None:
    source(session, "api")
    kind = operating_mode(session, "auto")
    create_zone(session, "belegt")
    andere = create_zone(session, "wird-umbenannt")
    kopf = api_token([("zone.manage", None), ("zone.read", None)])
    response = client.put(
        f"/api/v1/zones/{andere.id}",
        json={
            "name": "belegt", "display_name": "Andere",
            "operating_mode_id": kind.id, "sort_order": 0,
            "temperature_source_device_id": None,
        },
        headers=kopf,
    )
    assert response.status_code == 422
    assert "bereits vergeben" in response.text
    assert andere.name == "wird-umbenannt"


def test_a_duplicate_schedule_point_yields_422_with_a_message(client, api_token, session) -> None:
    """A domain-level business error becomes a 422 with field names, not a 500.

    Deliberately a case that the schema check lets through: two points at the same
    moment are formally valid and only fail on the rule.
    """
    source(session, "api")
    zone = create_zone(session, "zone-api-zeitplan")
    mode = create_mode(session, "api-tag", "Tag")
    kopf = api_token([("schedule.manage", None), ("zone.read", None)])
    payload = {"weekday": 1, "minute_of_day": 360, "mode_id": mode.id}
    assert client.post(
        f"/api/v1/zones/{zone.id}/schedule", json=payload, headers=kopf
    ).status_code in (200, 201)
    response = client.post(
        f"/api/v1/zones/{zone.id}/schedule", json=payload, headers=kopf
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
    kopf = api_token([("schedule.manage", None), ("zone.read", None)])
    response = client.delete(
        f"/api/v1/zones/{eigene.id}/schedule/{point.id}", headers=kopf
    )
    assert response.status_code == 404
    assert session.get(SchedulePoint, point.id) is not None


def test_an_override_through_the_api_holds_the_same_limit(client, api_token, session) -> None:
    """The limit lives in the domain, not in each adapter's schema."""
    source(session, "api")
    create_settings(session)
    zone = create_zone(session, "zone-api-grenze")
    kopf = api_token([("override.create", None), ("zone.read", None)])
    response = client.post(
        f"/api/v1/zones/{zone.id}/override",
        json={"temperature_c": "99.0"},
        headers=kopf,
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
        "session_lifetime_seconds": 1209600,
        "default_solar_setback_max_k": "2.0",
        "solar_setback_lookahead_hours": 3,
    }
    values.update(abweichungen)
    return values


def test_steuerung_lesen(client: TestClient, session: Session, api_token) -> None:
    create_settings(session)
    kopf = api_token([("zone.read", None)])
    response = client.get("/api/v1/control", headers=kopf)
    assert response.status_code == 200
    assert response.json()["control_armed"] is False


def test_arming_and_taking_it_back(
    client: TestClient, session: Session, api_token
) -> None:
    create_settings(session)
    kopf = api_token([("zone.read", None), ("control.arm", None)])

    armed = client.put(
        "/api/v1/control/armed",
        json={"armed": True, "reason": "Vergleich abgeschlossen"},
        headers=kopf,
    )
    assert armed.status_code == 200
    assert armed.json()["control_armed"] is True

    zurueck = client.put("/api/v1/control/armed", json={"armed": False}, headers=kopf)
    assert zurueck.status_code == 200
    assert zurueck.json()["control_armed"] is False


def test_arming_without_a_reason_is_refused(
    client: TestClient, session: Session, api_token
) -> None:
    """The same check as in the interface -- it lives in the domain, not in the
    schema, so it is the same for every adapter."""
    create_settings(session)
    kopf = api_token([("zone.read", None), ("control.arm", None)])
    response = client.put("/api/v1/control/armed", json={"armed": True}, headers=kopf)
    assert response.status_code == 422


def test_arming_needs_its_own_permission(
    client: TestClient, session: Session, api_token
) -> None:
    create_settings(session)
    kopf = api_token([("zone.read", None), ("setting.manage", None)])
    response = client.put(
        "/api/v1/control/armed", json={"armed": True, "reason": "x"}, headers=kopf
    )
    assert response.status_code == 403


def test_vorgaben_schreiben(client: TestClient, session: Session, api_token) -> None:
    create_settings(session)
    kopf = api_token([("zone.read", None), ("setting.manage", None)])
    response = client.put(
        "/api/v1/control/defaults",
        json=_defaults(shadow_interval_seconds=90),
        headers=kopf,
    )
    assert response.status_code == 200
    assert response.json()["shadow_interval_seconds"] == 90


def test_an_unusable_default_is_refused(
    client: TestClient, session: Session, api_token
) -> None:
    create_settings(session)
    kopf = api_token([("zone.read", None), ("setting.manage", None)])
    response = client.put(
        "/api/v1/control/defaults",
        json=_defaults(default_min_on_seconds=0),
        headers=kopf,
    )
    assert response.status_code == 422


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
    kopf = api_token([("zone.read", None), ("schedule.manage", None)])

    response = client.put(
        f"/api/v1/zones/{zone.id}/schedule/{point.id}",
        json={"weekday": 4, "minute_of_day": 480},
        headers=kopf,
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
    kopf = api_token([("zone.read", None), ("schedule.manage", None)])

    response = client.put(
        f"/api/v1/zones/{zone.id}/schedule/{beweglich.id}",
        json={"weekday": 2, "minute_of_day": 480},
        headers=kopf,
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
    kopf = api_token([("zone.read", None), ("schedule.manage", None)])

    response = client.put(
        f"/api/v1/zones/{zone.id}/schedule/{fremder.id}",
        json={"weekday": 2, "minute_of_day": 480},
        headers=kopf,
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
    kopf = api_token([("zone.read", None), ("schedule.manage", None)])
    client.post(
        f"/api/v1/zones/{zone.id}/schedule",
        json={"weekday": 3, "minute_of_day": 420, "mode_id": mode.id},
        headers=kopf,
    )
    entry = session.scalars(
        select(AuditEvent).where(AuditEvent.object_type == "schedule_point")
    ).one()
    source_code = session.get(ActorSource, entry.source_id).code
    assert source_code == "api"
