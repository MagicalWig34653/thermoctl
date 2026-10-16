"""Ersatztests fuer Sicherheitsaussagen der entfernten Alt-Oberflaeche (Raeume, Parameter,
Zeitplan, Betriebsarten, Kiosk). Siehe `docs/ui-migration/SICHERHEITSTESTS.md`.
"""

# ruff: noqa: E501

from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.helpers import create_mode, create_zone, source
from tests.test_kiosk_api import _cookie, _setup
from tests.test_kiosk_api import _csrf as _kiosk_csrf
from tests.test_ui_api_operations import ALLES, Als, _anlage, _call, _ereignisse, _fehler, _schalter
from tests.test_ui_api_rooms import ALLES as RAUM_ALLES
from tests.test_ui_api_rooms import _raum
from tests.test_ui_api_schedule import (
    PLAN,
    _day_times,
    _mode,
    _plan_leeren,
    _points,
    _version,
)
from tests.test_ui_api_schedule import _call as _schedule_call
from tests.test_ui_api_schedule import _fehler as _schedule_fehler
from thermoctl.db.base import utcnow
from thermoctl.db.models.operations import AuditEvent
from thermoctl.db.models.override import ZoneOverride
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.zone import ZoneSetpoint


def test_switching_pi_on_is_audited_naming_the_room(client_als: Als, session: Session) -> None:
    _, zone = _anlage(session)
    _schalter(session, zone)
    client = client_als(ALLES)
    url = f"/ui-api/rooms/{zone.id}/parameters"
    assert _call(client, "PUT", url, {"pi_enabled": True, "pi_confirm": True}).status_code == 200
    assert zone.pi_enabled is True
    events = _ereignisse(session, "zone_settings")
    assert events
    assert any(zone.display_name in event.summary for event in events)
    assert all(event.actor_user_id is not None for event in events)


def test_an_unchanged_window_temp_drop_switch_adds_no_second_audit_entry(
    client_als: Als, session: Session
) -> None:
    _, zone = _anlage(session)
    client = client_als(ALLES)
    url = f"/ui-api/rooms/{zone.id}/parameters"
    before = len(_ereignisse(session, "zone_settings"))
    assert _call(client, "PUT", url, {}).status_code == 200
    # Gespeichert wird immer protokolliert; der unveraenderte Schalter ergaenzt nichts.
    assert len(_ereignisse(session, "zone_settings")) == before + 1
    assert _call(client, "PUT", url, {"window_temp_drop_detection_enabled": False}).status_code == 200
    assert len(_ereignisse(session, "zone_settings")) == before + 2


def test_stepping_a_mode_the_room_does_not_show_conjures_no_setpoint(
    client_als: Als, session: Session
) -> None:
    zone = _raum(session)
    unused = create_mode(session, "nie-benutzt")
    client = client_als(RAUM_ALLES)
    _fehler(
        _call(client, "POST", f"/ui-api/rooms/{zone.id}/setpoint-step", {"mode_id": unused.id, "direction": "up"}),
        404,
        "mode_not_found",
    )
    assert session.get(ZoneSetpoint, (zone.id, unused.id)) is None


def test_a_blank_mode_code_is_refused_and_the_mode_stays(client_als: Als, session: Session) -> None:
    _raum(session)
    mode = _mode(session, "tag")
    client = client_als(PLAN)
    response = _schedule_call(client, "PUT", f"/ui-api/modes/{mode.id}", {"code": "  ", "name": "Neuer Name"})
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "validation"
    session.expire_all()
    assert mode.code == "tag" and mode.name != "Neuer Name"


def test_setting_up_an_empty_plan_refuses_a_day_outside_the_week_and_unusable_times(
    client_als: Als, session: Session
) -> None:
    zone = _raum(session)
    _plan_leeren(session, zone)
    client = client_als(PLAN)
    version = _version(client, zone)
    for first, second, extra, field in (
        (390, 1320, {"weekday": 9}, "weekday"),
        (390, 1320, {"weekday": 0}, "weekday"),
        (1440, 1320, {}, "time_of_day"),
        (-1, 1320, {}, "time_of_day"),
        (390, 390, {}, "time_of_day"),
    ):
        error = _schedule_fehler(_day_times(client, zone, version, first, second, **extra), 422, "validation")
        assert error["field"] == field
    assert _points(session, zone) == []


def test_a_kiosk_cannot_cancel_the_running_override_of_a_room_outside_its_scope(
    client: TestClient, session: Session
) -> None:
    _zone, _admin_user, _token, plaintext = _setup(session)
    foreign = create_zone(session, "keller")
    session.add(
        ZoneOverride(
            zone_id=foreign.id,
            temperature_c=Decimal("22.0"),
            starts_at=utcnow(),
            ends_at=None,
            source_id=source(session, "web").id,
        )
    )
    session.flush()
    _cookie(client, plaintext)
    response = client.post(f"/kiosk-api/rooms/{foreign.id}/override/cancel", headers=_kiosk_csrf(plaintext))
    assert response.status_code == 404
    session.expire_all()
    assert session.query(ZoneOverride).filter_by(zone_id=foreign.id).one().cancelled_at is None
    assert session.query(AuditEvent).filter_by(object_type="zone_setpoint").count() == 0


def test_a_point_of_another_zone_can_neither_change_its_mode_nor_be_deleted(
    client_als: Als, session: Session
) -> None:
    zone = _raum(session)
    other = create_zone(session, "bad")
    tag, nacht = _mode(session, "tag"), _mode(session, "nacht")
    foreign = SchedulePoint(zone_id=other.id, weekday=1, minute_of_day=5, setpoint_mode_id=tag.id)
    session.add(foreign)
    session.flush()
    client = client_als(PLAN)
    version = _version(client, zone)
    base = f"/ui-api/schedule/{zone.id}/points/{foreign.id}"
    _schedule_fehler(
        _schedule_call(client, "PUT", f"{base}/mode", {"version": version, "mode_id": nacht.id}),
        404,
        "point_not_found",
    )
    _schedule_fehler(
        _schedule_call(client, "DELETE", f"{base}?version={version}"), 404, "point_not_found"
    )
    session.expire_all()
    assert session.get(SchedulePoint, foreign.id) is not None
    assert foreign.setpoint_mode_id == tag.id
