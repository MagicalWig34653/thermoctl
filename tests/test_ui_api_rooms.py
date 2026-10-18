"""Räume unter `/ui-api/rooms`: Lesemodell, Alltagsaktionen, Rechte, Fehlerform."""

from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.helpers import (
    capability,
    create_device,
    create_settings,
    create_shadow_decision,
    create_zone,
    create_zone_state,
    sensor_status_of,
    source,
    zone_with_schedule,
)
from thermoctl.auth.csrf import CSRF_HEADER, csrf_token
from thermoctl.auth.sessions import COOKIE_NAME
from thermoctl.config import get_settings
from thermoctl.db.base import utcnow
from thermoctl.db.models.change_log import ChangeLog
from thermoctl.db.models.lookup import OperatingMode
from thermoctl.db.models.measurement import Measurement
from thermoctl.db.models.operations import AuditEvent, Setting
from thermoctl.db.models.override import ZoneOverride
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.sensor_failure import ZoneSensorFailureState
from thermoctl.db.models.vacation import Vacation
from thermoctl.db.models.zone import SetpointMode, Zone, ZoneSetpoint
from thermoctl.domain import emergency_operation

Als = Callable[[list[tuple[str, int | None]]], TestClient]

ALLES: list[tuple[str, int | None]] = [
    ("zone.read", None),
    ("override.create", None),
    ("override.cancel", None),
    ("setpoint.write", None),
    ("zone.manage", None),
]


def _csrf(client: TestClient) -> dict[str, str]:
    http_session = client.cookies.get(COOKIE_NAME)
    assert http_session is not None
    return {CSRF_HEADER: csrf_token(http_session, get_settings().secret_key.get_secret_value())}


def _call(
    client: TestClient, method: str, url: str, body: dict[str, Any] | None = None
) -> Any:
    return client.request(method, url, json=body, headers=_csrf(client))


def _raum(session: Session) -> Zone:
    source(session, "web")
    points = [(1, 420, "tag", Decimal("21.0")), (1, 1320, "nacht", Decimal("17.0"))]
    zone = zone_with_schedule(session, "wohnzimmer", points)
    tag = _mode(session, "tag")
    nacht = _mode(session, "nacht")
    for day in range(2, 8):
        session.add(SchedulePoint(zone_id=zone.id, weekday=day, minute_of_day=420,
                                  setpoint_mode_id=tag.id))
        session.add(SchedulePoint(zone_id=zone.id, weekday=day, minute_of_day=1320,
                                  setpoint_mode_id=nacht.id))
    session.flush()
    return zone


def _mode(session: Session, code: str) -> SetpointMode:
    return session.query(SetpointMode).filter_by(code=code).one()


def _fehler(response: Any, status: int, code: str) -> dict[str, Any]:
    assert response.status_code == status, response.text
    body = response.json()
    assert body["code"] == code
    assert isinstance(body["detail"], str) and body["detail"]
    return body  # type: ignore[no-any-return]


# -- Lesen -------------------------------------------------------------------------------


def test_list_without_session_is_json_401(client: TestClient) -> None:
    response = client.get("/ui-api/rooms", headers={"Accept": "text/html"})
    assert response.status_code == 401
    assert response.json() == {"detail": "Nicht angemeldet"}


def test_detail_without_session_is_json_401(client: TestClient) -> None:
    assert client.get("/ui-api/rooms/1").status_code == 401


def test_list_shows_state_setpoint_and_actions(client_als: Als, session: Session) -> None:
    zone = _raum(session)
    state = create_zone_state(session, zone)
    state.temperature_c = Decimal("20.50")
    state.measured_at = datetime(2026, 8, 29, 8, 0)
    decision = create_shadow_decision(session, zone)
    decision.scheduled_setpoint_c = Decimal("21.0")
    decision.would_heat = True
    client = client_als(ALLES)
    body = client.get("/ui-api/rooms").json()
    assert body["actuation"] == {
        "control_armed": False,
        "sending_allowed": False,
        "cluster_standby": False,
        "cluster_state": "leading",
        "determining_until": None,
        "state": "dry_run",
    }
    assert body["timezone"] == "Europe/Berlin"
    (room,) = body["rooms"]
    assert room["id"] == zone.id
    assert room["name"] == zone.display_name
    assert room["temperature_c"] == "20.50"
    assert room["measured_at"] == "2026-08-29T08:00:00+00:00"
    assert room["measured_age_seconds"] > 0
    assert room["sensor"]["status_code"] == "ok"
    assert room["setpoint"]["temperature_c"] in {"21.0", "17.0"}
    assert room["decision"]["would_heat"] is True
    assert room["decision"]["planned_setpoint_c"] == "21.0"
    assert room["override"] is None
    assert room["plan_applies"] is True
    assert {s["mode_name"] for s in room["day_track"]} == {"Tag", "Nacht"}
    assert sum(s["end_minute"] - s["start_minute"] for s in room["day_track"]) == 1440
    assert room["actions"] == {
        "override": True,
        "cancel_override": True,
        "edit_setpoint_step": True,
        "edit_mode_setpoints": True,
        "jump_next": True,
        "set_operating_mode": True,
    }


