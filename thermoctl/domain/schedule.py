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
from thermoctl.domain.modi import temperatur_pruefen

MINUTEN_JE_WOCHE = 7 * 24 * 60


@dataclass(frozen=True)
class Sollwert:
    """Das Ergebnis samt Begruendung — Grundsatz 5 aus CLAUDE.md."""

    temperature_c: Decimal
    grund: str
    modus_code: str | None
    # Der Modus, dessen hinterlegte Temperatur gerade gilt -- None, wenn der Sollwert
    # nicht aus einem Modus kommt (feste Uebersteuerung). Die Oberflaeche braucht ihn
    # fuer das Thermostat auf der Startseite: Wer dort verstellt, verstellt *diesen*
    # Modus und nicht "jetzt gerade".
    modus_id: int | None = None


# Bewusst NICHT `frozen=True`: Python haengt einer Ausnahme beim Werfen ihren
# Traceback an, und eine eingefrorene Dataclass verweigert genau das. Der Fehler
# faellt erst auf, wenn die Ausnahme tief genug durchgereicht wird — bei uns durch
# die Abhaengigkeitsaufloesung von FastAPI — und aeussert sich dann als
# `FrozenInstanceError` statt als der Fehler, den man sucht.
@dataclass
class Zeitplanfehler(Exception):
    feld: str
    meldung: str


@dataclass(frozen=True)
class Tagesabschnitt:
    """Ein durchgehender, an einer Tagesgrenze abgeschnittener Zeitplanbalken."""

    wochentag: int
    startminute: int
    endminute: int
    modusname: str
    # Die Kennung des geltenden Modus. Die Oberflaeche loest darueber die
    # Solltemperatur auf -- ohne sie liesse sich der Tagesplan nur benennen, nicht als
    # Waerme darstellen.
    modus_id: int = 0
    # Der Punkt, der diesen Balken beginnt -- None fuer den Rest, der vom Vortag
    # hereinragt. Die Oberflaeche braucht ihn, um einen Balken ziehen zu koennen;
    # ein Balken ohne eigenen Punkt gehoert einem anderen Tag und bleibt fest.
    punkt_id: int | None = None


# Nur fuer den lesbaren Audit-Text. Die Beschriftung der Oberflaeche steht in der
# Ansicht; hier geht es um einen Eintrag, der in Wochen noch verstaendlich sein soll.
_TAGE = {1: "Mo", 2: "Di", 3: "Mi", 4: "Do", 5: "Fr", 6: "Sa", 7: "So"}


def _beschriften(wochentag: int, minute: int) -> str:
    return f"{_TAGE[wochentag]} {minute // 60:02d}:{minute % 60:02d}"


def uhrzeit_in_minuten(uhrzeit: str) -> int:
    """Wandelt lokale `HH:MM`-Formulareingaben in die DB-Darstellung um."""
    teile = uhrzeit.strip().split(":")
    if len(teile) != 2 or not all(teil.isdigit() for teil in teile):
        raise Zeitplanfehler("uhrzeit", "Bitte eine gültige Uhrzeit eingeben.")
    stunde, minute = (int(teil) for teil in teile)
    if stunde > 23 or minute > 59:
        raise Zeitplanfehler("uhrzeit", "Bitte eine gültige Uhrzeit eingeben.")
    return stunde * 60 + minute


def wochenabschnitte(
    punkte: list[SchedulePoint], modusnamen: dict[int, str]
) -> list[Tagesabschnitt]:
    """Zerlegt den Wochenring in Balken, ohne den Ring an Montag 00:00 aufzubrechen."""
    if not punkte:
        return []
    sortiert = sorted(punkte, key=_punktminute)
    abschnitte: list[Tagesabschnitt] = []
    for wochentag in range(1, 8):
        tagesanfang = (wochentag - 1) * 1440
        grenzen = [
            tagesanfang,
            *(
                _punktminute(punkt)
                for punkt in sortiert
                if punkt.weekday == wochentag and punkt.minute_of_day > 0
            ),
            tagesanfang + 1440,
        ]
        for start, ende in zip(grenzen, grenzen[1:], strict=False):
            davor = [punkt for punkt in sortiert if _punktminute(punkt) <= start]
            gilt = max(davor or sortiert, key=_punktminute)
            beginnt_hier = next(
                (punkt for punkt in sortiert if _punktminute(punkt) == start), None
            )
            abschnitte.append(
                Tagesabschnitt(
                    wochentag=wochentag,
                    startminute=start - tagesanfang,
                    endminute=ende - tagesanfang,
                    modusname=modusnamen[gilt.setpoint_mode_id],
                    modus_id=gilt.setpoint_mode_id,
                    punkt_id=beginnt_hier.id if beginnt_hier else None,
                )
            )
    return abschnitte


