import os
import subprocess
import sys

import pytest
from sqlalchemy import Engine


def _alembic(url: str, *argumente: str) -> subprocess.CompletedProcess[str]:
    """Ruft Alembic als Unterprozess, damit echte Migrationslaeufe geprueft werden.

    Aufruf ueber ``sys.executable -m alembic`` und nicht ueber das Skript ``alembic``:
    Nur so liegt das Projektverzeichnis im Modulpfad des Unterprozesses. Beim
    Skriptaufruf beginnt der Modulpfad in ``.venv/bin``, und das Paket ``thermoctl``
    ist dann nur ueber die ``.pth`` des editierbaren Installs auffindbar — die
    unter macOS das Flag ``hidden`` tragen kann und dann beim Start uebersprungen
    wird. Der Umweg ueber ``-m`` macht den Test unabhaengig davon, wie das venv
    eingerichtet wurde.

    Laeuft gegen ``url`` statt gegen ``TEST_DATABASE_URL``: Die Migrationstests brauchen
    eine eigene Datenbank, getrennt von der Fixture ``engine`` — sonst legt Alembic
    Tabellen an, die ``Base.metadata.create_all()`` schon erzeugt hat.
    """
    umgebung = {
        **os.environ,
        "THERMOCTL_DATABASE_URL": url,
        "THERMOCTL_SECRET_KEY": "t" * 32,
    }
    return subprocess.run(
        [sys.executable, "-m", "alembic", *argumente],
        env=umgebung,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.migration
def test_migration_vorwaerts_und_rueckwaerts(migrations_database_url: str) -> None:
    hoch = _alembic(migrations_database_url, "upgrade", "head")
    assert hoch.returncode == 0, hoch.stderr
    runter = _alembic(migrations_database_url, "downgrade", "base")
    assert runter.returncode == 0, runter.stderr
    wieder_hoch = _alembic(migrations_database_url, "upgrade", "head")
    assert wieder_hoch.returncode == 0, wieder_hoch.stderr


@pytest.mark.migration
def test_modelle_und_migrationen_stimmen_ueberein(migrations_database_url: str) -> None:
    """`alembic check` meldet, wenn ein Modell ohne Migration geaendert wurde."""
    vorbereitung = _alembic(migrations_database_url, "upgrade", "head")
    assert vorbereitung.returncode == 0, vorbereitung.stderr
    ergebnis = _alembic(migrations_database_url, "check")
    assert ergebnis.returncode == 0, ergebnis.stdout + ergebnis.stderr


def test_fremdschluessel_werden_unter_sqlite_geprueft(engine: Engine) -> None:
    if engine.dialect.name != "sqlite":
        pytest.skip("nur fuer SQLite sinnvoll")
    with engine.connect() as verbindung:
        assert verbindung.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