def test_list_zone_without_state_or_decision(client_als: Als, session: Session) -> None:
    _raum(session)
    (room,) = client_als([("zone.read", None)]).get("/ui-api/rooms").json()["rooms"]
    assert room["temperature_c"] is None
    assert room["measured_age_seconds"] is None
    assert room["sensor"]["status_code"] == "unknown"
    assert room["decision"] is None
    assert room["emergency"] is None
    assert set(room["actions"].values()) == {False}


def test_list_actuation_states(
    client_als: Als, session: Session, client: TestClient
) -> None:
    _raum(session)
    settings = session.get(Setting, 1)
    assert settings is not None
    settings.control_armed = True
    http = client_als([("zone.read", None)])
    assert http.get("/ui-api/rooms").json()["actuation"]["state"] == "armed_restart_missing"
    http.app.state.sending_allowed = True  # type: ignore[attr-defined]
    try:
        body = http.get("/ui-api/rooms").json()["actuation"]
    finally:
        http.app.state.sending_allowed = False  # type: ignore[attr-defined]
    assert body["state"] == "live"
    assert body["control_armed"] and body["sending_allowed"]


def _plant(client: TestClient) -> dict[str, Any]:
    body = client.get("/ui-api/rooms").json()
    assert body["home"] is None
    return body["plant"]  # type: ignore[no-any-return]


def test_plant_notes_are_plant_view_only_and_need_a_session(
    client: TestClient, client_als: Als, tenant_client: Als, session: Session
) -> None:
    assert client.get("/ui-api/rooms").status_code == 401
    _raum(session)
    body = tenant_client([("zone.read", None)]).get("/ui-api/rooms").json()
    assert body["plant"] is None
    assert body["home"] == {"calm": True, "notice": None}
    plant = _plant(client_als([("zone.read", None)]))
    assert plant["bridge_reachable"] is None
    assert plant["silent_sensor_rooms"] == plant["stuck_sensor_rooms"] == []
    assert plant["window_alarm_rooms"] == []
    assert plant["vacation"] is None
    assert plant["may_create_zone"] is False


def test_plant_bridge_state_is_read_from_the_app_state(
    client_als: Als, session: Session
) -> None:
    _raum(session)
    http = client_als([("zone.read", None)])
    try:
        for value in (True, False):
            http.app.state.bridge_reachable = value  # type: ignore[attr-defined]
            assert _plant(http)["bridge_reachable"] is value
    finally:
        http.app.state.bridge_reachable = None  # type: ignore[attr-defined]


def test_plant_collects_problem_rooms_separately_and_only_visible_ones(
    client_als: Als, session: Session
) -> None:
    mine, other = _raum(session), create_zone(session, "fremd")
    for zone in (mine, other):
        state = create_zone_state(session, zone)
        state.window_alarm = True
        state.sensor_stuck = True
        state.sensor_status_id = sensor_status_of(session, "veraltet").id
    session.flush()
    plant = _plant(client_als([("zone.read", mine.id)]))
    ref = {"id": mine.id, "name": mine.display_name}
    assert plant["window_alarm_rooms"] == [ref]
    assert plant["stuck_sensor_rooms"] == [ref]
    assert plant["silent_sensor_rooms"] == [ref]
    assert "Fremd" not in str(plant)


