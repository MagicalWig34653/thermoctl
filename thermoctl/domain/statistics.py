"""When and for how long heating happened.

The source is the shadow log: the control cycle writes a row with `would_heat` for
every zone on every pass. That is dense sampling -- one data point every 60 seconds --
and from that the duration can genuinely be computed instead of estimated.

**Computed over the gaps, not over the row count.** A counter of "this many rows times
cycle length" would be simpler and wrong in two cases: when the cycle was set to a
different length at some point, and when the service was down. Every gap between two
consecutive data points counts for exactly as long as it actually was.

**Gaps get capped.** If the service stood still for a night, eight hours lie between
two data points. Counting that as heating time would be pure fabrication -- the plant
reported nothing during that time, and nobody knows what it actually did. A gap
significantly larger than the cycle therefore only counts up to the cap.

During the dry run this is a statement about what thermoctl *would have* heated. After
arming, about the same thing it actually did -- the rows are produced at the same
place.
"""

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.db.models.lookup import CommandOutcome
from thermoctl.db.models.state import DeviceCommand, ShadowDecision
from thermoctl.domain.time import local_time

# How much larger than a cycle a gap is allowed to be before it counts as a gap.
# Three cycles: a single missed pass is normal operation, three in a row is an outage.
GAP_FACTOR = 3

# Meross publishes 16 A for a representative plug, but neither the exact installed
# relay nor its electrical endurance. 500,000 electrical operations under load are
# therefore an explicitly replaceable assumption, borrowed only as an order of
# magnitude from Panasonic's separate 16-A ALZN5B05W power relay at 250 V resistive
# load (the source is linked next to the value in the UI). It is not a Meross rating;
# load current, inrush current, load type and temperature can change real endurance
# substantially.
#
# This module never reads `setting.assumed_relay_lifetime_operations` itself --
# `relay_operations` below is a domain function and stays free of a database lookup
# for a single scalar, the same reasoning `heating_periods` follows for
# `cycle_seconds`. Callers (the web view, REST, MCP) read the setting and pass it in.
# This constant remains only as the default for a caller that does not, and as the
# value a fresh installation's migration seeds the setting with.
DEFAULT_ASSUMED_RELAY_LIFETIME_OPERATIONS = 500_000
DAYS_PER_YEAR = 365


@dataclass(frozen=True)
class DayValue:
    day: date
    seconds: int


@dataclass(frozen=True)
class ZoneStatistics:
    zone_id: int
    days: list[DayValue]

    @property
    def seconds_total(self) -> int:
        return sum(t.seconds for t in self.days)


@dataclass(frozen=True)
class RelayDayValue:
    day: date
    operations: int

    @property
    def annual_projection(self) -> int:
        """What this single local calendar day's count would mean for a year."""
        return self.operations * DAYS_PER_YEAR


@dataclass(frozen=True)
class RelayDeviceStatistics:
    zone_id: int
    device_name: str
    days: list[RelayDayValue]
    # The installation's own setting, passed in by the caller -- see the note next to
    # `DEFAULT_ASSUMED_RELAY_LIFETIME_OPERATIONS` above for why this dataclass does
    # not read it itself. The default here only covers a caller (tests, mostly) that
    # does not care about the exact assumption.
    assumed_lifetime_operations: int = DEFAULT_ASSUMED_RELAY_LIFETIME_OPERATIONS

    @property
    def operations_total(self) -> int:
        return sum(value.operations for value in self.days)

    @property
    def annual_projection(self) -> int:
        """Selected days' arithmetic mean, projected to 365 calendar days."""
        if not self.days:
            return 0
        numerator = self.operations_total * DAYS_PER_YEAR
        # Integer half-up rather than Python's surprising banker's rounding. The UI
        # deliberately presents a whole operation, not false decimal precision.
        return (numerator + len(self.days) // 2) // len(self.days)

    @property
    def assumed_lifetime_percent_per_year(self) -> float:
        return self.annual_projection * 100 / self.assumed_lifetime_operations

    @property
    def assumed_lifetime_years(self) -> float | None:
        if self.annual_projection == 0:
            return None
        return self.assumed_lifetime_operations / self.annual_projection

    @property
    def wear_level(self) -> Literal["normal", "warning", "danger"]:
        if self.annual_projection >= self.assumed_lifetime_operations:
            return "danger"
        if self.annual_projection * 2 >= self.assumed_lifetime_operations:
            return "warning"
        return "normal"


def _switch_state(payload: str) -> bool | None:
    """Read today's Zigbee2MQTT or Meross on/off payload; unknown shapes stay unknown."""
    try:
        value = json.loads(payload)
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict):
        return None
    state = value.get("state")
    if state == "ON":
        return True
    if state == "OFF":
        return False
    togglex = value.get("togglex")
    if isinstance(togglex, dict):
        onoff = togglex.get("onoff")
        if onoff == 1:
            return True
        if onoff == 0:
            return False
    return None


