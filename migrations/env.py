from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from thermoctl.config import get_settings
from thermoctl.db.base import Base
from thermoctl.db import models  # noqa: F401 — lädt alle Modelle in die Metadaten
from thermoctl.db.migration_lock import migration_lock

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", get_settings().database_url)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    verbindungswerk = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with verbindungswerk.connect() as verbindung:
        # Verhindert, dass eine zweite, gleichzeitig gestartete Nachbildung
        # dieselbe Datenbank parallel migriert (docs/STATUS.md, offener Punkt).
        # Wirkt für jeden Alembic-Aufruf, auch von Hand -- nicht nur beim
        # Containerstart -- weil sie hier sitzt und nicht im Entrypoint.
        with migration_lock(verbindung, config.get_main_option("sqlalchemy.url")):
            context.configure(
                connection=verbindung,
                target_metadata=target_metadata,
                # Pflicht für SQLite: dort gibt es kein ALTER TABLE für Constraints,
                # Alembic baut die Tabelle stattdessen neu auf.
                render_as_batch=True,
            )
            with context.begin_transaction():
                context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
