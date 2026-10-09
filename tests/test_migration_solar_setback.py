"""Migration `f6c3a8d19b42`: `shadow_decision` bekommt die Sonnenabsenkung strukturiert."""

from decimal import Decimal

import pytest
from sqlalchemy import create_engine, inspect, text

from tests.test_migrations import _alembic


@pytest.mark.migration
def test_solar_setback_columns_upgrade_keep_old_rows_null_and_downgrade_cleanly(
    migrations_database_url: str,
) -> None:
    """Alte Zeilen bleiben NULL (nichts wird aus dem Text geraten), die Spalten nehmen
    danach Werte an, und der Downgrade entfernt Spalten *und* Index, ohne die Zeile zu
    verlieren."""
    reset = _alembic(migrations_database_url, "downgrade", "base")
    assert reset.returncode == 0, reset.stderr
    before = _alembic(migrations_database_url, "upgrade", "e5b92d7f3a18")
    assert before.returncode == 0, before.stderr

    db_engine = create_engine(migrations_database_url)
    try:
        with db_engine.begin() as connection:
            operating_mode_id = connection.execute(
                text("SELECT id FROM operating_mode ORDER BY id LIMIT 1")
            ).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO zone "
                    "(name, display_name, operating_mode_id, sort_order, created_at, updated_at) "
                    "VALUES ('absenkung-migration', 'Absenkung', :mode_id, 0, "
                    "'2026-10-09 10:00:00', '2026-10-09 10:00:00')"
                ),
                {"mode_id": operating_mode_id},
            )
            zone_id = connection.execute(
                text("SELECT id FROM zone WHERE name = 'absenkung-migration'")
            ).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO shadow_decision "
                    "(decided_at, zone_id, setpoint_reason, would_heat, outcome_code, reason) "
                    "VALUES ('2026-10-09 10:00:00', :zone_id, "
                    "'Zeitplan Sonnenabsenkung: -2,0 K', false, 'aus', 'Sollwert erreicht')"
                ),
                {"zone_id": zone_id},
            )
        assert "solar_setback_k" not in {
            column["name"] for column in inspect(db_engine).get_columns("shadow_decision")
        }

        up = _alembic(migrations_database_url, "upgrade", "head")
        assert up.returncode == 0, up.stderr
        with db_engine.begin() as connection:
            old = connection.execute(
                text("SELECT scheduled_setpoint_c, solar_setback_k FROM shadow_decision")
            ).one()
            assert tuple(old) == (None, None)
            connection.execute(
                text(
                    "INSERT INTO shadow_decision "
                    "(decided_at, zone_id, setpoint_reason, would_heat, outcome_code, reason, "
                    "scheduled_setpoint_c, solar_setback_k) "
                    "VALUES ('2026-10-09 10:01:00', :zone_id, 'Zeitplan', false, 'aus', "
                    "'Sollwert erreicht', 20.50, 2.0)"
                ),
                {"zone_id": zone_id},
            )
            new = connection.execute(
                text(
                    "SELECT scheduled_setpoint_c, solar_setback_k FROM shadow_decision "
                    "WHERE solar_setback_k IS NOT NULL"
                )
            ).one()
            assert tuple(new) == (Decimal("20.50"), Decimal("2.0"))
        assert "ix_shadow_decision_solar_setback" in {
            index["name"] for index in inspect(db_engine).get_indexes("shadow_decision")
        }

        down = _alembic(migrations_database_url, "downgrade", "-1")
        assert down.returncode == 0, down.stderr
        names = {column["name"] for column in inspect(db_engine).get_columns("shadow_decision")}
        assert "solar_setback_k" not in names
        assert "scheduled_setpoint_c" not in names
        assert "ix_shadow_decision_solar_setback" not in {
            index["name"] for index in inspect(db_engine).get_indexes("shadow_decision")
        }
        with db_engine.connect() as connection:
            assert connection.execute(text("SELECT COUNT(*) FROM shadow_decision")).scalar() == 2

        again = _alembic(migrations_database_url, "upgrade", "head")
        assert again.returncode == 0, again.stderr
    finally:
        db_engine.dispose()
