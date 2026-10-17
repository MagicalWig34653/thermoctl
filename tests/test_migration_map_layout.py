"""Migration `c5e1a9d3b072`: Anordnung der Gerätekarte -- Tabellen, Schranken, Kaskade, Rückweg."""

from typing import Any

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DBAPIError

from tests.test_migrations import _alembic
from thermoctl.db.models.map_layout import COORD_LIMIT, ZOOM_MAX, ZOOM_MIN

PREVIOUS = "a7c3e91d5b24"
TABLES = {"map_layout", "map_layout_node", "map_layout_default", "map_layout_default_node"}


class _Rollback(Exception):
    """Macht die Probe-Transaktion rückgängig, ohne dass etwas bleibt."""


def _rejected(engine: Any, sql: str, **params: Any) -> bool:
    """Wird dieses Statement (in eigener, verworfener Transaktion) abgelehnt?"""
    try:
        with engine.begin() as connection:
            if engine.dialect.name == "sqlite":
                connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            connection.execute(text(sql), params)
            raise _Rollback
    except _Rollback:
        return False
    except DBAPIError:
        # SQLite meldet eine verletzte CHECK-Bedingung als IntegrityError, MariaDB als
        # OperationalError -- beides ist die Ablehnung.
        return True


_NODE = (
    "INSERT INTO map_layout_default_node (node_key, x, y, collapsed) "
    "VALUES (:k, :x, :y, false)"
)
_DEFAULT = (
    "INSERT INTO map_layout_default (id, version, customized, view_x, view_y, view_k, "
    "updated_at) VALUES (:i, 1, true, :vx, :vy, :vk, '2026-10-17 10:00:00')"
)


@pytest.mark.migration
def test_map_layout_tables_forward_backward_forward(migrations_database_url: str) -> None:
    reset = _alembic(migrations_database_url, "downgrade", "base")
    assert reset.returncode == 0, reset.stderr
    up = _alembic(migrations_database_url, "upgrade", "head")
    assert up.returncode == 0, up.stderr

    engine = create_engine(migrations_database_url)
    try:
        inspector = inspect(engine)
        assert TABLES <= set(inspector.get_table_names())
        assert {c["name"] for c in inspector.get_columns("map_layout")} == {
            "user_id", "version", "customized", "view_x", "view_y", "view_k", "updated_at",
        }
        assert {c["name"] for c in inspector.get_columns("map_layout_node")} == {
            "id", "user_id", "node_key", "x", "y", "collapsed",
        }
        # Der Knotenschlüssel ist bewusst kein Fremdschlüssel (gelöschte Geräte).
        node_fks = inspector.get_foreign_keys("map_layout_node")
        assert {fk["referred_table"] for fk in node_fks} == {"user"}
        assert inspector.get_foreign_keys("map_layout_default_node") == []

        # Lage: beide oder keine, innerhalb der Grenzen.
        assert not _rejected(engine, _NODE, k="dev-1", x=0.5, y=-COORD_LIMIT)
        assert not _rejected(engine, _NODE, k="dev-1", x=None, y=None)
        assert _rejected(engine, _NODE, k="dev-1", x=1.0, y=None)
        assert _rejected(engine, _NODE, k="dev-1", x=COORD_LIMIT + 1, y=0)
        # Ansicht: alle drei oder keine, Zoom in den Grenzen.
        assert not _rejected(engine, _DEFAULT, i=1, vx=1.0, vy=2.0, vk=ZOOM_MIN)
        assert not _rejected(engine, _DEFAULT, i=1, vx=1.0, vy=2.0, vk=ZOOM_MAX)
        assert not _rejected(engine, _DEFAULT, i=1, vx=None, vy=None, vk=None)
        assert _rejected(engine, _DEFAULT, i=1, vx=1.0, vy=None, vk=1.0)
        assert _rejected(engine, _DEFAULT, i=1, vx=1.0, vy=2.0, vk=ZOOM_MAX + 0.1)
        assert _rejected(engine, _DEFAULT, i=1, vx=1.0, vy=2.0, vk=ZOOM_MIN / 2)
        # Genau eine Kopfzeile der Vorgabe.
        assert _rejected(engine, _DEFAULT, i=2, vx=None, vy=None, vk=None)

        with engine.begin() as connection:
            if engine.dialect.name == "sqlite":
                connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            connection.execute(
                text(
                    "INSERT INTO user (username, display_name, password_hash, is_active, "
                    "created_at) VALUES ('karte', 'K', 'x', true, '2026-10-17 10:00:00')"
                )
            )
            user_id = connection.execute(
                text("SELECT id FROM user WHERE username='karte'")
            ).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO map_layout (user_id, version, customized, updated_at) "
                    "VALUES (:u, 1, true, '2026-10-17 10:00:00')"
                ),
                {"u": user_id},
            )
            node = (
                "INSERT INTO map_layout_node (user_id, node_key, x, y, collapsed) "
                "VALUES (:u, 'dev-1', 0, 0, false)"
            )
            connection.execute(text(node), {"u": user_id})
        # Ein Knoten je Schlüssel und Benutzer.
        assert _rejected(engine, node, u=user_id)
        # Mit dem Benutzer verschwinden Kopf und Knoten.
        with engine.begin() as connection:
            if engine.dialect.name == "sqlite":
                connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            connection.execute(text("DELETE FROM user WHERE id = :u"), {"u": user_id})
            for table in ("map_layout", "map_layout_node"):
                assert connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one() == 0  # noqa: S608
    finally:
        engine.dispose()

    down = _alembic(migrations_database_url, "downgrade", PREVIOUS)
    assert down.returncode == 0, down.stderr
    engine = create_engine(migrations_database_url)
    try:
        names = set(inspect(engine).get_table_names())
        assert not (TABLES & names)
        assert {"user", "dashboard_layout"} <= names
    finally:
        engine.dispose()
    again = _alembic(migrations_database_url, "upgrade", "head")
    assert again.returncode == 0, again.stderr
