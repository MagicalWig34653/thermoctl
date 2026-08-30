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
from thermoctl.domain.modes import update_setpoints
from thermoctl.domain.schedule import (
    create_override,
    current_point,
    end_of_next_switch,
    next_point,
    resolved_setpoint,
    temperature_for_mode,
)

log = logging.getLogger(__name__)


class RemoteControlError(ValueError):
    """Der Wunsch ist verstanden, aber in diesem Zustand nicht ausfuehrbar."""


@dataclass(frozen=True)
class Boost:
    """Was ein Boost bewirkt hat."""

    mode_code: str
    temperature: Decimal
    bis: datetime


def set_setpoint(
    session: Session,
    zone: Zone,
    temperature: Decimal,
    now: datetime,
    *,
    user_id: int | None = None,
    token_id: int | None = None,
    source: str,
) -> Decimal:
    """Setzt die Solltemperatur des gerade geltenden Modus.

    Laeuft eine Uebersteuerung mit fester Temperatur, gibt es keinen Modus, den man
    verstellen koennte -- dann wird die Uebersteuerung selbst auf den neuen Wert
    gesetzt. Sonst spraenge der Regler beim naechsten Zustandsbericht auf den alten
    Wert zurueck und saehe aus, als habe er den Befehl verschluckt.
    """
    geltend = resolved_setpoint(session, zone, now)
    if geltend.mode_id is None:
        create_override(
            session, zone, temperature, _runendes_ende(session, zone, now),
            user_id=user_id, token_id=token_id, source=source,
        )
        return temperature
    update_setpoints(
        session, zone, {geltend.mode_id: temperature}, user_id=user_id, source=source
    )
    return temperature


def _runendes_ende(session: Session, zone: Zone, now: datetime) -> datetime | None:
    """Das Ende der laufenden Uebersteuerung -- damit die neue nicht laenger gilt."""
    running = session.scalars(
        select(ZoneOverride)
        .where(
            ZoneOverride.zone_id == zone.id,
            ZoneOverride.cancelled_at.is_(None),
            ZoneOverride.starts_at <= now,
        )
        .order_by(ZoneOverride.created_at.desc(), ZoneOverride.id.desc())
    ).first()
    return running.ends_at if running is not None else None


def boost(
    session: Session,
    zone: Zone,
    now: datetime,
    *,
    user_id: int | None = None,
    token_id: int | None = None,
    source: str,
) -> Boost:
    """Zieht die naechste Schaltung vor: ab sofort gilt, was als Naechstes kaeme.

    Umgesetzt als Uebersteuerung, die genau dann endet, wenn der Schaltpunkt
    planmaessig faellt. Danach uebernimmt der Zeitplan von selbst -- es bleibt nichts
    stehen, das jemand wieder aufraeumen muesste.
    """
    settings = session.get(Setting, 1)
    if settings is None:
        raise RemoteControlError("Die Einrichtung ist unvollständig.")
    timezone_name = ZoneInfo(settings.timezone)
    lokal = now.replace(tzinfo=ZoneInfo("UTC")).astimezone(timezone_name).replace(tzinfo=None)

    points = list(
        session.scalars(select(SchedulePoint).where(SchedulePoint.zone_id == zone.id))
    )
    if not points:
        raise RemoteControlError(
            "Diese Zone hat keinen Zeitplan — es gibt keine nächste Schaltung."
        )
    faellt_um = next_point(points, lokal)
    ende = end_of_next_switch(session, zone, now)
    if faellt_um is None or ende is None:  # pragma: no cover - punkte ist nicht leer
        raise RemoteControlError("Die nächste Schaltung lässt sich nicht bestimmen.")

    # Der Punkt, der ab `faellt_um` gilt -- also der, den wir vorziehen. Eine Minute
    # dahinter gefragt, damit `geltender_punkt` ihn und nicht seinen Vorgaenger liefert.
    kommend = current_point(points, faellt_um)
    if kommend is None:  # pragma: no cover - punkte ist nicht leer
        raise RemoteControlError("Die nächste Schaltung lässt sich nicht bestimmen.")
    mode = session.get(SetpointMode, kommend.setpoint_mode_id)
    temperature = temperature_for_mode(session, zone, kommend.setpoint_mode_id)
    if mode is None or temperature is None:
        raise RemoteControlError(
            "Für den nächsten Modus ist in dieser Zone keine Temperatur hinterlegt."
        )

    create_override(
        session, zone, temperature, ende,
        user_id=user_id, token_id=token_id, source=source,
    )
    log.info(
        "Naechste Schaltung vorgezogen",
        extra={"zone_id": zone.id, "modus": mode.code, "bis": ende.isoformat()},
    )
    return Boost(mode.code, temperature, ende)
