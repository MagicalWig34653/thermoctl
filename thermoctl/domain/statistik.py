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

from thermoctl.db.models.zustand import ShadowDecision

# Wie viel groesser als ein Zyklus ein Abstand sein darf, bevor er als Luecke gilt.
# Drei Zyklen: Ein einzelner ausgefallener Durchlauf ist Betrieb, drei hintereinander
# sind ein Ausfall.
LUECKENFAKTOR = 3


@dataclass(frozen=True)
class Tageswert:
    tag: date
    sekunden: int


@dataclass(frozen=True)
class Zonenstatistik:
    zone_id: int
    tage: list[Tageswert]

    @property
    def sekunden_gesamt(self) -> int:
        return sum(t.sekunden for t in self.tage)


def heizzeiten(
    session: Session,
    zone_ids: list[int],
    von: datetime,
    bis: datetime,
    *,
    zyklus_sekunden: int,
) -> dict[int, Zonenstatistik]:
    """Heizdauer je Zone und Tag im angegebenen Zeitraum, in Sekunden.

    `von` und `bis` sind naive UTC wie alles in diesem Projekt. Ein Abschnitt wird dem
    Tag seines **Beginns** zugeschlagen; bei einer Abtastung im Minutentakt liegt der
    Fehler an der Tagesgrenze unter einer Minute und damit unter der Aufloesung, in der
    die Zahl ueberhaupt angezeigt wird.
    """
    hoechstabstand = max(zyklus_sekunden, 1) * LUECKENFAKTOR
    eimer: dict[int, dict[date, int]] = defaultdict(lambda: defaultdict(int))
    if not zone_ids:
        return {}

    vorheriger: dict[int, tuple[datetime, bool]] = {}
    for zone_id, zeitpunkt, heizt in session.execute(
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
        letzter = vorheriger.get(zone_id)
        if letzter is not None:
            zuletzt, hat_geheizt = letzter
            if hat_geheizt:
                abstand = int((zeitpunkt - zuletzt).total_seconds())
                eimer[zone_id][zuletzt.date()] += min(abstand, hoechstabstand)
        vorheriger[zone_id] = (zeitpunkt, heizt)

    tage = [
        (von + timedelta(days=versatz)).date()
        for versatz in range((bis.date() - von.date()).days + 1)
    ]
    return {
        zone_id: Zonenstatistik(
            zone_id,
            [Tageswert(tag, eimer[zone_id].get(tag, 0)) for tag in tage],
        )
        for zone_id in zone_ids
    }


def als_dauer(sekunden: int) -> str:
    """`4h 05m`, `35m`, `–`. Stunden und Minuten, weil eine Heizung in diesen Groessen
    gedacht wird; Sekunden waeren eine Genauigkeit, die die Abtastung nicht hergibt."""
    if sekunden <= 0:
        return "–"
    minuten = round(sekunden / 60)
    if minuten < 60:
        return f"{minuten}m"
    return f"{minuten // 60}h {minuten % 60:02d}m"
