import base64

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.helpers import create_mode, create_settings, create_zone, source
from thermoctl.auth.csrf import csrf_token
from thermoctl.auth.sessions import COOKIE_NAME
from thermoctl.config import get_settings
from thermoctl.db.models.operations import AuditEvent
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.domain.schedule import (
    ScheduleError,
    change_schedule_point_mode,
    copy_schedule_day,
    paint_schedule_interval,
    schedule_snapshot,
    undo_schedule_gesture,
)
from thermoctl.web.shared import schedule as schedule_shared


def _snapshot(session: Session, zone_id: int) -> tuple[tuple[int, int, int], ...]:
    return schedule_snapshot(list(session.scalars(
        select(SchedulePoint).where(SchedulePoint.zone_id == zone_id)
    )))


def test_painting_an_empty_schedule_returns_to_frost_protection(
    session: Session,
) -> None:
    settings = create_settings(session)
    source(session)
    zone = create_zone(session, "erste-malerei")
    comfort = create_mode(session, "komfort", "Komfort")

    result = paint_schedule_interval(
        session, zone, weekday=1, start_minute=390, end_minute=480,
        mode_id=comfort.id, user_id=None,
    )

    assert result is not None
    assert _snapshot(session, zone.id) == (
        (1, 390, comfort.id), (1, 480, settings.frost_protection_mode_id)
    )


def test_painting_over_a_whole_existing_section_minimizes_the_points(
    session: Session,
) -> None:
    create_settings(session)
    source(session)
    zone = create_zone(session, "übermalen")
    night = create_mode(session, "nacht-über", "Nacht")
    day = create_mode(session, "tag-über", "Tag")
    away = create_mode(session, "weg-über", "Abwesend")
    _point(session, zone.id, 1, 0, night.id)
    _point(session, zone.id, 1, 360, day.id)
    _point(session, zone.id, 1, 420, away.id)
    _point(session, zone.id, 1, 600, night.id)

    paint_schedule_interval(
        session, zone, weekday=1, start_minute=330, end_minute=660,
        mode_id=day.id, user_id=None,
    )

    assert _snapshot(session, zone.id) == ((1, 330, day.id), (1, 660, night.id))


def test_painting_exactly_the_mode_already_there_is_a_no_op(session: Session) -> None:
    create_settings(session)
    source(session)
    zone = create_zone(session, "gleich")
    night = create_mode(session, "nacht-gleich", "Nacht")
    day = create_mode(session, "tag-gleich", "Tag")
    _point(session, zone.id, 1, 0, night.id)
    _point(session, zone.id, 1, 360, day.id)
    _point(session, zone.id, 1, 480, night.id)
    before = _snapshot(session, zone.id)

    assert paint_schedule_interval(
        session, zone, weekday=1, start_minute=360, end_minute=480,
        mode_id=day.id, user_id=None,
    ) is None
    assert _snapshot(session, zone.id) == before
    entries = session.scalars(
        select(AuditEvent).where(AuditEvent.object_type == "schedule")
    ).all()
    assert entries == []


def test_painting_across_midnight_is_rejected(session: Session) -> None:
    create_settings(session)
    zone = create_zone(session, "mitternacht")
    mode = create_mode(session, "nacht-mitternacht", "Nacht")
    with pytest.raises(ScheduleError, match="Mitternacht"):
        paint_schedule_interval(
            session, zone, weekday=1, start_minute=1380, end_minute=60,
            mode_id=mode.id, user_id=None,
        )


@pytest.mark.parametrize(
    ("weekday", "start", "end", "mode_id"),
    [(0, 60, 120, 1), (1, -1, 120, 1), (1, 60, 1441, 1), (1, 60, 120, 999999)],
)
def test_painting_rejects_invalid_domain_boundaries(
    session: Session, weekday: int, start: int, end: int, mode_id: int,
) -> None:
    create_settings(session)
    zone = create_zone(session, f"grenze-{weekday}-{start}-{end}-{mode_id}")
    with pytest.raises(ScheduleError):
        paint_schedule_interval(
            session, zone, weekday=weekday, start_minute=start, end_minute=end,
            mode_id=mode_id, user_id=None,
        )


def test_painting_to_end_of_sunday_restores_monday_mode(session: Session) -> None:
    create_settings(session)
    source(session)
    zone = create_zone(session, "sonntag-ende")
    night = create_mode(session, "sonntag-nacht", "Nacht")
    comfort = create_mode(session, "sonntag-komfort", "Komfort")
    _point(session, zone.id, 1, 0, night.id)
    paint_schedule_interval(
        session, zone, weekday=7, start_minute=1380, end_minute=1440,
        mode_id=comfort.id, user_id=None,
    )
    assert (7, 1380, comfort.id) in _snapshot(session, zone.id)


def test_copying_a_day_and_undoing_are_each_one_audited_gesture(session: Session) -> None:
    create_settings(session)
    source(session)
    zone = create_zone(session, "tag-kopieren")
    night = create_mode(session, "nacht-kopie", "Nacht")
    day = create_mode(session, "tag-kopie", "Tag")
    _point(session, zone.id, 1, 0, night.id)
    _point(session, zone.id, 1, 360, day.id)
    _point(session, zone.id, 1, 480, night.id)
    snapshots = copy_schedule_day(
        session, zone, source_weekday=1, target_weekdays=[1, 2, 3, 4, 5],
        user_id=None,
    )
    assert snapshots is not None
    before, after, revision = snapshots
    assert all((day_number, 360, day.id) in after for day_number in range(1, 6))
    undo_schedule_gesture(
        session, zone, before=before, expected_after=after,
        expected_revision=revision, user_id=None
    )
    assert _snapshot(session, zone.id) == before
    entries = session.scalars(
        select(AuditEvent).where(AuditEvent.object_type == "schedule")
    ).all()
    assert len(entries) == 2


