"""Änderungsprotokoll (Phase 1): Schreibfunktionen, Lesen, Rücknahme, Rechte.

Jeder Test benutzt die echten Domänenfunktionen darunter; geprüft wird, was im Protokoll
steht und was eine Rücknahme in der Anlage bewirkt -- nicht, dass ein Aufruf stattfand.
"""

import itertools
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal as D

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from tests.helpers import (
    capability,
    create_device,
    create_settings,
    create_user,
    create_zone,
    role,
    source,
    token_with_permissions,
    zone_with_schedule,
)
from thermoctl.db.base import utcnow
from thermoctl.db.models.change_log import ChangeLog, ChangeLogField
from thermoctl.db.models.device import DeviceCapabilityLink, ZoneDevice
from thermoctl.db.models.operations import AuditEvent
from thermoctl.db.models.override import ZoneOverride
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.vacation import Vacation
from thermoctl.db.models.zone import Zone
from thermoctl.domain import change_log as cl
from thermoctl.domain import schedule as schedule_domain
from thermoctl.domain import zone_settings
from thermoctl.domain.authz import Forbidden
from thermoctl.domain.change_log import (
    Actor,
    ChangeAlreadyEffective,
    ChangeAlreadyReverted,
    ChangeConflict,
    ChangeLogError,
    ChangeNotFound,
    ChangeNotPossible,
    ChangeNotRevertible,
    canonical,
)
from thermoctl.domain.control import arm
from thermoctl.domain.modes import DomainError
from thermoctl.domain.principal import Principal
from thermoctl.domain.remote_control import RemoteControlError
from thermoctl.domain.schedule import ScheduleError
from thermoctl.domain.sensor_failure_policy import (
    CurvePoint,
    PolicyError,
    ProfileValues,
    migration_default_profile_id,
    read_defaults,
    read_profile,
    save_profile,
)
from thermoctl.domain.zone_settings import ParameterOutOfRange, UnknownParameter

_COUNTER = itertools.count(1)


def _actor(session: Session, source: str = "mcp") -> Actor:
    user = create_user(session, f"ak{next(_COUNTER)}")
    token = token_with_permissions(session, user, [])
    return Actor(user_id=user.id, token_id=token.id, source=source)


def _principal(*grants: tuple[str, int | None]) -> Principal:
    return Principal(user_id=1, token_id=None, grants=frozenset(grants))


def _entries(session: Session) -> list[ChangeLog]:
    return list(session.scalars(select(ChangeLog).order_by(ChangeLog.id)))


def _fields(session: Session, change_id: int) -> list[tuple[str, str | None, str | None]]:
    rows = session.scalars(
        select(ChangeLogField)
        .where(ChangeLogField.change_id == change_id)
        .order_by(ChangeLogField.id)
    )
    return [(r.field, r.before_value, r.after_value) for r in rows]


def _parameter_zone(session: Session, name: str = "param") -> Zone:
    create_settings(session)
    return create_zone(session, name)


# --- kanonische Form -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (D("0.50"), "0.5"),
        (D("10.0"), "10"),
        (D("1E+2"), "100"),
        (D("-0.50"), "-0.5"),
        (D("0.00"), "0"),
        (True, "true"),
        (False, "false"),
        (None, None),
        (30, "30"),
        (datetime(2026, 10, 11, 8, 0, 0, 123456), "2026-10-11T08:00:00Z"),
        (
            datetime(2026, 10, 11, 10, 0, 0, tzinfo=UTC) + timedelta(0),
            "2026-10-11T10:00:00Z",
        ),
        (date(2026, 10, 11), "2026-10-11"),
        ("Notbetrieb", "Notbetrieb"),
    ],
)
def test_canonical_form(value: object, expected: str | None) -> None:
    assert canonical(value) == expected


def test_canonical_form_converts_aware_moments_to_utc() -> None:
    berlin = timezone(timedelta(hours=2))
    assert canonical(datetime(2026, 10, 11, 12, 0, tzinfo=berlin)) == "2026-10-11T10:00:00Z"


# --- Regelparameter ----------------------------------------------------------------------


def test_set_parameter_writes_an_entry_with_before_and_after(session: Session) -> None:
    zone = _parameter_zone(session)
    actor = _actor(session)

    recorded = cl.set_parameter(session, actor, zone, "hysteresis_k", D("0.7"))

    assert recorded.result == D("0.7")
    entry = session.get(ChangeLog, recorded.change_id)
    assert entry is not None
    assert (entry.change_type, entry.zone_id, entry.zone_name) == (
        "control_parameter",
        zone.id,
        zone.display_name,
    )
    assert entry.actor_user_id == actor.user_id
    assert entry.actor_token_id == actor.token_id
    assert entry.revertible is True and entry.forced is False
    assert "Hysterese" in entry.summary and zone.display_name in entry.summary
    assert _fields(session, entry.id) == [("hysteresis_k", None, "0.7")]
    # Das Audit-Log der Domäne bleibt zusätzlich bestehen.
    assert session.query(AuditEvent).filter_by(object_type="zone_settings").count() == 1


def test_set_parameter_after_a_second_change_records_the_previous_value(session: Session) -> None:
    zone = _parameter_zone(session)
    actor = _actor(session)
    cl.set_parameter(session, actor, zone, "hysteresis_k", D("0.7"))

    second = cl.set_parameter(session, actor, zone, "hysteresis_k", D("0.9"))

    assert _fields(session, second.change_id) == [("hysteresis_k", "0.7", "0.9")]  # type: ignore[arg-type]


def test_set_parameter_without_effect_writes_no_entry(session: Session) -> None:
    zone = _parameter_zone(session)
    actor = _actor(session)
    cl.set_parameter(session, actor, zone, "hysteresis_k", D("0.7"))

    again = cl.set_parameter(session, actor, zone, "hysteresis_k", D("0.70"))

    assert again.change_id is None
    assert len(_entries(session)) == 1


@pytest.mark.parametrize(
    ("name", "value", "error"),
    [
        ("hysteresis_k", D("99"), ParameterOutOfRange),
        ("gibt_es_nicht", D("1"), UnknownParameter),
    ],
)
def test_set_parameter_failure_writes_nothing(
    session: Session, name: str, value: D, error: type[Exception]
) -> None:
    zone = _parameter_zone(session)

    with pytest.raises(error):
        cl.set_parameter(session, _actor(session), zone, name, value)

    assert _entries(session) == []
    assert session.query(ChangeLogField).count() == 0


def test_integral_parameter_is_recorded_as_integer_text(session: Session) -> None:
    zone = _parameter_zone(session)

    recorded = cl.set_parameter(session, _actor(session), zone, "min_on_seconds", D("120"))

    assert _fields(session, recorded.change_id) == [("min_on_seconds", None, "120")]  # type: ignore[arg-type]
    assert zone.min_on_seconds == 120


