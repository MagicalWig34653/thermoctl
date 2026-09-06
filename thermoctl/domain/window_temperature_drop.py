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

**Not the plain maximum, the median of everything before the current reading.**
Cross-review finding: a single noisy sample -- a Zigbee reporting glitch, a brief
radio dropout that reports a stale-but-plausible high value -- can inflate a plain
`max()` enough to fake a 1.5 K drop against an otherwise unremarkable room. The
median of every reading *before* the most recent one is not moved by one such
outlier the way the maximum is: with three or more readings before the current
one, a single spurious high value is simply outvoted, not counted. It still keeps
the property the maximum had over the window's own oldest reading -- not anchored
to the window's edge, so a drop that only begins partway through is not diluted by
whatever the temperature was doing earlier -- because readings *after* the drop
began keep pulling the median down as they accumulate. The trade-off, accepted
deliberately: a single genuine spike right before a real drop is now weighed the
same as a single spurious one and can be partly outvoted too. With exactly one
reading before the current one (the smallest window this function accepts at all)
there is nothing to outvote anything with, so the median is just that one value --
identical to the old maximum-based behaviour in that minimal case.

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
to cross the threshold again from wherever it currently sits.

**The feedback loop, and why the hold alone does not close it.** Cross-review
finding: "wherever it currently sits" above is not a neutral starting point once
the hold has already lapsed once. The paragraph on the trigger above reasons
about ordinary cooldown -- a room heating on its normal schedule, briefly
unheated between cycles -- and that reasoning does **not** carry over to a room
this detection has itself kept unheated for the length of one or more holds
already: withheld heat over thirty real minutes, in cold weather with middling
insulation, can plausibly cool a room faster than the "a few tenths of a Kelvin"
ordinary-cooldown estimate the threshold is sized against, especially right after
the hold's own window re-primes with a fresh set of falling readings. Nothing
here stops that fresh reading from crossing the threshold again the moment the
hold lapses -- and again after the next one -- for as long as the room keeps
losing heat, which is not bounded by anything discussed so far: the detection can
keep re-triggering itself, indefinitely, off the very cooling it is causing.
Nothing freezes (the frost exception in `domain.control_loop.decide()` still
applies to a temperature-inferred window exactly as to a real one), but the zone
can stay switched off the entire winter with nobody able to see why from the
temperature curve alone, since every individual hold still looks like a
legitimate, bounded response to a fresh drop.

