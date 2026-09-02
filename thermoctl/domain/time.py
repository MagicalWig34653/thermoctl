"""Conversions at the boundary between stored UTC and configured local time."""

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

UTC = ZoneInfo("UTC")


def local_time(moment_utc: datetime, timezone_name: str | None) -> datetime:
    """Convert the project's naive UTC representation to an aware local moment."""
    return moment_utc.replace(tzinfo=UTC).astimezone(
        ZoneInfo(timezone_name) if timezone_name is not None else UTC
    )


def local_day_start_utc(day: date, timezone_name: str | None) -> datetime:
    """The naive UTC moment at which the given local calendar day begins.

    Statistics and the audit log both used to cut days at UTC midnight -- a local day
    in `Europe/Berlin` then began at 01:00 or 02:00, and the first one or two hours of
    every local day were attributed to the previous one. This is the fix both share:
    build the local midnight first, then convert it to UTC, instead of the other way
    around.

    Handled by `zoneinfo` rather than by hand, which is what makes summer time safe:
    on the day the clocks go forward, local midnight is still an unambiguous, existing
    wall-clock moment (the gap that day is at 02:00, not at 00:00, for every zone this
    project is configured with) and resolves to the correct UTC instant either side of
    the transition.
    """
    local_midnight = datetime.combine(
        day, time.min, tzinfo=ZoneInfo(timezone_name) if timezone_name is not None else UTC
    )
    return local_midnight.astimezone(UTC).replace(tzinfo=None)