def _zeitpunkt_belegt(session: Session, zone_id: int, wochentag: int, minute: int) -> bool:
    """Eigene Funktion statt einer eingebetteten Abfrage — wie bei den Zonennamen.

    Damit laesst sich der Wettlauf pruefen, den die Bedingung dahinter abfaengt: Sagt die
    Vorpruefung 'frei', weil eine gleichzeitige Anfrage denselben Zeitpunkt gerade belegt
    hat, muss der `IntegrityError` zu einer verstaendlichen Meldung werden statt zu 500.
    """
    return (
        session.scalar(
            select(SchedulePoint.id).where(
                SchedulePoint.zone_id == zone_id,
                SchedulePoint.weekday == wochentag,
                SchedulePoint.minute_of_day == minute,
            )
        )
        is not None
    )


def zeitplanpunkt_anlegen(
    session: Session,
    zone: Zone,
    *,
    wochentag: int,
    minute: int,
    modus_id: int,
    user_id: int | None,
    token_id: int | None = None,
    quelle: str = "web",
) -> SchedulePoint:
    if not 1 <= wochentag <= 7:
        raise Zeitplanfehler("wochentag", "Bitte einen Wochentag auswählen.")
    if not 0 <= minute <= 1439:
        raise Zeitplanfehler("uhrzeit", "Bitte eine gültige Uhrzeit eingeben.")
    if session.get(SetpointMode, modus_id) is None:
        raise Zeitplanfehler("modus", "Dieser Modus ist nicht bekannt.")
    if _zeitpunkt_belegt(session, zone.id, wochentag, minute):
        raise Zeitplanfehler("uhrzeit", "Zu diesem Zeitpunkt gibt es bereits einen Punkt.")
    punkt = SchedulePoint(
        zone_id=zone.id,
        weekday=wochentag,
        minute_of_day=minute,
        setpoint_mode_id=modus_id,
    )
    try:
        with session.begin_nested():
            session.add(punkt)
            session.flush()
            audit.record(
                session,
                source=quelle,
                action="create",
                object_type="schedule_point",
                object_id=str(punkt.id),
                summary=f"Zeitplanpunkt für Zone '{zone.display_name}' angelegt",
                user_id=user_id,
                token_id=token_id,
            )
            session.flush()
    except IntegrityError as exc:
        raise Zeitplanfehler(
            "uhrzeit", "Zu diesem Zeitpunkt gibt es bereits einen Punkt."
        ) from exc
    return punkt


