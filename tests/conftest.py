import os
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from thermoctl.config import Settings
from thermoctl.db.base import Base
from thermoctl.db.engine import create_engine_from_settings, session_factory

TEST_DATABASE_URL = os.environ.get("THERMOCTL_TEST_DATABASE_URL", "sqlite:///./test.db")


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings(
        _env_file=None, database_url=TEST_DATABASE_URL, secret_key="t" * 32
    )


@pytest.fixture(scope="session")
def engine(settings: Settings) -> Iterator[Engine]:
    werk = create_engine_from_settings(settings)
    Base.metadata.drop_all(werk)
    Base.metadata.create_all(werk)
    yield werk
    Base.metadata.drop_all(werk)
    werk.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    """Jeder Test laeuft in einer Transaktion, die anschliessend zurueckgerollt wird.

    Grenze dieser Isolation: Zurueckgerollt werden Datenaenderungen, nicht der Zaehler
    fuer Auto-Increment-Schluessel — und unter MariaDB fuehrt DDL zu einem impliziten
    Commit. Tests duerfen sich deshalb nicht auf bestimmte Kennungswerte verlassen und
    keine Schemaaenderungen vornehmen. Der Preis dieser Loesung ist bewusst gewaehlt:
    ein Schemaaufbau je Test waere unter MariaDB unertraeglich langsam.

    Dadurch teilen sich alle Tests ein Schema, ohne einander zu beeinflussen — unter
    MariaDB waere ein Neuaufbau je Test sonst spuerbar langsam.
    """
    verbindung = engine.connect()
    transaktion = verbindung.begin()
    sitzung = session_factory(engine)(bind=verbindung)
    try:
        yield sitzung
    finally:
        sitzung.close()
        transaktion.rollback()
        verbindung.close()
