"""What a dial does from the outside.

Home Assistant shows a thermostat per zone, a boost button, and a dial per mode. This
module answers what happens **on the domain level** when it is turned -- once, for all
adapters. The MQTT receiver calls it exactly as the interface could; the bounds and the
audit entries come from the same functions.

Two decisions are embedded in it:

* **The thermostat adjusts the mode, not "right now".** Whoever turns a dial in Home
  Assistant to 21 degrees almost always means "it should be 21 degrees warm here", not
  "for the next two hours". An override would be gone again after the next schedule
  point, and the dial would appear to jump back on its own. That is why it changes the
  setpoint of the mode currently in effect -- the same thing the thermostat on the
  start page does.
* **A dial on a bridge (Matter) sets a temporary override, not the mode.** Where the
  Home Assistant thermostat means "it should be 21 degrees here", a wheel on a wall
  panel or in the Home app means "warmer *now*" -- and the schedule is expected to take
  over again afterward. `set_temporary_setpoint` is that: an override that ends by
  itself (at the next schedule point, or after a fixed time -- a setting), replacing a
  running one instead of stacking beside it. `set_setpoint` above is unchanged.
* **Boost brings the next schedule point forward.** Not "heat at full blast": whatever
  would come next anyway now applies immediately, and exactly until the point in time
  it would have arrived at as scheduled. After that, the plan continues as if nothing
  had happened. A boost to a fixed value would instead have to guess how warm and for
  how long.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl import audit
from thermoctl.db.models.operations import Setting
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.zone import SetpointMode, Zone
from thermoctl.domain.modes import check_temperature, update_setpoints
from thermoctl.domain.schedule import (
    cancel_running_overrides,
    create_override,
    current_point,
    end_of_next_switch,
    next_point,
    resolved_setpoint,
    running_override,
    temperature_for_mode,
)
from thermoctl.domain.time import local_time

log = logging.getLogger(__name__)


class RemoteControlError(ValueError):
    """The request is understood, but cannot be carried out in this state."""


@dataclass(frozen=True)
class Boost:
    """What a boost has done."""

    mode_code: str
    temperature: Decimal
    bis: datetime


@dataclass(frozen=True)
class TemporarySetpoint:
    """What a turn of the dial has done."""

    temperature: Decimal
    ends_at: datetime


def set_temporary_setpoint(
    session: Session,
    zone: Zone,
    temperature: Decimal,
    now: datetime,
    *,
    user_id: int | None = None,
    token_id: int | None = None,
    source: str,
) -> TemporarySetpoint:
    """A temporary override with `temperature`, replacing whatever the dial set before.

    **Ends by itself, never unbounded.** By setting (`setting.temporary_setpoint_*`):
    at the next schedule point, or after a fixed number of minutes. Where there is no
    next point (a zone without a schedule) the same minutes are the fallback -- an
    override without an end would be the one thing a dial must not leave behind.

    **Replaces, does not stack.** Every override of the zone that is running now is
    ended first (`cancel_running_overrides`), except an absence's: its setback runs on
    underneath and applies again once this one is over. Without this, three turns left
    three running rows, and ending "the" override uncovered the previous dial value
    instead of the schedule.

    **Refuses a zone that is off.** The operating mode `off` beats every override
    (`resolved_setpoint`): the value would be accepted and do nothing, and the dial
    would look as if it had worked. A clear refusal is the honest answer.

    The temperature is checked *before* anything is ended -- a refused turn must not
    cost the zone the override it had. Ending the running ones and creating the new one
    happen under a zone lock (see below).
    """
    if zone.operating_mode.code == "off":
        raise RemoteControlError(
            "Diese Zone ist ausgeschaltet — ein Sollwert würde nichts bewirken. "
            "Zuerst die Betriebsart ändern."
        )
    temperature = check_temperature(temperature)
    settings = session.get(Setting, 1)
    if settings is None:
        raise RemoteControlError("Die Einrichtung ist unvollständig.")

    ends_at = (
        end_of_next_switch(session, zone, now)
        if settings.temporary_setpoint_until_next_switch
        else None
    )
    # Not strictly after `now` counts as "no next switch" too: in the repeated hour of
    # the clock change, `end_of_next_switch` reads a doubled local time as its first
    # occurrence and can return an end *before* now. An override that ends before it
    # starts would end the running one and never apply itself. (The cause sits in
    # `end_of_next_switch`, which boost uses as well -- left alone here on purpose.)
    if ends_at is None or ends_at <= now:
        ends_at = now + timedelta(minutes=settings.temporary_setpoint_minutes)

    # Lock order: the zone row first, then the override rows (the locking read in
    # `cancel_running_overrides`). Turns of the dial for the same zone queue up on the
    # zone instead of both reading "nothing running" and both inserting; `ui_api_schedule`
    # takes the zone before its schedule points the same way (parent before dependents,
    # so two paths cannot wait on each other crosswise). Both are no-ops on SQLite, which
    # has no row locks. Deliberately **not** inside a savepoint: on MariaDB with snapshot
    # isolation the locking read can fail with 1020 and end the transaction, and the
    # savepoint's own rollback would then fail with 1305 and hide the 1020 the caller
    # reacts to (`app._process_mqtt_message`). Nothing is lost by that: an exception
    # leaves the caller's transaction, which rolls back as a whole.
    session.execute(select(Zone.id).where(Zone.id == zone.id).with_for_update())
    replaced = cancel_running_overrides(session, zone, now)
    create_override(
        session, zone, temperature, ends_at,
        now=now, user_id=user_id, token_id=token_id, source=source,
    )
    audit.record(
        session,
        source=source,
        action="update",
        # The object kind a room's audit scope already knows (keyed by zone id).
        object_type="zone_setpoint",
        object_id=str(zone.id),
        summary=(
            f"Sollwert für Zone '{zone.display_name}' vorübergehend auf "
            f"{str(temperature).replace('.', ',')} °C gesetzt"
        ),
        detail=(
            f"bis {ends_at.isoformat()}, {len(replaced)} laufende "
            "Übersteuerung(en) ersetzt"
        ),
        user_id=user_id,
        token_id=token_id,
    )
    log.info(
        "Befristeter Sollwert gesetzt",
        extra={"zone_id": zone.id, "temperatur": str(temperature), "bis": ends_at.isoformat()},
    )
    return TemporarySetpoint(temperature, ends_at)


def set_setpoint(
    session: Session,
    zone: Zone,
    temperature: Decimal,
    now: datetime,
    *,
    user_id: int | None = None,
    token_id: int | None = None,
    source: str,
) -> Decimal:
    """Sets the setpoint of the currently effective mode.

    If an override with a fixed temperature is running, there is no mode to adjust --
    in that case the override itself is set to the new value. Otherwise the dial would
    jump back to the old value on the next state report and look as if it had
    swallowed the command.
    """
    in_effect = resolved_setpoint(session, zone, now)
    if in_effect.mode_id is None:
        create_override(
            session, zone, temperature, _rounded_end(session, zone, now),
            now=now, user_id=user_id, token_id=token_id, source=source,
        )
        return temperature
    update_setpoints(
        session, zone, {in_effect.mode_id: temperature}, user_id=user_id, source=source
    )
    return temperature


def _rounded_end(session: Session, zone: Zone, now: datetime) -> datetime | None:
    """The end of the currently running override -- so the new one lasts no longer."""
    running = running_override(session, zone, now)
    return running.ends_at if running is not None else None


def boost(
    session: Session,
    zone: Zone,
    now: datetime,
    *,
    user_id: int | None = None,
    token_id: int | None = None,
    source: str,
) -> Boost:
    """Brings the next schedule point forward: whatever would come next applies now.

    Implemented as an override that ends exactly when the schedule point would
    normally fall. After that, the schedule takes over on its own -- nothing is left
    behind that someone would have to clean up afterward.
    """
    settings = session.get(Setting, 1)
    if settings is None:
        raise RemoteControlError("Die Einrichtung ist unvollständig.")
    local = local_time(now, settings.timezone).replace(tzinfo=None)

    points = list(
        session.scalars(select(SchedulePoint).where(SchedulePoint.zone_id == zone.id))
    )
    if not points:
        raise RemoteControlError(
            "Diese Zone hat keinen Zeitplan — es gibt keine nächste Schaltung."
        )
    falls_at = next_point(points, local)
    end_at = end_of_next_switch(session, zone, now)
    if falls_at is None or end_at is None:  # pragma: no cover - points is not empty
        raise RemoteControlError("Die nächste Schaltung lässt sich nicht bestimmen.")

    # The point that applies from `faellt_um` onward -- the one we are bringing
    # forward. Queried one minute past it, so that `geltender_punkt` returns it and
    # not its predecessor.
    upcoming = current_point(points, falls_at)
    if upcoming is None:  # pragma: no cover - points is not empty
        raise RemoteControlError("Die nächste Schaltung lässt sich nicht bestimmen.")
    mode = session.get(SetpointMode, upcoming.setpoint_mode_id)
    temperature = temperature_for_mode(session, zone, upcoming.setpoint_mode_id)
    if mode is None or temperature is None:
        raise RemoteControlError(
            "Für den nächsten Modus ist in dieser Zone keine Temperatur hinterlegt."
        )

    create_override(
        session, zone, temperature, end_at,
        now=now, user_id=user_id, token_id=token_id, source=source,
    )
    log.info(
        "Nächste Schaltung vorgezogen",
        extra={"zone_id": zone.id, "modus": mode.code, "bis": end_at.isoformat()},
    )
    return Boost(mode.code, temperature, end_at)