def zeitplanpunkt_verschieben(
    session: Session,
    zone: Zone,
    punkt: SchedulePoint,
    *,
    wochentag: int,
    minute: int,
    user_id: int | None,
    token_id: int | None = None,
    quelle: str = "web",
) -> SchedulePoint:
    """Setzt einen vorhandenen Punkt auf einen anderen Zeitpunkt.

    Fachlich dasselbe wie Loeschen und neu Anlegen, aber als ein Vorgang: Der Punkt
    behaelt seine Kennung, das Audit-Protokoll zeigt eine Verschiebung statt zweier
    unzusammenhaengender Eintraege, und zwischendurch entsteht keine Luecke im Plan.

    Die Kollisionspruefung ist dieselbe Funktion wie beim Anlegen -- zwei eigene
    Pruefungen waeren zwei, die auseinanderlaufen koennen.
    """
    if not 1 <= wochentag <= 7:
        raise Zeitplanfehler("wochentag", "Bitte einen Wochentag auswählen.")
    if not 0 <= minute <= 1439:
        raise Zeitplanfehler("uhrzeit", "Bitte eine gültige Uhrzeit eingeben.")
    if punkt.weekday == wochentag and punkt.minute_of_day == minute:
        return punkt
    if _zeitpunkt_belegt(session, zone.id, wochentag, minute):
        raise Zeitplanfehler("uhrzeit", "Zu diesem Zeitpunkt gibt es bereits einen Punkt.")

    vorher = _beschriften(punkt.weekday, punkt.minute_of_day)
    nachher = _beschriften(wochentag, minute)
    try:
        with session.begin_nested():
            punkt.weekday = wochentag
            punkt.minute_of_day = minute
            session.flush()
            audit.record(
                session,
                source=quelle,
                action="update",
                object_type="schedule_point",
                object_id=str(punkt.id),
                summary=f"Zeitplanpunkt für Zone '{zone.display_name}' verschoben",
                detail=f"{vorher} → {nachher}",
                user_id=user_id,
                token_id=token_id,
            )
            session.flush()
    except IntegrityError as exc:
        raise Zeitplanfehler(
            "uhrzeit", "Zu diesem Zeitpunkt gibt es bereits einen Punkt."
        ) from exc
    return punkt


def zeitplanpunkt_loeschen(
    session: Session,
    zone: Zone,
    punkt: SchedulePoint,
    *,
    user_id: int | None,
    token_id: int | None = None,
    quelle: str = "web",
) -> None:
    punkt_id = punkt.id
    session.delete(punkt)
    audit.record(
        session,
        source=quelle,
        action="delete",
        object_type="schedule_point",
        object_id=str(punkt_id),
        summary=f"Zeitplanpunkt für Zone '{zone.display_name}' gelöscht",
        user_id=user_id,
        token_id=token_id,
    )


def zeitplan_uebernehmen(
    session: Session,
    ziel: Zone,
    # `vorlage` und nicht `quelle`: Der Name `quelle` steht im ganzen Projekt fuer die
    # Herkunft eines Audit-Eintrags (web, api, mcp). Zwei Bedeutungen in einer Signatur
    # waeren eine Falle fuer den naechsten Aufrufer.
    vorlage: Zone,
    *,
    user_id: int | None,
    token_id: int | None = None,
    quelle: str = "web",
) -> None:
    """Ersetzt den Zielplan atomar durch unabhängige Kopien des Quellplans."""
    quellpunkte = list(
        session.scalars(
            select(SchedulePoint).where(SchedulePoint.zone_id == vorlage.id)
        )
    )
    session.execute(delete(SchedulePoint).where(SchedulePoint.zone_id == ziel.id))
    session.add_all(
        SchedulePoint(
            zone_id=ziel.id,
            weekday=punkt.weekday,
            minute_of_day=punkt.minute_of_day,
            setpoint_mode_id=punkt.setpoint_mode_id,
        )
        for punkt in quellpunkte
    )
    audit.record(
        session,
        source=quelle,
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
    quelle: str = "web",
) -> ZoneOverride:
    """Legt eine konkrete Uebersteuerung an; Web, API und MCP teilen diese Mutation.

    Die Temperaturgrenze wird **hier** geprueft und nicht im Adapter. Bis zum
    Abschlussreview stand sie dreimal verschieden da: die Oberflaeche prueft von Hand ohne
    Nachkommastellen, die REST-Schnittstelle ueber ihr Schema — und der MCP-Server gar
    nicht. Ein Werkzeug haette dort `temperature_c=99` anlegen koennen, und dieser Wert
    fliesst in Teilprojekt 4 ungefiltert in die scharfe Regelentscheidung. Eine
    Eingabegrenze, die von der Wahl des Adapters abhaengt, ist keine.
    """
    temperature_c = temperatur_pruefen(temperature_c)
    # Frueher fest "api", auch wenn die Uebersteuerung aus der Oberflaeche kam: Die
    # Spalte `zone_override.source_id` beantwortet die Frage "worueber wurde das
    # eingestellt", und sie beantwortete sie fuer zwei von drei Adaptern falsch.
    quelle_id = session.scalar(select(ActorSource.id).where(ActorSource.code == quelle))
    if quelle_id is None:
        raise ValueError(f"Unbekannte Quelle {quelle!r}")
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
        .order_by(ZoneOverride.created_at.desc(), ZoneOverride.id.desc())
    ).first()
    if eintrag is not None:
        eintrag.cancelled_at = utcnow()
    return eintrag


