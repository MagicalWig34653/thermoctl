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
            # 1. SQLite prueft Fremdschluessel sonst gar nicht. Ohne das laufen Tests
            #    gruen, die unter MariaDB an einer Verletzung scheitern wuerden.
            zeiger = dbapi_connection.cursor()
            zeiger.execute("PRAGMA foreign_keys=ON")
            zeiger.close()
            # 2. Der pysqlite-Treiber beginnt Transaktionen nicht von sich aus und
            #    committet zwischendurch eigenmaechtig. Dadurch greifen SAVEPOINT und
            #    Rollback nicht: geschriebene Daten ueberleben ein Rollback und lecken in
            #    den naechsten Test. Mit isolation_level=None uebernimmt SQLAlchemy die
            #    Transaktionssteuerung selbst (siehe _sqlite_transaktion_beginnen).
            dbapi_connection.isolation_level = None

        @event.listens_for(engine, "begin")
        def _sqlite_transaktion_beginnen(conn) -> None:  # type: ignore[no-untyped-def]
            conn.exec_driver_sql("BEGIN")

    return engine


def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    sitzung = factory()
    try:
        yield sitzung
        sitzung.commit()
    except Exception:
        sitzung.rollback()
        raise
    finally:
        sitzung.close()
