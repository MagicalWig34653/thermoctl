"""Migration `c4a9e2f71b35`: Änderungsprotokoll -- Tabellen, Fremdschlüssel, Rückwärtsrichtung."""

import pytest
from sqlalchemy import create_engine, inspect, text

from tests.test_migrations import _alembic


@pytest.mark.migration
def test_change_log_tables_forward_backward_forward(migrations_database_url: str) -> None:
    reset = _alembic(migrations_database_url, "downgrade", "base")
    assert reset.returncode == 0, reset.stderr
    up = _alembic(migrations_database_url, "upgrade", "head")
    assert up.returncode == 0, up.stderr

    engine = create_engine(migrations_database_url)
    try:
        inspector = inspect(engine)
        assert {"change_log", "change_log_field"} <= set(inspector.get_table_names())
        columns = {c["name"]: c for c in inspector.get_columns("change_log")}
        assert {
            "occurred_at",
            "source_id",
            "actor_user_id",
            "actor_token_id",
            "change_type",
            "zone_id",
            "zone_name",
            "object_ref",
            "summary",
            "revertible",
            "not_revertible_reason",
            "reverted_at",
            "reverted_by_id",
            "reverts_id",
            "forced",
        } <= set(columns)
        indexes = {i["name"] for i in inspector.get_indexes("change_log")}
        assert {
            "ix_change_log_occurred_at",
            "ix_change_log_change_type",
            "ix_change_log_zone_id_occurred_at",
        } <= indexes

        with engine.begin() as connection:
            if engine.dialect.name == "sqlite":
                # SQLite setzt Fremdschlüssel nur auf Anforderung durch (die Anwendung tut es).
                connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            source_id = connection.execute(
                text("SELECT id FROM actor_source WHERE code = 'mcp'")
            ).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO change_log (occurred_at, source_id, change_type, summary, "
                    "revertible, forced) VALUES ('2026-10-11 08:00:00', :s, 'vacation', 'x', "
                    "true, false)"
                ),
                {"s": source_id},
            )
            change_id = connection.execute(text("SELECT id FROM change_log")).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO change_log_field (change_id, field, after_value) "
                    "VALUES (:c, 'ends_at', '2026-10-12T00:00:00Z')"
                ),
                {"c": change_id},
            )
            # CASCADE: mit dem Eintrag verschwinden seine Felder.
            connection.execute(text("DELETE FROM change_log WHERE id = :c"), {"c": change_id})
            assert connection.execute(text("SELECT COUNT(*) FROM change_log_field")).scalar() == 0
    finally:
        engine.dispose()

    down = _alembic(migrations_database_url, "downgrade", "b721e49c6a30")
    assert down.returncode == 0, down.stderr
    engine = create_engine(migrations_database_url)
    try:
        names = set(inspect(engine).get_table_names())
        assert "change_log" not in names and "change_log_field" not in names
        assert "audit_event" in names
    finally:
        engine.dispose()
    again = _alembic(migrations_database_url, "upgrade", "head")
    assert again.returncode == 0, again.stderr