def test_plant_vacation_running_and_planned(client_als: Als, session: Session) -> None:
    _raum(session)
    web = source(session, "web")
    http = client_als([("zone.read", None)])
    assert _plant(http)["vacation"] is None
    session.add(
        Vacation(
            starts_at=datetime(2099, 1, 1),
            ends_at=datetime(2099, 1, 9),
            setback_temperature_c=Decimal("15.0"),
            source_id=web.id,
        )
    )
    session.flush()
    assert _plant(http)["vacation"] == {
        "starts_at": "2099-01-01T00:00:00+00:00",
        "ends_at": "2099-01-09T00:00:00+00:00",
        "setback_temperature_c": "15.0",
        "running": False,
    }
    vacation = session.scalars(select(Vacation)).one()
    vacation.starts_at = utcnow() - timedelta(days=1)
    vacation.ends_at = utcnow() + timedelta(days=1)
    session.flush()
    assert _plant(http)["vacation"]["running"] is True


def test_plant_empty_state_button_needs_installation_wide_zone_manage(
    client_als: Als, session: Session
) -> None:
    zone = _raum(session)
    assert _plant(client_als([("zone.read", None), ("zone.manage", None)]))[
        "may_create_zone"
    ] is True
    assert _plant(client_als([("zone.read", zone.id), ("zone.manage", zone.id)]))[
        "may_create_zone"
    ] is False


def test_plant_before_setup_is_still_a_plant_view(
    client_als: Als, tenant_client: Als
) -> None:
    plant = _plant(client_als([("zone.read", None)]))
    assert plant["may_create_zone"] is False and plant["vacation"] is None
    assert tenant_client([("zone.read", None)]).get("/ui-api/rooms").json()["home"] == {
        "calm": True,
        "notice": None,
    }


def test_home_is_calm_only_when_nothing_is_pending(
    tenant_client: Als, session: Session
) -> None:
    zone = _raum(session)
    state = create_zone_state(session, zone)
    client = tenant_client([("zone.read", None)])
    assert client.get("/ui-api/rooms").json()["home"] == {"calm": True, "notice": None}
    state.window_open = True
    session.flush()
    home = client.get("/ui-api/rooms").json()["home"]
    assert home["calm"] is False
    assert home["notice"]["kind"] == "window"
    assert home["notice"]["zone_name"] == zone.display_name
    state.sensor_status_id = sensor_status_of(session, "veraltet").id
    state.measured_at = utcnow() - timedelta(hours=2)
    session.flush()
    notice = client.get("/ui-api/rooms").json()["home"]["notice"]
    assert notice["kind"] == "sensor"
    assert notice["measured_age_seconds"] >= 7000
    assert notice["stage_label"] is None


def test_home_puts_emergency_operation_before_everything_else(
    tenant_client: Als, session: Session
) -> None:
    zone = _raum(session)
    state = create_zone_state(session, zone)
    state.window_open = True
    device = create_device(session, "ersatz")
    session.add(
        ZoneSensorFailureState(
            zone_id=zone.id,
            stage=emergency_operation.STAGE_ERSATZQUELLE,
            active_source_device_id=device.id,
        )
    )
    session.flush()
    home = tenant_client([("zone.read", None)]).get("/ui-api/rooms").json()["home"]
    assert home["calm"] is False
    assert home["notice"]["kind"] == "notbetrieb"
    assert home["notice"]["stage_label"] and home["notice"]["detail"]
    assert home["notice"]["measured_age_seconds"] is None


def test_home_never_names_a_foreign_room(tenant_client: Als, session: Session) -> None:
    mine, other = _raum(session), create_zone(session, "fremd")
    create_zone_state(session, mine)
    create_zone_state(session, other).window_open = True
    session.flush()
    body = tenant_client([("zone.read", mine.id)]).get("/ui-api/rooms").json()
    assert body["home"] == {"calm": True, "notice": None}
    assert "Fremd" not in str(body)


def test_list_shows_only_visible_zones(client_als: Als, session: Session) -> None:
    mine = _raum(session)
    create_zone(session, "fremd")
    rooms = client_als([("zone.read", mine.id)]).get("/ui-api/rooms").json()["rooms"]
    assert [r["id"] for r in rooms] == [mine.id]
    assert "Fremd" not in str(rooms)


def _aussenquelle(session: Session, measured_at: datetime | None) -> None:
    """Richtet eine Außentemperaturquelle ein; mit Messwert, wenn `measured_at` gesetzt ist."""
    row = session.get(Setting, 1)
    assert row is not None
    device = create_device(session, "draussen")
    temperature = capability(session, "temperature")
    row.outdoor_temperature_source_device_id = device.id
    if measured_at is not None:
        session.add(
            Measurement(
                device_id=device.id,
                capability_id=temperature.id,
                value_numeric=Decimal("3.20"),
                measured_at=measured_at,
                received_at=measured_at,
            )
        )
    session.flush()


