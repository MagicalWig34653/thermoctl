"""Manueller Eingriff in den I-Anteil des PI-Reglers: zurücksetzen und setzen (Karte A391AABC).

Gemeinsame Domäne von Weboberfläche, REST und MCP (Grundsatz 6): Rechteprüfung, Wertebereich,
Vormerken, Audit. **Hier wirkt nichts auf die Regelung.** Der Eingriff fügt nur eine
Anforderung in `pi_manual_request` ein (die Schnittstellen ändern und löschen dort nie);
übernommen wird sie vom nächsten Regelzyklus (`services/shadow_run._pi_outcome`), der Wert und
Grund (`manuell_reset` / `manuell_gesetzt`) ins Schaltprotokoll schreibt. Tore,
Hysterese-Haltephase und sicherer Anlauf gelten dabei unverändert -- der Eingriff ändert nur
den gehaltenen Wert.

**Transaktion:** `request_reset`/`request_set` schreiben die bisherige (lesende) Transaktion der
Anfrage fest und beginnen mit dem Schreiben eine neue (siehe `_request`); bei belegter Datenbank
antworten sie mit `busy` (HTTP 409), und nichts ist geändert.

Recht: `zone.manage` dieser Zone (wie jede dauerhafte Änderung an der Regelung) **und** die
Anlagensicht (`pi_view.visible_to`): Wohnungskonten sehen und ändern den I-Anteil nie, auch nicht
mit `zone.manage`. Lesen: `zone.read` und Anlagensicht.
"""

import logging
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from thermoctl import audit
from thermoctl.db.base import utcnow
from thermoctl.db.models.state import PiManualRequest, ZoneState
from thermoctl.db.models.zone import Zone
from thermoctl.domain import pi_view
from thermoctl.domain.administration import is_collision
from thermoctl.domain.authz import Forbidden, require
from thermoctl.domain.pi_control import RESET_REASON_MANUAL_RESET, RESET_REASON_MANUAL_SET
from thermoctl.domain.principal import Principal

_PLACES = Decimal("0.000001")  # die Genauigkeit der Spalten (Numeric(12, 6))
_NOT_ACTIVE = "Für diesen Raum läuft keine PI-Regelung mit gespeichertem Zustand."
_BUSY = "Die Datenbank ist gerade belegt; es wurde nichts geändert. Bitte erneut versuchen."
log = logging.getLogger(__name__)


