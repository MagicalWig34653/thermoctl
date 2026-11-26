"""Migration `c7e2a9f4d813`: Kachelart `controller`, `show_pi`, Index für den letzten Reset.

Der Rückweg entfernt nur die neuen Kacheln; alles Übrige bleibt stehen.
"""

# ruff: noqa: S608 -- die Tabellennamen in den Abfragen sind feste Konstanten dieses Tests
import pytest
from sqlalchemy import create_engine, inspect, text

from tests.test_migrations import _alembic

PREVIOUS = "b5d1f8a3c264"
HEAD = "c7e2a9f4d813"
INDEX = "ix_shadow_decision_zone_integrator_decided"


@pytest.mark.migration
def test_upgrade_adds_tile_kind_column_and_index_and_downgrade_removes_them(
    migrations_database_url: str,
) -> None:
    assert _alembic(migrations_database_url, "downgrade", "base").returncode == 0
    up = _alembic(migrations_database_url, "upgrade", HEAD)
    assert up.returncode == 0, up.stderr
    engine = create_engine(migrations_database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO user (username, display_name, password_hash, is_active, "
                    "created_at) VALUES ('dash-pi', 'D', 'x', true, '2026-11-26 10:00:00')"
                )
            )
            user_id = connection.execute(
                text("SELECT id FROM user WHERE username = 'dash-pi'")
            ).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO dashboard_section (user_id, position, title) "
                    "VALUES (:u, 0, 'S')"
                ),
                {"u": user_id},
            )
            connection.execute(
                text(
                    "INSERT INTO dashboard_template (name, updated_at) "
                    "VALUES ('pi', '2026-11-26 10:00:00')"
                )
            )
            template_id = connection.execute(
                text("SELECT id FROM dashboard_template WHERE name = 'pi'")
            ).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO dashboard_template_section (template_id, position, title) "
                    "VALUES (:t, 0, 'S')"
                ),
                {"t": template_id},
            )
            for table, parent in (
                ("dashboard_tile", "dashboard_section"),
                ("dashboard_template_tile", "dashboard_template_section"),
            ):
                section_id = connection.execute(
                    text(f"SELECT MAX(id) FROM {parent}")
                ).scalar_one()
                for position, kind in enumerate(("controller", "note")):
                    connection.execute(
                        text(
                            f"INSERT INTO {table} (section_id, position, kind, title, "
                            "period_days, width, note) VALUES (:s, :p, :k, '', 1, 6, '')"
                        ),
                        {"s": section_id, "p": position, "k": kind},
                    )
                # Ohne Angabe ist die neue Einstellung aus.
                assert [
                    bool(row[0])
                    for row in connection.execute(text(f"SELECT show_pi FROM {table}"))
                ] == [False, False]
        assert INDEX in {i["name"] for i in inspect(engine).get_indexes("shadow_decision")}
        engine.dispose()

        down = _alembic(migrations_database_url, "downgrade", PREVIOUS)
        assert down.returncode == 0, down.stderr
        with engine.connect() as connection:
            for table in ("dashboard_tile", "dashboard_template_tile"):
                kinds = [row[0] for row in connection.execute(text(f"SELECT kind FROM {table}"))]
                assert kinds == ["note"]
        inspector = inspect(engine)
        assert "show_pi" not in {c["name"] for c in inspector.get_columns("dashboard_tile")}
        assert INDEX not in {i["name"] for i in inspector.get_indexes("shadow_decision")}
    finally:
        engine.dispose()
        _alembic(migrations_database_url, "upgrade", "head")