def test_list_carries_the_outdoor_reading_read_only_for_the_plant_view(
    client_als: Als, tenant_client: Als, session: Session
) -> None:
    """Außentemperatur wie die Alt-Startseite: eigener Text statt Zahl, wenn sie nicht stimmt."""
    _raum(session)
    admin = client_als(ALLES)
    # Ohne gewählte Quelle: keine Zahl, die man mit „draußen ist es warm“ verwechseln könnte.
    assert admin.get("/ui-api/rooms").json()["outdoor"] == {
        "status": "keine_quelle", "temperature_c": None, "measured_at": None,
    }
    # Frischer Wert.
    _aussenquelle(session, utcnow())
    outdoor = admin.get("/ui-api/rooms").json()["outdoor"]
    assert outdoor["status"] == "ok" and Decimal(outdoor["temperature_c"]) == Decimal("3.2")
    assert outdoor["measured_at"] is not None
    # Wohnungssicht: keine Anlagentechnik.
    assert tenant_client(ALLES).get("/ui-api/rooms").json()["outdoor"] is None


def test_list_never_shows_a_stale_outdoor_value_as_a_temperature(
    client_als: Als, session: Session
) -> None:
    _raum(session)
    _aussenquelle(session, utcnow() - timedelta(days=2))
    outdoor = client_als(ALLES).get("/ui-api/rooms").json()["outdoor"]
    assert outdoor["status"] == "veraltet"
    assert outdoor["temperature_c"] is None
    assert outdoor["measured_at"] is not None


def test_list_reports_a_forgotten_window_and_a_stale_measurement(
    client_als: Als, session: Session
) -> None:
    zone = _raum(session)
    state = create_zone_state(session, zone)
    client = client_als(ALLES)
    (room,) = client.get("/ui-api/rooms").json()["rooms"]
    assert room["sensor"]["window_alarm"] is False
    assert room["measurement_stale"] is False
    state.window_alarm = True
    state.sensor_status_id = sensor_status_of(session, "veraltet").id
    session.flush()
    (room,) = client.get("/ui-api/rooms").json()["rooms"]
    assert room["sensor"]["window_alarm"] is True
    assert room["measurement_stale"] is True
    # Auch die Einzelansicht trägt beide Felder.
    detail = client.get(f"/ui-api/rooms/{zone.id}").json()
    assert detail["sensor"]["window_alarm"] is True and detail["measurement_stale"] is True


def test_a_room_without_state_is_not_called_stale(client_als: Als, session: Session) -> None:
    _raum(session)
    (room,) = client_als(ALLES).get("/ui-api/rooms").json()["rooms"]
    assert room["measurement_stale"] is False


def test_list_without_setting_row(client_als: Als, session: Session) -> None:
    client = client_als([("zone.read", None)])
    body = client.get("/ui-api/rooms").json()
    assert body["rooms"] == []
    assert body["actuation"]["state"] == "dry_run"
    assert body["outdoor"] is None
    create_zone(session, "a")
    _fehler(client.get("/ui-api/rooms"), 503, "setup_incomplete")


def test_detail_has_modes_next_switch_vacation(client_als: Als, session: Session) -> None:
    zone = _raum(session)
    session.add(
        Vacation(
            starts_at=datetime(2099, 1, 1),
            ends_at=datetime(2099, 1, 9),
            setback_temperature_c=Decimal("15.0"),
            source_id=source(session, "web").id,
        )
    )
    session.flush()
    body = client_als([("zone.read", zone.id), ("setpoint.write", zone.id)]).get(
        f"/ui-api/rooms/{zone.id}"
    ).json()
    modes = {m["code"]: m for m in body["modes"]}
    assert modes["tag"]["temperature_c"] == "21.0"
    assert modes["nacht"]["temperature_c"] == "17.0"
    assert sum(m["is_current"] for m in body["modes"]) == 1
    assert body["next_switch"]["at"].endswith("+00:00")
    assert body["next_switch"]["temperature_c"] in {"21.0", "17.0"}
    assert body["next_switch"]["mode_name"] in {"Tag", "Nacht"}
    assert body["vacation"] == {
        "starts_at": "2099-01-01T00:00:00+00:00",
        "ends_at": "2099-01-09T00:00:00+00:00",
        "setback_temperature_c": "15.0",
        "running": False,
    }
    assert {k["code"] for k in body["operating_modes"]} == {"auto"}
    assert body["minimum_temperature_c"] and body["maximum_temperature_c"]


