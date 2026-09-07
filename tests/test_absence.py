from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.helpers import create_mode, create_settings, create_zone
from thermoctl.db.models.absence import Absence
from thermoctl.db.models.identity import User
from thermoctl.db.models.operations import AuditEvent
from thermoctl.db.models.override import ZoneOverride
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.zone import Zone, ZoneSetpoint
from thermoctl.domain import absence as absence_module
from thermoctl.domain.absence import absence_zones, end_absence, running_absence, start_absence
from thermoctl.domain.modes import DomainError
from thermoctl.domain.schedule import create_override, resolved_setpoint

NOW = datetime(2026, 9, 7, 10, 0)
END = NOW + timedelta(days=2)


def user(session: Session, name: str) -> User:
    entry = User(username=name, display_name=name, password_hash="x")
    session.add(entry)
    session.flush()
    return entry


def scheduled_zones(session: Session) -> tuple[Zone, Zone, Zone]:
    settings = create_settings(session)
    day = create_mode(session, "day")
    zones = tuple(create_zone(session, name) for name in ("wohn", "bad", "fremd"))
    for zone in zones:
        session.add_all([
            ZoneSetpoint(zone_id=zone.id, setpoint_mode_id=settings.frost_protection_mode_id,
                         temperature_c=Decimal("16.0")),
            ZoneSetpoint(zone_id=zone.id, setpoint_mode_id=day.id,
                         temperature_c=Decimal("21.0")),
            SchedulePoint(zone_id=zone.id, weekday=1, minute_of_day=0,
                          setpoint_mode_id=day.id),
        ])
    session.flush()
    return zones


def test_start_absence_groups_exactly_one_override_per_zone(session: Session) -> None:
    zones = scheduled_zones(session)
    owner = user(session, "mieter")
    entry = start_absence(session, list(zones[:2]), Decimal("17.5"), END,
                          now=NOW, user_id=owner.id)
    overrides = list(session.scalars(
        select(ZoneOverride).where(ZoneOverride.absence_id == entry.id)
    ))
    assert len(overrides) == 2
    assert {row.zone_id for row in overrides} == {zones[0].id, zones[1].id}
    assert {row.ends_at for row in overrides} == {END}
    assert {row.temperature_c for row in overrides} == {Decimal("17.5")}
    assert absence_zones(session, entry) == [zones[1], zones[0]]
    audit = session.scalars(select(AuditEvent).where(AuditEvent.object_type == "absence")).one()
    assert audit.action == "create" and "2 Zonen" in (audit.detail or "")


def test_absence_uses_existing_override_resolution_and_expires_automatically(
    session: Session,
) -> None:
    zone, _, untouched = scheduled_zones(session)
    start_absence(session, [zone], Decimal("17.0"), END, now=NOW)
    assert resolved_setpoint(session, zone, NOW).temperature_c == Decimal("17.0")
    assert resolved_setpoint(session, zone, END).temperature_c == Decimal("21.0")
    assert resolved_setpoint(session, untouched, NOW).temperature_c == Decimal("21.0")


def test_end_absence_only_ends_its_running_overrides(session: Session) -> None:
    first, second, independent_zone = scheduled_zones(session)
    entry = start_absence(session, [first, second], Decimal("17.0"), END, now=NOW)
    independent = create_override(
        session, independent_zone, Decimal("23.0"), END, now=NOW
    )
    end_at = NOW + timedelta(hours=1)
    end_absence(session, entry, now=end_at)
    grouped = list(session.scalars(
        select(ZoneOverride).where(ZoneOverride.absence_id == entry.id)
    ))
    assert entry.cancelled_at == end_at
    assert {row.cancelled_at for row in grouped} == {end_at}
    assert independent.cancelled_at is None
    assert running_absence(session, entry.created_by_user_id or -1, end_at) is None
    audits = list(session.scalars(
        select(AuditEvent).where(AuditEvent.object_type == "absence")
    ))
    assert [event.action for event in audits] == ["create", "update"]


