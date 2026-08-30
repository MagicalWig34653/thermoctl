from datetime import datetime

OK = "ok"
VERALTET = "veraltet"
NO_SOURCE = "keine_quelle"


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