def test_detail_without_schedule_has_no_next_switch(client_als: Als, session: Session) -> None:
    source(session, "web")
    create_settings(session)
    zone = create_zone(session, "leer")
    body = client_als([("zone.read", None)]).get(f"/ui-api/rooms/{zone.id}").json()
    assert body["next_switch"] is None
    assert body["vacation"] is None
    assert body["day_track"] == []


def test_unknown_and_invisible_zone_are_identical_404(client_als: Als, session: Session) -> None:
    mine = _raum(session)
    other = create_zone(session, "fremd")
    client = client_als([("zone.read", mine.id)])
    hidden = client.get(f"/ui-api/rooms/{other.id}")
    unknown = client.get("/ui-api/rooms/999999")
    assert hidden.status_code == unknown.status_code == 404
    assert hidden.json() == unknown.json()
    assert hidden.json()["code"] == "not_found"


def test_tenant_profile_with_the_rights_may_change_the_plant(
    tenant_client: Als, session: Session
) -> None:
    zone = _raum(session)
    client = tenant_client(ALLES)
    body = client.get(f"/ui-api/rooms/{zone.id}").json()
    assert body["actions"]["override"] is True
    assert body["actions"]["edit_setpoint_step"] is True
    assert body["actions"]["edit_mode_setpoints"] is True
    assert body["actions"]["set_operating_mode"] is True
    mode = _mode(session, "tag")
    response = _call(
        client, "PUT", f"/ui-api/rooms/{zone.id}/modes/{mode.id}/setpoint",
        {"temperature_c": "22.0"},
    )
    assert response.status_code == 200, response.text
    response = _call(client, "PUT", f"/ui-api/rooms/{zone.id}/operating-mode", {"code": "auto"})
    assert response.status_code == 200, response.text


def test_tenant_profile_without_the_rights_may_not_change_the_plant(
    tenant_client: Als, session: Session
) -> None:
    zone = _raum(session)
    client = tenant_client([("zone.read", None), ("override.create", None)])
    body = client.get(f"/ui-api/rooms/{zone.id}").json()
    assert body["actions"]["edit_mode_setpoints"] is False
    assert body["actions"]["set_operating_mode"] is False
    mode = _mode(session, "tag")
    _fehler(
        _call(client, "PUT", f"/ui-api/rooms/{zone.id}/modes/{mode.id}/setpoint",
              {"temperature_c": "22.0"}),
        403,
        "forbidden",
    )
    _fehler(
        _call(client, "PUT", f"/ui-api/rooms/{zone.id}/operating-mode", {"code": "auto"}),
        403,
        "forbidden",
    )


# -- Schreibende Endpunkte: gemeinsame Randfälle ----------------------------------------


def _schreibend(session: Session, zone: Zone) -> list[tuple[str, str, dict[str, Any], str]]:
    mode = _mode(session, "tag")
    return [
        ("POST", f"/ui-api/rooms/{zone.id}/override", {"temperature_c": "22.0"}, "override.create"),
        ("POST", f"/ui-api/rooms/{zone.id}/override/cancel", {}, "override.cancel"),
        (
            "PUT",
            f"/ui-api/rooms/{zone.id}/modes/{mode.id}/setpoint",
            {"temperature_c": "22.0"},
            "setpoint.write",
        ),
        (
            "POST",
            f"/ui-api/rooms/{zone.id}/setpoint-step",
            {"mode_id": mode.id, "direction": "up"},
            "setpoint.write",
        ),
        ("PUT", f"/ui-api/rooms/{zone.id}/operating-mode", {"code": "auto"}, "zone.manage"),
        ("POST", f"/ui-api/rooms/{zone.id}/jump-next", {}, "override.create"),
    ]


@pytest.mark.parametrize("index", range(6))
def test_writes_without_session_are_json_401(
    client: TestClient, session: Session, index: int
) -> None:
    zone = _raum(session)
    method, url, body, _perm = _schreibend(session, zone)[index]
    response = client.request(method, url, json=body)
    assert response.status_code == 401
    assert response.json() == {"detail": "Nicht angemeldet"}


