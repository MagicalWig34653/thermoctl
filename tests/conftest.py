import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import URL, Engine, create_engine, make_url, text
from sqlalchemy.orm import Session

from thermoctl.config import Settings
from thermoctl.db.base import Base
from thermoctl.db.engine import create_engine_from_settings

TEST_DATABASE_URL = os.environ.get("THERMOCTL_TEST_DATABASE_URL", "sqlite:///./test.db")


def _migrationsdatenbank_url(basis_url: str) -> str:
    """Leitet die Datenbank fuer die Migrationstests von ``TEST_DATABASE_URL`` ab.

    Die Migrationstests fuehren ``alembic upgrade``/``downgrade`` gegen eine **eigene**
    Datenbank aus, getrennt von der Fixture ``engine``: Sonst legt ``Base.metadata.create_all()``
    dieselben Tabellen an, die Alembic ebenfalls anlegen will, und die Migration scheitert an
    einer bereits vorhandenen Tabelle. Die Ableitung aus ``TEST_DATABASE_URL`` statt einer
    zweiten Konfiguration stellt sicher, dass die Migrationstests niemals unbemerkt gegen eine
    andere Datenbank laufen als der Rest der Suite.
    """
    url = make_url(basis_url)
    if url.get_backend_name() == "sqlite":
        if not url.database or url.database == ":memory:":
            # Eine In-Memory-Datenbank gehoert ohnehin genau einem Prozess. Die
            # Migrationstests laufen als eigener Unterprozess und bekommen deshalb
            # eine eigene, leere Datenbank — eine abgeleitete URL waere hier
            # gegenstandslos.
            return basis_url
        pfad = Path(url.database)
        neuer_pfad = pfad.with_name(f"{pfad.stem}-migrations{pfad.suffix}")
        return url.set(database=str(neuer_pfad)).render_as_string(hide_password=False)
    return url.set(database=f"{url.database}_migrations").render_as_string(hide_password=False)


MIGRATIONS_DATABASE_URL = _migrationsdatenbank_url(TEST_DATABASE_URL)


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings(
        _env_file=None, database_url=TEST_DATABASE_URL, secret_key="t" * 32
    )


@pytest.fixture(scope="session")
def migrations_database_url() -> Iterator[str]:
    """Stellt sicher, dass die Migrationsdatenbank existiert, und liefert ihre URL.

    Unter MariaDB existiert das Schema fuer die Migrationstests vor dem ersten Lauf
    noch nicht — es wird hier per ``CREATE DATABASE IF NOT EXISTS`` selbst angelegt.
    Unter SQLite legt die Datei-URL die Datenbank beim ersten Verbindungsaufbau
    automatisch an, hier ist nichts vorzubereiten.
    """
    ziel_url = make_url(MIGRATIONS_DATABASE_URL)
    if ziel_url.get_backend_name() != "sqlite":
        server_url = URL.create(
            ziel_url.drivername,
            username=ziel_url.username,
            password=ziel_url.password,
            host=ziel_url.host,
            port=ziel_url.port,
        )
        server_werk = create_engine(server_url, pool_pre_ping=True, future=True)
        try:
            with server_werk.connect() as verbindung:
                verbindung.execute(text(f"CREATE DATABASE IF NOT EXISTS `{ziel_url.database}`"))
                verbindung.commit()
        finally:
            server_werk.dispose()

    yield MIGRATIONS_DATABASE_URL

    # Symmetrisch zur Fixture `engine`, die ihre Tabellen wieder entfernt: Bleibt die
    # Migrationsdatenbank liegen, laeuft der naechste Durchlauf gegen einen alten
    # Schemastand und scheitert an etwas, das mit dem Code nichts zu tun hat.
    if ziel_url.get_backend_name() == "sqlite":
        if ziel_url.database and ziel_url.database != ":memory:":
            Path(ziel_url.database).unlink(missing_ok=True)
    else:
        server_werk = create_engine(server_url, pool_pre_ping=True, future=True)
        try:
            with server_werk.connect() as verbindung:
                verbindung.execute(text(f"DROP DATABASE IF EXISTS `{ziel_url.database}`"))
                verbindung.commit()
        finally:
            server_werk.dispose()


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

    Die Sitzung tritt der aeusseren Transaktion ueber ein Savepoint bei
    (``join_transaction_mode="create_savepoint"``). Loest ein Test absichtlich einen
    Fehler aus (z. B. einen ``IntegrityError`` bei einer Constraint-Verletzung) und die
    Sitzung rollt deshalb zurueck, betrifft das nur das Savepoint — die aeussere
    Transaktion bleibt bestehen und laesst sich im Teardown noch zurueckrollen.

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
    sitzung = Session(
        bind=verbindung, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )
    try:
        yield sitzung
    finally:
        sitzung.close()
        transaktion.rollback()
        verbindung.close()
