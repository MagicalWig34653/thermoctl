"""The real relay state at the first entry into emergency operation.

Concept `lokal/konzepte/sensorausfall-notbetrieb.md` 3.4: "Ein bereits
eingeschaltetes Relais darf zunächst seine verbleibende Mindest-Ein-Dauer erfüllen;
anschließend Aus anfordern. ... Bei bekanntem bisherigen Aus-Zustand kann dessen
bereits eingehaltene Aus-Zeit angerechnet werden; bei unbekanntem Zustand Aus senden
und volle Aus-Dauer warten."

`domain.emergency_cycle.advance()` can only credit what it is told
(`CycleInput.prior`). This module is the one place that answers "what do we really
know about this relay?" -- shared by the shadow run and the armed publisher
(Grundsatz 6), so both decide the first entry identically.

**Source: the command log (`device_command`), not `ActuatorEmergencyState`.** The
emergency row's `last_successful_command_*` is reset on every fresh episode and is
never written during normal operation, so at first entry it is empty by
construction. The command log is the only record that outlives episodes and carries
an exact send time.

**Conservative by construction (Grundsatz 7):**

- Only an *executed* `switch` command counts as knowledge. A failed or
  dry-run (`suppressed`) entry proves nothing about the relay -- and ignoring a failed
  entry keeps the answer stable across a retry: a failed first attempt must not
  turn "known on" into "unknown" on the next cycle.
- The send time is a lower bound for how long the relay has been in that state, so
  the credit is never larger than what was really reached.
- A relay already on for at least the minimum-on duration has nothing left to
  honour: reported as unknown (-> Aus, as before), not as a credited Ein phase.
- A send time in the future (clock jump) or an unreadable payload is unknown.
"""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.db.models.lookup import CommandOutcome
from thermoctl.db.models.state import DeviceCommand
from thermoctl.domain.emergency_cycle import PriorPhaseHint
from thermoctl.services.device_commands import EXECUTED

SWITCH_COMMAND = "switch"


def commanded_state(payload: str) -> bool | None:
    """The on/off state a logged `switch` payload asked for, or `None` if the
    payload is not one this service wrote (Zigbee2MQTT `{"state": "ON"}` or Meross
    `{"togglex": {"channel": 0, "onoff": 1}}`)."""
    try:
        data = json.loads(payload)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    state = data.get("state")
    if state == "ON":
        return True
    if state == "OFF":
        return False
    togglex = data.get("togglex")
    if isinstance(togglex, dict):
        onoff = togglex.get("onoff")
        if onoff == 1:
            return True
        if onoff == 0:
            return False
    return None


def entry_prior_hint(
    session: Session, device_id: int, *, now: datetime, min_on_seconds: int
) -> PriorPhaseHint | None:
    """What `emergency_cycle.advance()` may credit for `device_id` at first entry,
    or `None` for "unknown" (send Aus, wait the full Aus-Dauer)."""
    # No autoflush: the caller may hold a half-built, not yet flushable row (the
    # shadow run adds its `ActuatorEmergencyState` before filling `episode_id`).
    with session.no_autoflush:
        row = session.execute(
            select(DeviceCommand.sent_at, DeviceCommand.payload)
            .join(CommandOutcome, CommandOutcome.id == DeviceCommand.outcome_id)
            .where(
                DeviceCommand.device_id == device_id,
                DeviceCommand.command == SWITCH_COMMAND,
                CommandOutcome.code == EXECUTED,
            )
            .order_by(DeviceCommand.sent_at.desc(), DeviceCommand.id.desc())
            .limit(1)
        ).first()
    if row is None:
        return None
    on = commanded_state(row.payload)
    if on is None:
        return None
    elapsed = int((now - row.sent_at).total_seconds())
    if elapsed < 0:
        return None
    if on and elapsed >= min_on_seconds:
        return None
    return PriorPhaseHint(on=on, elapsed_seconds=elapsed)