@pytest.mark.parametrize("index", range(6))
def test_writes_without_permission_are_403(client_als: Als, session: Session, index: int) -> None:
    zone = _raum(session)
    method, url, body, perm = _schreibend(session, zone)[index]
    granted = [p for p in ALLES if p[0] != perm]
    before = session.query(ZoneOverride).count(), session.query(AuditEvent).count()
    _fehler(_call(client_als(granted), method, url, body), 403, "forbidden")
    assert (session.query(ZoneOverride).count(), session.query(AuditEvent).count()) == before


@pytest.mark.parametrize("index", range(6))
def test_writes_on_foreign_zone_are_404(client_als: Als, session: Session, index: int) -> None:
    zone = _raum(session)
    other = create_zone(session, "fremd")
    method, url, body, _perm = _schreibend(session, zone)[index]
    client = client_als([(code, other.id) for code, _ in ALLES])
    _fehler(_call(client, method, url, body), 404, "not_found")
    assert session.query(ZoneOverride).count() == 0


@pytest.mark.parametrize("index", range(6))
def test_writes_without_csrf_are_json_403(client_als: Als, session: Session, index: int) -> None:
    zone = _raum(session)
    method, url, body, _perm = _schreibend(session, zone)[index]
    response = client_als(ALLES).request(method, url, json=body, headers={CSRF_HEADER: "falsch"})
    assert response.status_code == 403
    assert response.json()["stale"] is True
    assert session.query(ZoneOverride).count() == 0


@pytest.mark.parametrize("index", [0, 2, 3, 4, 5])
def test_malformed_body_is_422_with_code(client_als: Als, session: Session, index: int) -> None:
    zone = _raum(session)
    method, url, _body, _perm = _schreibend(session, zone)[index]
    client = client_als(ALLES)
    bad = {"replace": "vielleicht"} if index == 5 else {"unerwartet": 1, "direction": "quer"}
    _fehler(_call(client, method, url, bad), 422, "validation")


# -- Übersteuerung ----------------------------------------------------------------------


def test_override_permanent_creates_row_and_protocol(client_als: Als, session: Session) -> None:
    zone = _raum(session)
    client = client_als(ALLES)
    response = _call(client, "POST", f"/ui-api/rooms/{zone.id}/override",
                     {"temperature_c": "22,5"})
    assert response.status_code == 200
    room = response.json()
    assert room["override"]["temperature_c"] == "22.5"
    assert room["override"]["ends_at"] is None
    assert room["setpoint"]["temperature_c"] == "22.5"
    assert room["plan_applies"] is False
    row = session.scalars(select(ZoneOverride)).one()
    assert (row.zone_id, row.temperature_c, row.ends_at) == (zone.id, Decimal("22.5"), None)
    assert row.created_by_user_id is not None
    entry = session.scalars(select(ChangeLog)).one()
    assert entry.zone_id == zone.id and entry.actor_user_id == row.created_by_user_id


def test_override_duration_and_next_switch(client_als: Als, session: Session) -> None:
    zone = _raum(session)
    client = client_als(ALLES)
    timed = _call(client, "POST", f"/ui-api/rooms/{zone.id}/override",
                  {"temperature_c": "20", "end": "duration", "duration_minutes": 30}).json()
    assert timed["override"]["ends_at"].endswith("+00:00")
    nxt = _call(client, "POST", f"/ui-api/rooms/{zone.id}/override",
                {"temperature_c": "19", "end": "next_switch"}).json()
    assert nxt["override"]["temperature_c"] == "19.0"
    assert nxt["override"]["ends_at"] is not None
    assert session.query(ZoneOverride).count() == 2


@pytest.mark.parametrize(
    "body, status, code",
    [
        ({"temperature_c": "warm"}, 422, "validation"),
        ({"temperature_c": "99"}, 422, "validation"),
        ({"temperature_c": "21", "end": "duration"}, 422, "validation"),
        ({"temperature_c": "21", "end": "duration", "duration_minutes": 0}, 422, "validation"),
    ],
)
def test_override_invalid_input(
    client_als: Als, session: Session, body: dict[str, Any], status: int, code: str
) -> None:
    zone = _raum(session)
    error = _fehler(_call(client_als(ALLES), "POST", f"/ui-api/rooms/{zone.id}/override", body),
                    status, code)
    assert error["field"]
    assert session.query(ZoneOverride).count() == 0


