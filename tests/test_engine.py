"""`thermoctl.db.engine.create_engine_from_settings` -- what a raw SQLite connection
actually comes back configured with.

Regression coverage for the CI failure `test_two_processes_racing_for_a_stale_claim_
only_one_wins` produced after v0.8.0: two threads with independent connections both
hit the same `cluster_claim` row, and on a slow enough runner the loser got an
`OperationalError` ("database is locked") instead of a matched `rowcount == 0`.

The cause was not what it first looked like. SQLite the *library* defaults
`busy_timeout` to 0, but Python's own `sqlite3` module does not use that default --
it passes `timeout=5.0` to the library itself unless told otherwise, and that was
already true before any of this. Measured directly: a bare `sqlite3.connect()`, and
a bare SQLAlchemy `create_engine("sqlite:///...")` with none of this module's own
pragmas involved, both already report `PRAGMA busy_timeout` as 5000. This module's
own `PRAGMA busy_timeout=5000` therefore changes nothing about that value -- it
makes the five-second wait explicit and independent of that driver default, rather
than fixing anything by itself. `tests/test_engine.py` tests that distinction
directly, not just the resulting number (see below).

The actual fix for the CI failure was two-fold. First, tracked down by reproducing
it outside pytest (a tight two-thread race against a plain, minimal SQLite
database, made to fail reliably instead of occasionally, so its cause could
actually be pinned down rather than guessed at from an intermittent CI log):
`services/cluster.py::try_become_leader` used to read before it wrote (check
whether the claim row exists, fetch the database's clock), and a plain, deferred
`BEGIN` takes no lock at all until the first statement runs -- a read acquires only
a SHARED one. When two connections both read first and then try to write, both
hold a SHARED lock and both attempt to escalate to a RESERVED one at the same
moment; SQLite does not resolve that through the busy handler at all, `busy_timeout`
included, because waiting there could deadlock two connections each holding what
the other needs -- it returns `SQLITE_BUSY` immediately instead, before
`busy_timeout` (5 seconds by driver default, or explicitly, makes no difference
here) ever gets a chance to retry.

The tempting fix at this layer -- `BEGIN IMMEDIATE` instead of plain `BEGIN`, so
every transaction takes the write lock up front and a competing writer waits on
the ordinary, retried case instead -- turned out to be the wrong one: it takes that
lock for *every* transaction on this engine, including a plain HTTP GET that never
writes, and including `tests/conftest.py::session`'s own outer transaction that
wraps a whole test in a savepoint. A second, genuinely independent connection from
the very same test (the `client` fixture's own request handling, say) then found
the write lock already held for the entire test and every one of its own writes
failed immediately -- a regression across most of the suite, not a fix. The actual
fix is narrower and lives in `services/cluster.py`: `try_become_leader` now writes
*before* it reads, so it never holds a SHARED lock to escalate from in the first
place. See its docstring and `tests/test_cluster.py` for that half.

A test that only re-runs the two-thread race (as `tests/test_cluster.py` already
does) would not actually watch the `busy_timeout` cause on its own -- it could pass
on a fast machine with a regression present and only fail again once timing
happens to line up unluckily. This test inspects the connection
`create_engine_from_settings` hands out directly, independent of timing.
"""

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import text

from thermoctl.config import Settings
from thermoctl.db.engine import create_engine_from_settings


def test_a_sqlite_connection_ends_up_with_a_nonzero_busy_timeout(tmp_path: Path) -> None:
    """A weak but still worthwhile guard: whatever the value's origin (see the
    module docstring -- currently Python's own driver default, not this
    module's own pragma), a connection from this engine must never end up with
    `busy_timeout=0`, or two writers racing for the same row get "database is
    locked" back immediately instead of one of them waiting briefly for the
    other's commit. `test_the_busy_timeout_pragma_is_actually_issued` below is
    the one that proves this module's own line runs at all."""
    database = tmp_path / "busy-timeout.db"
    engine = create_engine_from_settings(Settings(database_url=f"sqlite:///{database}"))
    with engine.connect() as connection:
        (timeout_ms,) = connection.execute(text("PRAGMA busy_timeout")).one()
    assert timeout_ms > 0
    engine.dispose()


