"""Was ein Drehregler von aussen bewirkt.

Home Assistant zeigt je Zone einen Thermostat, einen Boost-Knopf und je Modus einen
Drehregler. Dieses Modul beantwortet, was beim Drehen **fachlich** geschieht -- einmal,
fuer alle Adapter. Der MQTT-Empfaenger ruft es genauso auf wie es die Oberflaeche
koennte; die Grenzen und die Audit-Eintraege kommen aus denselben Funktionen.

Zwei Entscheidungen stecken darin:

* **Der Thermostat verstellt den Modus, nicht "jetzt gerade".** Wer in Home Assistant
  auf 21 Grad dreht, meint fast immer "hier soll es 21 Grad warm sein", nicht "die
  naechsten zwei Stunden". Eine Uebersteuerung waere nach dem naechsten Schaltpunkt
  wieder weg, und der Regler spraenge scheinbar von selbst zurueck. Deshalb aendert er
  die Solltemperatur des Modus, der gerade gilt -- dasselbe, was das Thermostat auf der
  Startseite tut.
* **Boost zieht die naechste Schaltung vor.** Nicht "heize auf Anschlag": Was als
  Naechstes ohnehin kaeme, gilt ab sofort, und zwar genau bis zu dem Zeitpunkt, an dem
  es planmaessig gekommen waere. Danach laeuft der Plan weiter, als waere nichts
  gewesen. Ein Boost auf einen festen Wert muesste dagegen raten, wie warm und wie
  lange.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.db.models.operations import Setting
from thermoctl.db.models.override import ZoneOverride
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.zone import SetpointMode, Zone
from thermoctl.domain.modi import sollwerte_aendern
from thermoctl.domain.schedule import (
    aufgeloester_sollwert,
    ende_der_naechsten_schaltung,
    geltender_punkt,
    naechster_punkt,
    temperatur_fuer_modus,
    uebersteuerung_anlegen,
)

log = logging.getLogger(__name__)


class Fernbedienungsfehler(ValueError):
    """Der Wunsch ist verstanden, aber in diesem Zustand nicht ausfuehrbar."""


@dataclass(frozen=True)
class Boost:
    """Was ein Boost bewirkt hat."""

    modus_code: str
    temperatur: Decimal
    bis: datetime


def sollwert_setzen(
    session: Session,
    zone: Zone,
    temperatur: Decimal,
    jetzt: datetime,
    *,
    user_id: int | None = None,
    token_id: int | None = None,
    quelle: str,
) -> Decimal:
    """Setzt die Solltemperatur des gerade geltenden Modus.

    Laeuft eine Uebersteuerung mit fester Temperatur, gibt es keinen Modus, den man
    verstellen koennte -- dann wird die Uebersteuerung selbst auf den neuen Wert
    gesetzt. Sonst spraenge der Regler beim naechsten Zustandsbericht auf den alten
    Wert zurueck und saehe aus, als habe er den Befehl verschluckt.
    """
    geltend = aufgeloester_sollwert(session, zone, jetzt)
    if geltend.modus_id is None:
        uebersteuerung_anlegen(
            session, zone, temperatur, _laufendes_ende(session, zone, jetzt),
            user_id=user_id, token_id=token_id, quelle=quelle,
        )
        return temperatur
    sollwerte_aendern(
        session, zone, {geltend.modus_id: temperatur}, user_id=user_id, quelle=quelle
    )
    return temperatur


def _laufendes_ende(session: Session, zone: Zone, jetzt: datetime) -> datetime | None:
    """Das Ende der laufenden Uebersteuerung -- damit die neue nicht laenger gilt."""
    laufend = session.scalars(
        select(ZoneOverride)
        .where(
            ZoneOverride.zone_id == zone.id,
            ZoneOverride.cancelled_at.is_(None),
            ZoneOverride.starts_at <= jetzt,
        )
        .order_by(ZoneOverride.created_at.desc(), ZoneOverride.id.desc())
    ).first()
    return laufend.ends_at if laufend is not None else None


def boost(
    session: Session,
    zone: Zone,
    jetzt: datetime,
    *,
    user_id: int | None = None,
    token_id: int | None = None,
    quelle: str,
) -> Boost:
    """Zieht die naechste Schaltung vor: ab sofort gilt, was als Naechstes kaeme.

    Umgesetzt als Uebersteuerung, die genau dann endet, wenn der Schaltpunkt
    planmaessig faellt. Danach uebernimmt der Zeitplan von selbst -- es bleibt nichts
    stehen, das jemand wieder aufraeumen muesste.
    """
    einstellungen = session.get(Setting, 1)
    if einstellungen is None:
        raise Fernbedienungsfehler("Die Einrichtung ist unvollständig.")
    zeitzone = ZoneInfo(einstellungen.timezone)
    lokal = jetzt.replace(tzinfo=ZoneInfo("UTC")).astimezone(zeitzone).replace(tzinfo=None)

    punkte = list(
        session.scalars(select(SchedulePoint).where(SchedulePoint.zone_id == zone.id))
    )
    if not punkte:
        raise Fernbedienungsfehler(
            "Diese Zone hat keinen Zeitplan — es gibt keine nächste Schaltung."
        )
    faellt_um = naechster_punkt(punkte, lokal)
    ende = ende_der_naechsten_schaltung(session, zone, jetzt)
    if faellt_um is None or ende is None:  # pragma: no cover - punkte ist nicht leer
        raise Fernbedienungsfehler("Die nächste Schaltung lässt sich nicht bestimmen.")

    # Der Punkt, der ab `faellt_um` gilt -- also der, den wir vorziehen. Eine Minute
    # dahinter gefragt, damit `geltender_punkt` ihn und nicht seinen Vorgaenger liefert.
    kommend = geltender_punkt(punkte, faellt_um)
    if kommend is None:  # pragma: no cover - punkte ist nicht leer
        raise Fernbedienungsfehler("Die nächste Schaltung lässt sich nicht bestimmen.")
    modus = session.get(SetpointMode, kommend.setpoint_mode_id)
    temperatur = temperatur_fuer_modus(session, zone, kommend.setpoint_mode_id)
    if modus is None or temperatur is None:
        raise Fernbedienungsfehler(
            "Für den nächsten Modus ist in dieser Zone keine Temperatur hinterlegt."
        )

    uebersteuerung_anlegen(
        session, zone, temperatur, ende,
        user_id=user_id, token_id=token_id, quelle=quelle,
    )
    log.info(
        "Naechste Schaltung vorgezogen",
        extra={"zone_id": zone.id, "modus": modus.code, "bis": ende.isoformat()},
    )
    return Boost(modus.code, temperatur, ende)