def test_start_is_atomic_when_second_override_fails(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, second, _ = scheduled_zones(session)
    original = absence_module.create_override
    calls = 0

    def fail_second(*args: object, **kwargs: object) -> ZoneOverride:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise DomainError("temperature_c", "Außerhalb der Grenzen")
        return original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(absence_module, "create_override", fail_second)
    with pytest.raises(DomainError, match="Außerhalb"):
        start_absence(session, [first, second], Decimal("17.0"), END, now=NOW)
    assert session.scalar(select(Absence)) is None
    assert session.scalar(select(ZoneOverride)) is None
    assert session.scalar(select(AuditEvent).where(AuditEvent.object_type == "absence")) is None


@pytest.mark.parametrize(
    ("zones", "end", "message"),
    [([], END, "mindestens ein Raum"), (["zone"], NOW, "Zukunft")],
)
def test_start_absence_rejects_invalid_window(
    session: Session, zones: list[str], end: datetime, message: str
) -> None:
    actual_zones = [] if not zones else [create_zone(session, zones[0])]
    with pytest.raises(DomainError) as caught:
        start_absence(session, actual_zones, Decimal("17.0"), end, now=NOW)
    # `notice`, nicht `str(exc)`: genau diesen Text zeigt die Weboberfläche an --
    # eine Ausnahme, die den Fehler nur im Log erklärt, hilft dem Bedienenden nicht.
    assert message in caught.value.notice


def test_running_absence_finds_only_its_owner(session: Session) -> None:
    first, second, _ = scheduled_zones(session)
    own = user(session, "own")
    other = user(session, "other")
    own_absence = start_absence(session, [first], Decimal("17"), END,
                                now=NOW, user_id=own.id)
    other_absence = start_absence(session, [second], Decimal("18"), END,
                                  now=NOW, user_id=other.id)
    assert running_absence(session, own.id, NOW) is own_absence
    assert running_absence(session, other.id, NOW) is other_absence
    assert running_absence(session, own.id, NOW - timedelta(seconds=1)) is None
    assert running_absence(session, own.id, END) is None


def test_second_running_absence_of_same_user_is_rejected(session: Session) -> None:
    first, second, _ = scheduled_zones(session)
    owner = user(session, "single")
    existing = start_absence(session, [first], Decimal("17"), END,
                             now=NOW, user_id=owner.id)
    with pytest.raises(DomainError) as caught:
        start_absence(session, [second], Decimal("18"), END,
                      now=NOW, user_id=owner.id)
    assert "bereits" in caught.value.notice
    # Die bestehende bleibt unangetastet -- eine abgelehnte zweite Abwesenheit darf
    # die erste nicht nebenbei beenden.
    assert existing.cancelled_at is None


def test_absence_keeps_the_override_temperature_bound_and_frost_semantics(
    session: Session,
) -> None:
    zone, _, _ = scheduled_zones(session)
    entry = start_absence(session, [zone], Decimal("-20.0"), END, now=NOW)
    assert entry.setback_temperature_c == Decimal("-20.0")
    assert resolved_setpoint(session, zone, NOW).temperature_c == Decimal("-20.0")
    with pytest.raises(DomainError):
        start_absence(session, [zone], Decimal("-20.1"), END, now=NOW)


def test_unknown_sources_roll_back_and_default_clock_is_shared(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    zone = create_zone(session, "clock")
    monkeypatch.setattr(absence_module, "utcnow", lambda: NOW)
    with pytest.raises(ValueError, match="Unbekannte Quelle"):
        start_absence(session, [zone], Decimal("17"), END, source="unknown")
    entry = start_absence(session, [zone], Decimal("17"), END)
    override = session.scalars(
        select(ZoneOverride).where(ZoneOverride.absence_id == entry.id)
    ).one()
    assert entry.starts_at == override.starts_at == NOW
    end_absence(session, entry)
    assert entry.cancelled_at == NOW


def test_end_absence_reports_a_missing_source(session: Session) -> None:
    entry = Absence(
        id=-1, starts_at=NOW, ends_at=END, setback_temperature_c=Decimal("17"),
        source_id=-1,
    )
    with pytest.raises(ValueError, match="Quelle"):
        end_absence(session, entry, now=NOW)
