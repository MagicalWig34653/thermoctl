"""Aktiv-Bereitschafts-Verbund: the atomic claim, and what governs it.

`services/cluster.py` is the only module that reads or writes `cluster_claim`.
These tests manage that row themselves with committed sessions on `engine`
directly, not the rollback-based `session` fixture every other test in this
suite uses: the concurrency test below genuinely needs two independent database
connections that can see each other's commits mid-test, which a session
scoped to a savepoint that gets rolled back at teardown never would -- and
every other test here that spans more than one call likewise needs its writes
to actually be visible to the next call, exactly the situation a real
takeover happens in.
"""

import threading
from datetime import datetime

import pytest
from sqlalchemy import Engine, delete, event
from sqlalchemy.orm import Session

from thermoctl.db.models.operations import ClusterClaim
from thermoctl.services import cluster

_UNCLAIMED = datetime(1970, 1, 1)
_TIMEOUT_S = 300


def _clear(engine: Engine) -> None:
    with Session(engine) as db_session:
        db_session.execute(delete(ClusterClaim))
        db_session.commit()


def _seed(engine: Engine, *, holder: str = "", expires_at: datetime = _UNCLAIMED) -> None:
    _clear(engine)
    with Session(engine) as db_session:
        db_session.add(ClusterClaim(id=1, holder_id=holder, expires_at=expires_at))
        db_session.commit()


@pytest.fixture(autouse=True)
def _no_row_left_behind_for_other_tests(engine: Engine):
    """Every other test in this suite relies on the fail-open default (see below)
    -- a row this file forgot to clean up would silently make some other test's
    switching attempt start reporting `switching_allowed() is False` for reasons
    that have nothing to do with what that test is checking."""
    yield
    _clear(engine)


def test_no_row_at_all_fails_open_to_leader(engine: Engine) -> None:
    """Clustering was never engaged for this schema: the whole test suite builds
    it via `Base.metadata.create_all()`, which never runs the migration's data
    seed. Both the read check and the write attempt must report "leads"
    unconditionally in that case -- otherwise every existing switching test in
    this suite would need a claim row it has never heard of, see this module's
    own docstring and `ClusterClaim`'s."""
    with Session(engine) as db_session:
        assert cluster.is_leader(db_session, holder="irgendwer") is True
        assert (
            cluster.try_become_leader(db_session, holder="irgendwer", timeout_seconds=_TIMEOUT_S)
            is True
        )


def test_an_unclaimed_row_is_claimed_immediately(engine: Engine) -> None:
    _seed(engine)
    with Session(engine) as db_session:
        assert cluster.try_become_leader(db_session, holder="a", timeout_seconds=_TIMEOUT_S)
        db_session.commit()
    with Session(engine) as db_session:
        assert cluster.is_leader(db_session, holder="a") is True
        assert cluster.is_leader(db_session, holder="b") is False


def test_a_fresh_claim_blocks_a_different_holder(engine: Engine) -> None:
    _seed(engine)
    with Session(engine) as db_session:
        assert cluster.try_become_leader(db_session, holder="a", timeout_seconds=_TIMEOUT_S)
        db_session.commit()
    with Session(engine) as db_session:
        assert not cluster.try_become_leader(
            db_session, holder="b", timeout_seconds=_TIMEOUT_S
        )
        db_session.commit()
    with Session(engine) as db_session:
        assert cluster.is_leader(db_session, holder="a") is True
        assert cluster.is_leader(db_session, holder="b") is False


def test_the_same_holder_renews_its_own_still_fresh_claim(engine: Engine) -> None:
    """A renewal, not a takeover -- the holder matches, so the `WHERE` clause's
    other branch (an expired `expires_at`) never has to fire."""
    _seed(engine)
    with Session(engine) as db_session:
        assert cluster.try_become_leader(db_session, holder="a", timeout_seconds=_TIMEOUT_S)
        db_session.commit()
    with Session(engine) as db_session:
        assert cluster.try_become_leader(db_session, holder="a", timeout_seconds=_TIMEOUT_S)
        db_session.commit()
    with Session(engine) as db_session:
        assert cluster.is_leader(db_session, holder="a") is True


def test_an_expired_claim_is_taken_over(engine: Engine) -> None:
    _seed(engine, holder="a", expires_at=_UNCLAIMED)
    with Session(engine) as db_session:
        assert cluster.try_become_leader(db_session, holder="b", timeout_seconds=_TIMEOUT_S)
        db_session.commit()
    with Session(engine) as db_session:
        assert cluster.is_leader(db_session, holder="b") is True
        assert cluster.is_leader(db_session, holder="a") is False


def test_release_makes_the_claim_immediately_takeable(engine: Engine) -> None:
    """The graceful-shutdown path: a departing leader must not make the other
    side wait out the full takeover timeout for no reason."""
    _seed(engine)
    with Session(engine) as db_session:
        assert cluster.try_become_leader(db_session, holder="a", timeout_seconds=_TIMEOUT_S)
        db_session.commit()
    with Session(engine) as db_session:
        cluster.release(db_session, holder="a")
        db_session.commit()
    with Session(engine) as db_session:
        assert cluster.is_leader(db_session, holder="a") is False
        assert cluster.try_become_leader(db_session, holder="b", timeout_seconds=_TIMEOUT_S)
        db_session.commit()
    with Session(engine) as db_session:
        assert cluster.is_leader(db_session, holder="b") is True