def test_boolean_parameter_is_recorded_as_true_and_false(session: Session) -> None:
    zone = _parameter_zone(session)
    actor = _actor(session)

    recorded = cl.set_parameter(session, actor, zone, "valve_protection_enabled", D("1"))

    assert _fields(session, recorded.change_id) == [  # type: ignore[arg-type]
        ("valve_protection_enabled", "false", "true")
    ]
    reverted = cl.revert_change(
        session,
        _principal(("zone.manage", zone.id)),
        recorded.change_id,
        actor=actor,  # type: ignore[arg-type]
    )
    assert zone.valve_protection_enabled is False
    assert [(f.field, f.before, f.after) for f in reverted.fields] == [
        ("valve_protection_enabled", "true", "false")
    ]


def test_revert_restores_inheritance(session: Session) -> None:
    zone = _parameter_zone(session)
    actor = _actor(session)
    changed = cl.set_parameter(session, actor, zone, "hysteresis_k", D("0.7"))
    assert changed.change_id is not None

    view = cl.revert_change(
        session, _principal(("zone.manage", zone.id)), changed.change_id, actor=actor
    )

    assert zone.hysteresis_k is None
    assert view.reverts_id == changed.change_id
    assert view.revertible is False
    assert view.not_revertible_reason == cl.REASON_REVERT
    assert view.forced is False
    assert view.change_type == "control_parameter"
    assert [(f.field, f.before, f.after) for f in view.fields] == [("hysteresis_k", "0.7", None)]
    original = session.get(ChangeLog, changed.change_id)
    assert original is not None
    assert original.reverted_by_id == view.id and original.reverted_at is not None


def test_revert_restores_the_previous_fixed_value(session: Session) -> None:
    zone = _parameter_zone(session)
    actor = _actor(session)
    cl.set_parameter(session, actor, zone, "hysteresis_k", D("0.7"))
    second = cl.set_parameter(session, actor, zone, "hysteresis_k", D("0.9"))

    cl.revert_change(
        session,
        _principal(("zone.manage", zone.id)),
        second.change_id,
        actor=actor,  # type: ignore[arg-type]
    )

    assert zone.hysteresis_k == D("0.7")


def test_revert_writes_an_audit_event_and_goes_through_the_domain_function(
    session: Session,
) -> None:
    zone = _parameter_zone(session)
    actor = _actor(session)
    changed = cl.set_parameter(session, actor, zone, "hysteresis_k", D("0.7"))
    before = session.query(AuditEvent).filter_by(object_type="zone_settings").count()

    cl.revert_change(
        session,
        _principal(("zone.manage", None)),
        changed.change_id,
        actor=actor,  # type: ignore[arg-type]
    )

    assert session.query(AuditEvent).filter_by(object_type="zone_settings").count() == before + 1
    revert_event = session.query(AuditEvent).filter_by(action="revert").one()
    assert revert_event.object_type == "change_log"
    assert revert_event.actor_user_id == actor.user_id


def test_revert_conflict_leaves_everything_untouched(session: Session) -> None:
    zone = _parameter_zone(session)
    actor = _actor(session)
    changed = cl.set_parameter(session, actor, zone, "hysteresis_k", D("0.7"))
    zone_settings.set_parameter(session, zone, "hysteresis_k", D("0.9"), user_id=None)

    with pytest.raises(ChangeConflict) as caught:
        cl.revert_change(
            session,
            _principal(("zone.manage", zone.id)),
            changed.change_id,
            actor=actor,  # type: ignore[arg-type]
        )

    assert caught.value.current == "hysteresis_k=0.9"
    assert caught.value.expected == "hysteresis_k=0.7"
    assert "0.9" in str(caught.value) and "0.7" in str(caught.value)
    assert zone.hysteresis_k == D("0.9")
    assert len(_entries(session)) == 1
    original = session.get(ChangeLog, changed.change_id)
    assert original is not None and original.reverted_at is None


def test_forced_revert_sets_the_value_anyway_and_says_so(session: Session) -> None:
    zone = _parameter_zone(session)
    actor = _actor(session)
    changed = cl.set_parameter(session, actor, zone, "hysteresis_k", D("0.7"))
    zone_settings.set_parameter(session, zone, "hysteresis_k", D("0.9"), user_id=None)

    view = cl.revert_change(
        session,
        _principal(("zone.manage", zone.id)),
        changed.change_id,
        force=True,
        actor=actor,  # type: ignore[arg-type]
    )

    assert zone.hysteresis_k is None
    assert view.forced is True
    # Der Rücknahme-Eintrag hält fest, was wirklich überschrieben wurde: 0.9, nicht 0.7.
    assert [(f.field, f.before, f.after) for f in view.fields] == [("hysteresis_k", "0.9", None)]


def test_a_change_can_only_be_reverted_once(session: Session) -> None:
    zone = _parameter_zone(session)
    actor = _actor(session)
    changed = cl.set_parameter(session, actor, zone, "hysteresis_k", D("0.7"))
    principal = _principal(("zone.manage", zone.id))
    cl.revert_change(session, principal, changed.change_id, actor=actor)  # type: ignore[arg-type]

    with pytest.raises(ChangeAlreadyReverted):
        cl.revert_change(session, principal, changed.change_id, actor=actor)  # type: ignore[arg-type]


def test_a_concurrent_revert_of_the_same_change_loses(session: Session, monkeypatch) -> None:
    """Zwei gleichzeitige Rücknahmen (mit `force` kämen sonst beide durch): Der zweite
    Aufrufer liest "noch nicht zurückgenommen", aber ein anderer hat den Eintrag in der
    Zwischenzeit beansprucht. Simuliert, indem die Zeile direkt vor der Beanspruchung belegt
    wird."""
    from sqlalchemy import update

    zone = _parameter_zone(session)
    actor = _actor(session)
    changed = cl.set_parameter(session, actor, zone, "hysteresis_k", D("0.7"))
    principal = _principal(("zone.manage", zone.id))
    real_begin_nested = session.begin_nested

    def occupied_first():  # type: ignore[no-untyped-def]
        session.execute(
            update(ChangeLog).where(ChangeLog.id == changed.change_id).values(reverted_at=utcnow())
        )
        return real_begin_nested()

    monkeypatch.setattr(session, "begin_nested", occupied_first)
    with pytest.raises(ChangeAlreadyReverted):
        cl.revert_change(session, principal, changed.change_id, force=True, actor=actor)  # type: ignore[arg-type]
    monkeypatch.undo()
    # nichts wurde zurückgesetzt, kein Rücknahme-Eintrag entstand
    assert zone_settings.control_parameters(session, zone).hysteresis_k == D("0.7")
    assert not session.scalars(select(ChangeLog).where(ChangeLog.reverts_id.is_not(None))).all()


