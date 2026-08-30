from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from thermoctl import audit
from thermoctl.db.models.operations import Setting
from thermoctl.db.models.override import ZoneOverride
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.zone import SetpointMode, Zone, ZoneSetpoint

# The project's one setpoint bound. It applies to mode setpoints as well as to
# overrides, and to all four adapters -- interface, REST, MCP and the Home Assistant
# card all read it from here.
#
# The lower bound used to be 5 degrees, then briefly 1. It is now
# **minus 20**: a setpoint in the negative range means "no heating happens here, and
# genuinely none" -- for a garage, a shed, or a room someone only wants to monitor and
# not temperature-control. With a setpoint of 1 degree, the plant still heats as soon
# as it gets colder; that is a different thing.
#
# Minus 20 and not arbitrarily low: below that there is no longer a genuine intent, only
# a typo or a broken payload, and that should keep standing out. It is also the range
# ordinary Zigbee sensors report.
#
# **This is a bound on input, not on physics.** Whoever sets a setpoint below roughly
# 4 degrees accepts that pipes may freeze; the software no longer stops them from doing
# so. Frost protection remains a mode of its own and still kicks in on a failed sensor
# and operating mode "off".
MINIMUM_TEMPERATURE_C = Decimal("-20.0")
MAXIMUM_TEMPERATURE_C = Decimal("35.0")


# Deliberately NOT `frozen=True`: Python attaches a traceback to an exception when it
# is raised, and a frozen dataclass refuses exactly that. The bug only surfaces once
# the exception is passed far enough — in our case through FastAPI's dependency
# resolution — and then shows up as `FrozenInstanceError` instead of the error you are
# actually looking for.
@dataclass
class DomainError(Exception):
    field: str
    notice: str


def _check_mode_values(
    session: Session, *, code: str, name: str, sort_order: int, mode_id: int | None = None
) -> tuple[str, str, int]:
    code = code.strip()
    name = name.strip()
    if not code:
        raise DomainError("code", "Der technische Code darf nicht leer sein.")
    if len(code) > 32:
        raise DomainError("code", "Der technische Code darf höchstens 32 Zeichen haben.")
    if not name:
        raise DomainError("name", "Der Name darf nicht leer sein.")
    if len(name) > 64:
        raise DomainError("name", "Der Name darf höchstens 64 Zeichen haben.")
    vorhandene_id = session.scalar(select(SetpointMode.id).where(SetpointMode.code == code))
    if vorhandene_id is not None and vorhandene_id != mode_id:
        raise DomainError("code", "Dieser technische Code ist bereits vergeben.")
    return code, name, sort_order


def create_mode(
    session: Session,
    *,
    code: str,
    name: str,
    sort_order: int,
    user_id: int,
    source: str = "web",
) -> SetpointMode:
    code, name, sort_order = _check_mode_values(
        session, code=code, name=name, sort_order=sort_order
    )
    mode = SetpointMode(code=code, name=name, sort_order=sort_order)
    session.add(mode)
    session.flush()
    audit.record(
        session,
        source=source,
        action="create",
        object_type="setpoint_mode",
        object_id=str(mode.id),
        summary=f"Sollwert-Modus '{mode.name}' angelegt",
        user_id=user_id,
    )
    return mode


def update_mode(
    session: Session,
    mode: SetpointMode,
    *,
    code: str,
    name: str,
    sort_order: int,
    user_id: int,
    source: str = "web",
) -> None:
    code, name, sort_order = _check_mode_values(
        session, code=code, name=name, sort_order=sort_order, mode_id=mode.id
    )
    mode.code = code
    mode.name = name
    mode.sort_order = sort_order
    audit.record(
        session,
        source=source,
        action="update",
        object_type="setpoint_mode",
        object_id=str(mode.id),
        summary=f"Sollwert-Modus '{mode.name}' geändert",
        user_id=user_id,
    )


