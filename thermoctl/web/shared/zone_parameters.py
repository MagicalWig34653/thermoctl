"""Zonenparameter: Felderlisten und Pruefung der Eingaben -- geteilt von den /ui-api-Endpunkten."""
from decimal import Decimal, InvalidOperation

from thermoctl.web.forms import FormError

FELDER = (
    "hysteresis_k",
    "min_on_seconds",
    "min_off_seconds",
    "sensor_timeout_seconds",
    "temperature_offset_k",
    "window_resume_delay_seconds",
    "solar_setback_max_k",
    "valve_protection_interval_days",
    "valve_protection_duration_minutes",
)


GANZZAHLEN = frozenset(FELDER) - {"hysteresis_k", "temperature_offset_k", "solar_setback_max_k"}


# PI (Beta), specification section 6/7. Handled like `valve_protection_interval_days`
# above -- parsed through `check_parameters` (so an empty field means "unchanged", not
# "inherited": PI is a non-nullable, non-inherited zone field) -- and like
# `valve_protection_enabled` for the switch itself, which is its own checkbox outside
# `check_parameters`.
PI_FELDER = (
    "pi_gain_per_k",
    "pi_integral_time_minutes",
    "pi_min_on_seconds",
    "pi_min_off_seconds",
)


GANZZAHLEN = GANZZAHLEN | (frozenset(PI_FELDER) - {"pi_gain_per_k"})


# Not part of `FELDER`: unlike the fields above, an empty value here does not mean
# "inherited from the global default" -- there is no meaningful plant-wide default for
# how much sun a particular room gets. It is its own, always-present, non-nullable
# zone value (default 0 -- off), so it gets its own bounds check instead of going
# through `check_parameters`.
SOLAR_GAIN_FACTOR_LIMITS = (Decimal("0"), Decimal("1"))


def check_parameters(values: dict[str, str]) -> dict[str, Decimal | int | None]:
    result: dict[str, Decimal | int | None] = {}
    for name, text in values.items():
        if not text:
            result[name] = None
            continue
        try:
            value: Decimal | int = (
                int(text) if name in GANZZAHLEN else Decimal(text.replace(",", "."))
            )
        except (ValueError, InvalidOperation) as exc:
            raise FormError(name, "Bitte eine gültige Zahl eingeben.") from exc
        if name != "temperature_offset_k" and value < 0:
            raise FormError(name, "Dieser Wert darf nicht negativ sein.")
        result[name] = value
    return result


def check_solar_gain_factor(text: str) -> Decimal:
    """Always present, never empty (unlike the fields in `FELDER`) -- 0 is the
    documented off-state, not a missing value."""
    try:
        value = Decimal(text.replace(",", "."))
    except InvalidOperation as exc:
        raise FormError("solar_gain_factor", "Bitte eine gültige Zahl eingeben.") from exc
    lower, upper = SOLAR_GAIN_FACTOR_LIMITS
    if not lower <= value <= upper:
        raise FormError(
            "solar_gain_factor", f"Bitte einen Wert zwischen {lower} und {upper} angeben."
        )
    return value
