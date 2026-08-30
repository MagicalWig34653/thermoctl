from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from thermoctl import audit
from thermoctl.db.base import utcnow
from thermoctl.db.models.lookup import ActorSource
from thermoctl.db.models.operations import Setting
from thermoctl.db.models.override import ZoneOverride
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.zone import SetpointMode, Zone, ZoneSetpoint
from thermoctl.domain.modes import check_temperature

MINUTES_PER_WEEK = 7 * 24 * 60


@dataclass(frozen=True)
class Setpoint:
    """Das Ergebnis samt Begruendung — Grundsatz 5 aus CLAUDE.md."""

    temperature_c: Decimal
    grund: str
    mode_code: str | None
    # Der Modus, dessen hinterlegte Temperatur gerade gilt -- None, wenn der Sollwert
    # nicht aus einem Modus kommt (feste Uebersteuerung). Die Oberflaeche braucht ihn
    # fuer das Thermostat auf der Startseite: Wer dort verstellt, verstellt *diesen*
    # Modus und nicht "jetzt gerade".
    mode_id: int | None = None


# Bewusst NICHT `frozen=True`: Python haengt einer Ausnahme beim Werfen ihren
# Traceback an, und eine eingefrorene Dataclass verweigert genau das. Der Fehler
# faellt erst auf, wenn die Ausnahme tief genug durchgereicht wird — bei uns durch
# die Abhaengigkeitsaufloesung von FastAPI — und aeussert sich dann als
# `FrozenInstanceError` statt als der Fehler, den man sucht.
@dataclass
class ScheduleError(Exception):
    feld: str
    notice: str


@dataclass(frozen=True)
class DaySegment:
    """Ein durchgehender, an einer Tagesgrenze abgeschnittener Zeitplanbalken."""

    weekday: int
    start_minute: int
    endminute: int
    mode_name: str
    # Die Kennung des geltenden Modus. Die Oberflaeche loest darueber die
    # Solltemperatur auf -- ohne sie liesse sich der Tagesplan nur benennen, nicht als
    # Waerme darstellen.
    mode_id: int = 0
    # Der Punkt, der diesen Balken beginnt -- None fuer den Rest, der vom Vortag
    # hereinragt. Die Oberflaeche braucht ihn, um einen Balken ziehen zu koennen;
    # ein Balken ohne eigenen Punkt gehoert einem anderen Tag und bleibt fest.
    point_id: int | None = None


# Nur fuer den lesbaren Audit-Text. Die Beschriftung der Oberflaeche steht in der
# Ansicht; hier geht es um einen Eintrag, der in Wochen noch verstaendlich sein soll.
_DAYS = {1: "Mo", 2: "Di", 3: "Mi", 4: "Do", 5: "Fr", 6: "Sa", 7: "So"}


def _label_for(weekday: int, minute: int) -> str:
    return f"{_DAYS[weekday]} {minute // 60:02d}:{minute % 60:02d}"


def time_of_day_in_minutes(time_of_day: str) -> int:
    """Wandelt lokale `HH:MM`-Formulareingaben in die DB-Darstellung um."""
    teile = time_of_day.strip().split(":")
    if len(teile) != 2 or not all(teil.isdigit() for teil in teile):
        raise ScheduleError("time_of_day", "Bitte eine gültige Uhrzeit eingeben.")
    stunde, minute = (int(teil) for teil in teile)
    if stunde > 23 or minute > 59:
        raise ScheduleError("time_of_day", "Bitte eine gültige Uhrzeit eingeben.")
    return stunde * 60 + minute