class PiInterventionError(Exception):
    """Der Eingriff wird abgewiesen; `code` ist für Adapter, `message` für Menschen."""

    def __init__(self, code: str, message: str, field: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.field = field


@dataclass(frozen=True)
class Pending:
    reason: str  # `manuell_reset` oder `manuell_gesetzt`
    integral: Decimal


@dataclass(frozen=True)
class PiStateView:
    zone_id: int
    enabled: bool
    integral: Decimal | None  # gespeicherter Wert, den der Regler zuletzt übernommen hat
    pending: Pending | None  # Eingriff, den der nächste Regelzyklus übernimmt


def check_value(raw: object) -> Decimal:
    """Der gewünschte I-Anteil: eine endliche Zahl von 0 bis 1, auf sechs Stellen gerundet.
    Wahrheitswerte und Text, den man nicht lesen kann, werden abgewiesen."""
    if isinstance(raw, bool) or raw is None:
        raise PiInterventionError("validation", "Bitte eine Zahl von 0 bis 1 angeben.", "value")
    try:
        value = raw if isinstance(raw, Decimal) else Decimal(str(raw))
    except (InvalidOperation, ValueError):
        raise PiInterventionError(
            "validation", "Bitte eine Zahl von 0 bis 1 angeben.", "value"
        ) from None
    if not value.is_finite() or value < 0 or value > 1:
        raise PiInterventionError(
            "validation", "Der I-Anteil muss zwischen 0 und 1 liegen.", "value"
        )
    value = value.quantize(_PLACES, rounding=ROUND_HALF_EVEN)
    return value + 0 if value == 0 else value  # "-0" ist 0 (und steht so im Audit)


def _require_plant_view(principal: Principal) -> None:
    if not pi_view.visible_to(principal):
        raise Forbidden("Der I-Anteil ist nur in der Anlagensicht zugänglich")


def _latest_request(session: Session, zone_id: int) -> PiManualRequest | None:
    return session.scalar(
        select(PiManualRequest)
        .where(PiManualRequest.zone_id == zone_id)
        .order_by(PiManualRequest.id.desc())
        .limit(1)
    )


def _view(session: Session, zone: Zone, state: ZoneState | None) -> PiStateView:
    waiting = _latest_request(session, zone.id)
    return PiStateView(
        zone_id=zone.id,
        enabled=bool(zone.pi_enabled),
        integral=state.pi_integral if state is not None else None,
        pending=Pending(waiting.reason, waiting.integral) if waiting is not None else None,
    )


def read_state(session: Session, principal: Principal, zone: Zone) -> PiStateView:
    require(principal, "zone.read", zone.id)
    _require_plant_view(principal)
    return _view(session, zone, session.get(ZoneState, zone.id))


def _request(
    session: Session,
    principal: Principal,
    zone: Zone,
    *,
    reason: str,
    value: Decimal,
    source: str,
    action: str,
    summary: str,
) -> PiStateView:
    require(principal, "zone.manage", zone.id)
    _require_plant_view(principal)
    state = session.get(ZoneState, zone.id)  # nur lesen, nie sperren oder schreiben
    if not zone.pi_enabled or state is None:
        raise PiInterventionError("pi_not_active", _NOT_ACTIVE)
    zone_id = zone.id
    try:
        # Die bisherige (nur lesende) Transaktion beenden und als Erstes schreiben: SQLite
        # lehnt „erst lesen, dann schreiben“ sofort mit `database is locked` ab, wenn dazwischen
        # jemand festgeschrieben hat. Als erste Anweisung wartet das INSERT auf die Sperre.
        session.commit()
        request = PiManualRequest(
            zone_id=zone_id, reason=reason, integral=value, requested_at=utcnow()
        )
        session.add(request)
        session.flush()
        state = session.get(ZoneState, zone_id, populate_existing=True)
        if state is None:  # pragma: no cover - Raum im selben Moment gelöscht
            raise PiInterventionError("pi_not_active", _NOT_ACTIVE)
        detail = f"bisher {state.pi_integral}, neu {value}; wirkt im nächsten Regelzyklus"
        earlier = session.scalar(
            select(PiManualRequest.reason)
            .where(PiManualRequest.zone_id == zone_id, PiManualRequest.id < request.id)
            .order_by(PiManualRequest.id.desc())
            .limit(1)
        )
        if earlier is not None:
            detail += f"; ersetzt die vorgemerkte Anforderung {earlier}"
        audit.record(
            session,
            source=source,
            action=action,
            object_type="zone",
            object_id=str(zone_id),
            summary=summary,
            user_id=principal.user_id,
            token_id=principal.token_id,
            detail=detail,
        )
        session.flush()
    except OperationalError as exc:
        if not _database_busy(exc):
            raise
        session.rollback()
        log.warning(
            "pi.manual_request_busy",
            extra={"event": "pi.manual_request_busy", "zone_id": zone_id, "reason": reason},
        )
        raise PiInterventionError("busy", _BUSY) from exc
    return _view(session, zone, state)


def _database_busy(exc: OperationalError) -> bool:
    """Eine Sperre oder ein Zusammenstoß mit einer anderen Transaktion -- nichts Kaputtes."""
    return is_collision(exc) or "database is locked" in str(exc.orig)


def request_reset(
    session: Session, principal: Principal, zone: Zone, *, source: str
) -> PiStateView:
    """„I zurücksetzen“: der nächste Regelzyklus beginnt mit 0."""
    return _request(
        session,
        principal,
        zone,
        reason=RESET_REASON_MANUAL_RESET,
        value=Decimal(0).quantize(_PLACES),
        source=source,
        action="pi.integral_reset",
        summary=f"I-Anteil von {zone.display_name} zurücksetzen angefordert",
    )


def request_set(
    session: Session, principal: Principal, zone: Zone, raw_value: object, *, source: str
) -> PiStateView:
    """„I setzen“: der nächste Regelzyklus rechnet mit diesem Wert (0 bis 1) weiter.
    Der Wert wird zuerst geprüft; ein abgewiesener Wert hinterlässt nichts."""
    require(principal, "zone.manage", zone.id)
    _require_plant_view(principal)
    value = check_value(raw_value)
    return _request(
        session,
        principal,
        zone,
        reason=RESET_REASON_MANUAL_SET,
        value=value,
        source=source,
        action="pi.integral_set",
        summary=f"I-Anteil von {zone.display_name} auf {value} setzen angefordert",
    )
