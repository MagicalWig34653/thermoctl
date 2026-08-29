from datetime import datetime

OK = "ok"
VERALTET = "veraltet"
KEINE_QUELLE = "keine_quelle"


def sensorzustand(
    juengste_messung: datetime | None,
    jetzt: datetime,
    timeout_s: int,
) -> str:
    if juengste_messung is None:
        return KEINE_QUELLE

    alter_s = (jetzt - juengste_messung).total_seconds()
    # Eine leicht vorgehende Sensoruhr soll keinen vermeintlichen Ausfall ausloesen.
    if alter_s <= timeout_s:
        return OK
    return VERALTET


def _dauertext(dauer_s: int) -> str:
    teile: list[str] = []
    rest = dauer_s
    for einheit_s, singular, plural in (
        (86_400, "Tag", "Tage"),
        (3_600, "Stunde", "Stunden"),
        (60, "Minute", "Minuten"),
        (1, "Sekunde", "Sekunden"),
    ):
        anzahl, rest = divmod(rest, einheit_s)
        if anzahl:
            teile.append(f"{anzahl} {singular if anzahl == 1 else plural}")
    return " ".join(teile) if teile else "0 Sekunden"


def zustandssatz(
    zustand: str,
    juengste_messung: datetime | None,
    jetzt: datetime,
) -> str:
    if zustand == KEINE_QUELLE:
        return "Noch nie einen Messwert empfangen — keine Sensorquelle verfuegbar."
    if zustand not in (OK, VERALTET):
        raise ValueError(f"Unbekannter Sensorzustand: {zustand}")
    if juengste_messung is None:
        raise ValueError(f"Sensorzustand {zustand} erfordert einen Messzeitpunkt")

    differenz_s = int((jetzt - juengste_messung).total_seconds())
    richtung = "vor" if differenz_s >= 0 else "in"
    dauer = _dauertext(abs(differenz_s))
    bewertung = (
        "Sensor ist betriebsbereit."
        if zustand == OK
        else "Sensor gilt als ausgefallen."
    )
    return f"Letzter Messwert {richtung} {dauer} — {bewertung}"