def week_segments(
    points: list[SchedulePoint], mode_names: dict[int, str]
) -> list[DaySegment]:
    """Zerlegt den Wochenring in Balken, ohne den Ring an Montag 00:00 aufzubrechen."""
    if not points:
        return []
    sortiert = sorted(points, key=_point_minute)
    segments: list[DaySegment] = []
    for weekday in range(1, 8):
        day_start = (weekday - 1) * 1440
        limits = [
            day_start,
            *(
                _point_minute(point)
                for point in sortiert
                if point.weekday == weekday and point.minute_of_day > 0
            ),
            day_start + 1440,
        ]
        for start, ende in zip(limits, limits[1:], strict=False):
            davor = [point for point in sortiert if _point_minute(point) <= start]
            gilt = max(davor or sortiert, key=_point_minute)
            beginnt_hier = next(
                (point for point in sortiert if _point_minute(point) == start), None
            )
            segments.append(
                DaySegment(
                    weekday=weekday,
                    start_minute=start - day_start,
                    endminute=ende - day_start,
                    mode_name=mode_names[gilt.setpoint_mode_id],
                    mode_id=gilt.setpoint_mode_id,
                    point_id=beginnt_hier.id if beginnt_hier else None,
                )
            )
    return segments


def _moment_taken(session: Session, zone_id: int, weekday: int, minute: int) -> bool:
    """Eigene Funktion statt einer eingebetteten Abfrage — wie bei den Zonennamen.

    Damit laesst sich der Wettlauf pruefen, den die Bedingung dahinter abfaengt: Sagt die
    Vorpruefung 'frei', weil eine gleichzeitige Anfrage denselben Zeitpunkt gerade belegt
    hat, muss der `IntegrityError` zu einer verstaendlichen Meldung werden statt zu 500.
    """
    return (
        session.scalar(
            select(SchedulePoint.id).where(
                SchedulePoint.zone_id == zone_id,
                SchedulePoint.weekday == weekday,
                SchedulePoint.minute_of_day == minute,
            )
        )
        is not None
    )


def create_schedule_point(
    session: Session,
    zone: Zone,
    *,
    weekday: int,
    minute: int,
    mode_id: int,
    user_id: int | None,
    token_id: int | None = None,
    source: str = "web",
) -> SchedulePoint:
    if not 1 <= weekday <= 7:
        raise ScheduleError("weekday", "Bitte einen Wochentag auswählen.")
    if not 0 <= minute <= 1439:
        raise ScheduleError("time_of_day", "Bitte eine gültige Uhrzeit eingeben.")
    if session.get(SetpointMode, mode_id) is None:
        raise ScheduleError("mode_id", "Dieser Modus ist nicht bekannt.")
    if _moment_taken(session, zone.id, weekday, minute):
        raise ScheduleError("time_of_day", "Zu diesem Zeitpunkt gibt es bereits einen Punkt.")
    point = SchedulePoint(
        zone_id=zone.id,
        weekday=weekday,
        minute_of_day=minute,
        setpoint_mode_id=mode_id,
    )
    try:
        with session.begin_nested():
            session.add(point)
            session.flush()
            audit.record(
                session,
                source=source,
                action="create",
                object_type="schedule_point",
                object_id=str(point.id),
                summary=f"Zeitplanpunkt für Zone '{zone.display_name}' angelegt",
                user_id=user_id,
                token_id=token_id,
            )
            session.flush()
    except IntegrityError as exc:
        raise ScheduleError(
            "uhrzeit", "Zu diesem Zeitpunkt gibt es bereits einen Punkt."
        ) from exc
    return point


def move_schedule_point(
    session: Session,
    zone: Zone,
    point: SchedulePoint,
    *,
    weekday: int,
    minute: int,
    user_id: int | None,
    token_id: int | None = None,
    source: str = "web",
) -> SchedulePoint:
    """Setzt einen vorhandenen Punkt auf einen anderen Zeitpunkt.

    Fachlich dasselbe wie Loeschen und neu Anlegen, aber als ein Vorgang: Der Punkt
    behaelt seine Kennung, das Audit-Protokoll zeigt eine Verschiebung statt zweier
    unzusammenhaengender Eintraege, und zwischendurch entsteht keine Luecke im Plan.

    Die Kollisionspruefung ist dieselbe Funktion wie beim Anlegen -- zwei eigene
    Pruefungen waeren zwei, die auseinanderlaufen koennen.
    """
    if not 1 <= weekday <= 7:
        raise ScheduleError("weekday", "Bitte einen Wochentag auswählen.")
    if not 0 <= minute <= 1439:
        raise ScheduleError("time_of_day", "Bitte eine gültige Uhrzeit eingeben.")
    if point.weekday == weekday and point.minute_of_day == minute:
        return point
    if _moment_taken(session, zone.id, weekday, minute):
        raise ScheduleError("time_of_day", "Zu diesem Zeitpunkt gibt es bereits einen Punkt.")

    vorher = _label_for(point.weekday, point.minute_of_day)
    nachher = _label_for(weekday, minute)
    try:
        with session.begin_nested():
            point.weekday = weekday
            point.minute_of_day = minute
            session.flush()
            audit.record(
                session,
                source=source,
                action="update",
                object_type="schedule_point",
                object_id=str(point.id),
                summary=f"Zeitplanpunkt für Zone '{zone.display_name}' verschoben",
                detail=f"{vorher} → {nachher}",
                user_id=user_id,
                token_id=token_id,
            )
            session.flush()
    except IntegrityError as exc:
        raise ScheduleError(
            "uhrzeit", "Zu diesem Zeitpunkt gibt es bereits einen Punkt."
        ) from exc
    return point