def test_a_revert_is_not_reverted_again(session: Session) -> None:
    zone = _parameter_zone(session)
    actor = _actor(session)
    changed = cl.set_parameter(session, actor, zone, "hysteresis_k", D("0.7"))
    principal = _principal(("zone.manage", zone.id))
    revert = cl.revert_change(session, principal, changed.change_id, actor=actor)  # type: ignore[arg-type]

    with pytest.raises(ChangeNotRevertible) as caught:
        cl.revert_change(session, principal, revert.id, actor=actor)

    assert (
        caught.value.reason
        == "Eine Rücknahme wird nicht erneut zurückgenommen; setzen Sie den Wert erneut."
    )
    assert zone.hysteresis_k is None


def test_revert_respects_the_limits_of_the_domain(session: Session) -> None:
    zone = _parameter_zone(session)
    actor = _actor(session)
    cl.set_parameter(session, actor, zone, "hysteresis_k", D("0.7"))
    second = cl.set_parameter(session, actor, zone, "hysteresis_k", D("0.9"))
    # Der gespeicherte Vorher-Wert liegt inzwischen außerhalb der Grenzen.
    field = session.query(ChangeLogField).filter_by(change_id=second.change_id).one()
    field.before_value = "99"
    session.flush()

    with pytest.raises(ParameterOutOfRange):
        cl.revert_change(
            session,
            _principal(("zone.manage", zone.id)),
            second.change_id,
            actor=actor,  # type: ignore[arg-type]
        )

    assert zone.hysteresis_k == D("0.9")
    original = session.get(ChangeLog, second.change_id)
    assert original is not None and original.reverted_at is None
    assert len(_entries(session)) == 2


def test_revert_needs_zone_manage_on_the_zone_of_the_entry(session: Session) -> None:
    zone = _parameter_zone(session)
    other = create_zone(session, "andere")
    actor = _actor(session)
    changed = cl.set_parameter(session, actor, zone, "hysteresis_k", D("0.7"))
    grants: list[tuple[str, int | None]] = [("zone.read", zone.id), ("schedule.manage", zone.id)]
    grants.append(("zone.manage", other.id))

    with pytest.raises(Forbidden):
        cl.revert_change(session, _principal(*grants), changed.change_id, actor=actor)  # type: ignore[arg-type]

    assert zone.hysteresis_k == D("0.7")


def test_rights_are_checked_before_the_state_is_disclosed(session: Session) -> None:
    zone = _parameter_zone(session)
    actor = _actor(session)
    changed = cl.set_parameter(session, actor, zone, "hysteresis_k", D("0.7"))
    zone_settings.set_parameter(session, zone, "hysteresis_k", D("0.9"), user_id=None)
    nobody = _principal(("zone.read", zone.id))

    with pytest.raises(Forbidden) as caught:
        cl.revert_change(session, nobody, changed.change_id, actor=actor)  # type: ignore[arg-type]

    assert "0.9" not in str(caught.value) and "0.7" not in str(caught.value)
    # ... auch bei einem schon zurückgenommenen Eintrag:
    cl.revert_change(
        session,
        _principal(("zone.manage", zone.id)),
        changed.change_id,
        force=True,
        actor=actor,  # type: ignore[arg-type]
    )
    with pytest.raises(Forbidden):
        cl.revert_change(session, nobody, changed.change_id, actor=actor)  # type: ignore[arg-type]


def test_revert_of_unknown_entry(session: Session) -> None:
    with pytest.raises(ChangeNotFound):
        cl.revert_change(session, _principal(), 424242, actor=_actor(session))
    with pytest.raises(ChangeNotFound):
        cl.get_change(session, 424242)


def test_revert_after_the_zone_is_gone(session: Session) -> None:
    zone = _parameter_zone(session)
    actor = _actor(session)
    changed = cl.set_parameter(session, actor, zone, "hysteresis_k", D("0.7"))
    session.execute(delete(Zone).where(Zone.id == zone.id))
    session.expire_all()

    entry = session.get(ChangeLog, changed.change_id)
    assert entry is not None and entry.zone_id is None and entry.zone_name == zone.display_name
    with pytest.raises(ChangeNotPossible) as caught:
        cl.revert_change(
            session,
            _principal(("zone.manage", None)),
            changed.change_id,
            actor=actor,  # type: ignore[arg-type]
        )

    assert zone.display_name in str(caught.value)
    # Ohne anlagenweites Recht scheitert es schon an den Rechten, nicht erst an der Zone.
    with pytest.raises(Forbidden):
        cl.revert_change(
            session,
            _principal(("zone.manage", 1)),
            changed.change_id,
            actor=actor,  # type: ignore[arg-type]
        )


def test_unknown_source_is_rejected_without_a_half_written_change(session: Session) -> None:
    # `cancel_override` kennt keine Quelle und prüft sie deshalb nicht selbst: Ohne die
    # Prüfung im Protokoll bliebe die Übersteuerung beendet und der Eintrag ginge als
    # IntegrityError auf `change_log.source_id` unter.
    zone, _point = _schedule_zone(session, "quelle")
    created = cl.create_override(session, _actor(session), zone, D("22"), None)
    bad = Actor(user_id=None, token_id=None, source="gibtsnicht")

    with pytest.raises(ValueError, match="gibtsnicht"):
        cl.cancel_override(session, bad, zone)

    assert created.result.cancelled_at is None
    assert [e.change_type for e in _entries(session)] == ["override"]


# --- Zeitplanpunkt -------------------------------------------------------------------------


def _schedule_zone(session: Session, name: str = "plan") -> tuple[Zone, SchedulePoint]:
    zone = zone_with_schedule(
        session, name, [(1, 360, "tag-" + name, D("21.0")), (1, 1320, "nacht-" + name, D("17.0"))]
    )
    point = session.scalars(
        select(SchedulePoint).where(
            SchedulePoint.zone_id == zone.id, SchedulePoint.minute_of_day == 360
        )
    ).one()
    return zone, point


def test_move_point_records_only_the_changed_fields(session: Session) -> None:
    zone, point = _schedule_zone(session)
    actor = _actor(session)

    recorded = cl.move_point(session, actor, zone, point, weekday=1, minute=450)

    assert recorded.result is point
    assert (point.weekday, point.minute_of_day) == (1, 450)
    entry = session.get(ChangeLog, recorded.change_id)
    assert entry is not None
    assert (entry.change_type, entry.object_ref) == ("schedule_point", str(point.id))
    assert _fields(session, entry.id) == [("minute_of_day", "360", "450")]
    assert "Montag 06:00" in entry.summary and "Montag 07:30" in entry.summary


def test_move_point_to_the_same_place_writes_nothing(session: Session) -> None:
    zone, point = _schedule_zone(session)

    recorded = cl.move_point(session, _actor(session), zone, point, weekday=1, minute=360)

    assert recorded.change_id is None
    assert _entries(session) == []


