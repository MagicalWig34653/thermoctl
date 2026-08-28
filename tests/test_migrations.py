import subprocess

import pytest
from sqlalchemy import Engine, inspect

from tests.conftest import TEST_DATABASE_URL


def _alembic(*argumente: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["alembic", *argumente],
        env={"THERMOCTL_DATABASE_URL": TEST_DATABASE_URL, "THERMOCTL_SECRET_KEY": "t" * 32,
             "PATH": "/usr/bin:/bin:/usr/local/bin:.venv/bin"},
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.migration
def test_migration_vorwaerts_und_rueckwaerts() -> None:
    hoch = _alembic("upgrade", "head")
    assert hoch.returncode == 0, hoch.stderr
    runter = _alembic("downgrade", "base")
    assert runter.returncode == 0, runter.stderr
    wieder_hoch = _alembic("upgrade", "head")
    assert wieder_hoch.returncode == 0, wieder_hoch.stderr


@pytest.mark.migration
def test_modelle_und_migrationen_stimmen_ueberein() -> None:
    """`alembic check` meldet, wenn ein Modell ohne Migration geaendert wurde."""
    _alembic("upgrade", "head")
    ergebnis = _alembic("check")
    assert ergebnis.returncode == 0, ergebnis.stdout + ergebnis.stderr


def test_fremdschluessel_werden_unter_sqlite_geprueft(engine: Engine) -> None:
    if engine.dialect.name != "sqlite":
        pytest.skip("nur fuer SQLite sinnvoll")
    with engine.connect() as verbindung:
        assert verbindung.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