def delete_schedule_point(
    session: Session,
    zone: Zone,
    point: SchedulePoint,
    *,
    user_id: int | None,
    token_id: int | None = None,
    source: str = "web",
) -> None:
    point_id = point.id
    session.delete(point)
    audit.record(
        session,
        source=source,
        action="delete",
        object_type="schedule_point",
        object_id=str(point_id),
        summary=f"Zeitplanpunkt für Zone '{zone.display_name}' gelöscht",
        user_id=user_id,
        token_id=token_id,
    )


def adopt_schedule(
    session: Session,
    ziel: Zone,
    # `vorlage` und nicht `quelle`: Der Name `quelle` steht im ganzen Projekt fuer die
    # Herkunft eines Audit-Eintrags (web, api, mcp). Zwei Bedeutungen in einer Signatur
    # waeren eine Falle fuer den naechsten Aufrufer.
    vorlage: Zone,
    *,
    user_id: int | None,
    token_id: int | None = None,
    source: str = "web",
) -> None:
    """Ersetzt den Zielplan atomar durch unabhängige Kopien des Quellplans."""
    source_points = list(
        session.scalars(
            select(SchedulePoint).where(SchedulePoint.zone_id == vorlage.id)
        )
    )
    session.execute(delete(SchedulePoint).where(SchedulePoint.zone_id == ziel.id))
    session.add_all(
        SchedulePoint(
            zone_id=ziel.id,
            weekday=point.weekday,
            minute_of_day=point.minute_of_day,
            setpoint_mode_id=point.setpoint_mode_id,
        )
        for point in source_points
    )
    audit.record(
        session,
        source=source,
        action="update",
        object_type="schedule",
        object_id=str(ziel.id),
        summary=(
            f"Zeitplan für Zone '{ziel.display_name}' von "
            f"'{vorlage.display_name}' übernommen"
        ),
        user_id=user_id,
        token_id=token_id,
    )


def _week_minute(moment: datetime) -> int:
    return (moment.isoweekday() - 1) * 24 * 60 + moment.hour * 60 + moment.minute


def _point_minute(point: SchedulePoint) -> int:
    return (point.weekday - 1) * 24 * 60 + point.minute_of_day


def current_point(
    points: list[SchedulePoint], moment: datetime
) -> SchedulePoint | None:
    """Der letzte Punkt vor oder genau auf dem Zeitpunkt.

    Die Woche ist ein Ring: liegt kein Punkt davor, gilt der letzte der Woche. Deshalb
    kann es weder Luecken noch Ueberlappungen geben, solange ueberhaupt ein Punkt da ist.
    """
    if not points:
        return None
    now = _week_minute(moment)
    davor = [p for p in points if _point_minute(p) <= now]
    return max(davor or points, key=_point_minute)