def test_override_to_next_switch_without_schedule_is_conflict(
    client_als: Als, session: Session
) -> None:
    source(session, "web")
    create_settings(session)
    zone = create_zone(session, "leer")
    _fehler(
        _call(client_als(ALLES), "POST", f"/ui-api/rooms/{zone.id}/override",
              {"temperature_c": "21", "end": "next_switch"}),
        409,
        "no_schedule",
    )
    assert session.query(ZoneOverride).count() == 0


def test_cancel_override(client_als: Als, session: Session) -> None:
    zone = _raum(session)
    client = client_als(ALLES)
    _fehler(_call(client, "POST", f"/ui-api/rooms/{zone.id}/override/cancel"), 409, "no_override")
    _call(client, "POST", f"/ui-api/rooms/{zone.id}/override", {"temperature_c": "22"})
    response = _call(client, "POST", f"/ui-api/rooms/{zone.id}/override/cancel")
    assert response.status_code == 200
    assert response.json()["override"] is None
    assert session.scalars(select(ZoneOverride)).one().cancelled_at is not None
    assert session.query(ChangeLog).count() == 2


# -- Sollwerte ---------------------------------------------------------------------------


def test_set_mode_setpoint_and_delete(client_als: Als, session: Session) -> None:
    zone = _raum(session)
    mode = _mode(session, "tag")
    client = client_als(ALLES)
    url = f"/ui-api/rooms/{zone.id}/modes/{mode.id}/setpoint"
    response = _call(client, "PUT", url, {"temperature_c": "22.5"})
    assert response.status_code == 200
    assert {m["code"]: m["temperature_c"] for m in response.json()["modes"]}["tag"] == "22.5"
    row = session.scalars(
        select(ZoneSetpoint).where(
            ZoneSetpoint.zone_id == zone.id, ZoneSetpoint.setpoint_mode_id == mode.id
        )
    ).one()
    assert row.temperature_c == Decimal("22.5")
    assert session.query(AuditEvent).filter_by(object_type="zone_setpoint").count() == 1
    deleted = _call(client, "PUT", url, {"temperature_c": None}).json()
    assert {m["code"]: m["temperature_c"] for m in deleted["modes"]}["tag"] is None


@pytest.mark.parametrize("value", ["abc", "99", "21.55"])
def test_set_mode_setpoint_invalid(client_als: Als, session: Session, value: str) -> None:
    zone = _raum(session)
    mode = _mode(session, "tag")
    error = _fehler(
        _call(client_als(ALLES), "PUT", f"/ui-api/rooms/{zone.id}/modes/{mode.id}/setpoint",
              {"temperature_c": value}),
        422,
        "validation",
    )
    assert error["field"]
    assert session.get(ZoneSetpoint, (zone.id, mode.id)).temperature_c == Decimal("21.0")  # type: ignore[union-attr]


def test_set_mode_setpoint_unknown_mode(client_als: Als, session: Session) -> None:
    zone = _raum(session)
    _fehler(
        _call(client_als(ALLES), "PUT", f"/ui-api/rooms/{zone.id}/modes/999999/setpoint",
              {"temperature_c": "21"}),
        404,
        "mode_not_found",
    )


def test_step_setpoint_moves_half_a_degree(client_als: Als, session: Session) -> None:
    zone = _raum(session)
    mode = _mode(session, "tag")
    client = client_als(ALLES)
    url = f"/ui-api/rooms/{zone.id}/setpoint-step"
    up = _call(client, "POST", url, {"mode_id": mode.id, "direction": "up"})
    assert up.status_code == 200
    assert {m["code"]: m["temperature_c"] for m in up.json()["modes"]}["tag"] == "21.5"
    _call(client, "POST", url, {"mode_id": mode.id, "direction": "down"})
    _call(client, "POST", url, {"mode_id": mode.id, "direction": "down"})
    session.expire_all()
    assert session.get(ZoneSetpoint, (zone.id, mode.id)).temperature_c == Decimal("20.5")  # type: ignore[union-attr]
    assert session.query(AuditEvent).filter_by(object_type="zone_setpoint").count() == 3


