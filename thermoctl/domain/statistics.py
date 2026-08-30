"""Wann und wie lange geheizt wurde.

Die Quelle ist das Schattenprotokoll: Der Regelzyklus schreibt fuer jede Zone in jedem
Durchlauf eine Zeile mit `would_heat`. Das ist eine dichte Abtastung -- alle 60 Sekunden
ein Messpunkt -- und daraus laesst sich die Dauer wirklich ausrechnen, statt sie zu
schaetzen.

**Gerechnet wird ueber die Abstaende, nicht ueber die Anzahl der Zeilen.** Ein Zaehler
"so viele Zeilen mal Zyklusdauer" waere einfacher und in zwei Faellen falsch: wenn der
Zyklus zwischendurch anders eingestellt war, und wenn der Dienst stand. Jeder Abstand
zwischen zwei aufeinanderfolgenden Messpunkten zaehlt so lange, wie er wirklich war.

**Luecken werden gekappt.** Stand der Dienst eine Nacht lang still, liegen zwischen zwei
Messpunkten acht Stunden. Sie als Heizzeit zu zaehlen, waere frei erfunden -- die Anlage
hat in dieser Zeit nichts gemeldet, und was sie tat, weiss niemand. Ein Abstand, der
deutlich groesser ist als der Zyklus, zaehlt deshalb nur bis zur Kappungsgrenze.

Im Trockenlauf ist das eine Aussage darueber, was thermoctl geheizt *haette*. Nach dem
Scharfschalten ueber dasselbe, was es getan hat -- die Zeilen entstehen an derselben
Stelle.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.db.models.state import ShadowDecision

# Wie viel groesser als ein Zyklus ein Abstand sein darf, bevor er als Luecke gilt.
# Drei Zyklen: Ein einzelner ausgefallener Durchlauf ist Betrieb, drei hintereinander
# sind ein Ausfall.
GAP_FACTOR = 3


@dataclass(frozen=True)
class DayValue:
    day: date
    seconds: int


@dataclass(frozen=True)
class ZoneStatistics:
    zone_id: int
    days: list[DayValue]

    @property
    def seconds_gesamt(self) -> int:
        return sum(t.seconds for t in self.days)


def heizzeiten(
    session: Session,
    zone_ids: list[int],
    von: datetime,
    bis: datetime,
    *,
    cycle_seconds: int,
) -> dict[int, ZoneStatistics]:
    """Heizdauer je Zone und Tag im angegebenen Zeitraum, in Sekunden.

    `von` und `bis` sind naive UTC wie alles in diesem Projekt. Ein Abschnitt wird dem
    Tag seines **Beginns** zugeschlagen; bei einer Abtastung im Minutentakt liegt der
    Fehler an der Tagesgrenze unter einer Minute und damit unter der Aufloesung, in der
    die Zahl ueberhaupt angezeigt wird.
    """
    maximum_interval = max(cycle_seconds, 1) * GAP_FACTOR
    eimer: dict[int, dict[date, int]] = defaultdict(lambda: defaultdict(int))
    if not zone_ids:
        return {}

    vorheriger: dict[int, tuple[datetime, bool]] = {}
    for zone_id, moment, heizt in session.execute(
        select(
            ShadowDecision.zone_id, ShadowDecision.decided_at, ShadowDecision.would_heat
        )
        .where(
            ShadowDecision.zone_id.in_(zone_ids),
            ShadowDecision.decided_at >= von,
            ShadowDecision.decided_at <= bis,
        )
        .order_by(ShadowDecision.zone_id, ShadowDecision.decided_at)
    ):
        last = vorheriger.get(zone_id)
        if last is not None:
            last_seen, hat_geheizt = last
            if hat_geheizt:
                interval = int((moment - last_seen).total_seconds())
                eimer[zone_id][last_seen.date()] += min(interval, maximum_interval)
        vorheriger[zone_id] = (moment, heizt)

    days = [
        (von + timedelta(days=versatz)).date()
        for versatz in range((bis.date() - von.date()).days + 1)
    ]
    return {
        zone_id: ZoneStatistics(
            zone_id,
            [DayValue(day, eimer[zone_id].get(day, 0)) for day in days],
        )
        for zone_id in zone_ids
    }


def as_duration(seconds: int) -> str:
    """`4h 05m`, `35m`, `–`. Stunden und Minuten, weil eine Heizung in diesen Groessen
    gedacht wird; Sekunden waeren eine Genauigkeit, die die Abtastung nicht hergibt."""
    if seconds <= 0:
        return "–"
    minutes = round(seconds / 60)
    if minutes < 60:
        return f"{minutes}m"
    return f"{minutes // 60}h {minutes % 60:02d}m"
