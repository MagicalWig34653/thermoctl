"""Migration `d4b8f2a60c19`: Kachelart `control` -- Rückweg entfernt sie, Übriges bleibt.

Dass `control` nach dem Hochziehen erlaubt ist, deckt `test_migration_dashboard.py` ab (es
läuft über alle `TILE_KINDS`); hier geht es um den Weg zurück.
"""

import pytest
from sqlalchemy import create_engine, text

from tests.test_migrations import _alembic

PREVIOUS = "c5e1a9d3b072"
HEAD = "d4b8f2a60c19"


@pytest.mark.migration
def test_downgrade_removes_control_tiles_only(migrations_database_url: str) -> None:
    assert _alembic(migrations_database_url, "downgrade", "base").returncode == 0
    up = _alembic(migrations_database_url, "upgrade", HEAD)
    assert up.returncode == 0, up.stderr
    engine = create_engine(migrations_database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO user (username, display_name, password_hash, is_active, "
                    "created_at) VALUES ('dash-ctl', 'D', 'x', true, '2026-10-17 10:00:00')"
                )
            )
            user_id = connection.execute(
                text("SELECT id FROM user WHERE username = 'dash-ctl'")
            ).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO dashboard_section (user_id, position, title) "
                    "VALUES (:u, 0, 'S')"
                ),
                {"u": user_id},
            )
            section_id = connection.execute(
                text("SELECT id FROM dashboard_section WHERE user_id = :u"), {"u": user_id}
            ).scalar_one()
            mode_id = connection.execute(
                text("SELECT id FROM operating_mode ORDER BY id LIMIT 1")
            ).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO zone (name, display_name, operating_mode_id, sort_order, "
                    "created_at, updated_at) VALUES ('ctl-zone', 'Z', :m, 0, "
                    "'2026-10-17 10:00:00', '2026-10-17 10:00:00')"
                ),
                {"m": mode_id},
            )
            zone_id = connection.execute(
                text("SELECT id FROM zone WHERE name = 'ctl-zone'")
            ).scalar_one()
            for position, kind in enumerate(("control", "room")):
                connection.execute(
                    text(
                        "INSERT INTO dashboard_tile (section_id, position, kind, zone_id, "
                        "title, period_days, width, note) VALUES "
                        "(:s, :p, :k, :z, '', 1, 6, '')"
                    ),
                    {"s": section_id, "p": position, "k": kind, "z": zone_id},
                )

        down = _alembic(migrations_database_url, "downgrade", PREVIOUS)
        assert down.returncode == 0, down.stderr
        with engine.connect() as connection:
            kinds = [
                row[0] for row in connection.execute(text("SELECT kind FROM dashboard_tile"))
            ]
        assert kinds == ["room"]
    finally:
        engine.dispose()
        _alembic(migrations_database_url, "upgrade", "head")
