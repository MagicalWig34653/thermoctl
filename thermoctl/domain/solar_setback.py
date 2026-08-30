"""Solar setback: correcting the setpoint downward when sunshine is expected soon.

Pure domain logic (CLAUDE.md principle 6, and the same discipline as
`control_loop.decide()`): everything here is a plain function over values already
handed in. No HTTP call, no database read, and no clock happen in this module -- the
forecast is fetched once by `thermoctl.integrations.forecast` and handed in as a
value, exactly like the resolved setpoint and the control parameters already are for
`control_loop.decide()`.

This is deliberately **not** a new rule inside `control_loop.decide()`. It is a
correction applied to the setpoint *before* the situation is built, so the existing
precedence (frost protection, window-open, minimum switch duration, hysteresis) is
completely unaffected -- a setback that already respects the frost-protection floor
arrives at `decide()` looking like any other setpoint.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal


@dataclass(frozen=True)
class HourlyForecast:
    """One hourly point of an already-fetched forecast, in naive UTC.

    Both fields are optional because Open-Meteo may omit either one; a point with
    neither is still useful as "no data for this hour", not a reason to discard the
    whole forecast.
    """

    time: datetime
    cloud_cover_percent: Decimal | None
    shortwave_radiation_w_m2: Decimal | None


# A commonly used rule of thumb for "the sun meaningfully contributes to heating a
# room" -- low enough that an overcast-but-bright transitional-season morning still
# counts (that is exactly the case this feature targets), high enough that it is not
# satisfied by every daylight hour regardless of weather. This is a physical judgment
# about solar irradiance, not a site-specific value -- unlike a device id, room name,
# or broker address (CLAUDE.md principle 1), a threshold like this stays meaningful
# across installations and does not belong in configuration.
SUNSHINE_THRESHOLD_W_M2 = Decimal(120)


def sun_expected(
    forecast: list[HourlyForecast], now: datetime, lookahead_hours: int
) -> bool:
    """Whether the forecast promises meaningful sunshine within the lookahead window.

    Looks only at `shortwave_radiation_w_m2`: it is the direct physical quantity, and
    Open-Meteo does not guarantee `cloud_cover` is any more reliably present than it
    is -- both are "may be missing", and radiation is the more informative of the two
    when it is there. An hour with no radiation value cannot promise sunshine, so it
    counts as "no" rather than as unknown-therefore-yes; the safe direction here is to
    under- rather than over-predict sun.
    """
    horizon = now + timedelta(hours=lookahead_hours)
    return any(
        point.shortwave_radiation_w_m2 is not None
        and point.shortwave_radiation_w_m2 >= SUNSHINE_THRESHOLD_W_M2
        and now <= point.time < horizon
        for point in forecast
    )


@dataclass(frozen=True)
class SetbackResult:
    """The setback actually applied -- both the amount and the resulting setpoint,
    so the caller never has to redo the subtraction (and risk redoing it slightly
    differently)."""

    reduction_k: Decimal
    setpoint_c: Decimal


def apply(
    setpoint_c: Decimal,
    frost_c: Decimal,
    *,
    factor: Decimal,
    max_reduction_k: Decimal,
    expects_sun: bool,
) -> SetbackResult | None:
    """The setpoint correction for one zone, or `None` if none applies.

    `None` covers every case where no setback should happen: no sun expected, a zone
    factor of zero (a room that does not profit from sun at all -- the documented
    default), or a configured maximum of zero. This is also what makes an unreachable
    forecast source safe: the caller simply never gets here without a forecast to
    begin with (see `thermoctl.integrations.forecast`), so "no forecast" and "sun not
    expected" both resolve to the same "no setback" outcome without a separate branch.

    Frost protection is an absolute floor here, never a rule this function might
    override: the reduction is capped at exactly the room left above `frost_c`, and
    dropped entirely once that room is zero -- a zone already at its frost-protection
    setpoint (operating mode 'off', or a sensor failure) never gets pushed below it.
    """
    if not expects_sun or factor <= 0 or max_reduction_k <= 0:
        return None
    room_above_frost = setpoint_c - frost_c
    if room_above_frost <= 0:
        return None
    # Both factors of this product are already confirmed positive above, and so is
    # `room_above_frost` -- `reduction` is therefore always positive here, never a
    # zero-sized "correction" that would need its own no-op case.
    reduction = min(factor * max_reduction_k, room_above_frost)
    return SetbackResult(reduction_k=reduction, setpoint_c=setpoint_c - reduction)