def test_step_setpoint_unknown_mode_and_limit(client_als: Als, session: Session) -> None:
    zone = _raum(session)
    mode = _mode(session, "tag")
    client = client_als(ALLES)
    url = f"/ui-api/rooms/{zone.id}/setpoint-step"
    _fehler(_call(client, "POST", url, {"mode_id": 999999, "direction": "up"}), 404,
            "mode_not_found")
    row = session.get(ZoneSetpoint, (zone.id, mode.id))
    assert row is not None
    row.temperature_c = Decimal("35.0")
    session.flush()
    error = _fehler(_call(client, "POST", url, {"mode_id": mode.id, "direction": "up"}), 409,
                    "limit_reached")
    assert error["field"]
    assert row.temperature_c == Decimal("35.0")


# -- Betriebsart -------------------------------------------------------------------------


def test_change_operating_mode(client_als: Als, session: Session) -> None:
    zone = _raum(session)
    session.add(OperatingMode(code="off", label="Aus"))
    session.flush()
    client = client_als(ALLES)
    url = f"/ui-api/rooms/{zone.id}/operating-mode"
    response = _call(client, "PUT", url, {"code": "off"})
    assert response.status_code == 200
    assert response.json()["operating_mode"] == {"code": "off", "label": "Aus"}
    assert response.json()["plan_applies"] is False
    session.refresh(zone)
    assert zone.operating_mode.code == "off"
    assert session.query(AuditEvent).filter_by(object_type="zone").count() == 1
    _call(client, "PUT", url, {"code": "off"})
    assert session.query(AuditEvent).filter_by(object_type="zone").count() == 1


def test_change_operating_mode_unknown_code(client_als: Als, session: Session) -> None:
    zone = _raum(session)
    error = _fehler(
        _call(client_als(ALLES), "PUT", f"/ui-api/rooms/{zone.id}/operating-mode",
              {"code": "turbo"}),
        422,
        "validation",
    )
    assert error["field"] == "code"


# -- Nächste Phase vorziehen -------------------------------------------------------------


def test_jump_next_creates_override_ending_at_next_switch(
    client_als: Als, session: Session
) -> None:
    zone = _raum(session)
    client = client_als(ALLES)
    url = f"/ui-api/rooms/{zone.id}/jump-next"
    response = _call(client, "POST", url, {})
    assert response.status_code == 200
    room = response.json()
    assert room["override"]["ends_at"] is not None
    assert room["override"]["temperature_c"] in {"21.0", "17.0"}
    row = session.scalars(select(ZoneOverride)).one()
    assert row.zone_id == zone.id and row.ends_at is not None
    # Läuft schon eine Übersteuerung: Konflikt, nichts doppelt; mit replace ersetzt.
    conflict = _fehler(_call(client, "POST", url, {}), 409, "override_running")
    assert conflict["field"] == "override"
    assert session.query(ZoneOverride).count() == 1
    replaced = _call(client, "POST", url, {"replace": True})
    assert replaced.status_code == 200
    rows = session.scalars(select(ZoneOverride).order_by(ZoneOverride.id)).all()
    assert len(rows) == 2 and rows[0].cancelled_at is not None and rows[1].cancelled_at is None


def test_jump_next_without_schedule(client_als: Als, session: Session) -> None:
    source(session, "web")
    create_settings(session)
    zone = create_zone(session, "leer")
    _fehler(_call(client_als(ALLES), "POST", f"/ui-api/rooms/{zone.id}/jump-next", {}), 409,
            "no_schedule")


def test_actions_without_setup_row_are_503(client_als: Als, session: Session) -> None:
    source(session, "web")
    zone = create_zone(session, "a")
    client = client_als(ALLES)
    mode = SetpointMode(code="x", name="X")
    session.add(mode)
    session.flush()
    for method, url, body in [
        ("POST", f"/ui-api/rooms/{zone.id}/jump-next", {}),
        ("POST", f"/ui-api/rooms/{zone.id}/setpoint-step", {"mode_id": mode.id, "direction": "up"}),
        ("GET", f"/ui-api/rooms/{zone.id}", None),
    ]:
        _fehler(_call(client, method, url, body), 503, "setup_incomplete")


def test_rooms_router_carries_csrf_protection() -> None:
    from thermoctl.auth.dependencies import csrf_protection
    from thermoctl.web.ui_api_rooms import router

    assert csrf_protection in [d.dependency for d in router.dependencies]
    assert not router.include_in_schema
