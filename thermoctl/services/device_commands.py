"""The one place every command towards an actuator gets written down.

`shadow_decision` says what the control loop decided. `audit_event` says what a
person did. This says what really left the service towards a device -- when, to
which zone and device, what payload, with what outcome, and why the regulation
wanted it. Once the plant is armed without a shadow-run comparison first, this is
the only place any of that stays answerable, possibly weeks later.
"""

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.db.models.device import Device
from thermoctl.db.models.lookup import ActorSource, CommandOutcome
from thermoctl.db.models.state import DeviceCommand
from thermoctl.db.models.zone import Zone

log = logging.getLogger(__name__)

# Every outcome the log understands; see `CommandOutcome` for what each means.
EXECUTED = "executed"
SUPPRESSED = "suppressed"
FAILED = "failed"


def record_command(
    session: Session,
    *,
    now: datetime,
    source: str,
    zone: Zone,
    device: Device,
    command: str,
    payload: str,
    outcome: str,
    error: str | None = None,
    reason: str | None = None,
) -> None:
    """Writes one entry into the actuator command log.

    **Never blocks the actuator command itself.** By the time this is called, the
    caller has already sent the command, deliberately withheld it (dry run), or
    watched it fail -- the physical or protocol-level effect, if any, has already
    happened. A broken log write must not roll back or crash the control loop on
    top of that: it is caught here, logged through the ordinary application log
    (which is what existed before this table did), and otherwise swallowed. A
    savepoint (`begin_nested`) keeps a failed write from poisoning the surrounding
    transaction -- the rest of the same publication cycle, including any log
    entries for other devices, is unaffected.
    """
    try:
        with session.begin_nested():
            source_id = session.scalar(
                select(ActorSource.id).where(ActorSource.code == source)
            )
            if source_id is None:
                raise ValueError(f"Unbekannte Quelle {source!r}")
            outcome_id = session.scalar(
                select(CommandOutcome.id).where(CommandOutcome.code == outcome)
            )
            if outcome_id is None:
                raise ValueError(f"Unbekanntes Ergebnis {outcome!r}")
            session.add(
                DeviceCommand(
                    sent_at=now,
                    source_id=source_id,
                    zone_id=zone.id,
                    zone_name=zone.display_name,
                    device_id=device.id,
                    device_name=device.display_name,
                    command=command,
                    payload=payload,
                    outcome_id=outcome_id,
                    error=error,
                    reason=reason,
                )
            )
            session.flush()
    except Exception:
        log.error(
            "Schalt-Protokolleintrag konnte nicht geschrieben werden",
            exc_info=True,
            extra={
                "zone_id": zone.id,
                "geraet": device.display_name,
                "befehl": command,
                "ergebnis": outcome,
            },
        )
