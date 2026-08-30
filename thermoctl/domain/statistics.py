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

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.db.models.state import ShadowDecision

# How much larger than a cycle a gap is allowed to be before it counts as a gap.
# Three cycles: a single missed pass is normal operation, three in a row is an outage.
GAP_FACTOR = 3


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


def heating_periods(
    session: Session,
    zone_ids: list[int],
    start_at: datetime,
    bis: datetime,
    *,
    cycle_seconds: int,
) -> dict[int, ZoneStatistics]:
    """Heating duration per zone and day in the given period, in seconds.

    `von` and `bis` are naive UTC like everything in this project. A segment is
    attributed to the day of its **start**; with sampling on a minute cadence, the
    error at a day boundary stays under a minute and thus below the resolution at
    which the number is even displayed.
    """
    maximum_interval = max(cycle_seconds, 1) * GAP_FACTOR
    eimer: dict[int, dict[date, int]] = defaultdict(lambda: defaultdict(int))
    if not zone_ids:
        return {}

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
                eimer[zone_id][last_seen.date()] += min(interval, maximum_interval)
        previous[zone_id] = (moment, heating)

    days = [
        (start_at + timedelta(days=offset)).date()
        for offset in range((bis.date() - start_at.date()).days + 1)
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