def temperatur_fuer_modus(session: Session, zone: Zone, modus_id: int) -> Decimal | None:
    """Die fuer diese Zone hinterlegte Temperatur eines Modus, oder None.

    Oeffentlich, weil das Thermostat der Startseite denselben Wert braucht, um eine
    halbe Stufe darauf zu rechnen -- und weil ein Unterstrich, den drei Module
    ignorieren, kein Schutz ist, sondern nur eine falsche Auskunft.
    """
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
    frost_temp = temperatur_fuer_modus(session, zone, frost_id) or Decimal("16.0")
    frost_code = session.scalar(select(SetpointMode.code).where(SetpointMode.id == frost_id))

    if zone.operating_mode.code == "off":
        return Sollwert(frost_temp, "Betriebsart Aus — Frostschutz", frost_code, frost_id)

    laufend = session.scalars(
        select(ZoneOverride)
        .where(
            ZoneOverride.zone_id == zone.id,
            ZoneOverride.cancelled_at.is_(None),
            ZoneOverride.starts_at <= jetzt_utc,
        )
        # `id` als zweites Merkmal: MariaDB legt DATETIME sekundengenau ab. Zwei
        # Uebersteuerungen derselben Sekunde -- etwa eine, die eine andere ersetzt --
        # haetten sonst denselben Zeitstempel, und welche gilt, entschiede die
        # Datenbank nach Gutduenken.
        .order_by(ZoneOverride.created_at.desc(), ZoneOverride.id.desc())
    ).first()
    if laufend is not None and (laufend.ends_at is None or laufend.ends_at > jetzt_utc):
        if laufend.temperature_c is not None:
            return Sollwert(
                laufend.temperature_c, "Uebersteuerung (feste Temperatur)", None, None
            )
        temp = temperatur_fuer_modus(session, zone, laufend.setpoint_mode_id or 0)
        code = session.scalar(
            select(SetpointMode.code).where(SetpointMode.id == laufend.setpoint_mode_id)
        )
        if temp is not None:
            return Sollwert(
                temp, f"Uebersteuerung auf Modus {code}", code, laufend.setpoint_mode_id
            )

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
        temp = temperatur_fuer_modus(session, zone, gilt.setpoint_mode_id)
        modus = session.get(SetpointMode, gilt.setpoint_mode_id)
        if temp is not None and modus is not None:
            uhrzeit = f"{gilt.minute_of_day // 60:02d}:{gilt.minute_of_day % 60:02d}"
            return Sollwert(
                temp, f"Zeitplan: Modus {modus.name} ab {uhrzeit}", modus.code, modus.id
            )

    return Sollwert(
        frost_temp, "Kein Zeitplan hinterlegt — Frostschutz", frost_code, frost_id
    )


def ende_der_naechsten_schaltung(
    session: Session, zone: Zone, jetzt_utc: datetime | None = None
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
    einstellungen = session.get(Setting, 1)
    zeitzone = ZoneInfo(einstellungen.timezone if einstellungen is not None else "Europe/Berlin")
    lokal = (jetzt_utc or utcnow()).replace(tzinfo=ZoneInfo("UTC")).astimezone(zeitzone)
    punkte = list(
        session.scalars(select(SchedulePoint).where(SchedulePoint.zone_id == zone.id))
    )
    ende = naechster_punkt(punkte, lokal.replace(tzinfo=None))
    if ende is None:
        return None
    return ende.replace(tzinfo=zeitzone).astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
