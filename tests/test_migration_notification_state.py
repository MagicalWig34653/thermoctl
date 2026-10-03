"""Migration `e5b92d7f3a18`: Zwischenzustaende fuer `sensor_failure_episode.notification_state`."""

from datetime import datetime

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from tests.test_migrations import _alembic


@pytest.mark.migration
def test_notification_state_intermediate_migration_upgrade_and_downgrade_with_data(
    migrations_database_url: str,
) -> None:
    """`e5b92d7f3a18` erweitert den CHECK-Constraint von
    `sensor_failure_episode.notification_state` um `melden_laeuft` und
    `entwarnung_laeuft`.

    Der Downgrade scheitert mit Zeilen in einem Zwischenzustand, wenn er sie nicht
    vorher umschreibt -- die leere Kette sieht das nicht. Erwartet: `melden_laeuft` ->
    `offen`, `entwarnung_laeuft` -> `gemeldet`, alle anderen Zeilen bleiben, und der
    alte Constraint gilt danach wieder.
    """
    reset = _alembic(migrations_database_url, "downgrade", "base")
    assert reset.returncode == 0, reset.stderr
    previous = _alembic(migrations_database_url, "upgrade", "d4a81c6e5b29")
    assert previous.returncode == 0, previous.stderr

    insert = text(
        "INSERT INTO sensor_failure_episode (zone_name, started_at, trigger_kind, "
        "profile_version, notification_state, fixed_on_seconds, fixed_off_seconds, "
        "recovery_seconds, recovery_samples, warm_restart_hysteresis_k, "
        "emergency_setpoint_c, sensor_timeout_seconds) "
        "VALUES (:name, :started, 'alle_quellen', 1, :state, 600, 1200, 60, 2, 1, 20, 1800)"
    )
    started = datetime(2026, 10, 1)
    db_engine = create_engine(migrations_database_url)
    try:
        with db_engine.begin() as connection:
            for name, state in (("a", "offen"), ("b", "gemeldet"), ("c", "entwarnung_gesendet")):
                connection.execute(insert, {"name": name, "started": started, "state": state})
        # The old constraint rejects the new values.
        with pytest.raises(DBAPIError), db_engine.begin() as connection:
            connection.execute(insert, {"name": "x", "started": started, "state": "melden_laeuft"})

        up = _alembic(migrations_database_url, "upgrade", "e5b92d7f3a18")
        assert up.returncode == 0, up.stderr
        with db_engine.begin() as connection:
            for name, state in (("d", "melden_laeuft"), ("e", "entwarnung_laeuft")):
                connection.execute(insert, {"name": name, "started": started, "state": state})
        with pytest.raises(DBAPIError), db_engine.begin() as connection:
            connection.execute(insert, {"name": "y", "started": started, "state": "kaputt"})

        def read() -> dict[str, str]:
            with db_engine.connect() as connection:
                return {
                    row[0]: row[1]
                    for row in connection.execute(
                        text("SELECT zone_name, notification_state FROM sensor_failure_episode")
                    )
                }

        assert read() == {
            "a": "offen",
            "b": "gemeldet",
            "c": "entwarnung_gesendet",
            "d": "melden_laeuft",
            "e": "entwarnung_laeuft",
        }

        down = _alembic(migrations_database_url, "downgrade", "-1")
        assert down.returncode == 0, down.stderr
        assert read() == {
            "a": "offen",
            "b": "gemeldet",
            "c": "entwarnung_gesendet",
            "d": "offen",
            "e": "gemeldet",
        }
        with pytest.raises(DBAPIError), db_engine.begin() as connection:
            connection.execute(insert, {"name": "z", "started": started, "state": "melden_laeuft"})
        with db_engine.connect() as connection:
            if connection.dialect.name == "sqlite":
                connection.exec_driver_sql("PRAGMA foreign_keys=ON")
                assert connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []
    finally:
        db_engine.dispose()