def relay_operations(
    session: Session,
    zone_ids: list[int],
    start_at: datetime,
    until: datetime,
    *,
    timezone_name: str | None = None,
    assumed_lifetime_operations: int = DEFAULT_ASSUMED_RELAY_LIFETIME_OPERATIONS,
) -> list[RelayDeviceStatistics]:
    """Confirmed commanded on/off state changes per device and local calendar day.

    ``assumed_lifetime_operations`` is the installation's own
    ``setting.assumed_relay_lifetime_operations``. This function stays free of a
    database lookup for that single scalar -- the same reasoning ``heating_periods``
    follows for ``cycle_seconds`` -- so the caller reads the setting and passes it
    through; the default here only covers a caller that does not care.

    Only command kind ``switch`` is a relay operation. ``setpoint`` messages to a
    self-regulating valve and ``thermostat`` messages to a TRV without an on/off
    output move actuators too, but do not operate a relay and therefore do not belong
    under this lifetime assumption.

    ``executed`` means that the adapter confirmed sending, not that thermoctl observed
    the physical relay. Suppressed commands certainly did not leave the dry-run bolt.
    Failed commands may or may not have moved hardware, so this statistic deliberately
    excludes them and never uses them as a new known state. This produces a defensible
    lower-bound history instead of presenting an uncertain attempt as measured wear.
    A first successfully commanded state is likewise only a baseline; without an older
    known state, no transition can be proven.

    Grouping uses ``(zone_id, device_name snapshot)`` rather than ``device_id``. That
    keeps a deleted device's history visible after its foreign key becomes NULL. The
    accepted downside is explicit: renaming a device splits its history, while reusing
    the same name within one zone can merge two devices. Grouping NULL device IDs would
    merge *all* deleted devices, and joining current devices would make them disappear.
    The stable zone ID remains mandatory so equal device names in different visible
    zones never mix and zone-scoped visibility can be applied before aggregation.

    ``start_at`` and ``until`` use the project's naive UTC representation. The caller
    obtains ``start_at`` through ``local_day_start_utc``; day labels are then derived
    with the same configured timezone here, never at UTC midnight.
    """
    if not zone_ids or until < start_at:
        return []

    rows = session.execute(
        select(DeviceCommand, CommandOutcome.code)
        .join(CommandOutcome, CommandOutcome.id == DeviceCommand.outcome_id)
        .where(
            DeviceCommand.zone_id.in_(zone_ids),
            DeviceCommand.command == "switch",
            DeviceCommand.sent_at >= start_at,
            DeviceCommand.sent_at <= until,
        )
        .order_by(
            DeviceCommand.zone_id,
            DeviceCommand.device_name,
            DeviceCommand.sent_at,
            DeviceCommand.id,
        )
    ).all()
    if not rows:
        return []

    by_device: dict[tuple[int, str], list[tuple[DeviceCommand, str]]] = defaultdict(list)
    for command, outcome in rows:
        # zone_id cannot be NULL after the filter, but SQLAlchemy correctly retains
        # the model's nullable annotation. Keeping the check makes the narrowing
        # explicit instead of hiding it in a cast.
        if command.zone_id is not None:
            by_device[(command.zone_id, command.device_name)].append((command, outcome))

    first_day = local_time(start_at, timezone_name).date()
    last_day = local_time(until, timezone_name).date()
    days = [
        first_day + timedelta(days=offset)
        for offset in range((last_day - first_day).days + 1)
    ]
    statistics: list[RelayDeviceStatistics] = []
    for (zone_id, device_name), commands in by_device.items():
        previous = session.execute(
            select(DeviceCommand, CommandOutcome.code)
            .join(CommandOutcome, CommandOutcome.id == DeviceCommand.outcome_id)
            .where(
                DeviceCommand.zone_id == zone_id,
                DeviceCommand.device_name == device_name,
                DeviceCommand.command == "switch",
                DeviceCommand.sent_at < start_at,
                CommandOutcome.code == "executed",
            )
            .order_by(DeviceCommand.sent_at.desc(), DeviceCommand.id.desc())
            .limit(1)
        ).first()
        previous_state = _switch_state(previous[0].payload) if previous is not None else None
        counts: dict[date, int] = defaultdict(int)
        for command, outcome in commands:
            if outcome != "executed":
                continue
            state = _switch_state(command.payload)
            if state is None:
                continue
            if previous_state is not None and state != previous_state:
                counts[local_time(command.sent_at, timezone_name).date()] += 1
            previous_state = state
        statistics.append(
            RelayDeviceStatistics(
                zone_id=zone_id,
                device_name=device_name,
                days=[RelayDayValue(day, counts[day]) for day in days],
                assumed_lifetime_operations=assumed_lifetime_operations,
            )
        )
    return statistics


