from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.helpers import create_mode, create_zone, source
from thermoctl.auth.csrf import csrf_token
from thermoctl.auth.sessions import COOKIE_NAME
from thermoctl.config import get_settings
from thermoctl.db.models.operations import AuditEvent
from thermoctl.db.models.schedule import SchedulePoint


def _csrf(client: TestClient) -> dict[str, str]:
    geheimnis = client.cookies[COOKIE_NAME]
    return {
        "X-CSRF-Token": csrf_token(
            geheimnis, get_settings().secret_key.get_secret_value()
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


def test_the_week_view_shows_the_wraparound_from_sunday_to_monday(
    client_als, session: Session
) -> None:
    zone = create_zone(session, "bad")
    day = create_mode(session, "tag", "Tag")
    night = create_mode(session, "nacht", "Nacht")
    _point(session, zone.id, 1, 360, day.id)
    _point(session, zone.id, 7, 1320, night.id)

    response = client_als([("zone.read", zone.id)]).get(f"/zones/{zone.id}/schedule")

    assert response.status_code == 200
    assert "Montag" in response.text and "Sonntag" in response.text
    assert 'title="Nacht ab 00:00"' in response.text
    assert 'title="Tag ab 06:00"' in response.text
    assert 'title="Nacht ab 22:00"' in response.text
    assert "Schaltpunkt anlegen" not in response.text


def test_creating_a_point_and_reporting_a_double_booking_understandably(
    client_als, session: Session
) -> None:
    source(session)
    zone = create_zone(session, "bad")
    mode = create_mode(session, "tag", "Tag")
    client = client_als([("schedule.manage", zone.id), ("zone.read", zone.id)])
    data = {"weekday": "2", "time_of_day": "03:15", "mode_id": str(mode.id)}

    angelegt = client.post(
        f"/zones/{zone.id}/schedule/points",
        data=data,
        headers=_csrf(client),
        follow_redirects=False,
    )
    doppelt = client.post(
        f"/zones/{zone.id}/schedule/points", data=data, headers=_csrf(client)
    )

    point = session.scalar(select(SchedulePoint).where(SchedulePoint.zone_id == zone.id))
    assert angelegt.status_code == 303
    assert point is not None and (point.weekday, point.minute_of_day) == (2, 195)
    assert doppelt.status_code == 200
    assert "Zu diesem Zeitpunkt gibt es bereits einen Punkt." in doppelt.text
    assert 'value="03:15"' in doppelt.text
    assert session.scalar(
        select(AuditEvent).where(AuditEvent.object_type == "schedule_point")
    ) is not None


def test_invalid_points_stay_in_the_form_with_their_input(
    client_als, session: Session
) -> None:
    zone = create_zone(session, "bad")
    client = client_als([("schedule.manage", zone.id)])
    response = client.post(
        f"/zones/{zone.id}/schedule/points",
        data={"weekday": "2", "time_of_day": "25:61", "mode_id": "unbekannt"},
        headers=_csrf(client),
    )
    assert response.status_code == 200
    assert "gültige Uhrzeit" in response.text
    assert 'value="25:61"' in response.text


def test_confirming_and_carrying_out_a_point_deletion(client_als, session: Session) -> None:
    source(session)
    zone = create_zone(session, "bad")
    mode = create_mode(session, "tag", "Tag")
    point = _point(session, zone.id, 2, 180, mode.id)
    point_id = point.id
    client = client_als([("schedule.manage", zone.id)])

    form = client.get(
        f"/zones/{zone.id}/schedule/points/{point_id}/delete"
    )
    response = client.post(
        f"/zones/{zone.id}/schedule/points/{point_id}/delete",
        headers=_csrf(client),
        follow_redirects=False,
    )

    assert form.status_code == 200 and "Dienstag" in form.text
    assert response.status_code == 303
    assert session.get(SchedulePoint, point_id) is None


def test_adopting_a_schedule_copies_exactly_and_leaves_the_source_unchanged(
    client_als, session: Session
) -> None:
    source(session)
    source_zone = create_zone(session, "quelle")
    target = create_zone(session, "ziel")
    day = create_mode(session, "tag", "Tag")
    night = create_mode(session, "nacht", "Nacht")
    _point(session, source_zone.id, 1, 360, day.id)
    _point(session, source_zone.id, 7, 1320, night.id)
    client = client_als(
        [("schedule.manage", target.id), ("zone.read", source_zone.id)]
    )

    response = client.post(
        f"/zones/{target.id}/schedule/adopt",
        data={"source_id": str(source_zone.id)},
        headers=_csrf(client),
        follow_redirects=False,
    )

    source_points = session.scalars(
        select(SchedulePoint).where(SchedulePoint.zone_id == source_zone.id)
    ).all()
    target_points = session.scalars(
        select(SchedulePoint).where(SchedulePoint.zone_id == target.id)
    ).all()
    assert response.status_code == 303
    assert [(p.weekday, p.minute_of_day, p.setpoint_mode_id) for p in target_points] == [
        (p.weekday, p.minute_of_day, p.setpoint_mode_id) for p in source_points
    ]
    assert len(source_points) == 2
    assert session.scalar(
        select(AuditEvent).where(
            AuditEvent.object_type == "schedule", AuditEvent.object_id == str(target.id)
        )
    ) is not None


def test_adopting_onto_an_existing_schedule_asks_first(
    client_als, session: Session
) -> None:
    source(session)
    source_zone = create_zone(session, "quelle")
    target = create_zone(session, "ziel")
    mode = create_mode(session, "tag", "Tag")
    old_point = _point(session, target.id, 2, 180, mode.id)
    _point(session, source_zone.id, 1, 360, mode.id)
    client = client_als(
        [("schedule.manage", target.id), ("zone.read", source_zone.id)]
    )
    pfad = f"/zones/{target.id}/schedule/adopt"

    nachfrage = client.post(
        pfad,
        data={"source_id": str(source_zone.id)},
        headers=_csrf(client),
    )
    assert nachfrage.status_code == 200
    assert "ersetzt ihn vollständig" in nachfrage.text
    assert session.get(SchedulePoint, old_point.id) is old_point

    bestaetigt = client.post(
        pfad,
        data={"source_id": str(source_zone.id), "confirmed": "ja"},
        headers=_csrf(client),
        follow_redirects=False,
    )
    assert bestaetigt.status_code == 303
    assert session.get(SchedulePoint, old_point.id) is None


def test_permissions_and_foreign_zones_yield_404(client_als, session: Session) -> None:
    eigene = create_zone(session, "eigene")
    fremde = create_zone(session, "fremde")
    mode = create_mode(session, "tag", "Tag")
    leser = client_als([("zone.read", eigene.id)])
    assert leser.get(f"/zones/{fremde.id}/schedule").status_code == 404
    assert (
        leser.post(
            f"/zones/{eigene.id}/schedule/points",
            data={"weekday": "1", "time_of_day": "06:00", "mode_id": str(mode.id)},
            headers=_csrf(leser),
        ).status_code
        == 404
    )

    administrator = client_als([("schedule.manage", eigene.id), ("zone.read", eigene.id)])
    assert (
        administrator.get(f"/zones/{fremde.id}/schedule/adopt").status_code == 404
    )


def test_the_adoption_form_and_a_faulty_selection(client_als, session: Session) -> None:
    target = create_zone(session, "ziel")
    client = client_als([("schedule.manage", target.id)])
    pfad = f"/zones/{target.id}/schedule/adopt"
    assert client.get(pfad).status_code == 200
    response = client.post(pfad, data={}, headers=_csrf(client))
    assert response.status_code == 200
    assert "Bitte eine Quellzone auswählen." in response.text


def test_a_nonsensical_selection_when_creating_a_point(client_als, session: Session) -> None:
    """Weekday and mode come from select fields -- a request still does not have to
    stick to that. Both paths are deliberately bypassed here."""
    source(session)
    zone = create_zone(session, "zone-unsinn")
    mode = create_mode(session, "unsinn-tag", "Tag")
    client = client_als([("schedule.manage", None), ("zone.read", None)])
    kopf = _csrf(client)

    for data, expected in (
        ({"weekday": "Montag", "time_of_day": "06:00", "mode_id": str(mode.id)}, "Wochentag"),
        ({"weekday": "1", "time_of_day": "06:00", "mode_id": "kein Modus"}, "Modus"),
    ):
        response = client.post(
            f"/zones/{zone.id}/schedule/points", data=data, headers=kopf
        )
        assert response.status_code == 200, data
        assert expected in response.text, data
    assert session.scalar(
        select(SchedulePoint).where(SchedulePoint.zone_id == zone.id)
    ) is None


def test_a_foreign_schedule_point_yields_404(client_als, session: Session) -> None:
    """A point belonging to another zone cannot be deleted via one's own."""
    source(session)
    eigene = create_zone(session, "eigene-zeitplan")
    fremde = create_zone(session, "fremde-zeitplan")
    mode = create_mode(session, "fremd-tag", "Tag")
    fremder = _point(session, fremde.id, 1, 360, mode.id)
    client = client_als([("schedule.manage", None), ("zone.read", None)])

    assert client.get(
        f"/zones/{eigene.id}/schedule/points/{fremder.id}/delete"
    ).status_code == 404
    assert client.post(
        f"/zones/{eigene.id}/schedule/points/{fremder.id}/delete", headers=_csrf(client)
    ).status_code == 404
    assert session.get(SchedulePoint, fremder.id) is not None


def test_adopting_from_itself_yields_404(client_als, session: Session) -> None:
    """A zone cannot adopt its schedule from itself -- that would be an operation
    with no effect that would look as if it had one."""
    source(session)
    zone = create_zone(session, "zone-selbstuebernahme")
    client = client_als([("schedule.manage", None), ("zone.read", None)])
    response = client.post(
        f"/zones/{zone.id}/schedule/adopt",
        data={"source_id": str(zone.id)},
        headers=_csrf(client),
    )
    assert response.status_code == 404


def test_an_unknown_schedule_point_yields_404(client_als, session: Session) -> None:
    source(session)
    zone = create_zone(session, "zone-unbekannter-punkt")
    client = client_als([("schedule.manage", None), ("zone.read", None)])
    assert client.get(f"/zones/{zone.id}/schedule/points/999999/delete").status_code == 404


# --- Moving (the target of dragging in the week view) -----------------------


def test_punkt_verschieben(client_als, session: Session) -> None:
    zone = create_zone(session, "bad")
    source(session, "web")
    day = create_mode(session, "tag", "Tag")
    point = _point(session, zone.id, 1, 360, day.id)
    mandant = client_als([("zone.read", None), ("schedule.manage", None)])

    response = mandant.post(
        f"/zones/{zone.id}/schedule/points/move",
        data={"point_id": str(point.id), "weekday": "3", "time_of_day": "07:15"},
        headers=_csrf(mandant),
        follow_redirects=False,
    )
    assert response.status_code == 303
    session.refresh(point)
    assert (point.weekday, point.minute_of_day) == (3, 435)


def test_moving_keeps_the_identifier_and_logs_where_from_and_to(
    client_als, session: Session
) -> None:
    """Deleting and recreating would be functionally the same, but would produce two
    unrelated audit entries and a gap in the schedule in between."""
    zone = create_zone(session, "bad")
    source(session, "web")
    day = create_mode(session, "tag", "Tag")
    point = _point(session, zone.id, 1, 360, day.id)
    previous_identifier = point.id
    mandant = client_als([("zone.read", None), ("schedule.manage", None)])

    mandant.post(
        f"/zones/{zone.id}/schedule/points/move",
        data={"point_id": str(point.id), "weekday": "2", "time_of_day": "22:30"},
        headers=_csrf(mandant),
    )
    session.refresh(point)
    assert point.id == previous_identifier
    entry = session.scalars(
        select(AuditEvent).where(AuditEvent.object_type == "schedule_point")
    ).one()
    assert entry.detail == "Mo 06:00 → Di 22:30"


def test_moving_onto_an_occupied_moment_is_refused(
    client_als, session: Session
) -> None:
    zone = create_zone(session, "bad")
    source(session, "web")
    day = create_mode(session, "tag", "Tag")
    night = create_mode(session, "nacht", "Nacht")
    beweglich = _point(session, zone.id, 1, 360, day.id)
    _point(session, zone.id, 1, 1320, night.id)
    mandant = client_als([("zone.read", None), ("schedule.manage", None)])

    response = mandant.post(
        f"/zones/{zone.id}/schedule/points/move",
        data={"point_id": str(beweglich.id), "weekday": "1", "time_of_day": "22:00"},
        headers=_csrf(mandant),
    )
    assert response.status_code == 200
    # The message belongs on the week view, not on the time-of-day field of the
    # create form: both paths report the same sentence, and in the first version
    # it ended up on a form the user had not even touched.
    assert "data-move-error" in response.text
    assert "wurde nicht verschoben" in response.text
    session.refresh(beweglich)
    assert beweglich.minute_of_day == 360


def test_moving_onto_its_own_place_is_not_an_error(
    client_als, session: Session
) -> None:
    """When dragging, a bar can easily land right back where it was. That must not
    fail as a collision with itself."""
    zone = create_zone(session, "bad")
    source(session, "web")
    day = create_mode(session, "tag", "Tag")
    point = _point(session, zone.id, 1, 360, day.id)
    mandant = client_als([("zone.read", None), ("schedule.manage", None)])

    response = mandant.post(
        f"/zones/{zone.id}/schedule/points/move",
        data={"point_id": str(point.id), "weekday": "1", "time_of_day": "06:00"},
        headers=_csrf(mandant),
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert not session.scalars(
        select(AuditEvent).where(AuditEvent.object_type == "schedule_point")
    ).all()


def test_verschieben_braucht_schedule_manage(client_als, session: Session) -> None:
    zone = create_zone(session, "bad")
    day = create_mode(session, "tag", "Tag")
    point = _point(session, zone.id, 1, 360, day.id)
    mandant = client_als([("zone.read", None)])
    response = mandant.post(
        f"/zones/{zone.id}/schedule/points/move",
        data={"point_id": str(point.id), "weekday": "2", "time_of_day": "07:00"},
        headers=_csrf(mandant),
    )
    assert response.status_code == 404
    session.refresh(point)
    assert point.weekday == 1


def test_nonsensical_target_data_is_refused(client_als, session: Session) -> None:
    zone = create_zone(session, "bad")
    day = create_mode(session, "tag", "Tag")
    point = _point(session, zone.id, 1, 360, day.id)
    mandant = client_als([("zone.read", None), ("schedule.manage", None)])
    for data in (
        {"weekday": "9", "time_of_day": "07:00"},
        {"weekday": "kein Tag", "time_of_day": "07:00"},
        {"weekday": "2", "time_of_day": "25:00"},
        {"weekday": "2", "time_of_day": ""},
    ):
        response = mandant.post(
            f"/zones/{zone.id}/schedule/points/move",
            data=data | {"point_id": str(point.id)},
            headers=_csrf(mandant),
        )
        assert response.status_code == 200, data
    session.refresh(point)
    assert (point.weekday, point.minute_of_day) == (1, 360)


def test_the_week_view_carries_the_point_identifier_for_dragging(
    client_als, session: Session
) -> None:
    """Without it, the bar has nothing it could move -- and dragging would be silently
    ineffective instead of visibly broken."""
    zone = create_zone(session, "bad")
    day = create_mode(session, "tag", "Tag")
    point = _point(session, zone.id, 1, 360, day.id)
    response = client_als([("zone.read", None), ("schedule.manage", None)]).get(
        f"/zones/{zone.id}/schedule"
    )
    assert f'data-point="{point.id}"' in response.text
    assert "schedule-draggable" in response.text


def test_without_schedule_manage_no_bar_can_be_dragged(
    client_als, session: Session
) -> None:
    """Counter-check: otherwise the test above would also be satisfied by a version
    that makes every bar draggable for everyone."""
    zone = create_zone(session, "bad")
    day = create_mode(session, "tag", "Tag")
    _point(session, zone.id, 1, 360, day.id)
    response = client_als([("zone.read", None)]).get(f"/zones/{zone.id}/schedule")
    assert response.status_code == 200
    assert "schedule-draggable" not in response.text


def test_the_create_route_still_reports_at_the_field(client_als, session: Session) -> None:
    """Counter-check to the dedicated channel for move errors: the path via the form
    should still get its message right where the input field is."""
    zone = create_zone(session, "bad")
    source(session, "web")
    day = create_mode(session, "tag", "Tag")
    _point(session, zone.id, 1, 360, day.id)
    mandant = client_als([("zone.read", None), ("schedule.manage", None)])

    response = mandant.post(
        f"/zones/{zone.id}/schedule/points",
        data={"weekday": "1", "time_of_day": "06:00", "mode_id": str(day.id)},
        headers=_csrf(mandant),
    )
    assert response.status_code == 200
    assert "bereits einen Punkt" in response.text
    assert "data-move-error" not in response.text
