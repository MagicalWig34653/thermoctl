"""Aktiv-Bereitschafts-Verbund: which of two instances currently leads.

Two processes can point at the same database and the same MQTT broker -- one
active, one standby, ready to take over if the active one stops renewing its
claim. Exactly one row (`db.models.operations.ClusterClaim`, id 1) says who that
is right now; this module is the only place that reads or writes it.

**The claim transfers through one atomic ``UPDATE``, not a read-then-write.**
Two processes racing to become leader both issue the same statement
(`try_become_leader` below); the database's own row lock serialises them, and
only one ever sees its ``WHERE`` clause still match by the time it is its turn
-- the loser's ``UPDATE`` matches zero rows and its `rowcount` says so. Nothing
about this depends on which process asked first; see
`tests/test_cluster.py::test_two_processes_racing_for_a_stale_claim_only_one_wins`
for a test that actually runs two threads against two independent sessions on
the same engine to prove it, not two calls made one after the other.

**The database's clock decides, never a host's.** Two machines never agree on
the time closely enough to arbitrate who gets to switch a heater -- one
running fast would let its standby take over too early, one running slow
would let a genuinely dead active instance's claim look fresh forever. Every
comparison and every write of `expires_at` here goes through `sqlalchemy.func.
now()`, evaluated by the database itself; no comparison anywhere reads either
process's own system clock.

**A missing row means clustering was never engaged.** The migration that adds
`cluster_claim` seeds exactly one row, unclaimed. Every test in this suite,
though, builds its schema via `Base.metadata.create_all()`, which creates the
table but never runs a migration's data seed -- so the table is empty there.
Treating an empty table as "this process leads" (rather than "nobody may ever
switch anything") is what keeps the whole existing test suite, and every
single, unclustered installation's behaviour, exactly as it was before this
module existed. A real installation's schema carries the row from the
migration that introduces it onward, and from that point on the row governs
strictly -- there is no way, in a schema built by Alembic, to end up back in
the fail-open case.
"""

import logging
import os
import uuid
from datetime import datetime, timedelta

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from thermoctl.db.models.operations import ClusterClaim, Setting

log = logging.getLogger(__name__)

# The one row this whole module reads and writes.
_CLAIM_ROW_ID = 1

# Far enough in the past that `expires_at <= <database's now>` is true on any
# database, in any timezone the connection might report it as -- a released or
# never-claimed row is immediately claimable, with no dependency on either
# side's clock (see the module docstring).
_UNCLAIMED = datetime(1970, 1, 1)

# The default cycle length and its multiplier, used only while `setting` is
# still missing (setup not yet completed) -- mirrors `app._SHADOW_INTERVAL_DEFAULT_S`
# and `Setting.cluster_takeover_cycles`'s own column default.
_DEFAULT_INTERVAL_S = 60
_DEFAULT_TAKEOVER_CYCLES = 5

_instance_id: str | None = None


def instance_id() -> str:
    """This process's cluster identity -- stable for the life of the process.

    `THERMOCTL_INSTANCE_ID` names it explicitly when set -- useful for logs and
    for an operator who wants to tell which of two containers currently leads
    without opening a database console. Left unset, a random identity is
    generated once, on first use, and kept for the rest of the process: it only
    has to be distinct from whatever else is talking to this database right
    now, not stable across a restart -- after a restart this process competes
    for the claim like any newcomer, exactly as it should.
    """
    global _instance_id
    if _instance_id is None:
        _instance_id = os.environ.get("THERMOCTL_INSTANCE_ID") or uuid.uuid4().hex
    return _instance_id


