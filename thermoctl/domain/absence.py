"""Abwesenheit: die eigenen Räume für einen Zeitraum sparsamer regeln.

Ausdrücklich **nicht** der anlagenweite Urlaubsbetrieb (`domain.schedule.
create_vacation`, Tabelle `vacation`). Der senkt jede Zone der Anlage ab -- auch die
Räume anderer Mieter, die davon nichts wissen. Eine Abwesenheit gilt nur für die
Räume, die der Handelnde tatsächlich bedienen darf, und ist deshalb nichts anderes
als eine Übersteuerung je Raum mit gemeinsamem Ende.

Die Tabelle `absence` ist allein die **Klammer** darum: ohne sie ließe sich eine so
entstandene Gruppe später weder als *eine* Abwesenheit anzeigen noch in *einem*
Schritt beenden, und wer sie beenden wollte, träfe womöglich eine Übersteuerung, die
inzwischen aus einem ganz anderen Grund entstanden ist.

**Keine zweite Regelengine.** Die Absenkung wirkt allein über die angelegten
Übersteuerungen und damit über `resolved_setpoint`; an der Rangfolge dort ändert
dieses Modul nichts.
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from thermoctl import audit
from thermoctl.db.base import utcnow
from thermoctl.db.models.absence import Absence
from thermoctl.db.models.lookup import ActorSource
from thermoctl.db.models.override import ZoneOverride
from thermoctl.db.models.zone import Zone
from thermoctl.domain.modes import DomainError
from thermoctl.domain.schedule import create_override


def start_absence(
    session: Session,
    zones: list[Zone],
    temperature_c: Decimal,
    ends_at: datetime,
    *,
    now: datetime | None = None,
    user_id: int | None = None,
    token_id: int | None = None,
    source: str = "web",
) -> Absence:
    """Startet eine Abwesenheit samt genau einer Übersteuerung je Zone.

    Eine schon laufende Abwesenheit desselben Benutzers wird abgelehnt. So bleibt
    die bestehende Abwesenheit unverändert, falls das Anlegen der neuen später
    scheitert. Der verschachtelte Transaktionsblock macht Klammer,
    Zonenübersteuerungen und Audit-Eintrag atomar: Eine halb abgesenkte Wohnung,
    deren unvollständige Klammer niemand zuverlässig auflösen kann, darf nicht
    entstehen.
    """
    if not zones:
        raise DomainError("zones", "Für eine Abwesenheit muss mindestens ein Raum gewählt sein.")
    moment = now if now is not None else utcnow()
    if ends_at <= moment:
        raise DomainError("ends_at", "Das Ende der Abwesenheit muss in der Zukunft liegen.")
    if user_id is not None and running_absence(session, user_id, moment) is not None:
        raise DomainError(
            "absence", "Es läuft bereits eine Abwesenheit. Diese zuerst beenden."
        )

    with session.begin_nested():
        source_id = session.scalar(select(ActorSource.id).where(ActorSource.code == source))
        if source_id is None:
            raise ValueError(f"Unbekannte Quelle {source!r}")
        absence = Absence(
            starts_at=moment, ends_at=ends_at, setback_temperature_c=temperature_c,
            created_by_user_id=user_id, created_by_token_id=token_id, source_id=source_id,
        )
        session.add(absence)
        session.flush()
        for zone in zones:
            override = create_override(
                session, zone, temperature_c, ends_at, now=moment,
                user_id=user_id, token_id=token_id, source=source,
            )
            override.absence_id = absence.id
        audit.record(
            session, source=source, action="create", object_type="absence",
            object_id=str(absence.id), summary="Abwesenheit für eigene Räume angesetzt",
            detail=f"{len(zones)} Zonen bis {ends_at.isoformat()}, {temperature_c} °C",
            user_id=user_id, token_id=token_id,
        )
        session.flush()
    return absence


def end_absence(
    session: Session, absence: Absence, *, now: datetime | None = None
) -> None:
    """Beendet die Abwesenheit und nur ihre noch laufenden Übersteuerungen.

    cancel_override wird bewusst nicht verwendet: Es sucht die jüngste
    Übersteuerung einer Zone, die inzwischen unabhängig von dieser Abwesenheit
    entstanden sein kann. Die Zuordnung über absence_id trifft ausschließlich die
    ursprüngliche Gruppe.
    """
    moment = now if now is not None else utcnow()
    absence.cancelled_at = moment
    # Kein Filter auf `starts_at`: eine Übersteuerung dieser Klammer, die erst
    # später beginnt, muss ebenfalls beendet werden -- sonst senkt sie den Raum ab,
    # nachdem der Benutzer die Abwesenheit längst beendet hat. Der reguläre Weg legt
    # solche Zeilen zwar nicht an, aber `resolved_setpoint` fragt `absence.cancelled_at`
    # nicht ab; die einzige Stelle, an der eine beendete Abwesenheit unwirksam wird,
    # sind ihre Übersteuerungen.
    overrides = session.scalars(
        select(ZoneOverride).where(
            ZoneOverride.absence_id == absence.id,
            ZoneOverride.cancelled_at.is_(None),
            or_(ZoneOverride.ends_at.is_(None), ZoneOverride.ends_at > moment),
        )
    )
    for override in overrides:
        override.cancelled_at = moment
    source = session.scalar(select(ActorSource.code).where(ActorSource.id == absence.source_id))
    if source is None:
        raise ValueError("Die Quelle der Abwesenheit ist nicht mehr vorhanden.")
    audit.record(
        session, source=source, action="update", object_type="absence",
        object_id=str(absence.id),
        summary="Abwesenheit für eigene Räume vorzeitig beendet",
        user_id=absence.created_by_user_id, token_id=absence.created_by_token_id,
    )


def running_absences(session: Session, user_id: int, now: datetime) -> list[Absence]:
    """**Alle** laufenden Abwesenheiten dieses Benutzers, jüngste zuerst.

    Normalerweise ist das höchstens eine -- `start_absence` lehnt eine zweite ab.
    Diese Prüfung ist aber ein Nachsehen vor dem Schreiben ohne Sperre: zwei
    gleichzeitig abgeschickte Formulare können beide "keine laufende" sehen und
    beide eine anlegen. Eine Datenbankbedingung dagegen gibt es nicht portabel --
    "höchstens eine laufende" hängt vom aktuellen Zeitpunkt ab und lässt sich nicht
    als statische UNIQUE-Bedingung formulieren.

    Deshalb wird das Rennen nicht verhindert, sondern seine Folge: "Abwesenheit
    beenden" beendet **jede** laufende. Sonst bliebe die zweite Klammer stehen und
    die Wohnung abgesenkt, obwohl der Benutzer sie beendet hat -- und niemand käme
    an sie heran, weil die Oberfläche nur eine anzeigt.
    """
    return list(
        session.scalars(
            select(Absence)
            .where(
                Absence.created_by_user_id == user_id,
                Absence.cancelled_at.is_(None),
                Absence.starts_at <= now,
                Absence.ends_at > now,
            )
            .order_by(Absence.starts_at.desc(), Absence.id.desc())
        )
    )


def running_absence(session: Session, user_id: int, now: datetime) -> Absence | None:
    """Die jüngste laufende Abwesenheit dieses Benutzers -- die, die die Oberfläche
    anzeigt. Zum Beenden benutzt sie `running_absences`, siehe dort."""
    laufende = running_absences(session, user_id, now)
    return laufende[0] if laufende else None


def absence_zones(session: Session, absence: Absence) -> list[Zone]:
    """Liefert die Zonen einer Abwesenheit in stabiler Anzeigenreihenfolge."""
    return list(
        session.scalars(
            select(Zone)
            .join(ZoneOverride, ZoneOverride.zone_id == Zone.id)
            .where(ZoneOverride.absence_id == absence.id)
            .order_by(Zone.sort_order, Zone.display_name, Zone.id)
        ).unique()
    )
