"""Migration `d8e3a51c7f24`: `self_regulating` (Boolean) -> `thermostat_mode` (String)."""

import pytest
from sqlalchemy import create_engine, inspect, text

from tests.test_migrations import _alembic

_PREVIOUS = "c4a9e2f71b35"


def _modes(url: str) -> dict[int, str]:
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            return {
                row[0]: row[1]
                for row in connection.execute(
                    text("SELECT sort_order, thermostat_mode FROM zone_device")
                )
            }
    finally:
        engine.dispose()


@pytest.mark.migration
def test_flag_becomes_mode_and_reading_only_downgrades_to_self_regulating(
    migrations_database_url: str,
) -> None:
    assert _alembic(migrations_database_url, "downgrade", "base").returncode == 0
    up = _alembic(migrations_database_url, "upgrade", _PREVIOUS)
    assert up.returncode == 0, up.stderr

    engine = create_engine(migrations_database_url)
    try:
        with engine.begin() as connection:
            tables = inspect(connection).get_table_names()
            assert "zone_device" in tables
            columns = {c["name"] for c in inspect(connection).get_columns("zone_device")}
            assert "self_regulating" in columns and "thermostat_mode" not in columns
    finally:
        engine.dispose()
    _seed_assignments(migrations_database_url, {1: False, 2: True})

    up = _alembic(migrations_database_url, "upgrade", "head")
    assert up.returncode == 0, up.stderr
    engine = create_engine(migrations_database_url)
    try:
        columns = {c["name"] for c in inspect(engine).get_columns("zone_device")}
        assert "thermostat_mode" in columns and "self_regulating" not in columns
    finally:
        engine.dispose()
    assert _modes(migrations_database_url) == {1: "controlled", 2: "self_regulating"}

    engine = create_engine(migrations_database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE zone_device SET thermostat_mode = 'reading_only' "
                    "WHERE sort_order = 1"
                )
            )
    finally:
        engine.dispose()

    down = _alembic(migrations_database_url, "downgrade", _PREVIOUS)
    assert down.returncode == 0, down.stderr
    engine = create_engine(migrations_database_url)
    try:
        with engine.connect() as connection:
            flags = {
                row[0]: bool(row[1])
                for row in connection.execute(
                    text("SELECT sort_order, self_regulating FROM zone_device")
                )
            }
    finally:
        engine.dispose()
    # reading_only ist verlustbehaftet: das alte Modell kennt nur `self_regulating`.
    assert flags == {1: True, 2: True}

    again = _alembic(migrations_database_url, "upgrade", "head")
    assert again.returncode == 0, again.stderr


def _seed_assignments(url: str, flags: dict[int, bool]) -> None:
    """Legt je Eintrag in `flags` (sort_order -> self_regulating) eine Zuordnung an."""
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            integration_id = connection.execute(text("SELECT id FROM integration")).first()
            role_id = connection.execute(text("SELECT id FROM device_role")).first()
            assert integration_id is not None and role_id is not None
            mode_id = connection.execute(text("SELECT id FROM operating_mode")).first()
            assert mode_id is not None
            connection.execute(
                text(
                    "INSERT INTO zone (name, display_name, operating_mode_id, sort_order, "
                    "created_at, updated_at, hysteresis_k, min_on_seconds) VALUES "
                    "('migzone', 'Migzone', :m, 1, '2026-10-01 00:00:00', "
                    "'2026-10-01 00:00:00', 0.5, 300)"
                ),
                {"m": mode_id[0]},
            )
            zone_id = connection.execute(text("SELECT id FROM zone")).scalar_one()
            for order, flag in flags.items():
                connection.execute(
                    text(
                        "INSERT INTO device (integration_id, external_id, display_name, "
                        "is_enabled) VALUES (:i, :e, :n, :t)"
                    ),
                    {"i": integration_id[0], "e": f"mig-{order}", "n": f"Mig {order}", "t": True},
                )
                device_id = connection.execute(
                    text("SELECT id FROM device WHERE external_id = :e"), {"e": f"mig-{order}"}
                ).scalar_one()
                connection.execute(
                    text(
                        "INSERT INTO zone_device (zone_id, device_id, device_role_id, "
                        "sort_order, self_regulating) VALUES (:z, :d, :r, :o, :f)"
                    ),
                    {"z": zone_id, "d": device_id, "r": role_id[0], "o": order, "f": flag},
                )
    finally:
        engine.dispose()