def test_move_point_collision_writes_nothing(session: Session) -> None:
    zone, point = _schedule_zone(session)

    with pytest.raises(ScheduleError):
        cl.move_point(session, _actor(session), zone, point, weekday=1, minute=1320)

    assert (point.weekday, point.minute_of_day) == (1, 360)
    assert _entries(session) == []


def test_revert_moves_the_point_back(session: Session) -> None:
    zone, point = _schedule_zone(session)
    actor = _actor(session)
    moved = cl.move_point(session, actor, zone, point, weekday=3, minute=450)

    view = cl.revert_change(
        session,
        _principal(("schedule.manage", zone.id)),
        moved.change_id,
        actor=actor,  # type: ignore[arg-type]
    )

    assert (point.weekday, point.minute_of_day) == (1, 360)
    assert [(f.field, f.before, f.after) for f in view.fields] == [
        ("minute_of_day", "450", "360"),
        ("weekday", "3", "1"),
    ]


def test_revert_of_a_moved_point_conflicts_when_it_moved_again(session: Session) -> None:
    zone, point = _schedule_zone(session)
    actor = _actor(session)
    moved = cl.move_point(session, actor, zone, point, weekday=1, minute=450)
    cl.move_point(session, actor, zone, point, weekday=1, minute=500)

    with pytest.raises(ChangeConflict) as caught:
        cl.revert_change(
            session,
            _principal(("schedule.manage", zone.id)),
            moved.change_id,
            actor=actor,  # type: ignore[arg-type]
        )

    assert caught.value.current == "minute_of_day=500"
    assert point.minute_of_day == 500


def test_revert_of_a_moved_point_fails_when_the_old_slot_is_taken(session: Session) -> None:
    zone, point = _schedule_zone(session)
    actor = _actor(session)
    moved = cl.move_point(session, actor, zone, point, weekday=1, minute=450)
    session.add(
        SchedulePoint(
            zone_id=zone.id, weekday=1, minute_of_day=360, setpoint_mode_id=point.setpoint_mode_id
        )
    )
    session.flush()

    with pytest.raises(ScheduleError):
        cl.revert_change(
            session,
            _principal(("schedule.manage", zone.id)),
            moved.change_id,
            actor=actor,  # type: ignore[arg-type]
        )

    assert point.minute_of_day == 450
    original = session.get(ChangeLog, moved.change_id)
    assert original is not None and original.reverted_at is None


def test_revert_of_a_moved_point_that_no_longer_exists(session: Session) -> None:
    zone, point = _schedule_zone(session)
    actor = _actor(session)
    moved = cl.move_point(session, actor, zone, point, weekday=1, minute=450)
    session.delete(point)
    session.flush()

    with pytest.raises(ChangeNotPossible):
        cl.revert_change(
            session,
            _principal(("schedule.manage", zone.id)),
            moved.change_id,
            actor=actor,  # type: ignore[arg-type]
        )


def test_revert_of_a_moved_point_needs_schedule_manage_not_zone_manage(session: Session) -> None:
    zone, point = _schedule_zone(session)
    actor = _actor(session)
    moved = cl.move_point(session, actor, zone, point, weekday=1, minute=450)

    with pytest.raises(Forbidden):
        cl.revert_change(
            session,
            _principal(("zone.manage", zone.id)),
            moved.change_id,
            actor=actor,  # type: ignore[arg-type]
        )

    assert point.minute_of_day == 450


# --- Übersteuerung und Boost -----------------------------------------------------------------


def test_create_override_records_temperature_and_end(session: Session) -> None:
    zone, _point = _schedule_zone(session, "uebers")
    actor = _actor(session)
    ends = datetime(2099, 1, 2, 3, 4, 5)

    recorded = cl.create_override(session, actor, zone, D("22.5"), ends)

    assert isinstance(recorded.result, ZoneOverride)
    entry = session.get(ChangeLog, recorded.change_id)
    assert entry is not None
    assert (entry.change_type, entry.object_ref) == ("override", str(recorded.result.id))
    assert _fields(session, entry.id) == [
        ("temperature_c", None, "22.5"),
        ("ends_at", None, "2099-01-02T03:04:05Z"),
    ]


def test_open_ended_override_has_an_empty_end(session: Session) -> None:
    zone, _point = _schedule_zone(session, "offen")

    recorded = cl.create_override(session, _actor(session), zone, D("20"), None)

    assert _fields(session, recorded.change_id) == [  # type: ignore[arg-type]
        ("temperature_c", None, "20"),
        ("ends_at", None, None),
    ]


def test_create_override_out_of_range_writes_nothing(session: Session) -> None:
    zone, _point = _schedule_zone(session, "heiss")

    with pytest.raises(DomainError):
        cl.create_override(session, _actor(session), zone, D("99"), None)

    assert _entries(session) == []
    assert session.query(ZoneOverride).count() == 0


def test_revert_override_cancels_it_and_needs_both_rights(session: Session) -> None:
    zone, _point = _schedule_zone(session, "beide")
    actor = _actor(session)
    created = cl.create_override(session, actor, zone, D("22"), utcnow() + timedelta(hours=2))
    only_create = _principal(("override.create", zone.id))
    only_cancel = _principal(("override.cancel", zone.id))

    for principal in (only_create, only_cancel):
        with pytest.raises(Forbidden):
            cl.revert_change(session, principal, created.change_id, actor=actor)  # type: ignore[arg-type]
    assert created.result.cancelled_at is None

    view = cl.revert_change(
        session,
        _principal(("override.create", zone.id), ("override.cancel", zone.id)),
        created.change_id,  # type: ignore[arg-type]
        actor=actor,
    )

    assert created.result.cancelled_at is not None
    assert view.change_type == "override" and view.reverts_id == created.change_id
    assert [(f.field, f.before, f.after) for f in view.fields] == [
        ("temperature_c", "22", None),
        ("ends_at", canonical(created.result.ends_at), None),
    ]


def test_revert_override_that_already_ended_is_moot(session: Session) -> None:
    zone, _point = _schedule_zone(session, "beendet")
    actor = _actor(session)
    created = cl.create_override(session, actor, zone, D("22"), None)
    cl.cancel_override(session, actor, zone)
    principal = _principal(("override.create", zone.id), ("override.cancel", zone.id))

    with pytest.raises(ChangeAlreadyEffective):
        cl.revert_change(session, principal, created.change_id, actor=actor)  # type: ignore[arg-type]

    original = session.get(ChangeLog, created.change_id)
    assert original is not None
    assert original.reverted_at is not None and original.reverted_by_id is None
    assert [e.change_type for e in _entries(session)] == ["override", "override_cancel"]
    with pytest.raises(ChangeAlreadyReverted):
        cl.revert_change(session, principal, created.change_id, actor=actor)  # type: ignore[arg-type]


