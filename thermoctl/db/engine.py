from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, event, make_url
from sqlalchemy.orm import Session, sessionmaker

from thermoctl.config import Settings


def create_engine_from_settings(settings: Settings) -> Engine:
    engine = create_engine(settings.database_url, pool_pre_ping=True, future=True)
    if engine.dialect.name == "sqlite":
        url = make_url(settings.database_url)
        # WAL needs a real file to put its `-wal`/`-shm` companions next to; an
        # in-memory database has none, and SQLite silently keeps `journal_mode`
        # at "memory" for it regardless, so there is nothing to switch and
        # nothing gained by asking. Covers the plain forms this project's own
        # setup can produce (an empty database, or the bare ":memory:") and the
        # common SQLite URI forms ("file::memory:", "file:foo?mode=memory",
        # both with or without "cache=shared") should a future configuration
        # ever pass `uri=true` -- not attempted, and not exhaustive: SQLite has
        # other ways to name an in-memory database under `uri=true` (the
        # `vfs=memdb` query parameter, for one), and any of those would be
        # misclassified as file-backed here. Harmless if so -- `PRAGMA
        # journal_mode=WAL` against such a database is a silent no-op, the same
        # as it would be without this check at all.
        query = {key.lower(): value for key, value in url.query.items()}
        uri_mode = str(query.get("uri", "")).lower() in ("true", "1", "yes")
        database = (url.database or "").lower()
        is_memory = (
            not url.database
            or url.database == ":memory:"
            or (uri_mode and (database.startswith("file::memory:") or "mode=memory" in database))
        )
        is_file_backed = not is_memory

        @event.listens_for(engine, "connect")
        def _prepare_sqlite_connection(dbapi_connection, connection_record) -> None:  # type: ignore[no-untyped-def]
            # 1. SQLite otherwise does not check foreign keys at all. Without this, tests
            #    pass that would fail on a violation under MariaDB.
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            # 1b. Two writers on the same SQLite file (regelschleife, web request,
            #     Verbund-Anspruch, Aufräumarbeiten -- all through this one engine)
            #     need to wait briefly for each other instead of "database is
            #     locked" back *immediately* the moment one of them holds the
            #     write lock. Python's own `sqlite3` module already sets this --
            #     it passes `timeout=5.0` to the underlying library itself by
            #     default, unrelated to anything SQLAlchemy or this module does
            #     (confirmed by reading it back: a fresh connection reports
            #     `PRAGMA busy_timeout` as 5000 with no pragma of ours involved).
            #     This line does not change that behaviour; it makes it explicit
            #     and independent of that driver default ever changing or of a
            #     future connection being opened some other way that does not
            #     carry it -- five seconds is long enough to ride out any real
            #     commit here (these are single-row updates and small batches,
            #     never a long-held transaction) but short enough that a
            #     genuinely stuck writer still surfaces as an error well within
            #     one shadow-loop cycle (60s by default -- see
            #     `services/cluster.py::_DEFAULT_INTERVAL_S`) instead of quietly
            #     stacking up unnoticed.
            cursor.execute("PRAGMA busy_timeout=5000")
            # 1c. WAL lets readers (web requests, the shadow loop's own reads)
            #     proceed while a writer is mid-transaction instead of blocking on
            #     each other the way the default rollback journal does -- exactly
            #     this application's pattern: one writer loop, concurrent web
            #     reads, a second instance racing for the Verbund-Anspruch. It
            #     adds `-wal` and `-shm` files next to the database file and
            #     relies on proper shared-memory locking, which makes it a poor
            #     fit for a database file placed on a network share or a
            #     cloud-synced folder (NFS/SMB locking is unreliable for WAL's
            #     shared index, and a sync client can upload the main file before
            #     the `-wal` file is checkpointed into it, corrupting the copy it
            #     uploads). Not this deployment's situation -- the database lives
            #     inside the container's own volume -- but worth knowing if
            #     someone points `database_url` at one anyway.
            if is_file_backed:
                cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()
            # 2. The pysqlite driver does not begin transactions on its own and commits
            #    on its own initiative in between. This means SAVEPOINT and rollback do
            #    not take hold: data written survives a rollback and leaks into the next
            #    test. With isolation_level=None, SQLAlchemy takes over transaction
            #    control itself (see _begin_sqlite_transaction).
            dbapi_connection.isolation_level = None

        @event.listens_for(engine, "begin")
        def _begin_sqlite_transaction(conn) -> None:  # type: ignore[no-untyped-def]
            # Deliberately plain `BEGIN`, not `BEGIN IMMEDIATE` -- see
            # `services/cluster.py::try_become_leader` for why the latter looked
            # tempting and turned out to be the wrong fix. `BEGIN IMMEDIATE` takes
            # SQLite's write lock the instant a transaction starts, before any
            # statement runs -- but *every* transaction on this engine starts one,
            # including a plain HTTP GET that never writes, and including
            # `tests/conftest.py::session`'s own outer transaction that wraps a
            # whole test in a savepoint (a second, genuinely independent
            # connection from the same test, e.g. the `client` fixture's request
            # handling, then finds the write lock already held for the entire
            # test and every one of its own writes fails immediately). Plain
            # `BEGIN` keeps read-only work from taking a write lock it never
            # needs; `try_become_leader` avoids the one situation that actually
            # needed `BEGIN IMMEDIATE` by writing before it reads instead.
            conn.exec_driver_sql("BEGIN")

    return engine


def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    http_session = factory()
    try:
        yield http_session
        http_session.commit()
    except Exception:
        http_session.rollback()
        raise
    finally:
        http_session.close()
