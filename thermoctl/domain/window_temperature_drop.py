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

**The reference point: not the plain maximum, and not its median either.**
Second cross-review round, two findings against the first fix (a bare maximum,
then a median over every reading before the current one):

* A single noisy sample -- a Zigbee reporting glitch, a brief radio dropout that
  reports a stale-but-plausible high value -- can inflate a plain `max()` enough
  to fake a drop against an otherwise unremarkable room (first finding).
* The median over-corrected: it needs the readings *after* a drop begins to
  already be the **majority** of the historical readings before it reflects them
  at all, which is backwards for a short window -- right after a window opens is
  exactly when only one or two readings could possibly show it yet, and a plain
  median of, say, two historical readings (one old, one at the actual pre-drop
  peak) can already sit far enough below the peak to miss a real drop entirely.
  Measured on the reviewer's own example: `[20.00, 20.10, 18.60]` (threshold
  1.5 K) reads as a 1.50 K drop against the plain maximum (correctly detected)
  but only 1.45 K against the median of its two historical readings (missed) --
  precisely the moment fast detection matters most, right after a window opens
  and the trailing window still holds mostly old readings.

The reference used now: **the historical maximum, unless there are at least
three historical readings to draw a second-highest from.** With fewer than
three readings before the current one there is nothing to safely discard
without also losing the only readings that could show an early drop at all --
the reference is just their maximum, identical to the very first version of
this function. With three or more, the reference is the **second-highest**
of them: a single spurious high reading is simply out-voted by whatever the
next-highest historical reading was, while two or more readings at the real
pre-drop level still carry the comparison, unlike the median's need for an
outright majority. Concretely, over the reviewer's own numbers again: two
historical readings `[20.00, 20.10]` are too few to filter, so the reference
stays their maximum (20.10), the drop against 18.60 is 1.50 K, and it is
detected exactly as the very first version of this function did.

**The residual gap, stated plainly rather than hidden.** With only one or two
readings before the current one -- the shortest supported window, or a sparse
one after a reporting gap -- there are not enough historical readings to
out-vote anything, and a single spurious high reading in that short history can
still fake a drop exactly as the unfixed `max()`-only version always could.
This is an accepted trade-off, not an oversight: demanding at least three
historical readings before discarding one is what keeps a drop beginning right
at the window's edge detectable at all (see the paragraph above); protecting
the two- and one-reading case as well would need discarding data these short
windows do not have to spare. A window with only sparse history is already the
less certain case (`history_covers_duration`), and the operator who turns this
feature on at all has already accepted that a temperature-only guess can be
wrong in ways a real contact never is (see below).

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
one cumulative streak of suspicion may continue before detection is forced to
stand down regardless of what the temperature is currently doing.