def delete_guard(session: Session, mode: SetpointMode) -> str | None:
    """Why this mode cannot be deleted — or None if it can.

    The ordering is deliberate: **frost protection is checked first.** The setup
    wizard creates it with `is_builtin=True`, so the general lock also applies to it —
    and if that lock took effect first, then in every real plant exactly the most
    important mode would get the meaningless message 'the application needs it'
    instead of the reason that actually matters: it is the fallback on sensor failure.
    """
    settings = session.get(Setting, 1)
    if settings is not None and settings.frost_protection_mode_id == mode.id:
        return (
            "Der Frostschutzmodus kann nicht gelöscht werden — er ist die Rückfallebene, "
            "wenn ein Sensor ausfällt."
        )
    if mode.is_builtin:
        return "Eingebaute Modi können nicht gelöscht werden, weil die Anwendung sie benötigt."
    uses = sum(
        session.scalar(select(func.count()).select_from(model).where(column == mode.id)) or 0
        for model, column in (
            (SchedulePoint, SchedulePoint.setpoint_mode_id),
            (ZoneOverride, ZoneOverride.setpoint_mode_id),
        )
    )
    if uses:
        return (
            "Dieser Modus kann nicht gelöscht werden, weil Zeitpläne oder historische "
            "Übersteuerungen ihn noch verwenden."
        )
    return None


def delete_mode(
    session: Session, mode: SetpointMode, *, user_id: int, source: str = "web"
) -> None:
    lock = delete_guard(session, mode)
    if lock is not None:
        raise DomainError("mode_id", lock)
    # Setpoints have no meaning without their mode. They are deliberately removed here
    # in the same transaction; schedules and history, by contrast, prevent deletion
    # above because their record must be preserved.
    session.execute(delete(ZoneSetpoint).where(ZoneSetpoint.setpoint_mode_id == mode.id))
    mode_id = mode.id
    mode_name = mode.name
    session.delete(mode)
    audit.record(
        session,
        source=source,
        action="delete",
        object_type="setpoint_mode",
        object_id=str(mode_id),
        summary=f"Sollwert-Modus '{mode_name}' gelöscht",
        user_id=user_id,
    )


def _grad(value: Decimal) -> str:
    """`1,0` -- with a comma, because the message is shown to the user."""
    return f"{value:.1f}".replace(".", ",")


def check_temperature(temperature: Decimal) -> Decimal:
    if not temperature.is_finite():
        raise DomainError("temperature_c", "Der Sollwert muss eine endliche Zahl sein.")
    if temperature < MINIMUM_TEMPERATURE_C or temperature > MAXIMUM_TEMPERATURE_C:
        # Built from the constants, not copied by hand: a message that states the
        # bound again would drift from it the next time it moves.
        raise DomainError(
            "temperatur",
            f"Der Sollwert muss zwischen {_grad(MINIMUM_TEMPERATURE_C)} und "
            f"{_grad(MAXIMUM_TEMPERATURE_C)} °C liegen.",
        )
    exponent = temperature.as_tuple().exponent
    if isinstance(exponent, int) and exponent < -1:
        raise DomainError(
            "temperatur", "Der Sollwert darf höchstens eine Nachkommastelle haben."
        )
    return temperature.quantize(Decimal("0.1"))


def update_setpoints(
    session: Session,
    zone: Zone,
    values: dict[int, Decimal | None],
    *,
    # `None` means: nobody is logged in. That applies to a command from Home
    # Assistant, which has no account behind it. This used to be `int`, and the
    # adapters passed through `principal.user_id or 0` -- an id that does not exist
    # and on which MariaDB rejects the audit entry's foreign key.
    user_id: int | None,
    source: str = "web",
) -> None:
    # Check all values first, only then touch any row. The view catches the domain
    # error and shows the form again; without this ordering, an earlier valid input
    # would get saved despite a later error.
    checked_values = {
        mode_id: check_temperature(temperature) if temperature is not None else None
        for mode_id, temperature in values.items()
    }
    vorhandene = {
        row.setpoint_mode_id: row
        for row in session.scalars(
            select(ZoneSetpoint).where(ZoneSetpoint.zone_id == zone.id)
        )
    }
    changed = False
    for mode_id, temperature in checked_values.items():
        row = vorhandene.get(mode_id)
        if temperature is None:
            if row is not None:
                session.delete(row)
                changed = True
            continue
        temperature = check_temperature(temperature)
        if row is None:
            session.add(
                ZoneSetpoint(
                    zone_id=zone.id, setpoint_mode_id=mode_id, temperature_c=temperature
                )
            )
            changed = True
        elif row.temperature_c != temperature:
            row.temperature_c = temperature
            changed = True
    if changed:
        audit.record(
            session,
            source=source,
            action="update",
            object_type="zone_setpoint",
            object_id=str(zone.id),
            summary=f"Sollwerte für Zone '{zone.display_name}' geändert",
            user_id=user_id,
        )