The fix is a second, independent limit on top of the hold: a **cap** on how long
one uninterrupted streak of suspicion -- covering both a held episode and any
run of holds re-triggered by fresh drops right where the previous one lapsed,
with no real gap of ordinary control in between -- may continue before detection
is forced to stand down regardless of what the temperature is currently doing.
`temperature_detection_cap_exceeded()` below measures the streak's age off the
same `zone_state.window_open_since` clock the hold already reads (that clock is
deliberately *not* reset while a streak continues re-triggering itself, which is
exactly what makes it usable here too). Once the cap
(`window_temp_drop_max_suspected_minutes`, default 90 -- three holds' worth, long
enough that one drawn-out but genuine airing episode does not trip it, short
enough that a feedback loop cannot silently own the zone for hours) is reached,
`services/ingest.py` enters a **silence**: for
`window_temp_drop_silence_minutes` (default 60) afterwards,
`temperature_detection_still_silenced()` below forces the zone closed regardless
of any fresh drop, tracked in its own persisted deadline
(`zone_state.window_temp_drop_silence_until`) rather than reusing `window_open_
since` -- the two must not collide, since silence has to keep blocking detection
even though the zone itself is not counted as open during it. An hour of actual,
undisturbed control is long enough for real heating to show whether the room
still cools the way the trigger threshold assumes, once it is no longer being
switched off by the very mechanism under test; if the zone is still genuinely
losing heat through an actually open window, ordinary hysteresis and (once the
room falls far enough) the frost exception keep answering that on their own
during the silence, exactly as they would for a zone with detection turned off
altogether. Sixty minutes, like the threshold and the hold above, is a reasoned
choice rather than a measured one, and both new figures are adjustable with
bounds in `domain.control.WINDOW_TEMP_DROP_LIMITS`, the same as every other
threshold this feature uses.
"""

import statistics
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
# minutes (well below this line) -- **but only under ordinary operation**, where
# the room is unheated for no longer than a normal control cycle allows; it does
# not describe a room this very detection has already kept unheated for a
# sustained stretch (see the module docstring's "feedback loop" section, and
# `WINDOW_TEMP_DROP_MAX_SUSPECTED_MINUTES` below for the limit that follows from
# that). A sensor anywhere near an opened window in cold weather typically loses
# upwards of a full Kelvin in that time, often much faster right next to the
# draught. 1.5 K sits with a clear margin above the former and comfortably inside
# the latter -- but "typically" is not "always", which is exactly why the feature
# defaults to **off** and the switch lives with the operator, not with this
# default. Bounds in `domain.control.WINDOW_TEMP_DROP_LIMITS`.
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

# The hard cap on one uninterrupted streak of suspicion -- see the module
# docstring's "feedback loop" section for why the hold alone does not already
# bound this. Three holds' worth: long enough that a single genuine, drawn-out
# airing (open well past the usual few minutes of "Stoßlüften", but still one
# continuous episode) is not cut short by it; short enough that a feedback loop
# of the detection re-triggering off its own withheld heat cannot own the zone
# for hours unnoticed. Bounds in `domain.control.WINDOW_TEMP_DROP_LIMITS`.
WINDOW_TEMP_DROP_MAX_SUSPECTED_MINUTES = 90

# How long detection stands down once the cap above is reached, before it may
# trigger again at all -- see the module docstring's "feedback loop" section.
# An hour is long enough for ordinary control, running undisturbed, to show
# whether the room actually keeps cooling the way the threshold above assumes
# once it is no longer being switched off by detection itself; short enough that
# a zone genuinely affected by a lingering false trigger is re-evaluated well
# within the same day rather than needing a manual restart. Bounds in
# `domain.control.WINDOW_TEMP_DROP_LIMITS`.
WINDOW_TEMP_DROP_SILENCE_MINUTES = 60


def window_open_suspected(
    values: Sequence[Decimal],
    *,
    history_covers_duration: bool,
    drop_threshold_k: Decimal = WINDOW_TEMP_DROP_THRESHOLD_K,
) -> bool:
    """Whether the trailing temperature history looks like a just-opened window.

    `values` is every measurement within the trailing window, **ordered by time,
    oldest first** -- the caller (`services/ingest.py`) builds exactly this window
    the same way `domain.fault.stuck_reading`'s caller builds its own. The
    reference point compared against the current (most recent) reading is the
    **median of every value before it**, not the window's plain maximum -- see
    the module docstring's "Not the plain maximum" section for why: a single
    noisy sample can otherwise inflate the maximum enough to fake a drop that
    never really happened, and the median resists exactly that while still not
    being anchored to the window's own oldest, possibly unrelated, reading.

    Returns `False` -- nothing suspected, not a verdict -- whenever the window
    itself cannot be trusted: fewer than two samples, or `history_covers_duration`
    is `False` (the caller's own answer to "does the stored history reach back far
    enough", exactly `stuck_reading`'s contract). A zone whose sensor was only just
    assigned has not "just seen a steep drop", it has simply never been asked.
    """
    if not history_covers_duration or len(values) < 2:
        return False
    reference = statistics.median(values[:-1])
    drop = reference - values[-1]
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


def temperature_detection_cap_exceeded(
    window_open_since: datetime | None,
    now: datetime,
    *,
    max_suspected_minutes: int = WINDOW_TEMP_DROP_MAX_SUSPECTED_MINUTES,
) -> bool:
    """Whether one uninterrupted streak of suspicion has run for too long.

    See the module docstring's "feedback loop" section for why the hold above
    is not, on its own, a bound on this. `window_open_since` is the same clock
    `temperature_detection_still_holding` reads -- the streak's age is exactly
    how long the zone has counted as open without a real gap of ordinary
    control, whether that stretch is one long hold or several holds
    re-triggered right where the previous one lapsed. `None` never exceeds the
    cap: nothing has been running yet for it to measure.
    """
    if window_open_since is None:
        return False
    age_s = (now - window_open_since).total_seconds()
    return age_s >= max_suspected_minutes * 60


def temperature_detection_still_silenced(
    silence_until: datetime | None,
    now: datetime,
) -> bool:
    """Whether detection is still standing down after the cap was last reached.

    `silence_until` is `zone_state.window_temp_drop_silence_until`, set the
    moment the cap fires and read back every cycle afterwards until it lapses --
    its own clock, deliberately not `window_open_since`: silence must keep
    blocking detection even though the zone itself no longer counts as open
    during it, which `window_open_since` alone could not express. `None` (never
    silenced, or the silence already lapsed and was cleared) is never silenced.
    """
    return silence_until is not None and now < silence_until


def temperature_drop_history_cutoff(now: datetime, window_minutes: int) -> datetime:
    """The start of the trailing window `window_open_suspected` reasons over.

    Its own tiny function only so `services/ingest.py`'s two queries (the
    coverage check and the value fetch) and any test both compute the identical
    boundary instead of two slightly different subtractions drifting apart.
    """
    return now - timedelta(minutes=window_minutes)