def test_revert_override_that_expired_is_moot(session: Session) -> None:
    zone, _point = _schedule_zone(session, "abgelaufen")
    actor = _actor(session)
    past = utcnow() - timedelta(hours=3)
    created = cl.create_override(session, actor, zone, D("22"), past + timedelta(hours=1), now=past)
    principal = _principal(("override.create", zone.id), ("override.cancel", zone.id))

    with pytest.raises(ChangeAlreadyEffective):
        cl.revert_change(session, principal, created.change_id, actor=actor)  # type: ignore[arg-type]

    assert created.result.cancelled_at is None


def test_revert_override_conflicts_with_a_newer_running_one(session: Session) -> None:
    zone, _point = _schedule_zone(session, "neuer")
    actor = _actor(session)
    old = cl.create_override(
        session, actor, zone, D("22"), None, now=utcnow() - timedelta(minutes=10)
    )
    newer = cl.create_override(session, actor, zone, D("25"), None)
    principal = _principal(("override.create", zone.id), ("override.cancel", zone.id))

    with pytest.raises(ChangeConflict) as caught:
        cl.revert_change(session, principal, old.change_id, actor=actor)  # type: ignore[arg-type]

    assert str(newer.result.id) in caught.value.current
    assert old.result.cancelled_at is None and newer.result.cancelled_at is None

    view = cl.revert_change(session, principal, old.change_id, force=True, actor=actor)  # type: ignore[arg-type]

    assert view.forced is True
    assert old.result.cancelled_at is not None
    assert newer.result.cancelled_at is None


def test_revert_override_ignores_a_newer_one_that_already_expired(session: Session) -> None:
    zone, _point = _schedule_zone(session, "abgelaufen2")
    actor = _actor(session)
    old = cl.create_override(session, actor, zone, D("22"), None, now=utcnow() - timedelta(hours=5))
    newer = cl.create_override(
        session,
        actor,
        zone,
        D("25"),
        utcnow() - timedelta(hours=1),
        now=utcnow() - timedelta(hours=2),
    )
    principal = _principal(("override.create", zone.id), ("override.cancel", zone.id))

    view = cl.revert_change(session, principal, old.change_id, actor=actor)  # type: ignore[arg-type]

    assert view.forced is False
    assert old.result.cancelled_at is not None
    assert newer.result.cancelled_at is None


def test_revert_override_for_a_deleted_override_row(session: Session) -> None:
    zone, _point = _schedule_zone(session, "weg")
    actor = _actor(session)
    created = cl.create_override(session, actor, zone, D("22"), None)
    session.delete(created.result)
    session.flush()

    with pytest.raises(ChangeNotPossible):
        cl.revert_change(
            session,
            _principal(("override.create", zone.id), ("override.cancel", zone.id)),
            created.change_id,  # type: ignore[arg-type]
            actor=actor,
        )


def test_boost_is_recorded_as_its_own_kind_and_reverted_like_an_override(
    session: Session,
) -> None:
    zone = zone_with_schedule(
        session, "boostz", [(1, 0, "tag-bz", D("21.0")), (1, 1320, "nacht-bz", D("18.0"))]
    )
    actor = _actor(session)

    recorded = cl.boost(session, actor, zone, utcnow())

    entry = session.get(ChangeLog, recorded.change_id)
    assert entry is not None and entry.change_type == "boost"
    created = session.scalars(select(ZoneOverride).where(ZoneOverride.zone_id == zone.id)).one()
    assert entry.object_ref == str(created.id)
    assert _fields(session, entry.id) == [
        ("temperature_c", None, canonical(recorded.result.temperature)),
        ("ends_at", None, canonical(recorded.result.bis)),
    ]
    assert recorded.result.mode_code in entry.summary

    cl.revert_change(
        session,
        _principal(("override.create", zone.id), ("override.cancel", zone.id)),
        entry.id,
        actor=actor,
    )
    assert created.cancelled_at is not None


def test_boost_without_a_schedule_writes_nothing(session: Session) -> None:
    create_settings(session)
    zone = create_zone(session, "ohneplan")

    with pytest.raises(RemoteControlError):
        cl.boost(session, _actor(session), zone, utcnow())

    assert _entries(session) == []


# --- Beenden (nicht zurücknehmbar) ---------------------------------------------------------


def test_cancel_override_is_recorded_and_not_revertible(session: Session) -> None:
    zone, _point = _schedule_zone(session, "ende")
    actor = _actor(session)
    created = cl.create_override(session, actor, zone, D("22"), None)

    cancelled = cl.cancel_override(session, actor, zone)

    assert cancelled.result is created.result
    entry = session.get(ChangeLog, cancelled.change_id)
    assert entry is not None
    assert (entry.change_type, entry.revertible) == ("override_cancel", False)
    assert entry.not_revertible_reason == cl.REASON_OVERRIDE_CANCEL
    assert entry.object_ref == str(created.result.id)
    with pytest.raises(ChangeNotRevertible) as caught:
        cl.revert_change(session, _principal(("override.cancel", zone.id)), entry.id, actor=actor)
    assert caught.value.reason == cl.REASON_OVERRIDE_CANCEL
    assert "nicht" in caught.value.reason


def test_cancel_override_without_a_running_one_writes_nothing(session: Session) -> None:
    zone, _point = _schedule_zone(session, "keine")

    cancelled = cl.cancel_override(session, _actor(session), zone)

    assert cancelled.result is None and cancelled.change_id is None
    assert _entries(session) == []


# --- Urlaub ----------------------------------------------------------------------------------


def _vacation(session: Session, actor: Actor) -> cl.Recorded[Vacation]:
    create_settings(session)
    today = datetime.now(UTC).date()
    return cl.create_vacation(
        session,
        actor,
        start_date=today + timedelta(days=2),
        end_date=today + timedelta(days=5),
        setback_temperature_c=D("15"),
        timezone_name="Europe/Berlin",
    )


def test_create_vacation_is_recorded_plant_wide(session: Session) -> None:
    actor = _actor(session)

    recorded = _vacation(session, actor)

    entry = session.get(ChangeLog, recorded.change_id)
    assert entry is not None
    assert (entry.change_type, entry.zone_id, entry.zone_name) == ("vacation", None, None)
    assert entry.object_ref == str(recorded.result.id)
    assert sorted(_fields(session, entry.id)) == [
        ("ends_at", None, canonical(recorded.result.ends_at)),
        ("setback_temperature_c", None, "15"),
        ("starts_at", None, canonical(recorded.result.starts_at)),
    ]


def test_second_vacation_is_refused_and_writes_nothing(session: Session) -> None:
    actor = _actor(session)
    _vacation(session, actor)
    count = len(_entries(session))
    today = datetime.now(UTC).date()

    with pytest.raises(ScheduleError):
        cl.create_vacation(
            session,
            actor,
            start_date=today + timedelta(days=3),
            end_date=today + timedelta(days=4),
            setback_temperature_c=D("15"),
            timezone_name="Europe/Berlin",
        )

    assert len(_entries(session)) == count


