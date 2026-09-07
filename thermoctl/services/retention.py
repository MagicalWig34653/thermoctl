import logging
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from thermoctl.db.models.measurement import Measurement
from thermoctl.db.models.operations import Setting
from thermoctl.db.models.state import ShadowDecision

log = logging.getLogger(__name__)

# Bekannter, dokumentierter Vorbehalt (v0.8.1, siehe docs/STATUS.md): Beide
# Funktionen hier lesen vor jedem Löschblock (`session.get(Setting, 1)`,
# die anschließende SELECT-Auswahl der zu löschenden IDs) und schreiben erst
# danach. Unter SQLite hält eine Transaktion, die zuerst liest, nur eine
# SHARED-Sperre; will sie danach schreiben, muss sie auf eine RESERVED-Sperre
# hochstufen. Läuft zeitgleich eine zweite, unabhängige Verbindung (Webzugriff,
# Regelschleife) durch denselben Ablauf, können beide gleichzeitig auf SHARED
# sitzen und beide beim Hochstufen scheitern -- sofort, nicht nach Wartezeit,
# denn genau diesen Fall retten SQLites Busy-Handler und damit auch der in
# db/engine.py gesetzte busy_timeout nicht. Siehe
# services/cluster.py::try_become_leader für die Herleitung und das
# Referenzmuster der Lösung: dort schreibt die Funktion inzwischen zuerst und
# liest nur noch nachträglich, wo nötig. Hier bewusst nicht nachgezogen -- das
# Fenster ist eng (zwei nebenläufige Aufräumläufe oder ein Aufräumlauf, der
# genau eine laufende Schreiboperation trifft), und die sichtbare Folge ist ein
# einzelner fehlgeschlagener Aufräumlauf mit Logeintrag, kein stiller
# Datenfehler -- der nächste geplante Lauf holt das Übersprungene nach.


def delete_old_measurements(session: Session, now: datetime, *, batch_size: int = 5000) -> int:
    """Deletes expired measurements in short, database-agnostic blocks."""
    if batch_size <= 0:
        raise ValueError("Blockgröße muss größer als null sein")
    settings = session.get(Setting, 1)
    assert settings is not None, "setting-Zeile fehlt — Einrichtung unvollständig"
    if settings.measurement_retention_days == 0:
        return 0

    limit = now - timedelta(days=settings.measurement_retention_days)
    count = 0
    while True:
        ids = list(
            session.scalars(
                select(Measurement.id)
                .where(Measurement.measured_at < limit)
                .order_by(Measurement.id)
                .limit(batch_size)
            )
        )
        if not ids:
            break
        session.execute(delete(Measurement).where(Measurement.id.in_(ids)))
        count += len(ids)
    log.info("Alte Messwerte gelöscht", extra={"anzahl": count})
    return count


def delete_old_shadow_decisions(
    session: Session, now: datetime, *, batch_size: int = 5000
) -> int:
    """Deletes expired shadow decisions in short, database-agnostic blocks."""
    if batch_size <= 0:
        raise ValueError("Blockgröße muss größer als null sein")
    settings = session.get(Setting, 1)
    assert settings is not None, "setting-Zeile fehlt — Einrichtung unvollständig"

    limit = now - timedelta(days=settings.shadow_decision_retention_days)
    count = 0
    while True:
        ids = list(
            session.scalars(
                select(ShadowDecision.id)
                .where(ShadowDecision.decided_at < limit)
                .order_by(ShadowDecision.decided_at, ShadowDecision.id)
                .limit(batch_size)
            )
        )
        if not ids:
            break
        session.execute(delete(ShadowDecision).where(ShadowDecision.id.in_(ids)))
        count += len(ids)
    log.info("Alte Schattenentscheidungen gelöscht", extra={"anzahl": count})
    return count