def test_release_by_a_non_holder_does_nothing(engine: Engine) -> None:
    """A standby that was never leading must not be able to evict the leader by
    calling release on its own -- `release`'s `WHERE` only ever matches its
    caller's own claim."""
    _seed(engine)
    with Session(engine) as db_session:
        assert cluster.try_become_leader(db_session, holder="a", timeout_seconds=_TIMEOUT_S)
        db_session.commit()
    with Session(engine) as db_session:
        cluster.release(db_session, holder="jemand-anderes")
        db_session.commit()
    with Session(engine) as db_session:
        assert cluster.is_leader(db_session, holder="a") is True


def test_takeover_timeout_multiplies_interval_and_cycles() -> None:
    class Fake:
        shadow_interval_seconds = 30
        cluster_takeover_cycles = 4

    assert cluster.takeover_timeout_seconds(Fake()) == 120  # type: ignore[arg-type]
    assert cluster.takeover_timeout_seconds(None) == 300  # 60 * 5, the built-in defaults


def test_instance_id_is_stable_within_the_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """Not stable across a restart -- see the module docstring -- but a single
    process must see the same identity on every call, or a renewal would read as
    a takeover by an impostor every single cycle."""
    monkeypatch.setattr(cluster, "_instance_id", None)
    monkeypatch.delenv("THERMOCTL_INSTANCE_ID", raising=False)
    first = cluster.instance_id()
    second = cluster.instance_id()
    assert first == second


def test_instance_id_honours_the_explicit_environment_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cluster, "_instance_id", None)
    monkeypatch.setenv("THERMOCTL_INSTANCE_ID", "leitstelle-nord")
    assert cluster.instance_id() == "leitstelle-nord"
    monkeypatch.setattr(cluster, "_instance_id", None)


def test_two_processes_racing_for_a_stale_claim_only_one_wins(engine: Engine) -> None:
    """The actual proof of atomicity, not just of the logic in isolation: two
    threads, two independent database connections, real commits, released to
    race at the same instant via a barrier -- not two calls made one after
    another, which any implementation (including a broken read-then-write one)
    would get right by accident. Exactly one thread's `UPDATE` must see its
    `WHERE` still match; the other's `rowcount` must come back 0.
    """
    _seed(engine)
    barrier = threading.Barrier(2)
    results: dict[str, bool] = {}
    errors: list[BaseException] = []

    def attempt(holder: str) -> None:
        try:
            barrier.wait(timeout=5)
            with Session(engine) as db_session:
                results[holder] = cluster.try_become_leader(
                    db_session, holder=holder, timeout_seconds=_TIMEOUT_S
                )
                db_session.commit()
        except BaseException as exc:  # pragma: no cover - only on an actual failure
            errors.append(exc)

    threads = [
        threading.Thread(target=attempt, args=(holder,)) for holder in ("instanz-a", "instanz-b")
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not errors, errors
    assert set(results) == {"instanz-a", "instanz-b"}
    assert sorted(results.values()) == [False, True]

    winner = "instanz-a" if results["instanz-a"] else "instanz-b"
    loser = "instanz-b" if winner == "instanz-a" else "instanz-a"
    with Session(engine) as db_session:
        assert cluster.is_leader(db_session, holder=winner) is True
        assert cluster.is_leader(db_session, holder=loser) is False


def test_try_become_leader_writes_before_it_reads_cluster_claim(engine: Engine) -> None:
    """Pins the actual fix behind the test above, independent of any timing: a
    `SELECT` against `cluster_claim` before the `UPDATE` is exactly what made the
    race above fail with "database is locked" on SQLite reliably, not just on a
    slow runner -- see `try_become_leader`'s own docstring for the mechanism
    (a SHARED lock on both sides, escalated to a RESERVED one at the same moment,
    a case SQLite refuses to resolve via `busy_timeout`). `_claim_exists`'s read
    for the "clustering never engaged" case must therefore come *after* the
    write attempt, not before it, regardless of which branch that write takes.

    Verified here by recording, via SQLAlchemy's own instrumentation rather than
    by timing two threads against each other, which statement against
    `cluster_claim` a single call to `try_become_leader` issues first -- the row
    exists in this case, so the behaviour is unchanged (a normal takeover) and
    only the order is examined. `try_become_leader` also runs `SELECT func.now()`
    to read the database's own clock; that statement has no `FROM` clause, never
    touches `cluster_claim` at all and so never takes the SHARED lock this test
    is about -- it is filtered out here rather than the fix being weakened to
    avoid it.
    """
    _seed(engine)
    statements: list[str] = []

    def _record(conn, cursor, statement, parameters, context, executemany):  # type: ignore[no-untyped-def]
        if "cluster_claim" in statement:
            statements.append(statement.strip().split()[0].upper())

    event.listen(engine, "before_cursor_execute", _record)
    try:
        with Session(engine) as db_session:
            cluster.try_become_leader(db_session, holder="a", timeout_seconds=_TIMEOUT_S)
            db_session.commit()
    finally:
        event.remove(engine, "before_cursor_execute", _record)

    assert statements, "try_become_leader issued no statement against cluster_claim at all"
    assert statements[0] == "UPDATE", statements
