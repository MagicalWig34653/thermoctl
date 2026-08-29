import logging
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from thermoctl.db.models.messwert import Measurement
from thermoctl.db.models.operations import Setting

log = logging.getLogger(__name__)


def alte_messwerte_loeschen(session: Session, jetzt: datetime, *, blockgroesse: int = 5000) -> int:
    """Loescht abgelaufene Messwerte in kurzen, datenbankagnostischen Bloecken."""
    if blockgroesse <= 0:
        raise ValueError("Blockgroesse muss groesser als null sein")
    einstellungen = session.get(Setting, 1)
    assert einstellungen is not None, "setting-Zeile fehlt — Einrichtung unvollstaendig"
    if einstellungen.measurement_retention_days == 0:
        return 0

    grenze = jetzt - timedelta(days=einstellungen.measurement_retention_days)
    anzahl = 0
    while True:
        ids = list(
            session.scalars(
                select(Measurement.id)
                .where(Measurement.measured_at < grenze)
                .order_by(Measurement.id)
                .limit(blockgroesse)
            )
        )
        if not ids:
            break
        session.execute(delete(Measurement).where(Measurement.id.in_(ids)))
        anzahl += len(ids)
    log.info("Alte Messwerte geloescht", extra={"anzahl": anzahl})
    return anzahl
