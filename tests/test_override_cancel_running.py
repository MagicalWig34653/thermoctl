"""`schedule.cancel_running_overrides` und der Zeitstempel von `cancel_override`.

Das Sollwertrad ersetzt eine laufende Übersteuerung, statt eine weitere daneben zu
stapeln. Dafür braucht die Domäne eine Hilfe, die alle gerade laufenden Zeilen einer
Zone beendet -- nur nicht die der Abwesenheit, deren Absenkung gewollt neben einer
kurzen Aufheizung weiterläuft (`schedule._running_override`).
"""

from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.helpers import create_mode, create_settings, create_zone, source
from thermoctl.db.models.override import ZoneOverride
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.zone import Zone, ZoneSetpoint
from thermoctl.domain.absence import start_absence
from thermoctl.domain.schedule import (
    cancel_override,
    cancel_running_overrides,
    create_override,
    resolved_setpoint,
)

NOW = datetime(2026, 9, 7, 10, 0)  # ein Montag


def _zone(session: Session) -> Zone:
    settings = create_settings(session)
    settings.timezone = "UTC"
    source(session, "system")
    zone = create_zone(session, "radzone")
    day = create_mode(session, "tag")
    session.add_all(
        [
            ZoneSetpoint(zone_id=zone.id, setpoint_mode_id=day.id, temperature_c=Decimal("21.0")),
            SchedulePoint(zone_id=zone.id, weekday=1, minute_of_day=0, setpoint_mode_id=day.id),
        ]
    )
    session.flush()
    return zone


def _override(
    session: Session, zone: Zone, temperature: str, ends_at: datetime | None,
    *, starts_at: datetime = NOW,
) -> ZoneOverride:
    return create_override(
        session, zone, Decimal(temperature), ends_at, now=starts_at, source="system"
    )


def test_every_running_override_of_the_zone_is_ended_at_the_given_moment(
    session: Session,
) -> None:
    zone = _zone(session)
    first = _override(session, zone, "22.0", None)
    second = _override(session, zone, "23.0", NOW + timedelta(hours=3))

    ended = cancel_running_overrides(session, zone, NOW)

    assert {row.id for row in ended} == {first.id, second.id}
    assert first.cancelled_at == NOW and second.cancelled_at == NOW
    # Nichts gelöscht: die Zeilen bleiben als Verlauf.
    assert session.scalars(select(ZoneOverride).where(ZoneOverride.zone_id == zone.id)).all()
    assert resolved_setpoint(session, zone, NOW).temperature_c == Decimal("21.0")


def test_an_absence_override_is_left_alone(session: Session) -> None:
    zone = _zone(session)
    start_absence(session, [zone], Decimal("17.0"), NOW + timedelta(days=2), now=NOW)
    heating = _override(session, zone, "23.0", NOW + timedelta(hours=2))

    ended = cancel_running_overrides(session, zone, NOW + timedelta(minutes=5))

    assert [row.id for row in ended] == [heating.id]
    absence_row = session.scalars(
        select(ZoneOverride).where(ZoneOverride.absence_id.is_not(None))
    ).one()
    assert absence_row.cancelled_at is None
    # Die Absenkung gilt wieder, sobald die kurze Aufheizung weg ist.
    assert resolved_setpoint(
        session, zone, NOW + timedelta(minutes=5)
    ).temperature_c == Decimal("17.0")


def test_overrides_of_other_zones_and_finished_ones_are_untouched(session: Session) -> None:
    zone = _zone(session)
    other = create_zone(session, "andere")
    foreign = _override(session, other, "24.0", None)
    expired = _override(session, zone, "22.0", NOW - timedelta(minutes=1),
                        starts_at=NOW - timedelta(hours=1))
    already = _override(session, zone, "20.0", None)
    already.cancelled_at = NOW - timedelta(minutes=30)

    assert cancel_running_overrides(session, zone, NOW) == []

    assert foreign.cancelled_at is None
    assert expired.cancelled_at is None  # lief schon ab, kein neuer Zeitstempel
    assert already.cancelled_at == NOW - timedelta(minutes=30)


def test_a_not_yet_started_override_is_not_running_and_stays(session: Session) -> None:
    zone = _zone(session)
    later = _override(session, zone, "22.0", NOW + timedelta(hours=5),
                      starts_at=NOW + timedelta(hours=1))

    assert cancel_running_overrides(session, zone, NOW) == []
    assert later.cancelled_at is None


def test_cancel_override_uses_the_given_moment_not_the_clock(session: Session) -> None:
    zone = _zone(session)
    row = _override(session, zone, "22.0", None)

    cancel_override(session, zone, now=NOW + timedelta(minutes=7))

    assert row.cancelled_at == NOW + timedelta(minutes=7)