def test_revert_vacation_ends_it(session: Session) -> None:
    actor = _actor(session)
    recorded = _vacation(session, actor)

    with pytest.raises(Forbidden):
        cl.revert_change(
            session, _principal(("vacation.manage", 5)), recorded.change_id, actor=actor
        )  # type: ignore[arg-type]
    view = cl.revert_change(
        session,
        _principal(("vacation.manage", None)),
        recorded.change_id,
        actor=actor,  # type: ignore[arg-type]
    )

    assert recorded.result.cancelled_at is not None
    assert view.zone_id is None and view.change_type == "vacation"
    assert dict((f.field, f.after) for f in view.fields) == {
        "starts_at": None,
        "ends_at": None,
        "setback_temperature_c": None,
    }


def test_revert_vacation_already_cancelled_is_moot(session: Session) -> None:
    actor = _actor(session)
    recorded = _vacation(session, actor)
    cancelled = cl.cancel_vacation(session, actor)

    entry = session.get(ChangeLog, cancelled.change_id)
    assert entry is not None
    assert (entry.change_type, entry.revertible) == ("vacation_cancel", False)
    assert entry.not_revertible_reason == cl.REASON_VACATION_CANCEL
    with pytest.raises(ChangeAlreadyEffective):
        cl.revert_change(
            session,
            _principal(("vacation.manage", None)),
            recorded.change_id,
            actor=actor,  # type: ignore[arg-type]
        )
    with pytest.raises(ChangeNotRevertible):
        cl.revert_change(session, _principal(("vacation.manage", None)), entry.id, actor=actor)


def test_cancel_vacation_without_one_writes_nothing(session: Session) -> None:
    create_settings(session)

    cancelled = cl.cancel_vacation(session, _actor(session))

    assert cancelled.result is None and cancelled.change_id is None
    assert _entries(session) == []


def test_revert_vacation_conflicts_when_another_one_is_up_first(session: Session) -> None:
    actor = _actor(session)
    recorded = _vacation(session, actor)
    # Ein früherer, direkt angelegter Urlaub (über die Domäne nicht möglich).
    session.add(
        Vacation(
            starts_at=utcnow() + timedelta(hours=1),
            ends_at=utcnow() + timedelta(days=1),
            setback_temperature_c=D("16"),
            source_id=source(session, "mcp").id,
        )
    )
    session.flush()

    with pytest.raises(ChangeConflict):
        cl.revert_change(
            session,
            _principal(("vacation.manage", None)),
            recorded.change_id,
            actor=actor,  # type: ignore[arg-type]
        )

    assert recorded.result.cancelled_at is None


def test_revert_vacation_whose_row_is_gone(session: Session) -> None:
    actor = _actor(session)
    recorded = _vacation(session, actor)
    session.delete(recorded.result)
    session.flush()

    with pytest.raises(ChangeNotPossible):
        cl.revert_change(
            session,
            _principal(("vacation.manage", None)),
            recorded.change_id,
            actor=actor,  # type: ignore[arg-type]
        )


# --- Trockenlauf -----------------------------------------------------------------------------


def test_force_dry_run_is_recorded_and_never_revertible(session: Session) -> None:
    settings = create_settings(session)
    actor = _actor(session)
    arm(session, True, reason="geprüft", user_id=None)
    assert settings.control_armed is True

    recorded = cl.force_dry_run(session, actor, reason="Sensor spinnt")

    assert recorded.result is True and settings.control_armed is False
    entry = session.get(ChangeLog, recorded.change_id)
    assert entry is not None
    assert (entry.change_type, entry.revertible, entry.zone_id) == ("force_dry_run", False, None)
    assert entry.not_revertible_reason == cl.REASON_FORCE_DRY_RUN
    assert "Sensor spinnt" in entry.summary
    assert _fields(session, entry.id) == [("control_armed", "true", "false")]
    with pytest.raises(ChangeNotRevertible):
        cl.revert_change(
            session, _principal(("control.disarm", None)), entry.id, force=True, actor=actor
        )
    assert settings.control_armed is False


def test_force_dry_run_when_already_disarmed_writes_nothing(session: Session) -> None:
    create_settings(session)

    recorded = cl.force_dry_run(session, _actor(session))

    assert recorded.result is False and recorded.change_id is None
    assert _entries(session) == []


# --- Notbetriebsvorgaben ---------------------------------------------------------------------

BASE = ProfileValues("Notbetrieb Vorgabe", 600, 1200, 60, 2, D("1"), ())
CURVE = (
    CurvePoint(D("-10"), 1200, 600),
    CurvePoint(D("0"), 600, 1200),
    CurvePoint(D("15"), 0, 1800),
)


def _defaults_setup(session: Session) -> int:
    create_settings(session)
    save_profile(session, BASE)
    return migration_default_profile_id(session)


def _new_defaults(curve: tuple[CurvePoint, ...] = CURVE) -> ProfileValues:
    return ProfileValues("Notbetrieb Vorgabe", 900, 1200, 60, 2, D("1"), curve)


def test_defaults_record_scalar_fields_and_curve_points(session: Session) -> None:
    profile_id = _defaults_setup(session)
    actor = _actor(session)

    recorded = cl.save_sensor_failure_defaults(
        session, actor, _new_defaults(), profile_id=profile_id, emergency_setpoint_c=D("17.5")
    )

    entry = session.get(ChangeLog, recorded.change_id)
    assert entry is not None
    assert (entry.change_type, entry.zone_id, entry.object_ref) == (
        "sensor_failure_defaults",
        None,
        str(profile_id),
    )
    assert _fields(session, entry.id) == [
        ("curve_point:-10", None, "1200:600"),
        ("curve_point:0", None, "600:1200"),
        ("curve_point:15", None, "0:1800"),
        ("emergency_setpoint_c", "20", "17.5"),
        ("fixed_on_seconds", "600", "900"),
    ]


def test_defaults_revert_restores_values_and_removes_curve(session: Session) -> None:
    profile_id = _defaults_setup(session)
    actor = _actor(session)
    recorded = cl.save_sensor_failure_defaults(
        session, actor, _new_defaults(), profile_id=profile_id, emergency_setpoint_c=D("17.5")
    )

    with pytest.raises(Forbidden):
        cl.revert_change(
            session, _principal(("zone.manage", None)), recorded.change_id, actor=actor
        )  # type: ignore[arg-type]
    view = cl.revert_change(
        session,
        _principal(("setting.manage", None)),
        recorded.change_id,
        actor=actor,  # type: ignore[arg-type]
    )

    profile = read_profile(session, profile_id)
    assert profile.values.fixed_on_seconds == 600
    assert profile.values.curve_points == ()
    assert read_defaults(session).emergency_setpoint_c == D("20")
    assert dict((f.field, f.after) for f in view.fields)["curve_point:0"] is None


