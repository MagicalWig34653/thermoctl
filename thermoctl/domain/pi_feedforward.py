"""Outdoor-temperature feed-forward for the PI controller (pure, no database).

Duty share `c * (setpoint - outdoor_smoothed)`, added to the PI output
(`pi_control.pi_arithmetic(feedforward_duty=...)`). The building loses heat in
proportion to the difference to the outside, so the demand that PI would otherwise have
to learn through its integral (hours of delay after a cold night) is known beforehand.

Two functions, both pure -- the caller reads the outdoor samples and the zone's
parameters and passes them in:

- `smoothed_outdoor()`: time-weighted moving average over the last hours, with a
  fall-back to the last valid value and a timeout.
- `feedforward_duty()`: the duty share itself.

**Setpoint.** The caller passes the *plan* setpoint, i.e. the one *before* the solar
setback is subtracted. The solar setback already lowers the setpoint when the sun
heats the room; the feed-forward is blind to the sun (the outdoor sensor does not
measure irradiation through the windows). With the lowered setpoint the same sun
would act twice -- once on the setpoint, once on the feed-forward.

The permitted range of the gain (`FEEDFORWARD_GAIN_MIN`..`FEEDFORWARD_GAIN_MAX`) is
checked by the parameter validation, not by `feedforward_duty()`.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import Decimal

# Without a valid outdoor value in the window, the last valid one is used for this long
# at most; after that the feed-forward is off (0) rather than based on stale data.
FEEDFORWARD_OUTDOOR_TIMEOUT = timedelta(hours=3)

# Allowed range of the gain c (duty per kelvin of setpoint minus outdoor temperature);
# enforced by the parameter validation, exported here for it.
FEEDFORWARD_GAIN_MIN = Decimal("0.005")
FEEDFORWARD_GAIN_MAX = Decimal("0.05")

_MICROSECOND = timedelta(microseconds=1)


def smoothed_outdoor(
    samples: Sequence[tuple[datetime, Decimal | None]],
    now: datetime,
    smoothing_hours: int,
) -> Decimal | None:
    """Moving average of the outdoor temperature over the last `smoothing_hours`.

    `samples` are `(timestamp, value)` pairs in any order; `None` marks an invalid
    reading (`unavailable`, `unknown`) and is skipped: the previous valid value simply
    keeps holding until the next valid one. Samples after `now` are ignored.

    Each value counts for as long as it *held* (until the next valid sample, the last
    one until `now`), not once per sample -- the sensor reports on change, so a
    count-weighted mean would favour the hours in which the weather moved. The newest
    valid sample before the window start counts from the window start on, because it
    was the value in force then.

    Without a valid sample inside the window the last valid value is returned if it is
    at most `FEEDFORWARD_OUTDOOR_TIMEOUT` old, otherwise `None` (no feed-forward).
    """
    if smoothing_hours <= 0:
        raise ValueError("smoothing_hours muss positiv sein.")
    window_start = now - timedelta(hours=smoothing_hours)
    valid = sorted(
        ((at, value) for at, value in samples if value is not None and at <= now),
        key=lambda sample: sample[0],
    )
    inside = [sample for sample in valid if sample[0] >= window_start]
    if not inside:
        if valid and now - valid[-1][0] <= FEEDFORWARD_OUTDOOR_TIMEOUT:
            return valid[-1][1]
        return None

    segments: list[tuple[datetime, datetime, Decimal]] = []
    before = [sample for sample in valid if sample[0] < window_start]
    if before:
        segments.append((window_start, inside[0][0], before[-1][1]))
    for index, (at, value) in enumerate(inside):
        end = inside[index + 1][0] if index + 1 < len(inside) else now
        segments.append((at, end, value))

    weighted = Decimal(0)
    total = 0
    for start, end, value in segments:
        weight = (end - start) // _MICROSECOND
        weighted += value * weight
        total += weight
    if total == 0:
        # Only samples exactly at `now`: nothing has held for any time yet.
        return inside[-1][1]
    return weighted / total


def feedforward_duty(
    *,
    enabled: bool,
    gain: Decimal,
    setpoint_c: Decimal,
    outdoor_smoothed_c: Decimal | None,
) -> Decimal:
    """The feed-forward duty share, `clamp(gain * (setpoint - outdoor), 0, 1)`.

    0 if disabled or if there is no outdoor value (`None`, see `smoothed_outdoor`).
    `setpoint_c` is the plan setpoint *before* the solar setback (see the module
    docstring). Never negative: an outdoor temperature above the setpoint does not
    cool, it only adds nothing.
    """
    if not enabled or outdoor_smoothed_c is None:
        return Decimal(0)
    return max(Decimal(0), min(Decimal(1), gain * (setpoint_c - outdoor_smoothed_c)))
