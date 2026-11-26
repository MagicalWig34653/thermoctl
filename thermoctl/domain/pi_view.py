"""Lesesicht auf den I-Anteil des PI-Reglers. Nur lesend: nichts hier wirkt auf die Regelung.

Ein Zurücksetzen hat keine eigene Spalte. Es steht im Schattenprotokoll: eine Entscheidung mit
`pi_integrator_action == "zurueckgesetzt"`, der Grund in `pi_reset_reason`. Heute entsteht
das nur nach mehr als 24 h ohne Heizanforderung; ältere Zeilen tragen auch andere Gründe.
Die Aufbewahrungsfrist des Protokolls begrenzt, wie weit zurück ein Reset bekannt ist.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from thermoctl.db.models.state import ShadowDecision
from thermoctl.domain.pi_control import INTEGRATOR_RESET
from thermoctl.domain.principal import Principal
from thermoctl.domain.ui_profile import WebUiProfile


@dataclass(frozen=True)
class PiReset:
    at: datetime  # naive UTC wie in der Datenbank
    reason: str | None


def visible_to(principal: Principal) -> bool:
    """Sieht dieser Principal die PI-Zahlen (I-Anteil und woraus er sich errechnen lässt)?

    Anlagentechnik: Wohnungskonten sehen sie nirgends, auch nicht über Betriebsübersicht
    oder MCP (Vorgabe des Projektinhabers 2026-11-17). Das ersetzt keine Rechteprüfung;
    Raum- und Leserechte prüft der Adapter zusätzlich.
    """
    return principal.ui_profile is WebUiProfile.ADMIN


def run_began_before(session: Session, zone_id: int, moment: datetime) -> bool:
    """Ist die letzte Zeile der Zone vor `moment` schon ein Reset?"""
    previous = session.execute(
        select(ShadowDecision.pi_integrator_action)
        .where(ShadowDecision.zone_id == zone_id, ShadowDecision.decided_at < moment)
        .order_by(ShadowDecision.decided_at.desc())
        .limit(1)
    ).first()
    return previous is not None and previous[0] == INTEGRATOR_RESET


def last_reset(session: Session, zone_id: int) -> PiReset | None:
    """Der jüngste Reset des I-Anteils einer Zone (Index
    `ix_shadow_decision_zone_integrator_decided`). Eine ununterbrochene Folge von
    Reset-Zeilen ist ein Reset und trägt wie im Verlauf Zeit und Grund ihrer ersten
    Zeile. Rechte prüft der Adapter."""
    resets = select(ShadowDecision.decided_at, ShadowDecision.pi_reset_reason).where(
        ShadowDecision.zone_id == zone_id,
        ShadowDecision.pi_integrator_action == INTEGRATOR_RESET,
    )
    newest = session.execute(resets.order_by(ShadowDecision.decided_at.desc()).limit(1)).first()
    if newest is None:
        return None
    boundary = session.execute(
        select(ShadowDecision.decided_at)
        .where(
            ShadowDecision.zone_id == zone_id,
            ShadowDecision.decided_at < newest[0],
            or_(
                ShadowDecision.pi_integrator_action.is_(None),
                ShadowDecision.pi_integrator_action != INTEGRATOR_RESET,
            ),
        )
        .order_by(ShadowDecision.decided_at.desc())
        .limit(1)
    ).scalar()
    if boundary is not None:
        resets = resets.where(ShadowDecision.decided_at > boundary)
    first = session.execute(resets.order_by(ShadowDecision.decided_at).limit(1)).one()
    return PiReset(at=first[0], reason=first[1])
