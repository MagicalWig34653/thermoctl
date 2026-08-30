"""Checks at startup whether the database schema matches the code.

Without this check, the service fails at the first query of its lifecycle with a
sixty-line SQLAlchemy traceback whose most informative line reads `no such table:
user` -- correct, but useless: it does not say that a migration is missing, and
certainly not which command helps. An outdated schema is even more unpleasant,
because it does not fail at startup but at some later point, at a column that does
not yet exist.

The container migrates itself (`docker/entrypoint.sh`), a local `uvicorn` start does
not. That is exactly where this case occurs.
"""

import logging
from pathlib import Path

from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import SQLAlchemyError

log = logging.getLogger(__name__)

COMMAND = "alembic upgrade head"


class SchemaPasstNicht(RuntimeError):
    """The service cannot start because the schema is missing or outdated."""


def _migration_head() -> str | None:
    """The target revision according to the migration directory, or None if it
    cannot be found.

    The directory lives outside the package and is only shipped where it is
    needed (repository, container image). Whoever installs thermoctl merely as a
    wheel does not have it -- the comparison is then simply skipped, instead of
    triggering a false alarm.
    """
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory
    except ImportError:  # pragma: no cover - alembic is a hard dependency
        return None

    ini = Path("alembic.ini")
    if not ini.is_file():
        return None
    try:
        verzeichnis = ScriptDirectory.from_config(Config(str(ini)))
        koepfe = verzeichnis.get_heads()
    except Exception:  # pragma: no cover - a broken configuration is no reason to abort startup
        return None
    # Multiple heads would be an error in the history, not in this database.
    return koepfe[0] if len(koepfe) == 1 else None


def database_state(engine: Engine) -> str | None:
    """The recorded revision, or None if the database has no schema."""
    try:
        if not inspect(engine).has_table("alembic_version"):
            return None
        with engine.connect() as verbindung:
            entry = verbindung.scalar(text("SELECT version_num FROM alembic_version"))
            return str(entry) if entry is not None else None
    except SQLAlchemyError:  # pragma: no cover - an unreachable database reports itself
        return None


def check_schema(engine: Engine) -> None:
    """Aborts startup if the schema is missing or lags behind the code.

    If the head cannot be determined, only the absence of the schema is checked --
    better one check fewer than one that blocks at the wrong moment.
    """
    state = database_state(engine)
    if state is None:
        if not inspect(engine).has_table("user"):
            raise SchemaPasstNicht(
                f"Die Datenbank hat kein Schema. Vor dem ersten Start einmal '{COMMAND}' "
                "ausfuehren; das Container-Abbild erledigt das selbst."
            )
        # Tables without an Alembic stamp: this is how the test suite creates its
        # schema (`Base.metadata.create_all()`). This is no reason to abort startup --
        # only the version comparison then has no basis.
        log.warning(
            "Das Schema wurde nicht ueber Alembic angelegt, der Versionsvergleich entfaellt"
        )
        return

    kopf = _migration_head()
    if kopf is None:
        log.debug("Migrationsverzeichnis nicht gefunden, Versionsvergleich entfaellt")
        return
    if state != kopf:
        raise SchemaPasstNicht(
            f"Das Datenbankschema steht auf {state}, der Code erwartet {kopf}. "
            f"'{COMMAND}' ausfuehren."
        )
