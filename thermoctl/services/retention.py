import logging
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from thermoctl.db.models.measurement import Measurement
from thermoctl.db.models.operations import Setting
from thermoctl.db.models.state import ShadowDecision

log = logging.getLogger(__name__)


def delete_old_measurements(session: Session, now: datetime, *, batch_size: int = 5000) -> int:
    """Deletes expired measurements in short, database-agnostic blocks."""
    if batch_size <= 0:
        raise ValueError("Blockgroesse muss groesser als null sein")
    settings = session.get(Setting, 1)
    assert settings is not None, "setting-Zeile fehlt — Einrichtung unvollstaendig"
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
    log.info("Alte Messwerte geloescht", extra={"anzahl": count})
    return count


def delete_old_shadow_decisions(
    session: Session, now: datetime, *, batch_size: int = 5000
) -> int:
    """Deletes expired shadow decisions in short, database-agnostic blocks."""
    if batch_size <= 0:
        raise ValueError("Blockgroesse muss groesser als null sein")
    settings = session.get(Setting, 1)
    assert settings is not None, "setting-Zeile fehlt — Einrichtung unvollstaendig"

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
    log.info("Alte Schattenentscheidungen geloescht", extra={"anzahl": count})
    return count
