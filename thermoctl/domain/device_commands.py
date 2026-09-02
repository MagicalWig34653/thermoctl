"""Reading the actuator command log -- the query REST and MCP share.

`services/device_commands.record_command` is the write side, used only by the
publication cycle. The web view (`web/device_commands_views.py`) runs its own read
query, built before REST and MCP had any use for the same data -- rewriting it was
out of scope for this change. `list_commands` is the read side for the two adapters
added afterwards, so a filter added to one of them cannot silently behave differently
from the other -- Grundsatz 6, applied to the two adapters this change actually owns.

The table has no retention (`docs/offene-entscheidungen.md`, 2026-09-01) and grows
without bound for as long as the plant runs, which is why `limit` is mandatory and
capped rather than left to the caller.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.db.models.lookup import ActorSource, CommandOutcome
from thermoctl.db.models.state import DeviceCommand

DEFAULT_LIMIT = 100
MAX_LIMIT = 500


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
) -> list[CommandLogEntry]:
    """The actuator command log, newest first, capped at `limit` rows.

    No permission check here: both callers require `audit.read` -- the same right
    the web view demands -- before this ever runs, and this function does not know
    which adapter it is being called from.

    `zone_name` matches the snapshot column, not a join to `zone`: a deleted zone has
    no row to join to anymore, and the snapshot is exactly what lets its entries stay
    findable by name regardless.
    """
    if limit < 1 or limit > MAX_LIMIT:
        raise ValueError(f"limit muss zwischen 1 und {MAX_LIMIT} liegen")

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
        query.order_by(DeviceCommand.sent_at.desc(), DeviceCommand.id.desc()).limit(limit)
    ).all()
    return [
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
        )
        for entry, source_row, outcome_row in rows
    ]