def test_undo_refuses_to_overwrite_a_later_edit(session: Session) -> None:
    create_settings(session)
    source(session)
    zone = create_zone(session, "altes-undo")
    mode = create_mode(session, "undo-mode", "Tag")
    result = paint_schedule_interval(
        session, zone, weekday=1, start_minute=360, end_minute=480,
        mode_id=mode.id, user_id=None,
    )
    assert result is not None
    before, after, revision = result
    _point(session, zone.id, 2, 600, mode.id)
    with pytest.raises(ScheduleError, match="inzwischen"):
        undo_schedule_gesture(
            session, zone, before=before, expected_after=after,
            expected_revision=revision, user_id=None
        )


def test_copying_an_empty_or_only_source_day_is_a_no_op(session: Session) -> None:
    create_settings(session)
    zone = create_zone(session, "leere-kopie")
    assert copy_schedule_day(
        session, zone, source_weekday=1, target_weekdays=[2], user_id=None
    ) is None
    mode = create_mode(session, "nur-quelle", "Tag")
    _point(session, zone.id, 1, 360, mode.id)
    assert copy_schedule_day(
        session, zone, source_weekday=1, target_weekdays=[1], user_id=None
    ) is None
    with pytest.raises(ScheduleError):
        copy_schedule_day(
            session, zone, source_weekday=8, target_weekdays=[1], user_id=None
        )


def test_copying_an_already_copied_pattern_is_idempotent(session: Session) -> None:
    create_settings(session)
    zone = create_zone(session, "idempotent-copy")
    day = create_mode(session, "idempotent-copy-day", "Tag")
    night = create_mode(session, "idempotent-copy-night", "Nacht")
    _point(session, zone.id, 1, 540, day.id)
    _point(session, zone.id, 1, 1080, night.id)

    first = copy_schedule_day(
        session, zone, source_weekday=1, target_weekdays=list(range(1, 8)), user_id=None
    )
    second = copy_schedule_day(
        session, zone, source_weekday=1, target_weekdays=list(range(1, 8)), user_id=None
    )

    assert first is not None
    assert second is None
































def test_undo_token_parser_rejects_a_bad_signature_and_non_mapping(monkeypatch) -> None:
    valid_shape = schedule_shared.undo_token(1, ((), (), 1))
    encoded, _signature = valid_shape.split(".", 1)
    with pytest.raises(ValueError):
        schedule_shared.undo_payload(encoded + ".bad")

    encoded_list = base64.urlsafe_b64encode(b"[]").decode().rstrip("=")
    monkeypatch.setattr(schedule_shared.hmac, "compare_digest", lambda _a, _b: True)
    with pytest.raises(ValueError):
        schedule_shared.undo_payload(encoded_list + ".accepted-for-shape-test")




def _csrf(client: TestClient) -> dict[str, str]:
    secret = client.cookies[COOKIE_NAME]
    return {
        "X-CSRF-Token": csrf_token(
            secret, get_settings().secret_key.get_secret_value()
        )
    }


def _point(session: Session, zone_id: int, day: int, minute: int, mode_id: int) -> SchedulePoint:
    point = SchedulePoint(
        zone_id=zone_id,
        weekday=day,
        minute_of_day=minute,
        setpoint_mode_id=mode_id,
    )
    session.add(point)
    session.flush()
    return point


























# --- Moving (the target of dragging in the week view) -----------------------


















def test_without_schedule_manage_no_bar_can_be_dragged(
    client_als, session: Session
) -> None:
    """Counter-check: otherwise the test above would also be satisfied by a version
    that makes every bar draggable for everyone."""
    create_settings(session)
    zone = create_zone(session, "bad")
    day = create_mode(session, "tag", "Tag")
    _point(session, zone.id, 1, 360, day.id)
    response = client_als([("zone.read", None)]).get(f"/zones/{zone.id}/schedule")
    assert response.status_code == 200
    assert "schedule-draggable" not in response.text










def _first_point(session: Session, zone_id: int, minute: int) -> SchedulePoint:
    point = session.scalar(
        select(SchedulePoint).where(
            SchedulePoint.zone_id == zone_id, SchedulePoint.minute_of_day == minute
        )
    )
    assert point is not None
    return point






# --- Changing the mode -----------------------------------------------------




def test_an_unknown_mode_is_rejected_by_the_domain(session: Session) -> None:
    zone = create_zone(session, "unbekannter-modus")
    comfort = create_mode(session, "komfort", "Komfort")
    point = _point(session, zone.id, 1, 390, comfort.id)

    with pytest.raises(ScheduleError) as error:
        change_schedule_point_mode(
            session, zone, point, mode_id=999999, user_id=None
        )

    assert error.value.field == "mode_id"
    assert point.setpoint_mode_id == comfort.id


def test_changing_to_the_current_mode_does_not_write_an_audit_event(
    session: Session,
) -> None:
    source(session, "web")
    zone = create_zone(session, "modus-unverändert")
    comfort = create_mode(session, "komfort", "Komfort")
    point = _point(session, zone.id, 1, 390, comfort.id)

    returned = change_schedule_point_mode(
        session, zone, point, mode_id=comfort.id, user_id=None
    )

    assert returned is point
    assert not session.scalars(
        select(AuditEvent).where(AuditEvent.object_type == "schedule_point")
    ).all()












# --- schedule preview (24h forecast) -----------------------------------------------






