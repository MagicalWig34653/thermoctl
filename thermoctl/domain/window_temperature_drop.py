"""Inferring a probably-open window from nothing but a zone's own temperature.

Only reached for a zone that has **no** window contact assigned at all -- the
project owner's explicit decision (see `services/ingest.py::_window_open`): a real
contact, once assigned, decides exclusively, even while it currently reads
unknown. This module exists for the remaining case, a zone with no contact and the
per-zone switch (`Zone.window_temp_drop_detection_enabled`, default off) turned on.

**The trigger.** `window_open_suspected()` below is the same shape as
`domain.fault.stuck_reading`, deliberately: a pure function over a short trailing
window of measurements, with a threshold that is a documented, adjustable guess
rather than an invented constant. A fast *drop* -- Kelvin lost within a short,
configurable number of minutes -- is what separates a window being opened from a
room merely cooling once heating stops or a sensor slowly drifting: opening a
window to a cold outside pulls noticeably more heat out of the air right at the
sensor, within minutes, than either of those ever does. `services/ingest.py`
builds the trailing window and decides whether the stored history reaches back far
enough to trust it (`history_covers_duration`, exactly the same contract
`stuck_reading` already established) -- this module never touches the database or
the clock.

**What this deliberately cannot rule out** (the project owner asked this be
written down, not glossed over): a door opened near the sensor, a draught from
another window in the same air volume, or a sensor mounted where normal airflow
already swings by more than the threshold. None of that is distinguishable from an
actually opened window using temperature alone -- there is no second signal here to
cross-check against, unlike a real contact. That is exactly why the default is
**off** per zone (`Zone.window_temp_drop_detection_enabled`) and why the frost-
protection exception in `domain.control_loop.decide()` applies to a temperature-
inferred window exactly as it does to a real one: a false trigger costs comfort
(heat withheld while the guess holds), never a frozen pipe.

**Ending the suspicion.** A pure drop trigger has no way to say "the window is
closed again" on its own -- the room may simply have reached a new, colder,
temporarily stable point while still genuinely open, and `decide()` withholding
heat means there is no active source left that could make the temperature *rise*
and signal a close (a real recovery-based reset would be circular: no heat, so no
recovery, so the suspicion would in practice never lift on its own). The only
sound release left is a **bounded hold**: once triggered, the zone counts as open
for at most `window_temp_drop_hold_minutes` (independent of the trigger window),
then automatically reverts to "not currently suspected" and control resumes
normally -- `temperature_detection_still_holding()` below. This is a deliberate
trade-off, spelled out rather than hidden: a window left open for longer than the
hold quietly stops being flagged and heating may resume against it, exactly the
"Stoßlüften" assumption already used elsewhere in this codebase
(`domain.window_alarm`'s forgotten-window minutes). A *fresh* drop within the hold
window re-extends the state as an ordinary consequence of it still measuring
"currently suspected" every cycle; a fresh drop **after** the hold has lapsed can
retrigger it independently, but only if the room is still cooling steeply enough
to cross the threshold again from wherever it currently sits -- an already-cold,
now-stable room past the hold will not.
"""

from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import Decimal

# How far back the drop is measured. Long enough that a handful of Zigbee reporting
# gaps do not starve the window of samples; short enough that "steep" still means
# something -- a drop spread across an hour is not what opening a window looks like.
# Bounds in `domain.control.WINDOW_TEMP_DROP_LIMITS`.
WINDOW_TEMP_DROP_WINDOW_MINUTES = 15

# The Kelvin drop across that window that counts as "steep enough to be a window,
# not a cooldown". **This number is a reasoned guess, not a measurement of this or
# any real installation** -- the project owner asked that this be said plainly
# rather than dressed up as more certain than it is (task instructions, explicitly).
# The reasoning behind the guess: ordinary post-heating cooldown in a reasonably
# insulated room loses on the order of a few tenths of a Kelvin over fifteen
# minutes (well below this line); a sensor anywhere near an opened window in cold
# weather typically loses upwards of a full Kelvin in that time, often much faster
# right next to the draught. 1.5 K sits with a clear margin above the former and
# comfortably inside the latter -- but "typically" is not "always", which is
# exactly why the feature defaults to **off** and the switch lives with the
# operator, not with this default. Bounds in `domain.control.WINDOW_TEMP_DROP_LIMITS`.
WINDOW_TEMP_DROP_THRESHOLD_K = Decimal("1.5")

# How long a temperature-triggered suspicion is trusted without a fresh drop before
# it automatically lapses -- see the module docstring's "Ending the suspicion"
# section for why a bounded hold is the only sound release available here. Chosen
# to match the same order of magnitude as `setting.window_alarm_open_minutes`'s
# "Stoßlüften" assumption (30 minutes) elsewhere in this codebase, for the same
# reason: long enough that an ordinary, deliberate airing does not lapse and
# silently resume heating against an actually open window mid-airing; short enough
# that a false trigger does not withhold heat for the rest of the day. Bounds in
# `domain.control.WINDOW_TEMP_DROP_LIMITS`.
WINDOW_TEMP_DROP_HOLD_MINUTES = 30


def window_open_suspected(
    values: Sequence[Decimal],
    *,
    history_covers_duration: bool,
    drop_threshold_k: Decimal = WINDOW_TEMP_DROP_THRESHOLD_K,
) -> bool:
    """Whether the trailing temperature history looks like a just-opened window.

    `values` is every measurement within the trailing window, **ordered by time,
    oldest first** -- the caller (`services/ingest.py`) builds exactly this window
    the same way `domain.fault.stuck_reading`'s caller builds its own. The largest
    value anywhere in the window minus the most recent one is the steepest drop
    captured -- not simply the first value minus the last, so a drop that starts
    partway through the window (rather than exactly at its edge) is not diluted by
    whatever the temperature happened to be doing before it began. Mirrors
    `stuck_reading`'s use of a span across the window for the same reason: robust
    to *where* inside the window the interesting part sits.

    Returns `False` -- nothing suspected, not a verdict -- whenever the window
    itself cannot be trusted: fewer than two samples, or `history_covers_duration`
    is `False` (the caller's own answer to "does the stored history reach back far
    enough", exactly `stuck_reading`'s contract). A zone whose sensor was only just
    assigned has not "just seen a steep drop", it has simply never been asked.
    """
    if not history_covers_duration or len(values) < 2:
        return False
    drop = max(values) - values[-1]
    return drop >= drop_threshold_k


def temperature_detection_still_holding(
    window_open_since: datetime | None,
    now: datetime,
    *,
    hold_minutes: int = WINDOW_TEMP_DROP_HOLD_MINUTES,
) -> bool:
    """Whether a previously triggered temperature-based suspicion is still held.

    See the module docstring's "Ending the suspicion" section for why this bounded
    hold, rather than a recovery signal, is the release this module uses.
    `window_open_since` is the zone's own `window_open_since` clock
    (`zone_state.window_open_since`) -- shared with a real contact's episode
    tracking, since both mean exactly the same thing: since when the zone has
    counted as open. `None` (never opened, or the clock was just cleared) never
    holds.
    """
    if window_open_since is None:
        return False
    age_s = (now - window_open_since).total_seconds()
    return age_s < hold_minutes * 60


def temperature_drop_history_cutoff(now: datetime, window_minutes: int) -> datetime:
    """The start of the trailing window `window_open_suspected` reasons over.

    Its own tiny function only so `services/ingest.py`'s two queries (the
    coverage check and the value fetch) and any test both compute the identical
    boundary instead of two slightly different subtractions drifting apart.
    """
    return now - timedelta(minutes=window_minutes)
