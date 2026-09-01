"""Conversions at the boundary between stored UTC and configured local time."""

from datetime import datetime
from zoneinfo import ZoneInfo


def local_time(moment_utc: datetime, timezone_name: str | None) -> datetime:
    """Convert the project's naive UTC representation to an aware local moment."""
    utc = ZoneInfo("UTC")
    return moment_utc.replace(tzinfo=utc).astimezone(
        ZoneInfo(timezone_name) if timezone_name is not None else utc
    )
