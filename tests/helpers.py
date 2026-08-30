"""Shared test helpers.

Used by several test files and grows with the schema: every task that
introduces new entities adds its own creation function here.
"""

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from fastapi import FastAPI
from fastapi.routing import APIRoute
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session
from starlette.routing import BaseRoute

from thermoctl.db.models.credential import ApiToken, ApiTokenPermission
from thermoctl.db.models.device import Device
from thermoctl.db.models.identity import AccessGroup, GroupPermission, User, UserAccessGroup
from thermoctl.db.models.lookup import (
    PERMISSIONS,
    ActorSource,
    DeviceCapability,
    DeviceRole,
    Integration,
    OperatingMode,
    Permission,
    SensorStatus,
)
from thermoctl.db.models.measurement import DeviceHealth, Measurement
from thermoctl.db.models.operations import Setting
from thermoctl.db.models.override import ZoneOverride
from thermoctl.db.models.passkey import UserPasskey
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.state import ShadowDecision, ZoneState
from thermoctl.db.models.zone import SetpointMode, Zone, ZoneSetpoint

# A violated CHECK constraint arrives as a different exception depending on the
# database: SQLite reports IntegrityError, MariaDB reports error 4025, which
# pymysql maps to OperationalError. The constraint fires in both cases — only
# the exception class differs. A violated UNIQUE constraint, by contrast, is
# always IntegrityError everywhere; don't use this constant there, or the test
# would check less than it should.
CONSTRAINT_ERRORS = (IntegrityError, OperationalError)


def create_settings(
    session: Session,
    hysteresis: Decimal = Decimal("0.30"),
    min_ein: int = 300,
    session_duration_s: int | None = None,
) -> Setting:
    extra: dict[str, int] = {}
    if session_duration_s is not None:
        extra["session_lifetime_seconds"] = session_duration_s
    settings = Setting(
        id=1,
        # `eingebaut=True` as in production: the setup wizard creates the frost
        # protection mode as a builtin mode. Without this, every test using this
        # fixture would check a state that no real installation ever has -- and
        # that is exactly how the ordering of the deletion locks went unnoticed.
        frost_protection_mode_id=create_mode(session, "frost", eingebaut=True).id,
        default_hysteresis_k=hysteresis,
        default_min_on_seconds=min_ein,
        **extra,
    )
    session.add(settings)
    session.flush()
    return settings


def operating_mode(session: Session, code: str = "auto") -> OperatingMode:
    kind = session.query(OperatingMode).filter_by(code=code).one_or_none()
    if kind is None:
        kind = OperatingMode(code=code, label=code)
        session.add(kind)
        session.flush()
    return kind


def create_zone(session: Session, name: str) -> Zone:
    zone = Zone(name=name, display_name=name.capitalize(),
                operating_mode_id=operating_mode(session).id)
    session.add(zone)
    session.flush()
    return zone


def create_all_permissions(session: Session) -> None:
    """Creates every permission, the way the migration does in every real database.

    The group page shows the permissions that *exist*. In a test that only
    creates the two permissions it happens to need itself, it consequently
    shows two -- that says nothing about the page.
    """
    for code, _beschreibung, _zone_scoped in PERMISSIONS:
        ensure_permission(session, code)


def create_mode(
    session: Session, code: str, name: str | None = None, *, eingebaut: bool = False
) -> SetpointMode:
    mode = SetpointMode(code=code, name=name or code.capitalize(), is_builtin=eingebaut)
    session.add(mode)
    session.flush()
    return mode


def source(session: Session, code: str = "web") -> ActorSource:
    q = session.query(ActorSource).filter_by(code=code).one_or_none()
    if q is None:
        q = ActorSource(code=code, label=code)
        session.add(q)
        session.flush()
    return q


def integration(session: Session, code: str = "zigbee2mqtt") -> Integration:
    a = session.query(Integration).filter_by(code=code).one_or_none()
    if a is None:
        a = Integration(code=code, label=code)
        session.add(a)
        session.flush()
    return a


def role(session: Session, code: str) -> DeviceRole:
    r = session.query(DeviceRole).filter_by(code=code).one_or_none()
    if r is None:
        r = DeviceRole(code=code, label=code)
        session.add(r)
        session.flush()
    return r


