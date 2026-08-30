from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from thermoctl.config import Settings


def create_engine_from_settings(settings: Settings) -> Engine:
    engine = create_engine(settings.database_url, pool_pre_ping=True, future=True)
    if engine.dialect.name == "sqlite":

        @event.listens_for(engine, "connect")
        def _sqlite_verbindung_vorbereiten(dbapi_connection, connection_record) -> None:  # type: ignore[no-untyped-def]
            # 1. SQLite otherwise does not check foreign keys at all. Without this, tests
            #    pass that would fail on a violation under MariaDB.
            zeiger = dbapi_connection.cursor()
            zeiger.execute("PRAGMA foreign_keys=ON")
            zeiger.close()
            # 2. The pysqlite driver does not begin transactions on its own and commits
            #    on its own initiative in between. This means SAVEPOINT and rollback do
            #    not take hold: data written survives a rollback and leaks into the next
            #    test. With isolation_level=None, SQLAlchemy takes over transaction
            #    control itself (see _begin_sqlite_transaction).
            dbapi_connection.isolation_level = None

        @event.listens_for(engine, "begin")
        def _begin_sqlite_transaction(conn) -> None:  # type: ignore[no-untyped-def]
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