def next_point(points: list[SchedulePoint], moment: datetime) -> datetime | None:
    """Wann der naechste Schaltpunkt faellt — Grundlage fuer 'bis zur naechsten Schaltung'."""
    if not points:
        return None
    now = _week_minute(moment)
    kandidaten = sorted(_point_minute(p) for p in points)
    spaeter = [m for m in kandidaten if m > now]
    ziel = spaeter[0] if spaeter else kandidaten[0] + MINUTES_PER_WEEK
    week_start = (moment - timedelta(days=moment.isoweekday() - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return week_start + timedelta(minutes=ziel)


def create_override(
    session: Session,
    zone: Zone,
    temperature_c: Decimal,
    ends_at: datetime | None,
    *,
    user_id: int | None = None,
    token_id: int | None = None,
    source: str = "web",
) -> ZoneOverride:
    """Legt eine konkrete Uebersteuerung an; Web, API und MCP teilen diese Mutation.

    Die Temperaturgrenze wird **hier** geprueft und nicht im Adapter. Bis zum
    Abschlussreview stand sie dreimal verschieden da: die Oberflaeche prueft von Hand ohne
    Nachkommastellen, die REST-Schnittstelle ueber ihr Schema — und der MCP-Server gar
    nicht. Ein Werkzeug haette dort `temperature_c=99` anlegen koennen, und dieser Wert
    fliesst in Teilprojekt 4 ungefiltert in die scharfe Regelentscheidung. Eine
    Eingabegrenze, die von der Wahl des Adapters abhaengt, ist keine.
    """
    temperature_c = check_temperature(temperature_c)
    # Frueher fest "api", auch wenn die Uebersteuerung aus der Oberflaeche kam: Die
    # Spalte `zone_override.source_id` beantwortet die Frage "worueber wurde das
    # eingestellt", und sie beantwortete sie fuer zwei von drei Adaptern falsch.
    source_id = session.scalar(select(ActorSource.id).where(ActorSource.code == source))
    if source_id is None:
        raise ValueError(f"Unbekannte Quelle {source!r}")
    entry = ZoneOverride(
        zone_id=zone.id,
        temperature_c=temperature_c,
        starts_at=utcnow(),
        ends_at=ends_at,
        created_by_user_id=user_id,
        created_by_token_id=token_id,
        source_id=source_id,
    )
    session.add(entry)
    session.flush()
    return entry


def cancel_override(session: Session, zone: Zone) -> ZoneOverride | None:
    """Beendet die juengste noch aktive Uebersteuerung, ohne Historie zu loeschen."""
    entry = session.scalars(
        select(ZoneOverride)
        .where(ZoneOverride.zone_id == zone.id, ZoneOverride.cancelled_at.is_(None))
        .order_by(ZoneOverride.created_at.desc(), ZoneOverride.id.desc())
    ).first()
    if entry is not None:
        entry.cancelled_at = utcnow()
    return entry


def temperature_for_mode(session: Session, zone: Zone, mode_id: int) -> Decimal | None:
    """Die fuer diese Zone hinterlegte Temperatur eines Modus, oder None.

    Oeffentlich, weil das Thermostat der Startseite denselben Wert braucht, um eine
    halbe Stufe darauf zu rechnen -- und weil ein Unterstrich, den drei Module
    ignorieren, kein Schutz ist, sondern nur eine falsche Auskunft.
    """
    return session.scalar(
        select(ZoneSetpoint.temperature_c).where(
            ZoneSetpoint.zone_id == zone.id, ZoneSetpoint.setpoint_mode_id == mode_id
        )
    )


def resolved_setpoint(session: Session, zone: Zone, now_utc: datetime) -> Setpoint:
    """Welcher Sollwert gerade gilt, und warum.

    Rangfolge: Betriebsart 'off' schlaegt alles, dann eine laufende Uebersteuerung,
    dann der Zeitplan, zuletzt der Frostschutz.
    """
    settings = session.get(Setting, 1)
    assert settings is not None, "setting-Zeile fehlt — Einrichtung unvollstaendig"
    frost_id = settings.frost_protection_mode_id
    frost_temp = temperature_for_mode(session, zone, frost_id) or Decimal("16.0")
    frost_code = session.scalar(select(SetpointMode.code).where(SetpointMode.id == frost_id))

    if zone.operating_mode.code == "off":
        return Setpoint(frost_temp, "Betriebsart Aus — Frostschutz", frost_code, frost_id)

    running = session.scalars(
        select(ZoneOverride)
        .where(
            ZoneOverride.zone_id == zone.id,
            ZoneOverride.cancelled_at.is_(None),
            ZoneOverride.starts_at <= now_utc,
        )
        # `id` als zweites Merkmal: MariaDB legt DATETIME sekundengenau ab. Zwei
        # Uebersteuerungen derselben Sekunde -- etwa eine, die eine andere ersetzt --
        # haetten sonst denselben Zeitstempel, und welche gilt, entschiede die
        # Datenbank nach Gutduenken.
        .order_by(ZoneOverride.created_at.desc(), ZoneOverride.id.desc())
    ).first()
    if running is not None and (running.ends_at is None or running.ends_at > now_utc):
        if running.temperature_c is not None:
            return Setpoint(
                running.temperature_c, "Uebersteuerung (feste Temperatur)", None, None
            )
        temp = temperature_for_mode(session, zone, running.setpoint_mode_id or 0)
        code = session.scalar(
            select(SetpointMode.code).where(SetpointMode.id == running.setpoint_mode_id)
        )
        if temp is not None:
            return Setpoint(
                temp, f"Uebersteuerung auf Modus {code}", code, running.setpoint_mode_id
            )

    # Zeitplaene stehen in lokaler Zeit, damit sich die Nachtabsenkung bei der
    # Zeitumstellung nicht verschiebt.
    lokal = now_utc.replace(tzinfo=ZoneInfo("UTC")).astimezone(
        ZoneInfo(settings.timezone)
    )
    points = list(
        session.scalars(select(SchedulePoint).where(SchedulePoint.zone_id == zone.id))
    )
    gilt = current_point(points, lokal.replace(tzinfo=None))
    if gilt is not None:
        temp = temperature_for_mode(session, zone, gilt.setpoint_mode_id)
        mode = session.get(SetpointMode, gilt.setpoint_mode_id)
        if temp is not None and mode is not None:
            time_of_day = f"{gilt.minute_of_day // 60:02d}:{gilt.minute_of_day % 60:02d}"
            return Setpoint(
                temp, f"Zeitplan: Modus {mode.name} ab {time_of_day}", mode.code, mode.id
            )

    return Setpoint(
        frost_temp, "Kein Zeitplan hinterlegt — Frostschutz", frost_code, frost_id
    )


def end_of_next_switch(
    session: Session, zone: Zone, now_utc: datetime | None = None
) -> datetime | None:
    """Wann die naechste Schaltung faellt, als naive UTC — oder None ohne Zeitplan.

    Liegt hier und nicht im Adapter, weil Oberflaeche und REST-Schnittstelle beide danach
    fragen. Bis zum Abschlussreview von Teilprojekt 3 stand die Rechnung zweimal da, in
    beiden Adaptern getrennt: Eine spaetere Korrektur an der Zeitzonenbehandlung waere in
    einem Pfad nachgezogen und im anderen vergessen worden, und dieselbe Zone haette je
    nach Weg ein anderes Ende bekommen.

    `jetzt_utc` ist ausdruecklich angebbar, weil der Boost beides zugleich braucht: den
    Punkt, der als Naechstes kaeme, *und* seinen Zeitpunkt. Griff diese Funktion dabei
    zur echten Uhr, waehrend der Aufrufer mit einem uebergebenen Zeitpunkt rechnet,
    bezoegen sich beide Haelften auf verschiedene Augenblicke -- die Uebersteuerung
    endete dann irgendwann, nur nicht an ihrem Schaltpunkt.
    """
    settings = session.get(Setting, 1)
    timezone_name = ZoneInfo(settings.timezone if settings is not None else "Europe/Berlin")
    lokal = (now_utc or utcnow()).replace(tzinfo=ZoneInfo("UTC")).astimezone(timezone_name)
    points = list(
        session.scalars(select(SchedulePoint).where(SchedulePoint.zone_id == zone.id))
    )
    ende = next_point(points, lokal.replace(tzinfo=None))
    if ende is None:
        return None
    return ende.replace(tzinfo=timezone_name).astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
