"""Conversions at the boundary between stored UTC and configured local time."""

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from thermoctl.db.base import utcnow

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


def _duration_in_words(seconds: float, *, prefix: str) -> str:
    for limit, divisor, singular, plural in (
        (60, 1, "Sekunde", "Sekunden"),
        (3600, 60, "Minute", "Minuten"),
        (86400, 3600, "Stunde", "Stunden"),
    ):
        if seconds < limit:
            value = int(seconds // divisor)
            return f"{prefix} {value} {singular if value == 1 else plural}"
    days = int(seconds // 86400)
    return f"{prefix} {days} {'Tag' if days == 1 else 'Tagen'}"


def age_in_words(moment: datetime | None, now: datetime | None = None) -> str:
    """How long ago something was, in words — 'vor 3 Minuten' instead of a timestamp.

    For a heating control this is exactly the question: is this reading fresh, or has
    it been sitting there since yesterday? A raw timestamp with microseconds doesn't
    answer that, it demands mental arithmetic — and when in doubt, you get it wrong.

    `moment` can also lie in the future — a running override's `ends_at`, for
    instance. That is not "a slightly stale reading", it is a different statement
    ("still running, for another 42 minutes" instead of "last seen 3 minutes ago"),
    and used to collapse into the same wrong answer, "gerade eben", no matter how far
    off it still was. The two are told apart here so nobody has to remember, at every
    call site, that this filter only ever meant the past.

    All points in time are naive UTC, as throughout the whole project.
    """
    if moment is None:
        return "noch nie"
    elapsed = ((now or utcnow()) - moment).total_seconds()
    if elapsed < 0:
        return _duration_in_words(-elapsed, prefix="noch")
    return _duration_in_words(elapsed, prefix="vor")