def test_defaults_revert_restores_a_removed_curve_point(session: Session) -> None:
    profile_id = _defaults_setup(session)
    actor = _actor(session)
    cl.save_sensor_failure_defaults(
        session, actor, _new_defaults(), profile_id=profile_id, emergency_setpoint_c=D("20")
    )
    smaller = (CURVE[0], CurvePoint(D("15"), 0, 1800))
    recorded = cl.save_sensor_failure_defaults(
        session, actor, _new_defaults(smaller), profile_id=profile_id, emergency_setpoint_c=D("20")
    )
    assert _fields(session, recorded.change_id) == [("curve_point:0", "600:1200", None)]  # type: ignore[arg-type]

    cl.revert_change(
        session,
        _principal(("setting.manage", None)),
        recorded.change_id,
        actor=actor,  # type: ignore[arg-type]
    )

    points = read_profile(session, profile_id).values.curve_points
    assert [(p.outdoor_c, p.on_seconds, p.off_seconds) for p in points] == [
        (D("-10"), 1200, 600),
        (D("0"), 600, 1200),
        (D("15"), 0, 1800),
    ]


def test_defaults_conflict_and_force(session: Session) -> None:
    profile_id = _defaults_setup(session)
    actor = _actor(session)
    recorded = cl.save_sensor_failure_defaults(
        session, actor, _new_defaults(()), profile_id=profile_id, emergency_setpoint_c=D("17.5")
    )
    cl.save_sensor_failure_defaults(
        session,
        actor,
        ProfileValues("Notbetrieb Vorgabe", 1000, 1200, 60, 2, D("1"), ()),
        profile_id=profile_id,
        emergency_setpoint_c=D("17.5"),
    )
    principal = _principal(("setting.manage", None))

    with pytest.raises(ChangeConflict) as caught:
        cl.revert_change(session, principal, recorded.change_id, actor=actor)  # type: ignore[arg-type]
    assert caught.value.current == "fixed_on_seconds=1000"
    assert caught.value.expected == "fixed_on_seconds=900"
    assert read_profile(session, profile_id).values.fixed_on_seconds == 1000

    view = cl.revert_change(session, principal, recorded.change_id, force=True, actor=actor)  # type: ignore[arg-type]
    assert view.forced is True
    assert read_profile(session, profile_id).values.fixed_on_seconds == 600
    assert read_defaults(session).emergency_setpoint_c == D("20")


def test_defaults_invalid_write_changes_and_records_nothing(session: Session) -> None:
    profile_id = _defaults_setup(session)
    bad_curve = (CURVE[0], CURVE[1])

    with pytest.raises(PolicyError):
        cl.save_sensor_failure_defaults(
            session,
            _actor(session),
            _new_defaults(bad_curve),
            profile_id=profile_id,
            emergency_setpoint_c=D("17.5"),
        )

    assert _entries(session) == []
    assert read_profile(session, profile_id).values.fixed_on_seconds == 600


def test_defaults_without_a_change_write_nothing(session: Session) -> None:
    profile_id = _defaults_setup(session)

    recorded = cl.save_sensor_failure_defaults(
        session, _actor(session), BASE, profile_id=profile_id, emergency_setpoint_c=D("20")
    )

    assert recorded.change_id is None and _entries(session) == []


# --- Notbetriebs-Einstellung einer Zone ------------------------------------------------------


def _thermostat_assignment(session: Session, zone_id: int) -> int:
    device = create_device(session, f"cl-thermostat-{zone_id}")
    session.add(
        DeviceCapabilityLink(
            device_id=device.id, capability_id=capability(session, "thermostat").id
        )
    )
    assignment = ZoneDevice(
        zone_id=zone_id,
        device_id=device.id,
        device_role_id=role(session, "actuator").id,
        thermostat_mode="self_regulating",
    )
    session.add(assignment)
    session.flush()
    return assignment.id


def test_policy_records_changed_fields_and_reverts(session: Session) -> None:
    _defaults_setup(session)
    zone = create_zone(session, "notbetrieb")
    actor = _actor(session)

    recorded = cl.save_sensor_failure_policy(
        session, actor, zone, enabled=True, emergency_setpoint_c=D("18")
    )

    entry = session.get(ChangeLog, recorded.change_id)
    assert entry is not None
    assert (entry.change_type, entry.zone_id) == ("sensor_failure_policy", zone.id)
    assert _fields(session, entry.id) == [
        ("emergency_setpoint_c", None, "18"),
        ("enabled", "false", "true"),
    ]
    with pytest.raises(Forbidden):
        cl.revert_change(session, _principal(("zone.manage", zone.id + 1)), entry.id, actor=actor)

    cl.revert_change(session, _principal(("zone.manage", zone.id)), entry.id, actor=actor)

    assert zone.sensor_failure_enabled is False
    assert zone.sensor_failure_emergency_setpoint_c is None


def test_policy_revert_restores_the_inherited_profile(session: Session) -> None:
    _defaults_setup(session)
    own = save_profile(session, ProfileValues("Eigenes", 900, 1200, 60, 2, D("1"), ()))
    zone = create_zone(session, "eigenesprofil")
    actor = _actor(session)
    recorded = cl.save_sensor_failure_policy(session, actor, zone, enabled=False, profile_id=own.id)

    assert _fields(session, recorded.change_id) == [  # type: ignore[arg-type]
        ("profile_id", None, str(own.id))
    ]
    cl.revert_change(
        session,
        _principal(("zone.manage", zone.id)),
        recorded.change_id,
        actor=actor,  # type: ignore[arg-type]
    )

    assert zone.sensor_failure_profile_id is None


def test_policy_revert_conflicts_when_changed_again(session: Session) -> None:
    _defaults_setup(session)
    zone = create_zone(session, "notbetrieb2")
    actor = _actor(session)
    first = cl.save_sensor_failure_policy(
        session, actor, zone, enabled=True, emergency_setpoint_c=D("18")
    )
    cl.save_sensor_failure_policy(session, actor, zone, enabled=True, emergency_setpoint_c=D("19"))

    with pytest.raises(ChangeConflict) as caught:
        cl.revert_change(
            session,
            _principal(("zone.manage", zone.id)),
            first.change_id,
            actor=actor,  # type: ignore[arg-type]
        )

    assert caught.value.current == "emergency_setpoint_c=19"
    assert zone.sensor_failure_emergency_setpoint_c == D("19")


def test_policy_backup_offsets_are_recorded_and_need_device_manage_to_revert(
    session: Session,
) -> None:
    _defaults_setup(session)
    zone = create_zone(session, "ausgleich")
    assignment_id = _thermostat_assignment(session, zone.id)
    actor = _actor(session)

    recorded = cl.save_sensor_failure_policy(
        session, actor, zone, enabled=False, backup_offsets={assignment_id: D("1.5")}
    )

    assert _fields(session, recorded.change_id) == [  # type: ignore[arg-type]
        (f"backup_offset:{assignment_id}", "0", "1.5")
    ]
    with pytest.raises(Forbidden, match="device.manage"):
        cl.revert_change(
            session,
            _principal(("zone.manage", zone.id)),
            recorded.change_id,
            actor=actor,  # type: ignore[arg-type]
        )
    row = session.get(ZoneDevice, assignment_id)
    assert row is not None and row.temperature_backup_offset_k == D("1.5")

    cl.revert_change(
        session,
        _principal(("zone.manage", zone.id), ("device.manage", zone.id)),
        recorded.change_id,  # type: ignore[arg-type]
        actor=actor,
    )

    assert row.temperature_backup_offset_k == D("0")


