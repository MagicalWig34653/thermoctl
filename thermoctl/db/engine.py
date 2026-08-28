from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from thermoctl.config import Settings


def create_engine_from_settings(settings: Settings) -> Engine:
    engine = create_engine(settings.database_url, pool_pre_ping=True, future=True)
    if engine.dialect.name == "sqlite":

        @event.listens_for(engine, "connect")
        def _fremdschluessel_einschalten(dbapi_connection, connection_record) -> None:  # type: ignore[no-untyped-def]
            # SQLite prueft Fremdschluessel sonst gar nicht. Ohne das laufen Tests
            # gruen, die unter MariaDB an einer Verletzung scheitern wuerden.
            zeiger = dbapi_connection.cursor()
            zeiger.execute("PRAGMA foreign_keys=ON")
            zeiger.close()

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