**Second cross-review round, why the first version of the cap never actually
fired.** The first cap measured the streak's age off `zone_state.
window_open_since` -- the same clock the hold reads -- reasoning that this clock
"is never reset while a streak keeps re-triggering itself". That reasoning
missed exactly the noisy case its own threshold docstring already calls out:
`services/ingest.py::advance_zone_state` clears `window_open_since` on **every**
cycle the zone is not currently judged open, and a fresh re-check right at the
hold's boundary can flicker to "not detected" for a single noisy cycle even
while the room is, in fact, still cooling -- exactly the kind of single-sample
noise the reference-point fix above exists to tolerate on the *trigger* side,
but the hold's own re-check is a fresh evaluation each time and is not immune to
it. One such flicker used to reset `window_open_since`, and with it the entire
streak measurement, back to zero -- silently, every time, which is why a
reviewer replaying 20 runs of 420 simulated minutes of realistically noisy
cooling never saw the cap fire even once: every run hit at least one such
flicker long before 90 minutes accumulated.

The cap now measures its own, separate notion of "streak", tracked in
`zone_state.window_temp_drop_streak_started_at` and `zone_state.
window_temp_drop_last_detected_at`, deliberately decoupled from
`window_open_since` and from whether the zone is reported open this exact
cycle: a fresh detection extends the existing streak, rather than starting a
new one, as long as the **gap since the streak was last detected** is within
`window_temp_drop_gap_tolerance_minutes` (`temperature_detection_gap_within_
tolerance()` below) -- short enough to bridge exactly the single- or few-cycle
flicker described above, deliberately much shorter than the hold itself so it
never turns into a second hold of its own. `temperature_detection_cap_exceeded()`
then measures the streak's age off `window_temp_drop_streak_started_at` alone.
Once the cap (`window_temp_drop_max_suspected_minutes`, default 90 -- three
holds' worth, long enough that one drawn-out but genuine airing episode does
not trip it, short enough that a feedback loop cannot silently own the zone for
hours) is reached, `services/ingest.py` enters a **silence**: for
`window_temp_drop_silence_minutes` (default 60) afterwards,
`temperature_detection_still_silenced()` below forces the zone closed regardless
of any fresh drop, tracked in its own persisted deadline
(`zone_state.window_temp_drop_silence_until`) rather than reusing either clock
above -- silence has to keep blocking detection even though the zone itself is
not counted as open during it, and the streak bookkeeping is reset to a clean
slate the moment the cap fires, so whatever triggers next after the silence
starts counting from zero. An hour of actual, undisturbed control is long
enough for real heating to show whether the room still cools the way the
trigger threshold assumes, once it is no longer being switched off by the very
mechanism under test; if the zone is still genuinely losing heat through an
actually open window, ordinary hysteresis and (once the room falls far enough)
the frost exception keep answering that on their own during the silence,
exactly as they would for a zone with detection turned off altogether.

**The residual gap in the cap, stated plainly.** Tolerating a *short*
interruption is not the same as tolerating an unbounded one: if the gap between
two detected cycles genuinely exceeds `window_temp_drop_gap_tolerance_minutes`
-- a real recovery, or a run of noisy misses longer than the tolerance bridges
-- the streak is treated as over and a later re-trigger starts counting from
zero again. A room whose false triggers happen to be spaced further apart than
the tolerance, each briefly withholding heat and then genuinely recovering
enough to reset the gap, could in principle keep doing that indefinitely
without ever reaching the cap. This is accepted rather than solved: closing it
completely would mean either a much longer tolerance (which starts merging
genuinely separate episodes together) or tracking total suspected time across
unrelated episodes forever (which no longer describes "one streak" at all).
Sixty minutes of silence and ninety of cap, like the threshold and the hold
above, are reasoned choices rather than measured ones, and every figure here is
adjustable with bounds in `domain.control.WINDOW_TEMP_DROP_LIMITS`.
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

# How long a gap between two detected cycles is still tolerated as "the same
# streak continuing" rather than "a new one starting" -- see the module
# docstring's "why the first version of the cap never actually fired" section.
# Deliberately much shorter than the hold above: this only needs to bridge a
# single noisy re-check flickering false at the hold's own boundary, not to
# grant a second, independent grace period of its own. Ten minutes comfortably
# covers a handful of consecutive missed cycles at any realistic control-cycle
# length while still being unmistakably shorter than the hold. Bounds in
# `domain.control.WINDOW_TEMP_DROP_LIMITS`.
WINDOW_TEMP_DROP_GAP_TOLERANCE_MINUTES = 10

# The hard cap on one cumulative streak of suspicion -- see the module
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
    historical **maximum** -- unless there are at least three readings before
    the current one, in which case it is their **second-highest** value
    instead. See the module docstring's "reference point" and "residual gap"
    sections for the reasoning and the trade-off this accepts: with fewer than
    three historical readings there is nothing to safely discard without also
    losing the only readings that could show an early drop at all, so a single
    outlier can still fool it there exactly as a plain maximum always could.

    Returns `False` -- nothing suspected, not a verdict -- whenever the window
    itself cannot be trusted: fewer than two samples, or `history_covers_duration`
    is `False` (the caller's own answer to "does the stored history reach back far
    enough", exactly `stuck_reading`'s contract). A zone whose sensor was only just
    assigned has not "just seen a steep drop", it has simply never been asked.
    """
    if not history_covers_duration or len(values) < 2:
        return False
    historical = values[:-1]
    current = values[-1]
    reference = (
        max(historical)
        if len(historical) < 3
        else sorted(historical, reverse=True)[1]
    )
    drop = reference - current
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


def temperature_detection_gap_within_tolerance(
    last_detected_at: datetime | None,
    now: datetime,
    *,
    gap_tolerance_minutes: int = WINDOW_TEMP_DROP_GAP_TOLERANCE_MINUTES,
) -> bool:
    """Whether a fresh detection continues the existing cumulative streak.

    See the module docstring's "why the first version of the cap never
    actually fired" section: a streak's cumulative age is tracked off its own
    clock (`zone_state.window_temp_drop_streak_started_at`), extended rather
    than restarted as long as the gap since it was last actually detected
    (`last_detected_at`, `zone_state.window_temp_drop_last_detected_at`) is
    within this tolerance. `None` (never detected yet) is never within
    tolerance -- there is no streak yet to continue.
    """
    if last_detected_at is None:
        return False
    gap_s = (now - last_detected_at).total_seconds()
    return gap_s <= gap_tolerance_minutes * 60


def temperature_detection_cap_exceeded(
    streak_started_at: datetime | None,
    now: datetime,
    *,
    max_suspected_minutes: int = WINDOW_TEMP_DROP_MAX_SUSPECTED_MINUTES,
) -> bool:
    """Whether one cumulative streak of suspicion has run for too long.

    See the module docstring's "feedback loop" section for why the hold above
    is not, on its own, a bound on this. `streak_started_at` is the streak's
    own clock (`zone_state.window_temp_drop_streak_started_at`) -- deliberately
    not `window_open_since`, which the reported window state and the hold both
    read and which gets cleared on every cycle the zone is not currently judged
    open (see `temperature_detection_gap_within_tolerance` for why that clock
    cannot also serve the cap). `None` never exceeds the cap: nothing has been
    running yet for it to measure.
    """
    if streak_started_at is None:
        return False
    age_s = (now - streak_started_at).total_seconds()
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
