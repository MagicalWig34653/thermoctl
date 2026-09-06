from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

OK = "ok"
VERALTET = "veraltet"
NO_SOURCE = "keine_quelle"

# Below one typical sensor's resolution step (0.1 K is common among the Zigbee
# temperature sensors this plant uses), but well above what floating-point rounding
# of a single stored value could ever contribute. A span at or under this threshold
# means "every sample in the window landed on the same raw value" -- not "the value
# barely moved". A sensor genuinely oscillating between two adjacent resolution
# steps (22.7 / 22.8 °C) has a span of 0.1 °C, comfortably above this line, and is
# therefore never mistaken for a stuck reading -- see `stuck_reading()` below.
STUCK_READING_SPAN_C = Decimal("0.05")


def stuck_reading(
    values: Sequence[Decimal],
    *,
    history_covers_duration: bool,
    span_threshold_c: Decimal = STUCK_READING_SPAN_C,
) -> bool:
    """Whether a temperature reading counts as suspiciously unmoving.

    Deliberately **not** bit-for-bit equality across every sample: a sensor with a
    coarse resolution legitimately toggles between two adjacent raw values while the
    true temperature holds almost exactly still, and treating that as "stuck" would
    flag the physics of digitisation, not a fault. Comparing the *span* -- the
    largest value minus the smallest, across every sample in the window -- against a
    threshold below one resolution step catches a value that truly never moves
    without also catching that legitimate oscillation.

    Returns `False` -- nothing to report, not a verdict -- whenever the window
    itself cannot be trusted: fewer than two samples (nothing to compare), or
    `history_covers_duration` is `False`. The latter is the caller's own answer to
    "does the stored history reach back far enough to cover the configured
    duration" -- a sensor that was assigned to this zone twenty minutes ago has not
    "held still for twelve hours", it has simply never been asked that question.
    """
    if not history_covers_duration or len(values) < 2:
        return False
    return (max(values) - min(values)) <= span_threshold_c


def sensor_state(
    latest_reading: datetime | None,
    now: datetime,
    timeout_s: int,
) -> str:
    if latest_reading is None:
        return NO_SOURCE

    age_s = (now - latest_reading).total_seconds()
    # A sensor clock running slightly fast must not trigger a false failure.
    if age_s <= timeout_s:
        return OK
    return VERALTET


def _duration_text(duration_s: int) -> str:
    parts: list[str] = []
    rest = duration_s
    for unit_s, singular, plural in (
        (86_400, "Tag", "Tage"),
        (3_600, "Stunde", "Stunden"),
        (60, "Minute", "Minuten"),
        (1, "Sekunde", "Sekunden"),
    ):
        count, rest = divmod(rest, unit_s)
        if count:
            parts.append(f"{count} {singular if count == 1 else plural}")
    return " ".join(parts) if parts else "0 Sekunden"


def state_row(
    state: str,
    latest_reading: datetime | None,
    now: datetime,
) -> str:
    if state == NO_SOURCE:
        return "Noch nie einen Messwert empfangen — keine Sensorquelle verfuegbar."
    if state not in (OK, VERALTET):
        raise ValueError(f"Unbekannter Sensorzustand: {state}")
    if latest_reading is None:
        raise ValueError(f"Sensorzustand {state} erfordert einen Messzeitpunkt")

    difference_s = int((now - latest_reading).total_seconds())
    direction = "vor" if difference_s >= 0 else "in"
    duration = _duration_text(abs(difference_s))
    assessment = (
        "Sensor ist betriebsbereit."
        if state == OK
        else "Sensor gilt als ausgefallen."
    )
    return f"Letzter Messwert {direction} {duration} — {assessment}"
