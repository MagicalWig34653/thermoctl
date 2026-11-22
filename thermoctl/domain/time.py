"""Conversions at the boundary between stored UTC and configured local time."""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from thermoctl.db.base import utcnow

UTC = ZoneInfo("UTC")


def local_time(moment_utc: datetime, timezone_name: str | None) -> datetime:
    """Convert the project's naive UTC representation to an aware local moment.

    `fold=0` is set explicitly: a naive datetime can carry `fold=1` (the readings of
    `wall_clock_reached_at` do), and `astimezone` into the very same zone object returns
    its input unchanged -- for UTC that would hand a `fold` of 1 on to the result, where
    `schedule_clock` reads it as "second pass of a repeated hour". The `fold` of the
    result is the local fold, never one inherited from the input.
    """
    return moment_utc.replace(tzinfo=UTC, fold=0).astimezone(
        ZoneInfo(timezone_name) if timezone_name is not None else UTC
    )


def schedule_clock(moment_utc: datetime, timezone_name: str | None) -> datetime:
    """The clock the weekly schedule runs on: naive local time that never runs backwards.

    A schedule point is written in wall-clock terms ("Sunday 02:30"), but the wall clock
    is not monotone: when clocks go back, the repeated hour (Europe/Berlin 02:00-03:00 in
    October) shows the same readings twice. Evaluated against the wall clock, the plan
    would step *back* in the second pass -- the point at 02:30 applies from 00:30 UTC, and
    from 01:00 UTC the wall clock reads 02:00 again, so the predecessor applies once more.
    The plan runs over real time instead: every switch fires exactly once and the setpoint
    never steps back (decision of the project owner, "Plan über echte Zeit führen").

    The schedule clock equals the wall clock everywhere except in the *second* pass of a
    repeated hour (the local `datetime` has `fold == 1` there). In it, the clock stands
    still on the last wall-clock reading of the first pass: the wall clock at the moment
    of the change `T` plus the width of the step back, minus one microsecond (Berlin:
    02:59:59.999999). So points inside the repeated hour fire in the first pass only, and
    a point at 03:00 does not fire in the second pass but when the wall clock reaches
    03:00 -- the wall clock is simply ahead of the schedule clock until then.

    Skipped hours (clocks go forward) need nothing: the wall clock jumps past a point and
    `current_point` lets the point apply from there on. Zones without daylight saving never
    have `fold == 1`; for them (and for `None` = UTC) this is the wall clock unchanged.

    `zoneinfo` does not expose its transition table, so `T` is found by bisection in whole
    seconds -- only in the second pass of a repeated hour, about an hour a year; every other
    moment costs a single `fold` test. `T` lies in `(moment - width, moment]`: in the first
    pass `fold` is 0, from `T` on it is 1 (monotone in that interval).
    """
    local = local_time(moment_utc, timezone_name)
    if not local.fold:
        return local.replace(tzinfo=None)
    first_pass_offset = local.replace(fold=0).utcoffset()
    second_pass_offset = local.utcoffset()
    assert first_pass_offset is not None and second_pass_offset is not None
    width = first_pass_offset - second_pass_offset
    before = (moment_utc - width).replace(microsecond=0)
    after = moment_utc.replace(microsecond=0)
    while after - before > timedelta(seconds=1):
        middle = before + timedelta(seconds=(after - before).total_seconds() // 2)
        if local_time(middle, timezone_name).fold:
            after = middle
        else:
            before = middle
    # `after` is the moment of the change; the wall clock shows its new reading there.
    changed_at = local_time(after, timezone_name).replace(tzinfo=None)
    return changed_at + width - timedelta(microseconds=1)


def wall_clock_reached_at(wall: datetime, timezone_name: str | None) -> tuple[datetime, ...]:
    """The naive UTC moments at which the local wall clock reaches `wall` (naive local).

    The wall clock is not a clean function of real time, and a schedule point is
    expressed in wall-clock terms ("Sunday 02:30"). Converting such a naive local time
    with `replace(tzinfo=...)` silently picks one reading and loses the other cases:

    * **Normal time:** exactly one moment.
    * **Repeated hour** (clocks go back, Europe/Berlin 02:00-03:00 in October): the wall
      clock shows `wall` twice, so two moments come back, earliest first. Taking only
      the first one is the defect this replaces -- in the second pass it lies in the
      past.
    * **Skipped hour** (clocks go forward, 02:00-03:00 in March): the wall clock never
      shows `wall`. Whoever asks "when does the schedule point apply" is answered by
      the moment the clock jumps past it, because `current_point` lets a point apply as
      soon as the wall clock is at or beyond it -- so the single moment returned is the
      transition itself (first instant at which the wall clock is `>= wall`). Neither
      `fold` reading is that moment: `fold=0` gives the *later* instant (still read with
      the old offset, 02:30 CET = 01:30 UTC), `fold=1` the earlier one (00:30 UTC), and
      the transition lies strictly between them, found by bisection here because
      `zoneinfo` does not expose its transition table.

    A reading is genuine only if converting it back yields `wall` again; that round trip
    is what separates the three cases without any knowledge of the zone's rules.
    """
    zone = ZoneInfo(timezone_name) if timezone_name is not None else UTC
    readings = [
        wall.replace(tzinfo=zone, fold=fold).astimezone(UTC).replace(tzinfo=None, fold=0)
        for fold in (1, 0)
    ]
    genuine = {
        moment
        for moment in readings
        if local_time(moment, timezone_name).replace(tzinfo=None) == wall
    }
    if genuine:
        return tuple(sorted(genuine))
    # Skipped time: `before` is still on the old side of the transition (its wall clock
    # lies before `wall`), `after` already on the new side (at or beyond `wall`).
    before, after = readings
    while after - before > timedelta(seconds=1):
        # Whole seconds only: halving an odd number of seconds must not leave a fraction,
        # or the result would not be the exact second of the transition.
        middle = before + timedelta(seconds=(after - before).total_seconds() // 2)
        if local_time(middle, timezone_name).replace(tzinfo=None) >= wall:
            after = middle
        else:
            before = middle
    return (after,)


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