def capability(session: Session, code: str) -> DeviceCapability:
    f = session.query(DeviceCapability).filter_by(code=code).one_or_none()
    if f is None:
        f = DeviceCapability(code=code, label=code)
        session.add(f)
        session.flush()
    return f


def create_device(session: Session, external_id: str) -> Device:
    g = Device(integration_id=integration(session).id, external_id=external_id,
               display_name=external_id)
    session.add(g)
    session.flush()
    return g


# The real descriptions and scopes, the way the migration seeds them.
_MODEL_PERMISSIONS = {
    code: (description, zone_scoped)
    for code, description, zone_scoped in PERMISSIONS
}


def ensure_permission(session: Session, code: str, zone_scoped: bool | None = None) -> Permission:
    """Creates a permission the way the migration does -- with its real description.

    Previously, the description was simply the code. That is a state no
    instance ever has, and it hid the fact that the group page only showed
    codes: in the tests, the code and the plain-language text looked the same.
    """
    p = session.query(Permission).filter_by(code=code).one_or_none()
    if p is None:
        description, aus_modell = _MODEL_PERMISSIONS.get(code, (code, False))
        p = Permission(
            code=code,
            description=description,
            is_zone_scoped=aus_modell if zone_scoped is None else zone_scoped,
        )
        session.add(p)
        session.flush()
    return p


def create_user(session: Session, name: str) -> User:
    user_record = User(username=name, display_name=name.upper(), password_hash="platzhalter")
    session.add(user_record)
    session.flush()
    return user_record


def _group_with_permissions(
    session: Session, name: str, permissions: list[tuple[str, int | None]]
) -> AccessGroup:
    group = AccessGroup(name=name)
    session.add(group)
    session.flush()
    for code, zone_id in permissions:
        permission_obj = ensure_permission(session, code, zone_scoped=zone_id is not None)
        session.add(
            GroupPermission(
                access_group_id=group.id, permission_id=permission_obj.id, zone_id=zone_id
            )
        )
    session.flush()
    return group


def user_with_permissions(
    session: Session,
    name: str,
    permissions: list[tuple[str, int | None]],
    second_group: list[tuple[str, int | None]] | None = None,
) -> User:
    """Creates a user and attaches them to one (or two) access group(s) with the
    given ``(code, zone_id)`` permissions."""
    user_record = create_user(session, name)
    group = _group_with_permissions(session, f"gruppe-{name}", permissions)
    session.add(UserAccessGroup(user_id=user_record.id, access_group_id=group.id))
    if second_group is not None:
        group_two = _group_with_permissions(session, f"gruppe-{name}-2", second_group)
        session.add(UserAccessGroup(user_id=user_record.id, access_group_id=group_two.id))
    session.flush()
    return user_record


def token_with_permissions(
    session: Session, user_record: User, permissions: list[tuple[str, int | None]]
) -> ApiToken:
    """Creates an API token for ``nutzer`` and enters the given permissions."""
    token = ApiToken(
        user_id=user_record.id,
        name=f"token-{user_record.username}",
        # Truncated to 16 characters: that is how long the column is. SQLite
        # accepts longer values without complaint, MariaDB rejects them -- a
        # test with a long username would otherwise only fail under MariaDB,
        # and that gets looked for in the wrong place.
        prefix=f"pfx-{user_record.username}"[:16],
        token_hash=f"hash-{user_record.username}",
    )
    session.add(token)
    session.flush()
    for code, zone_id in permissions:
        permission_obj = ensure_permission(session, code, zone_scoped=zone_id is not None)
        session.add(
            ApiTokenPermission(
                api_token_id=token.id, permission_id=permission_obj.id, zone_id=zone_id
            )
        )
    session.flush()
    return token
def point(weekday: int, minute_of_day: int, mode_code: str) -> SchedulePoint:
    return SchedulePoint(
        weekday=weekday, minute_of_day=minute_of_day, setpoint_mode_id=0
    )


