from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.db.base import utcnow
from thermoctl.db.models.lookup import ActorSource
from thermoctl.db.models.operations import Setting
from thermoctl.db.models.override import ZoneOverride
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.zone import SetpointMode, Zone, ZoneSetpoint

MINUTEN_JE_WOCHE = 7 * 24 * 60


@dataclass(frozen=True)
class Sollwert:
    """Das Ergebnis samt Begruendung — Grundsatz 5 aus CLAUDE.md."""

    temperature_c: Decimal
    grund: str
    modus_code: str | None


def _wochenminute(zeitpunkt: datetime) -> int:
    return (zeitpunkt.isoweekday() - 1) * 24 * 60 + zeitpunkt.hour * 60 + zeitpunkt.minute


def _punktminute(punkt: SchedulePoint) -> int:
    return (punkt.weekday - 1) * 24 * 60 + punkt.minute_of_day


def geltender_punkt(
    punkte: list[SchedulePoint], zeitpunkt: datetime
) -> SchedulePoint | None:
    """Der letzte Punkt vor oder genau auf dem Zeitpunkt.

    Die Woche ist ein Ring: liegt kein Punkt davor, gilt der letzte der Woche. Deshalb
    kann es weder Luecken noch Ueberlappungen geben, solange ueberhaupt ein Punkt da ist.
    """
    if not punkte:
        return None
    jetzt = _wochenminute(zeitpunkt)
    davor = [p for p in punkte if _punktminute(p) <= jetzt]
    return max(davor or punkte, key=_punktminute)


def naechster_punkt(punkte: list[SchedulePoint], zeitpunkt: datetime) -> datetime | None:
    """Wann der naechste Schaltpunkt faellt — Grundlage fuer 'bis zur naechsten Schaltung'."""
    if not punkte:
        return None
    jetzt = _wochenminute(zeitpunkt)
    kandidaten = sorted(_punktminute(p) for p in punkte)
    spaeter = [m for m in kandidaten if m > jetzt]
    ziel = spaeter[0] if spaeter else kandidaten[0] + MINUTEN_JE_WOCHE
    wochenanfang = (zeitpunkt - timedelta(days=zeitpunkt.isoweekday() - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return wochenanfang + timedelta(minutes=ziel)


def uebersteuerung_anlegen(
    session: Session,
    zone: Zone,
    temperature_c: Decimal,
    ends_at: datetime | None,
    *,
    user_id: int | None = None,
    token_id: int | None = None,
) -> ZoneOverride:
    """Legt eine konkrete Uebersteuerung an; Web und API teilen diese Mutation."""
    quelle_id = session.scalar(select(ActorSource.id).where(ActorSource.code == "api"))
    if quelle_id is None:
        raise RuntimeError("Actor-Quelle 'api' fehlt")
    eintrag = ZoneOverride(
        zone_id=zone.id,
        temperature_c=temperature_c,
        starts_at=utcnow(),
        ends_at=ends_at,
        created_by_user_id=user_id,
        created_by_token_id=token_id,
        source_id=quelle_id,
    )
    session.add(eintrag)
    session.flush()
    return eintrag


def uebersteuerung_aufheben(session: Session, zone: Zone) -> ZoneOverride | None:
    """Beendet die juengste noch aktive Uebersteuerung, ohne Historie zu loeschen."""
    eintrag = session.scalars(
        select(ZoneOverride)
        .where(ZoneOverride.zone_id == zone.id, ZoneOverride.cancelled_at.is_(None))
        .order_by(ZoneOverride.created_at.desc())
    ).first()
    if eintrag is not None:
        eintrag.cancelled_at = utcnow()
    return eintrag


def _temperatur_fuer_modus(session: Session, zone: Zone, modus_id: int) -> Decimal | None:
    return session.scalar(
        select(ZoneSetpoint.temperature_c).where(
            ZoneSetpoint.zone_id == zone.id, ZoneSetpoint.setpoint_mode_id == modus_id
        )
    )


def aufgeloester_sollwert(session: Session, zone: Zone, jetzt_utc: datetime) -> Sollwert:
    """Welcher Sollwert gerade gilt, und warum.

    Rangfolge: Betriebsart 'off' schlaegt alles, dann eine laufende Uebersteuerung,
    dann der Zeitplan, zuletzt der Frostschutz.
    """
    einstellungen = session.get(Setting, 1)
    assert einstellungen is not None, "setting-Zeile fehlt — Einrichtung unvollstaendig"
    frost_id = einstellungen.frost_protection_mode_id
    frost_temp = _temperatur_fuer_modus(session, zone, frost_id) or Decimal("16.0")
    frost_code = session.scalar(select(SetpointMode.code).where(SetpointMode.id == frost_id))

    if zone.operating_mode.code == "off":
        return Sollwert(frost_temp, "Betriebsart Aus — Frostschutz", frost_code)

    laufend = session.scalars(
        select(ZoneOverride)
        .where(
            ZoneOverride.zone_id == zone.id,
            ZoneOverride.cancelled_at.is_(None),
            ZoneOverride.starts_at <= jetzt_utc,
        )
        .order_by(ZoneOverride.created_at.desc())
    ).first()
    if laufend is not None and (laufend.ends_at is None or laufend.ends_at > jetzt_utc):
        if laufend.temperature_c is not None:
            return Sollwert(laufend.temperature_c, "Uebersteuerung (feste Temperatur)", None)
        temp = _temperatur_fuer_modus(session, zone, laufend.setpoint_mode_id or 0)
        code = session.scalar(
            select(SetpointMode.code).where(SetpointMode.id == laufend.setpoint_mode_id)
        )
        if temp is not None:
            return Sollwert(temp, f"Uebersteuerung auf Modus {code}", code)

    # Zeitplaene stehen in lokaler Zeit, damit sich die Nachtabsenkung bei der
    # Zeitumstellung nicht verschiebt.
    lokal = jetzt_utc.replace(tzinfo=ZoneInfo("UTC")).astimezone(
        ZoneInfo(einstellungen.timezone)
    )
    punkte = list(
        session.scalars(select(SchedulePoint).where(SchedulePoint.zone_id == zone.id))
    )
    gilt = geltender_punkt(punkte, lokal.replace(tzinfo=None))
    if gilt is not None:
        temp = _temperatur_fuer_modus(session, zone, gilt.setpoint_mode_id)
        modus = session.get(SetpointMode, gilt.setpoint_mode_id)
        if temp is not None and modus is not None:
            uhrzeit = f"{gilt.minute_of_day // 60:02d}:{gilt.minute_of_day % 60:02d}"
            return Sollwert(temp, f"Zeitplan: Modus {modus.name} ab {uhrzeit}", modus.code)

    return Sollwert(frost_temp, "Kein Zeitplan hinterlegt — Frostschutz", frost_code)
