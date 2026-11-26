"""Lesesicht auf den I-Anteil des PI-Reglers. Nur lesend: nichts hier wirkt auf die Regelung.

Ein Zurücksetzen hat keine eigene Spalte. Es steht im Schattenprotokoll: eine Entscheidung mit
`pi_integrator_action == "zurueckgesetzt"`, der Grund in `pi_reset_reason`. Heute entsteht
das nur nach mehr als 24 h ohne Heizanforderung; ältere Zeilen tragen auch andere Gründe.
Die Aufbewahrungsfrist des Protokolls begrenzt, wie weit zurück ein Reset bekannt ist.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.db.models.state import ShadowDecision
from thermoctl.domain.pi_control import INTEGRATOR_RESET


@dataclass(frozen=True)
class PiReset:
    at: datetime  # naive UTC wie in der Datenbank
    reason: str | None


def last_reset(session: Session, zone_id: int) -> PiReset | None:
    """Der jüngste Reset des I-Anteils einer Zone (Index
    `ix_shadow_decision_zone_integrator_decided`). Rechte prüft der Adapter."""
    row = session.execute(
        select(ShadowDecision.decided_at, ShadowDecision.pi_reset_reason)
        .where(
            ShadowDecision.zone_id == zone_id,
            ShadowDecision.pi_integrator_action == INTEGRATOR_RESET,
        )
        .order_by(ShadowDecision.decided_at.desc())
        .limit(1)
    ).first()
    return None if row is None else PiReset(at=row[0], reason=row[1])