def test_policy_rejects_foreign_assignments_before_writing(session: Session) -> None:
    _defaults_setup(session)
    zone = create_zone(session, "fremd-zuordnung")

    with pytest.raises(ValueError, match="gehören nicht zu dieser Zone"):
        cl.save_sensor_failure_policy(
            session, _actor(session), zone, enabled=True, backup_offsets={999999: D("1")}
        )

    assert zone.sensor_failure_enabled is False
    assert _entries(session) == []


def test_policy_without_a_change_writes_nothing(session: Session) -> None:
    _defaults_setup(session)
    zone = create_zone(session, "unveraendert")

    recorded = cl.save_sensor_failure_policy(session, _actor(session), zone, enabled=False)

    assert recorded.change_id is None and _entries(session) == []


# --- Lesen ----------------------------------------------------------------------------------


def _three_entries(session: Session) -> tuple[Zone, Zone, list[int]]:
    create_settings(session)
    first = create_zone(session, "liste-a")
    second = create_zone(session, "liste-b")
    mcp = _actor(session, "mcp")
    web = _actor(session, "web")
    ids: list[int] = []
    for actor, zone, name, value in (
        (mcp, first, "hysteresis_k", D("0.7")),
        (web, second, "hysteresis_k", D("0.8")),
        (mcp, first, "min_on_seconds", D("120")),
    ):
        recorded = cl.set_parameter(session, actor, zone, name, value)
        assert recorded.change_id is not None
        ids.append(recorded.change_id)
    base = datetime(2026, 10, 1, 12, 0)
    for offset, change_id in enumerate(ids):
        entry = session.get(ChangeLog, change_id)
        assert entry is not None
        entry.occurred_at = base + timedelta(hours=offset)
    session.flush()
    return first, second, ids


def test_list_changes_is_newest_first_with_display_names(session: Session) -> None:
    first, _second, ids = _three_entries(session)

    views = cl.list_changes(session)

    assert [v.id for v in views] == [ids[2], ids[1], ids[0]]
    newest = views[0]
    assert newest.source == "mcp" and newest.zone_name == first.display_name
    assert newest.user_name is not None and newest.user_name.startswith("AK")
    assert newest.token_name is not None and newest.token_name.startswith("token-ak")
    assert [(f.field, f.after) for f in newest.fields] == [("min_on_seconds", "120")]


def test_list_changes_filters(session: Session) -> None:
    first, second, ids = _three_entries(session)

    assert [v.id for v in cl.list_changes(session, zone_id=second.id)] == [ids[1]]
    assert [v.id for v in cl.list_changes(session, source="web")] == [ids[1]]
    assert [v.id for v in cl.list_changes(session, change_type="schedule_point")] == []
    assert len(cl.list_changes(session, change_type="control_parameter", zone_id=first.id)) == 2
    assert [
        v.id
        for v in cl.list_changes(
            session, from_at=datetime(2026, 10, 1, 13, 0), to_at=datetime(2026, 10, 1, 14, 0)
        )
    ] == [ids[1]]
    aware = datetime(2026, 10, 1, 15, 30, tzinfo=timezone(timedelta(hours=2)))
    assert [v.id for v in cl.list_changes(session, from_at=aware)] == [ids[2]]


def test_list_changes_can_hide_reverted_entries(session: Session) -> None:
    first, _second, ids = _three_entries(session)
    revert = cl.revert_change(
        session, _principal(("zone.manage", first.id)), ids[0], actor=_actor(session)
    )

    shown = [v.id for v in cl.list_changes(session)]
    hidden = [v.id for v in cl.list_changes(session, include_reverted=False)]

    assert ids[0] in shown and revert.id in shown
    assert ids[0] not in hidden and revert.id in hidden


def test_list_changes_limit_and_offset(session: Session) -> None:
    _first, _second, ids = _three_entries(session)

    assert [v.id for v in cl.list_changes(session, limit=1)] == [ids[2]]
    assert [v.id for v in cl.list_changes(session, limit=1, offset=1)] == [ids[1]]
    for bad in (0, 501):
        with pytest.raises(ValueError, match="limit"):
            cl.list_changes(session, limit=bad)
    with pytest.raises(ValueError, match="offset"):
        cl.list_changes(session, offset=-1)
    assert len(cl.list_changes(session, limit=500)) == 3


def test_list_changes_on_empty_log(session: Session) -> None:
    assert cl.list_changes(session) == []


def test_get_change_returns_view_with_fields(session: Session) -> None:
    _first, _second, ids = _three_entries(session)

    view = cl.get_change(session, ids[0])

    assert view.id == ids[0] and view.fields[0].field == "hysteresis_k"


def test_view_keeps_the_zone_name_of_the_time_after_the_zone_is_deleted(
    session: Session,
) -> None:
    zone = _parameter_zone(session, "verschwindet")
    recorded = cl.set_parameter(session, _actor(session), zone, "hysteresis_k", D("0.7"))
    session.execute(delete(Zone).where(Zone.id == zone.id))
    session.expire_all()

    view = cl.get_change(session, recorded.change_id)  # type: ignore[arg-type]

    assert view.zone_id is None and view.zone_name == "Verschwindet"


def test_every_error_is_a_value_error() -> None:
    for error in (
        ChangeNotFound,
        ChangeNotRevertible,
        ChangeAlreadyReverted,
        ChangeAlreadyEffective,
        ChangeNotPossible,
        ChangeConflict,
    ):
        assert issubclass(error, ChangeLogError) and issubclass(error, ValueError)


def test_deleting_a_user_keeps_the_entry(session: Session) -> None:
    zone = _parameter_zone(session)
    actor = _actor(session)
    recorded = cl.set_parameter(session, actor, zone, "hysteresis_k", D("0.7"))
    from thermoctl.db.models.identity import User

    session.execute(delete(User).where(User.id == actor.user_id))
    session.expire_all()

    entry = session.get(ChangeLog, recorded.change_id)
    assert entry is not None and entry.actor_user_id is None and entry.actor_token_id is None


def test_schedule_domain_functions_are_still_the_ones_underneath() -> None:
    # Die Umhüllung ruft die vorhandene Funktion, keine Kopie: Dieselbe Funktion, die auch
    # Web und REST benutzen, hält Grenzen und Kollisionsprüfung.
    assert cl.schedule_domain is schedule_domain
    assert cl.zone_settings_domain is zone_settings
