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

from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.helpers import capability, create_device, create_zone, user_with_permissions
from thermoctl.auth.passwords import hash_password
from thermoctl.db.models.device import Device, DeviceCapabilityLink
from thermoctl.db.models.identity import User
from thermoctl.db.models.measurement import Measurement
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.zone import SetpointMode, Zone, ZoneSetpoint
from thermoctl.domain.ui_profile import WebUiProfile


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


def create_login_tenant_user(
    session: Session, username: str, password: str, permissions: list[tuple[str, int | None]]
) -> User:
    """Wie ``create_login_user``, nur mit Mieterprofil (``ui_profile=TENANT``).

    Eigene, schmale Funktion statt eines zusätzlichen Arguments an
    ``create_login_user``: dessen Signatur wird von etlichen bestehenden Tests
    genutzt, und das Profil ist ohnehin eine vom Recht unabhängige Eigenschaft der
    Gruppe (siehe ``thermoctl.domain.ui_profile``), nicht der einzelnen Berechtigung.
    """
    user = user_with_permissions(session, username, permissions, ui_profile=WebUiProfile.TENANT)
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


def create_temperature_device(session: Session, external_id: str) -> Device:
    """A device that genuinely measures temperature -- for the outdoor-temperature
    source picker, which `domain.device_assignment.check_capability` rejects a
    device for only when a *contradicting* capability is on record. A plain
    device with no capability at all would pass that check for the wrong reason
    (nothing known, so nothing to contradict) -- this one actually proves the
    happy path.
    """
    device = create_device(session, external_id)
    session.add(
        DeviceCapabilityLink(
            device_id=device.id, capability_id=capability(session, "temperature").id
        )
    )
    session.flush()
    return device


def create_switch_only_device(session: Session, external_id: str) -> Device:
    """A device on record as a plain switch and nothing else -- the counterexample
    for the outdoor-temperature source: `check_capability` must reject exactly
    this one, since a switch capability is known and temperature is not among it.
    """
    device = create_device(session, external_id)
    session.add(
        DeviceCapabilityLink(
            device_id=device.id, capability_id=capability(session, "switch").id
        )
    )
    session.flush()
    return device


def seed_outdoor_measurement(
    session: Session, device: Device, *, value_c: Decimal, measured_at: datetime
) -> Measurement:
    """A single temperature reading from `device`, at a chosen instant -- unlike
    ``tests.helpers.create_measurement``, whose fixed timestamp is only ever fresh
    or stale by accident of when a test happens to run relative to it.
    """
    measurement = Measurement(
        device_id=device.id,
        capability_id=capability(session, "temperature").id,
        value_numeric=value_c,
        measured_at=measured_at,
        received_at=measured_at,
    )
    session.add(measurement)
    session.flush()
    return measurement