def takeover_timeout_seconds(setting: Setting | None) -> int:
    """How long the active instance may go without renewing before it counts as
    dead. `setting.shadow_interval_seconds * setting.cluster_takeover_cycles`,
    both configurable -- see their columns' docstrings. Falls back to the
    built-in defaults while `setting` is still missing (setup not yet
    completed), the same situation `app._shadow_interval_s` already handles the
    same way for the cycle length alone.
    """
    interval = setting.shadow_interval_seconds if setting is not None else _DEFAULT_INTERVAL_S
    cycles = (
        setting.cluster_takeover_cycles if setting is not None else _DEFAULT_TAKEOVER_CYCLES
    )
    return interval * cycles


def _claim_exists(session: Session) -> bool:
    return (
        session.execute(
            select(ClusterClaim.id).where(ClusterClaim.id == _CLAIM_ROW_ID)
        ).first()
        is not None
    )


def try_become_leader(session: Session, *, holder: str, timeout_seconds: int) -> bool:
    """Attempts to become (or remain) the active instance for this control cycle.

    Called once per shadow-loop iteration, by both instances alike, before
    either one decides whether to actually run the cycle. Returns whether
    `holder` leads as of right now.

    The `WHERE` clause is what makes this safe under real concurrency: it
    matches either the row already belonging to `holder` (a renewal) or a row
    whose `expires_at` has already passed **according to the database**,
    computed against a value this same call already fetched from it a moment
    earlier -- never against either process's own clock. Two processes issuing
    this at the same moment both see a matching row *before* either commits;
    the database's row lock still only lets one of the two `UPDATE`s actually
    take hold, and the loser's `rowcount` comes back 0. See the module
    docstring for why a missing row instead returns `True` unconditionally.
    """
    if not _claim_exists(session):
        return True
    database_now = session.execute(select(func.now())).scalar()
    assert database_now is not None  # pragma: no cover - every real backend answers this
    new_expiry = database_now + timedelta(seconds=timeout_seconds)
    result = session.execute(
        update(ClusterClaim)
        .where(ClusterClaim.id == _CLAIM_ROW_ID)
        .where(
            or_(
                ClusterClaim.holder_id == holder,
                ClusterClaim.expires_at <= database_now,
            )
        )
        .values(holder_id=holder, expires_at=new_expiry)
    )
    # `rowcount` only exists on `CursorResult`, not on the general `Result` type;
    # for an `UPDATE` it is always a `CursorResult` (same reasoning as
    # `domain/passkey.py`'s own `rowcount` use).
    return int(result.rowcount) == 1  # type: ignore[attr-defined]


def is_leader(session: Session, *, holder: str) -> bool:
    """Whether `holder` may switch something right now -- the read-only check.

    Deliberately its own query, not a cache of what `try_become_leader` last
    returned: a switching attempt several actuators into a long cycle must see
    that leadership was lost *during* this same cycle (a slow instance running
    past its own renewed timeout), not the answer from when the cycle began.
    See the module docstring for the missing-row case.
    """
    if not _claim_exists(session):
        return True
    return (
        session.execute(
            select(ClusterClaim.id).where(
                ClusterClaim.id == _CLAIM_ROW_ID,
                ClusterClaim.holder_id == holder,
                ClusterClaim.expires_at > func.now(),
            )
        ).first()
        is not None
    )


def release(session: Session, *, holder: str) -> None:
    """Gives up the claim immediately -- a graceful shutdown, not a crash.

    Without this, the most common case (an orderly restart of the active
    instance) would sit through the full takeover timeout for no reason: the
    departing instance already knows it is leaving. Sets `expires_at` to a
    fixed point far in the past rather than computing "now minus a bit" --
    that needs no clock at all, host or database, to already be stale by any
    definition (see `_UNCLAIMED`). A no-op if `holder` does not currently hold
    the claim (nothing to release) or the row does not exist at all (see the
    module docstring) -- the `WHERE` simply matches nothing.
    """
    session.execute(
        update(ClusterClaim)
        .where(ClusterClaim.id == _CLAIM_ROW_ID, ClusterClaim.holder_id == holder)
        .values(holder_id="", expires_at=_UNCLAIMED)
    )