def heating_periods(
    session: Session,
    zone_ids: list[int],
    start_at: datetime,
    bis: datetime,
    *,
    cycle_seconds: int,
    timezone_name: str | None = None,
) -> dict[int, ZoneStatistics]:
    """Heating duration per zone and day in the given period, in seconds.

    `von` and `bis` are naive UTC like everything in this project, but the **day** a
    segment is attributed to, and the day labels the caller sees, are local --
    `timezone_name` is the configured `setting.timezone`. Grouping by the UTC date
    instead, as this used to, cut every local day at 01:00 or 02:00 in
    `Europe/Berlin`: the first hour or two of a day's heating was counted against the
    day before. A segment is attributed to the local day of its **start**; with
    sampling on a minute cadence, the error at a day boundary stays under a minute and
    thus below the resolution at which the number is even displayed.

    Daylight saving time needs no special case here: a day with 23 or 25 wall-clock
    hours falls out on its own once bucketing goes by local date instead of a fixed
    24-hour span -- the extra or missing hour simply lands, correctly, in whichever
    local calendar day it actually occurred on.
    """
    maximum_interval = max(cycle_seconds, 1) * GAP_FACTOR
    eimer: dict[int, dict[date, int]] = defaultdict(lambda: defaultdict(int))
    if not zone_ids:
        return {}

    def local_date(moment: datetime) -> date:
        return local_time(moment, timezone_name).date()

    previous: dict[int, tuple[datetime, bool]] = {}
    for zone_id, moment, heating in session.execute(
        select(
            ShadowDecision.zone_id, ShadowDecision.decided_at, ShadowDecision.would_heat
        )
        .where(
            ShadowDecision.zone_id.in_(zone_ids),
            ShadowDecision.decided_at >= start_at,
            ShadowDecision.decided_at <= bis,
        )
        .order_by(ShadowDecision.zone_id, ShadowDecision.decided_at)
    ):
        last = previous.get(zone_id)
        if last is not None:
            last_seen, was_heating = last
            if was_heating:
                interval = int((moment - last_seen).total_seconds())
                eimer[zone_id][local_date(last_seen)] += min(interval, maximum_interval)
        previous[zone_id] = (moment, heating)

    start_day = local_date(start_at)
    end_day = local_date(bis)
    days = [
        start_day + timedelta(days=offset) for offset in range((end_day - start_day).days + 1)
    ]
    return {
        zone_id: ZoneStatistics(
            zone_id,
            [DayValue(day, eimer[zone_id].get(day, 0)) for day in days],
        )
        for zone_id in zone_ids
    }


def as_duration(seconds: int) -> str:
    """`4h 05m`, `35m`, `–`. Hours and minutes, because a heating system is thought of
    in these units; seconds would be a precision the sampling cannot actually provide."""
    if seconds <= 0:
        return "–"
    minutes = round(seconds / 60)
    if minutes < 60:
        return f"{minutes}m"
    return f"{minutes // 60}h {minutes % 60:02d}m"
