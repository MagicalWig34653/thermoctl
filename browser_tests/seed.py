"""Test data for the browser suite, built directly against the live server's own
SQLite file rather than through the UI.

Reuses ``tests/helpers.py`` wherever it already does the job: those functions
already know the schema's foreign-key order and build rows the same way the
migrations do. Only what genuinely needs a real login (a password hash, not the
test suite's placeholder) or must not repeat ``/setup``'s own inserts (the builtin
setpoint modes and the single ``Setting`` row, both already created once by the
admin bootstrap in ``conftest.py``) gets its own function here.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.helpers import create_zone, user_with_permissions
from thermoctl.auth.passwords import hash_password
from thermoctl.db.models.identity import User
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.zone import SetpointMode, Zone, ZoneSetpoint


def create_login_user(
    session: Session, username: str, password: str, permissions: list[tuple[str, int | None]]
) -> User:
    """A user who can actually log in through the browser.

    ``tests.helpers.user_with_permissions`` sets a placeholder hash -- fine for the
    HTTP test suite, which never runs the login form, but useless here.
    """
    user = user_with_permissions(session, username, permissions)
    user.password_hash = hash_password(password)
    session.flush()
    return user


def _builtin_mode(session: Session, code: str) -> SetpointMode:
    mode = session.scalar(select(SetpointMode).where(SetpointMode.code == code))
    if mode is None:  # pragma: no cover - only if /setup's own seeding ever changes
        raise AssertionError(
            f"Eingebauter Modus '{code}' fehlt -- ist die Einrichtung wirklich gelaufen?"
        )
    return mode


def create_schedule_zone(
    session: Session,
    name: str,
    *,
    day_temperature: Decimal = Decimal("21.0"),
    night_temperature: Decimal = Decimal("17.0"),
) -> Zone:
    """A zone with a two-point weekly schedule (Monday: day at 06:00, night at 22:00).

    Uses the "tag"/"nacht" modes the setup wizard already created instead of
    ``tests.helpers.zone_with_schedule`` — that helper inserts its own ``Setting``
    row with a fixed id of 1, which already exists once a real admin has been set
    up, and duplicating it would fail on the primary key.
    """
    zone = create_zone(session, name)
    tag = _builtin_mode(session, "tag")
    nacht = _builtin_mode(session, "nacht")
    session.add(
        ZoneSetpoint(zone_id=zone.id, setpoint_mode_id=tag.id, temperature_c=day_temperature)
    )
    session.add(
        ZoneSetpoint(zone_id=zone.id, setpoint_mode_id=nacht.id, temperature_c=night_temperature)
    )
    # Weekday 1 (Monday, WEEKDAYS in thermoctl/web/schedule_views.py) only -- every
    # other day of the grid stays empty on purpose, so the schedule-editor test has
    # a day column with nothing painted on it yet.
    session.add(
        SchedulePoint(zone_id=zone.id, weekday=1, minute_of_day=6 * 60, setpoint_mode_id=tag.id)
    )
    session.add(
        SchedulePoint(
            zone_id=zone.id, weekday=1, minute_of_day=22 * 60, setpoint_mode_id=nacht.id
        )
    )
    session.flush()
    return zone


def create_bare_zone(session: Session, name: str) -> Zone:
    """A zone with no schedule and no assigned actuator.

    Exactly what ``pi_eligible()`` (thermoctl/domain/pi_control.py) rejects with
    "Kein gewöhnlicher Schaltaktor zugeordnet." -- the simplest fixture for an
    "unsuitable zone", since eligibility already fails on the very first check.
    """
    return create_zone(session, name)