def zone_with_schedule(
    session: Session,
    name: str,
    points: list[tuple[int, int, str, Decimal]],
    operating_mode: str = "auto",
    frost_protection: Decimal = Decimal("16.0"),
    override: tuple[Decimal, datetime | None] | None = None,
) -> Zone:
    frost = create_mode(session, f"frost-{name}", "Frostschutz")
    session.add(Setting(id=1, timezone="Europe/Berlin", frost_protection_mode_id=frost.id))
    kind = session.query(OperatingMode).filter_by(code=operating_mode).one_or_none()
    if kind is None:
        kind = OperatingMode(code=operating_mode, label=operating_mode)
        session.add(kind)
        session.flush()
    zone = Zone(name=name, display_name=name.capitalize(), operating_mode_id=kind.id)
    session.add(zone)
    session.flush()
    session.add(ZoneSetpoint(
        zone_id=zone.id, setpoint_mode_id=frost.id, temperature_c=frost_protection
    ))
    for weekday, minute_of_day, mode_code, temperature in points:
        mode = session.query(SetpointMode).filter_by(code=mode_code).one_or_none()
        if mode is None:
            mode = create_mode(session, mode_code)
        session.add(ZoneSetpoint(
            zone_id=zone.id, setpoint_mode_id=mode.id, temperature_c=temperature
        ))
        session.add(SchedulePoint(
            zone_id=zone.id, weekday=weekday, minute_of_day=minute_of_day,
            setpoint_mode_id=mode.id,
        ))
    if override is not None:
        temperature, end_at = override
        session.add(ZoneOverride(
            zone_id=zone.id, temperature_c=temperature,
            starts_at=datetime(2026, 8, 31, 0, 0), ends_at=end_at,
            source_id=source(session).id,
        ))
    session.flush()
    return zone


def alle_api_routen(app: FastAPI) -> list[APIRoute]:
    """Every route of the application, including those from included routers.

    Since FastAPI 0.141, `include_router()` no longer creates a flat list:
    instead of the individual routes, an `_IncludedRouter` sits in
    `app.routes`, carrying the original router under `original_router`.
    `app.routes` alone therefore only returned `/healthz` and the pages
    FastAPI generates itself — the guards in `test_endpunktabdeckung.py` and
    `test_csrf.py` ran into nothing, without turning red.

    Hence this, centralized once, with recursion over `original_router`.
    """
    found: list[APIRoute] = []

    def _durchgehen(routen: Sequence[BaseRoute]) -> None:
        for route in routen:
            eingebundener = getattr(route, "original_router", None)
            if eingebundener is not None:
                _durchgehen(eingebundener.routes)
            elif isinstance(route, APIRoute):
                found.append(route)

    _durchgehen(app.routes)
    return found


def create_measurement(
    session: Session, device: Device, capability_id: int, *, value: Decimal
) -> Measurement:
    moment = datetime(2026, 8, 29, 8, 0)
    measurement = Measurement(
        device_id=device.id,
        capability_id=capability_id,
        value_numeric=value,
        measured_at=moment,
        received_at=moment,
    )
    session.add(measurement)
    session.flush()
    return measurement


def create_device_state(session: Session, device: Device) -> DeviceHealth:
    state = DeviceHealth(
        device_id=device.id,
        last_payload_at=datetime(2026, 8, 29, 8, 0),
        payload_count=1,
    )
    session.add(state)
    session.flush()
    return state


def sensor_status_of(session: Session, code: str = "ok") -> SensorStatus:
    status = session.query(SensorStatus).filter_by(code=code).one_or_none()
    if status is None:
        status = SensorStatus(code=code, label=code)
        session.add(status)
        session.flush()
    return status


def create_zone_state(session: Session, zone: Zone) -> ZoneState:
    state = ZoneState(
        zone_id=zone.id,
        sensor_status_id=sensor_status_of(session).id,
        updated_at=datetime(2026, 8, 29, 8, 0),
    )
    session.add(state)
    session.flush()
    return state


def create_shadow_decision(session: Session, zone: Zone) -> ShadowDecision:
    decision = ShadowDecision(
        decided_at=datetime(2026, 8, 29, 8, 0),
        zone_id=zone.id,
        setpoint_reason="Zeitplan",
        would_heat=False,
        outcome_code="aus",
        reason="Sollwert ist erreicht.",
    )
    session.add(decision)
    session.flush()
    return decision


def create_passkey(
    session: Session, user_record: User, credential_id: str = "kennung", sign_count: int = 0
) -> UserPasskey:
    """A stored passkey. The public key here is a placeholder —
    tests that actually verify it generate one with a software authenticator."""
    entry = UserPasskey(
        user_id=user_record.id,
        credential_id=credential_id,
        public_key="platzhalter",
        sign_count=sign_count,
        label=f"Passkey {credential_id}",
    )
    session.add(entry)
    session.flush()
    return entry
