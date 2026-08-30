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
* **Boost brings the next schedule point forward.** Not "heat at full blast": whatever
  would come next anyway now applies immediately, and exactly until the point in time
  it would have arrived at as scheduled. After that, the plan continues as if nothing
  had happened. A boost to a fixed value would instead have to guess how warm and for
  how long.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.db.models.operations import Setting
from thermoctl.db.models.override import ZoneOverride
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.zone import SetpointMode, Zone
from thermoctl.domain.modes import update_setpoints
from thermoctl.domain.schedule import (
    create_override,
    current_point,
    end_of_next_switch,
    next_point,
    resolved_setpoint,
    temperature_for_mode,
)

log = logging.getLogger(__name__)


class RemoteControlError(ValueError):
    """The request is understood, but cannot be carried out in this state."""


@dataclass(frozen=True)
class Boost:
    """What a boost has done."""

    mode_code: str
    temperature: Decimal
    bis: datetime


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
    geltend = resolved_setpoint(session, zone, now)
    if geltend.mode_id is None:
        create_override(
            session, zone, temperature, _runendes_ende(session, zone, now),
            user_id=user_id, token_id=token_id, source=source,
        )
        return temperature
    update_setpoints(
        session, zone, {geltend.mode_id: temperature}, user_id=user_id, source=source
    )
    return temperature


def _runendes_ende(session: Session, zone: Zone, now: datetime) -> datetime | None:
    """The end of the currently running override -- so the new one lasts no longer."""
    running = session.scalars(
        select(ZoneOverride)
        .where(
            ZoneOverride.zone_id == zone.id,
            ZoneOverride.cancelled_at.is_(None),
            ZoneOverride.starts_at <= now,
        )
        .order_by(ZoneOverride.created_at.desc(), ZoneOverride.id.desc())
    ).first()
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
    timezone_name = ZoneInfo(settings.timezone)
    lokal = now.replace(tzinfo=ZoneInfo("UTC")).astimezone(timezone_name).replace(tzinfo=None)

    points = list(
        session.scalars(select(SchedulePoint).where(SchedulePoint.zone_id == zone.id))
    )
    if not points:
        raise RemoteControlError(
            "Diese Zone hat keinen Zeitplan — es gibt keine nächste Schaltung."
        )
    faellt_um = next_point(points, lokal)
    ende = end_of_next_switch(session, zone, now)
    if faellt_um is None or ende is None:  # pragma: no cover - points is not empty
        raise RemoteControlError("Die nächste Schaltung lässt sich nicht bestimmen.")

    # The point that applies from `faellt_um` onward -- the one we are bringing
    # forward. Queried one minute past it, so that `geltender_punkt` returns it and
    # not its predecessor.
    kommend = current_point(points, faellt_um)
    if kommend is None:  # pragma: no cover - points is not empty
        raise RemoteControlError("Die nächste Schaltung lässt sich nicht bestimmen.")
    mode = session.get(SetpointMode, kommend.setpoint_mode_id)
    temperature = temperature_for_mode(session, zone, kommend.setpoint_mode_id)
    if mode is None or temperature is None:
        raise RemoteControlError(
            "Für den nächsten Modus ist in dieser Zone keine Temperatur hinterlegt."
        )

    create_override(
        session, zone, temperature, ende,
        user_id=user_id, token_id=token_id, source=source,
    )
    log.info(
        "Naechste Schaltung vorgezogen",
        extra={"zone_id": zone.id, "modus": mode.code, "bis": ende.isoformat()},
    )
    return Boost(mode.code, temperature, ende)
