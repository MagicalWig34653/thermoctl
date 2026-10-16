"""Schließt Testlücken der abgelösten Alt-Oberfläche für Räume, Notbetrieb-Banner, Urlaub, Zonen.

Gegen `/ui-api/rooms`, `/kiosk-api/state`, `/ui-api/vacation`, `/ui-api/room-form` und
`/ui-api/rooms/{id}/delete-impact`.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.helpers import (
    create_device,
    create_settings,
    create_shadow_decision,
    create_zone,
    create_zone_state,
    role,
    source,
    user_with_permissions,
    zone_with_schedule,
)
from thermoctl.auth.csrf import CSRF_HEADER, csrf_token
from thermoctl.auth.kiosk import KIOSK_COOKIE_NAME, KIOSK_CSRF_COOKIE_NAME
from thermoctl.auth.sessions import COOKIE_NAME
from thermoctl.config import get_settings
from thermoctl.db.models.device import ZoneDevice
from thermoctl.db.models.operations import AuditEvent, Setting
from thermoctl.db.models.override import ZoneOverride
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.sensor_failure import ZoneSensorFailureState
from thermoctl.db.models.state import ShadowDecision
from thermoctl.db.models.vacation import Vacation
from thermoctl.db.models.zone import SetpointMode, Zone
from thermoctl.domain import emergency_operation
from thermoctl.domain.kiosk import issue_kiosk_token
from thermoctl.domain.schedule import next_switch
from thermoctl.domain.zones import latest_decisions_by_zone

Als = Callable[[list[tuple[str, int | None]]], TestClient]

LESEN: list[tuple[str, int | None]] = [("zone.read", None)]
OVERRIDE: list[tuple[str, int | None]] = [("zone.read", None), ("override.create", None)]


def _csrf(client: TestClient) -> dict[str, str]:
    cookie = client.cookies.get(COOKIE_NAME)
    assert cookie is not None
    return {CSRF_HEADER: csrf_token(cookie, get_settings().secret_key.get_secret_value())}


def _call(client: TestClient, method: str, url: str, body: dict[str, Any] | None = None) -> Any:
    return client.request(method, url, json=body, headers=_csrf(client))


def _fixed_now(monkeypatch: pytest.MonkeyPatch, moment: datetime) -> None:
    monkeypatch.setattr("thermoctl.web.ui_api_rooms.utcnow", lambda: moment)


def _plan(session: Session, name: str, points: list[tuple[int, int, str, Decimal]]) -> Zone:
    """Wie `zone_with_schedule`, aber mit beliebig vielen Schaltpunkten je Modus."""
    seen: dict[str, tuple[int, int, str, Decimal]] = {}
    for point in points:
        seen.setdefault(point[2], point)
    zone = zone_with_schedule(session, name, list(seen.values()))
    for weekday, minute, code, _temperature in points:
        if (weekday, minute, code) in {(p[0], p[1], p[2]) for p in seen.values()}:
            continue
        mode = session.query(SetpointMode).filter_by(code=code).one()
        session.add(
            SchedulePoint(
                zone_id=zone.id, weekday=weekday, minute_of_day=minute, setpoint_mode_id=mode.id
            )
        )
    session.flush()
    return zone


# -- Tagesverlauf in der Zeit der Anlage ---------------------------------------------------


@pytest.mark.parametrize(
    ("utc_now", "plan_of", "fraction"),
    [
        # Winter, UTC+1: So 23:30 UTC ist Mo 00:30 Berlin.
        (datetime(2026, 1, 4, 23, 30), "montag", 30 * 100 / 1440),
        # Sommer, UTC+2: So 22:30 UTC ist Mo 00:30 Berlin.
        (datetime(2026, 8, 2, 22, 30), "montag", 30 * 100 / 1440),
        # Eine Stunde davor ist es lokal noch Sonntag 23:30.
        (datetime(2026, 8, 2, 21, 30), "sonntag", (23 * 60 + 30) * 100 / 1440),
    ],
)
def test_the_day_track_and_now_follow_the_local_weekday_near_midnight(
    client_als: Als,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
    utc_now: datetime,
    plan_of: str,
    fraction: float,
) -> None:
    source(session, "web")
    # Montag (1): Nacht bis 06:00, dann Tag.  Sonntag (7): ganztägig Tag.
    _plan(
        session,
        "plan",
        [
            (1, 0, "nacht", Decimal("17.0")),
            (1, 360, "tag", Decimal("21.0")),
            (7, 0, "tag", Decimal("21.0")),
        ],
    )
    _fixed_now(monkeypatch, utc_now)

    body = client_als(LESEN).get("/ui-api/rooms").json()

    (room,) = body["rooms"]
    track = [(s["start_minute"], s["end_minute"], s["mode_name"]) for s in room["day_track"]]
    if plan_of == "montag":
        assert track == [(0, 360, "Nacht"), (360, 1440, "Tag")]
    else:
        assert track == [(0, 1440, "Tag")]
    assert body["now_fraction"] == pytest.approx(fraction)
    assert body["timezone"] == "Europe/Berlin"


# -- Weitere Lesefelder ----------------------------------------------------------------------


def _plan_zone(session: Session) -> Any:
    source(session, "web")
    return _plan(
        session,
        "wohnen",
        [
            (day, minute, mode, temp)
            for day in range(1, 8)
            for minute, mode, temp in (
                (420, "tag", Decimal("21.0")),
                (1320, "nacht", Decimal("17.0")),
            )
        ],
    )


def test_an_override_names_itself_in_the_setpoint_reason(client_als: Als, session: Session) -> None:
    zone = _plan_zone(session)
    client = client_als(OVERRIDE)

    before = client.get("/ui-api/rooms").json()["rooms"][0]["setpoint"]["reason"]
    created = _call(
        client,
        "POST",
        f"/ui-api/rooms/{zone.id}/override",
        {"temperature_c": "23", "end": "permanent"},
    )
    assert created.status_code == 200, created.text
    room = client.get("/ui-api/rooms").json()["rooms"][0]

    assert "Übersteuerung" not in before
    assert room["setpoint"]["reason"] == "Übersteuerung (feste Temperatur)"
    assert room["setpoint"]["temperature_c"] == "23.0"
    assert room["setpoint"]["mode_id"] is None and room["setpoint"]["mode_name"] is None
    assert room["plan_applies"] is False


def test_the_latest_decision_wins_by_time_then_by_id(session: Session) -> None:
    create_settings(session)
    zone = create_zone(session, "entscheidung")
    other = create_zone(session, "andere")
    first = create_shadow_decision(session, zone)
    second = create_shadow_decision(session, zone)  # gleiche Zeit, höhere id
    older = create_shadow_decision(session, zone)
    older.decided_at = first.decided_at - timedelta(minutes=5)  # höchste id, aber älter
    foreign = create_shadow_decision(session, other)
    session.flush()
    assert first.decided_at == second.decided_at and second.id > first.id
    assert older.id > second.id

    found = latest_decisions_by_zone(session, [zone.id, other.id])

    assert found[zone.id].id == second.id
    assert found[other.id].id == foreign.id
    assert latest_decisions_by_zone(session, []) == {}


def test_the_overview_shows_the_latest_of_several_decisions(
    client_als: Als, session: Session
) -> None:
    zone = _plan_zone(session)
    stamp = datetime(2026, 8, 29, 8, 0)
    for reason, would_heat in (("erste", False), ("zweite", True)):
        decision = ShadowDecision(
            decided_at=stamp,
            zone_id=zone.id,
            setpoint_reason="Zeitplan",
            would_heat=would_heat,
            outcome_code="an" if would_heat else "aus",
            reason=reason,
        )
        session.add(decision)
        session.flush()

    (room,) = client_als(LESEN).get("/ui-api/rooms").json()["rooms"]

    assert room["decision"]["reason"] == "zweite" and room["decision"]["would_heat"] is True


def test_the_poll_interval_is_the_configured_control_interval(
    client_als: Als, session: Session
) -> None:
    _plan_zone(session)
    client = client_als(LESEN)
    assert client.get("/ui-api/rooms").json()["poll_interval_seconds"] == 60  # Vorgabe

    setting = session.get(Setting, 1)
    assert setting is not None
    setting.shadow_interval_seconds = 17
    session.flush()

    assert client.get("/ui-api/rooms").json()["poll_interval_seconds"] == 17


def test_the_next_switch_line_is_what_the_domain_computes(
    client_als: Als, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    zone = _plan_zone(session)
    now = datetime(2026, 8, 5, 12, 0)  # Mittwoch 14:00 lokal: nächster Wechsel 22:00 lokal
    _fixed_now(monkeypatch, now)
    expected = next_switch(session, zone, now)
    assert expected is not None

    body = client_als(LESEN).get(f"/ui-api/rooms/{zone.id}").json()

    shown = body["next_switch"]
    assert shown["at"] == expected.at.replace(tzinfo=UTC).isoformat()
    assert shown["temperature_c"] == format(expected.setpoint.temperature_c, "f")
    assert shown["reason"] == expected.setpoint.reason
    assert shown["mode_id"] == expected.setpoint.mode_id
    # Und es ist tatsächlich der Nachtwechsel um 22:00 lokal (20:00 UTC), nicht irgendeiner.
    assert shown["at"] == "2026-08-05T20:00:00+00:00"
    assert shown["temperature_c"] == "17.0" and shown["mode_name"] == "Nacht"


def test_a_forgotten_window_shows_the_window_alarm(client_als: Als, session: Session) -> None:
    zone = _plan_zone(session)
    state = create_zone_state(session, zone)
    state.window_open = True
    state.window_alarm = True
    session.flush()

    (room,) = client_als(LESEN).get("/ui-api/rooms").json()["rooms"]

    assert room["sensor"]["window_open"] is True and room["sensor"]["window_alarm"] is True


# -- Notbetrieb: Ersatzquelle-Banner mit echtem Zustand -----------------------------------------


def _ersatzquelle(session: Session, zone_id: int, device_name: str) -> None:
    device = create_device(session, f"ersatz-{zone_id}")
    device.display_name = device_name
    session.add(
        ZoneSensorFailureState(
            zone_id=zone_id,
            stage=emergency_operation.STAGE_ERSATZQUELLE,
            active_source_device_id=device.id,
        )
    )
    session.flush()


def test_the_ersatzquelle_banner_reaches_the_rooms_of_an_administrator(
    client_als: Als, session: Session
) -> None:
    create_settings(session)
    broken = create_zone(session, "kueche")
    fine = create_zone(session, "flur")
    _ersatzquelle(session, broken.id, "Thermostat Küche")

    rooms = {r["id"]: r for r in client_als(LESEN).get("/ui-api/rooms").json()["rooms"]}

    banner = rooms[broken.id]["emergency"]
    assert banner["stage"] == emergency_operation.STAGE_ERSATZQUELLE
    assert banner["headline"] == "Ersatzquelle aktiv: Thermostat Küche"
    assert banner["detail"]
    assert rooms[fine.id]["emergency"] is None


def test_the_ersatzquelle_banner_reaches_the_rooms_of_a_tenant(
    tenant_client: Als, session: Session
) -> None:
    create_settings(session)
    zone = create_zone(session, "schlafen")
    _ersatzquelle(session, zone.id, "Thermostat Flur")

    (room,) = tenant_client([("zone.read", zone.id)]).get("/ui-api/rooms").json()["rooms"]

    assert room["emergency"]["headline"] == "Ersatzquelle aktiv: Thermostat Flur"
    assert room["emergency"]["stage"] == emergency_operation.STAGE_ERSATZQUELLE


def test_the_kiosk_state_carries_the_ersatzquelle_banner_of_the_real_state(
    client: TestClient, session: Session
) -> None:
    source(session, "kiosk")
    source(session, "web")
    create_settings(session)
    zone = create_zone(session, "kiosk-kueche")
    other = create_zone(session, "kiosk-ruhig")
    admin = user_with_permissions(
        session, "kiosk-admin", [("token.manage", None), ("zone.read", None)]
    )
    _ersatzquelle(session, zone.id, "Thermostat Bad")
    _token, plaintext = issue_kiosk_token(
        session, admin, "Küche", [zone.id, other.id], control_allowed=False, expires_at=None
    )
    client.cookies.set(KIOSK_COOKIE_NAME, plaintext)
    client.cookies.set(
        KIOSK_CSRF_COOKIE_NAME,
        csrf_token(plaintext, get_settings().secret_key.get_secret_value()),
    )

    rooms = {r["id"]: r for r in client.get("/kiosk-api/state").json()["rooms"]}

    assert rooms[zone.id]["emergency"]["headline"] == "Ersatzquelle aktiv: Thermostat Bad"
    assert rooms[zone.id]["emergency"]["detail"]
    assert rooms[other.id]["emergency"] is None


# -- Urlaub beenden ---------------------------------------------------------------------------


def test_ending_a_vacation_writes_an_audit_event(client_als: Als, session: Session) -> None:
    create_settings(session)
    source(session, "web")
    now = datetime.now(UTC).replace(tzinfo=None)
    vacation = Vacation(
        starts_at=now - timedelta(days=1),
        ends_at=now + timedelta(days=5),
        setback_temperature_c=Decimal("15.0"),
        source_id=source(session, "web").id,
    )
    session.add(vacation)
    session.flush()
    client = client_als([("zone.read", None), ("vacation.manage", None)])
    assert (
        session.scalars(select(AuditEvent).where(AuditEvent.object_type == "vacation")).all() == []
    )

    response = _call(client, "DELETE", "/ui-api/vacation")

    assert response.status_code == 200, response.text
    assert response.json()["vacation"] is None
    (event,) = session.scalars(select(AuditEvent).where(AuditEvent.object_type == "vacation")).all()
    assert event.action == "update" and event.object_id == str(vacation.id)
    assert event.summary == "Urlaubsbetrieb vorzeitig beendet"
    assert event.actor_user_id is not None


# -- Raum anlegen / löschen -----------------------------------------------------------------------


def test_delete_impact_counts_every_kind_of_dependency_for_a_zone_bound_manager(
    client_als: Als, session: Session
) -> None:
    source(session, "web")
    zone = zone_with_schedule(
        session,
        "voll",
        [(1, 420, "tag", Decimal("21.0")), (1, 1320, "nacht", Decimal("17.0"))],
    )
    other = create_zone(session, "daneben")
    for target, name in ((zone, "ventil-a"), (zone, "ventil-b"), (other, "ventil-c")):
        session.add(
            ZoneDevice(
                zone_id=target.id,
                device_id=create_device(session, name).id,
                device_role_id=role(session, "actuator").id,
            )
        )
    session.add(
        ZoneOverride(
            zone_id=zone.id,
            temperature_c=Decimal("22.0"),
            starts_at=datetime(2026, 8, 1),
            ends_at=None,
            source_id=source(session, "web").id,
        )
    )
    create_shadow_decision(session, zone)
    create_shadow_decision(session, zone)
    create_shadow_decision(session, zone)
    create_shadow_decision(session, other)
    session.flush()
    client = client_als([("zone.read", zone.id), ("zone.manage", zone.id)])

    body = client.get(f"/ui-api/rooms/{zone.id}/delete-impact").json()

    counts = {
        k: body[k]
        for k in ("schedule_points", "devices", "setpoints", "overrides", "shadow_decisions")
    }
    # Zeitplan 2 Punkte; Sollwerte: je Modus (Tag, Nacht) plus Frostschutz; Fremdes zählt nicht.
    assert counts == {
        "schedule_points": 2,
        "devices": 2,
        "setpoints": 3,
        "overrides": 1,
        "shadow_decisions": 3,
    }
    assert all(value > 0 for value in counts.values())


def test_the_empty_room_form_needs_an_installation_wide_permission(
    client_als: Als, session: Session
) -> None:
    create_settings(session)
    zone = create_zone(session, "eigener")
    bound = client_als([("zone.read", zone.id), ("zone.manage", zone.id)])

    refused = bound.get("/ui-api/room-form")

    assert refused.status_code == 403, refused.text
    assert refused.json()["code"] == "forbidden"
    # Mit zone_id für den eigenen Raum geht es weiter; anlagenweit auch ohne zone_id.
    assert bound.get("/ui-api/room-form", params={"zone_id": zone.id}).status_code == 200
    everywhere = client_als([("zone.read", None), ("zone.manage", None)])
    assert everywhere.get("/ui-api/room-form").status_code == 200