def test_the_busy_timeout_pragma_is_actually_issued(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unlike the test above, this one does not just read back a number that
    Python's own `sqlite3` module would already report without any help from
    `create_engine_from_settings` (see the module docstring) -- it watches every
    statement run on the raw DBAPI connection and confirms that *this module's
    own* `PRAGMA busy_timeout=5000` is actually among them, independent of what
    the ambient value already happened to be.

    `sqlite3.Cursor` is a C type and cannot be monkeypatched directly (its
    `execute` cannot be reassigned); `sqlite3.connect` itself is an ordinary
    Python-level function, so it is wrapped here instead, attaching SQLite's own
    `set_trace_callback` to each new connection *before* handing it back to
    SQLAlchemy -- which is what then invokes `create_engine_from_settings`'s
    "connect" listener, including its `PRAGMA busy_timeout=5000` call, on that
    same connection.
    """
    executed: list[str] = []
    original_connect = sqlite3.dbapi2.connect

    def _connect(*args: object, **kwargs: object) -> sqlite3.Connection:
        connection = original_connect(*args, **kwargs)  # type: ignore[arg-type]
        connection.set_trace_callback(executed.append)
        return connection

    # SQLAlchemy's pysqlite dialect calls `sqlite3.dbapi2.connect`, not
    # `sqlite3.connect` -- the same function object today, but looked up
    # through a different module's namespace, so patching `sqlite3.connect`
    # alone would silently miss every connection this test needs to see.
    monkeypatch.setattr(sqlite3.dbapi2, "connect", _connect)

    database = tmp_path / "busy-timeout-explicit.db"
    engine = create_engine_from_settings(Settings(database_url=f"sqlite:///{database}"))
    with engine.connect():
        pass
    engine.dispose()

    assert "PRAGMA busy_timeout=5000" in executed, executed


def test_a_file_backed_sqlite_connection_uses_wal(tmp_path: Path) -> None:
    """WAL lets concurrent readers (web requests, the shadow loop's own reads)
    proceed while a writer is mid-transaction instead of blocking on each other --
    see `create_engine_from_settings`'s inline comment for why this application's
    pattern (one writer loop, concurrent web reads, a second instance racing for
    the Verbund-Anspruch) benefits from it."""
    database = tmp_path / "wal.db"
    engine = create_engine_from_settings(Settings(database_url=f"sqlite:///{database}"))
    with engine.connect() as connection:
        (mode,) = connection.execute(text("PRAGMA journal_mode")).one()
    assert mode.lower() == "wal"
    engine.dispose()


def test_an_in_memory_sqlite_connection_is_left_at_the_default_journal_mode(
) -> None:
    """WAL needs a real file to put its `-wal`/`-shm` companions next to; an
    in-memory database has none, and asking anyway would just be a silent no-op
    (SQLite keeps "memory" journal mode for it regardless) -- this test pins that
    `create_engine_from_settings` does not even try, rather than relying on
    SQLite's own no-op to paper over it."""
    engine = create_engine_from_settings(Settings(database_url="sqlite:///:memory:"))
    with engine.connect() as connection:
        (mode,) = connection.execute(text("PRAGMA journal_mode")).one()
    assert mode.lower() == "memory"
    # The busy_timeout still applies -- an in-memory database can still be shared
    # across threads via a shared cache, and costs nothing to set regardless.
    with engine.connect() as connection:
        (timeout_ms,) = connection.execute(text("PRAGMA busy_timeout")).one()
    assert timeout_ms > 0
    engine.dispose()


def test_a_uri_form_in_memory_sqlite_connection_is_also_left_alone(
) -> None:
    """The URI shorthand for an in-memory database (`file::memory:`, with
    `uri=true` so SQLAlchemy passes the string through to SQLite's own URI
    parser instead of treating it as a plain filename) is not the plain
    ":memory:" form the check above already covers, and does not appear
    anywhere in this project's own configuration today -- but if a future
    `database_url` ever used it, it must not be misclassified as file-backed
    and pointed at `PRAGMA journal_mode=WAL` for a database with no file to
    put `-wal`/`-shm` companions next to."""
    engine = create_engine_from_settings(
        Settings(database_url="sqlite:///file::memory:?cache=shared&uri=true")
    )
    with engine.connect() as connection:
        (mode,) = connection.execute(text("PRAGMA journal_mode")).one()
    assert mode.lower() == "memory"
    engine.dispose()


def test_a_transaction_does_not_take_the_write_lock_for_a_read_alone(
    tmp_path: Path,
) -> None:
    """The deliberate other half of the design decision in the module docstring:
    a transaction started through `create_engine_from_settings` stays a plain,
    deferred `BEGIN`, so a session that only ever reads must not block a second,
    independent connection's write -- unlike `BEGIN IMMEDIATE`, which would take
    SQLite's write lock the moment *any* transaction starts, read-only or not,
    and was rejected for exactly that reason (see the module docstring).

    Proven without any timing dependency: open a session through the engine, run
    only a *read* in it and deliberately do not commit, then have a wholly
    separate connection through the same engine write and commit. That second
    write must succeed while the first session's read transaction is still open.
    """
    database = tmp_path / "deferred.db"
    engine = create_engine_from_settings(Settings(database_url=f"sqlite:///{database}"))
    with engine.connect() as connection:
        connection.execute(text("CREATE TABLE t (id INTEGER PRIMARY KEY)"))
        connection.commit()

    reader = engine.connect()
    reader.execute(text("SELECT * FROM t"))  # a read only -- never a write

    with engine.connect() as writer:
        writer.execute(text("INSERT INTO t (id) VALUES (1)"))
        writer.commit()

    reader.rollback()
    reader.close()
    engine.dispose()


def test_foreign_keys_are_still_enforced_alongside_the_new_pragmas(
    tmp_path: Path,
) -> None:
    """Guards against the new pragmas having been inserted in a way that
    accidentally replaces or short-circuits the existing `PRAGMA foreign_keys=ON`
    (finding 1 in `create_engine_from_settings`'s own comment) rather than
    running alongside it."""
    database = tmp_path / "foreign-keys.db"
    engine = create_engine_from_settings(Settings(database_url=f"sqlite:///{database}"))
    with engine.connect() as connection:
        (enabled,) = connection.execute(text("PRAGMA foreign_keys")).one()
    assert enabled == 1
    engine.dispose()
