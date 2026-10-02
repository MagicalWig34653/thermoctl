"""Reading the actuator command log -- the query REST and MCP share.

`services/device_commands.record_command` is the write side, used only by the
publication cycle. The web view (`web/device_commands_views.py`) runs its own read
query, built before REST and MCP had any use for the same data -- rewriting it was
out of scope for this change. `list_commands` is the read side for the two adapters
added afterwards, so a filter added to one of them cannot silently behave differently
from the other -- Grundsatz 6, applied to the two adapters this change actually owns.

The table has no retention, deliberately: unlike a measurement, a command is rare
(sent only on change) and any single row can be the one someone needs weeks later to
explain an incident -- an automatic deletion would remove exactly the evidence this
log was built for. It therefore grows without bound for as long as the plant runs,
which is why `limit` is mandatory and capped rather than left to the caller.

**Auftrag 8b** adds `actuator_decision` rows (action != `"normal"`) into this
same log, merged by time with the `device_command` rows above --
"Notbetriebsentscheidungen ... zusätzlich zu echten Befehlen, eindeutig
gekennzeichnet" (plan Auftrag 8). `action == "normal"` is excluded on
purpose: it is written every single regulation cycle for every tracked
assignment regardless of whether anything changed, and would flood this log
exactly the way `docs/STATUS.md` says the log "bleibt selten" for real
commands -- only a genuine decision (silence, handover, restore, a cycle
switch) is a protocol-worthy event. `entry_kind` tells the two kinds of row
apart (`"befehl"` for an actual send attempt, `"entscheidung"` for a decision
that may or may not have led to one); `simulated` is `False` for every
`"befehl"` row (a dry-run send is already visible through its own
`outcome="suppressed"`) and mirrors `ActuatorDecision.simulated` for an
`"entscheidung"` row -- shadow-run diagnostics look identical in shape to a
scharf decision, only this flag tells them apart.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.db.models.lookup import ActorSource, CommandOutcome
from thermoctl.db.models.sensor_failure import ActuatorDecision
from thermoctl.db.models.state import DeviceCommand

DEFAULT_LIMIT = 100
MAX_LIMIT = 500

ENTRY_KIND_COMMAND = "befehl"
ENTRY_KIND_DECISION = "entscheidung"


@dataclass(frozen=True)
class CommandLogEntry:
    id: int
    sent_at: datetime
    source: str
    zone_name: str
    device_name: str
    command: str
    payload: str
    outcome: str
    error: str | None
    reason: str | None
    entry_kind: str = ENTRY_KIND_COMMAND
    simulated: bool = False


def naive_utc(moment: datetime | None) -> datetime | None:
    """Normalizes an incoming filter bound to the naive UTC the column is stored in.

    A caller may reasonably send an aware value (REST and MCP both accept ISO 8601
    with an offset). A naive value is assumed to already be UTC, the convention every
    other naive datetime in this project follows -- there is no local timezone to
    guess it from at this layer.
    """
    if moment is None or moment.tzinfo is None:
        return moment
    return moment.astimezone(UTC).replace(tzinfo=None)


def list_commands(
    session: Session,
    *,
    zone_name: str | None = None,
    from_at: datetime | None = None,
    to_at: datetime | None = None,
    outcome: str | None = None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> list[CommandLogEntry]:
    """The actuator command log, newest first, capped at `limit` rows.

    No permission check here: both callers require `audit.read` -- the same right
    the web view demands -- before this ever runs, and this function does not know
    which adapter it is being called from.

    `zone_name` matches the snapshot column, not a join to `zone`: a deleted zone has
    no row to join to anymore, and the snapshot is exactly what lets its entries stay
    findable by name regardless.

    `outcome` only ever matches a `"befehl"` row -- an `ActuatorDecision` has no
    `CommandOutcome` of its own (it may not have led to a send attempt at all,
    see the module docstring), so any `outcome` filter excludes every
    `"entscheidung"` row from the result rather than guessing which of them
    might match.

    `offset` (the HTML view's pagination, REST/MCP never pass it) is applied
    **after** the two sources are merged and re-sorted, not pushed down into
    either query: the two tables are sorted independently, and only the
    merged order is the one the caller actually wants page boundaries drawn
    against.
    """
    if limit < 1 or limit > MAX_LIMIT:
        raise ValueError(f"limit muss zwischen 1 und {MAX_LIMIT} liegen")
    if offset < 0:
        raise ValueError("offset darf nicht negativ sein")
    fetch = offset + limit

    query = (
        select(DeviceCommand, ActorSource, CommandOutcome)
        .join(ActorSource, ActorSource.id == DeviceCommand.source_id)
        .join(CommandOutcome, CommandOutcome.id == DeviceCommand.outcome_id)
    )
    if zone_name:
        query = query.where(DeviceCommand.zone_name == zone_name)
    if from_at is not None:
        query = query.where(DeviceCommand.sent_at >= naive_utc(from_at))
    if to_at is not None:
        query = query.where(DeviceCommand.sent_at <= naive_utc(to_at))
    if outcome:
        query = query.where(CommandOutcome.code == outcome)

    rows = session.execute(
        query.order_by(DeviceCommand.sent_at.desc(), DeviceCommand.id.desc()).limit(fetch)
    ).all()
    entries = [
        CommandLogEntry(
            id=entry.id,
            sent_at=entry.sent_at,
            source=source_row.code,
            zone_name=entry.zone_name,
            device_name=entry.device_name,
            command=entry.command,
            payload=entry.payload,
            outcome=outcome_row.code,
            error=entry.error,
            reason=entry.reason,
            entry_kind=ENTRY_KIND_COMMAND,
            simulated=False,
        )
        for entry, source_row, outcome_row in rows
    ]

    if not outcome:
        decision_query = select(ActuatorDecision).where(ActuatorDecision.action != "normal")
        if zone_name:
            decision_query = decision_query.where(ActuatorDecision.zone_name == zone_name)
        if from_at is not None:
            decision_query = decision_query.where(
                ActuatorDecision.decided_at >= naive_utc(from_at)
            )
        if to_at is not None:
            decision_query = decision_query.where(
                ActuatorDecision.decided_at <= naive_utc(to_at)
            )
        decisions = session.scalars(
            decision_query.order_by(
                ActuatorDecision.decided_at.desc(), ActuatorDecision.id.desc()
            ).limit(fetch)
        ).all()
        entries.extend(
            CommandLogEntry(
                id=decision.id,
                sent_at=decision.decided_at,
                source="regelung",
                zone_name=decision.zone_name,
                device_name=decision.device_name,
                command=decision.action,
                payload="",
                outcome=decision.reason_code,
                error=None,
                reason=decision.reason,
                entry_kind=ENTRY_KIND_DECISION,
                simulated=decision.simulated,
            )
            for decision in decisions
        )

    entries.sort(key=lambda row: (row.sent_at, row.id), reverse=True)
    return entries[offset : offset + limit]
