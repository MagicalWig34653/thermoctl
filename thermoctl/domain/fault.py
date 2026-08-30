from datetime import datetime

OK = "ok"
VERALTET = "veraltet"
NO_SOURCE = "keine_quelle"


def sensor_state(
    juengste_messung: datetime | None,
    now: datetime,
    timeout_s: int,
) -> str:
    if juengste_messung is None:
        return NO_SOURCE

    age_s = (now - juengste_messung).total_seconds()
    # Eine leicht vorgehende Sensoruhr soll keinen vermeintlichen Ausfall ausloesen.
    if age_s <= timeout_s:
        return OK
    return VERALTET


def _duration_text(duration_s: int) -> str:
    teile: list[str] = []
    rest = duration_s
    for einheit_s, singular, plural in (
        (86_400, "Tag", "Tage"),
        (3_600, "Stunde", "Stunden"),
        (60, "Minute", "Minuten"),
        (1, "Sekunde", "Sekunden"),
    ):
        count, rest = divmod(rest, einheit_s)
        if count:
            teile.append(f"{count} {singular if count == 1 else plural}")
    return " ".join(teile) if teile else "0 Sekunden"


def state_row(
    state: str,
    juengste_messung: datetime | None,
    now: datetime,
) -> str:
    if state == NO_SOURCE:
        return "Noch nie einen Messwert empfangen — keine Sensorquelle verfuegbar."
    if state not in (OK, VERALTET):
        raise ValueError(f"Unbekannter Sensorzustand: {state}")
    if juengste_messung is None:
        raise ValueError(f"Sensorzustand {state} erfordert einen Messzeitpunkt")

    differenz_s = int((now - juengste_messung).total_seconds())
    richtung = "vor" if differenz_s >= 0 else "in"
    duration = _duration_text(abs(differenz_s))
    assessment = (
        "Sensor ist betriebsbereit."
        if state == OK
        else "Sensor gilt als ausgefallen."
    )
    return f"Letzter Messwert {richtung} {duration} — {assessment}"
