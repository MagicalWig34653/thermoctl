"""German display text for physical values in rule explanations."""

from decimal import Decimal


def _physical_text(value: Decimal, places: int, unit: str) -> str:
    rounded = f"{value:.{places}f}"
    if Decimal(rounded) == 0:
        rounded = f"{Decimal(0):.{places}f}"
    return f"{rounded.replace('.', ',')} {unit}"


def temperature_text(value: Decimal) -> str:
    return _physical_text(value, 1, "°C")


def difference_text(value: Decimal, *, places: int = 2) -> str:
    return _physical_text(value, places, "K")


def percent_text(value: Decimal) -> str:
    return _physical_text(value, 1, "%")
