"""Migration `a7c3e91d5b24`: Dashboard-Layout -- Tabellen, Kaskaden, Schranken, Rückweg."""

from typing import Any

import pytest
from sqlalchemy import Connection, create_engine, inspect, text
from sqlalchemy.exc import DBAPIError

from tests.test_migrations import _alembic
from thermoctl.db.models.dashboard import DESTINATIONS, METRICS, PERIODS_DAYS, TILE_KINDS, WIDTHS

PREVIOUS = "d8e3a51c7f24"
TABLES = {"dashboard_layout", "dashboard_section", "dashboard_tile"}

_TILE = (
    "INSERT INTO dashboard_tile (section_id, position, kind, zone_id, title, period_days, "
    "width, metric, destination, note) VALUES (:s, :p, :k, :z, '', :d, :w, :m, :t, '')"
)


_SECTION = "INSERT INTO dashboard_section (user_id, position, title) VALUES (:u, 0, 's')"


def _dash_user(connection: Connection) -> int:
    row = connection.execute(text("SELECT id FROM user WHERE username='dash'")).scalar_one()
    return int(row)


def _tile(connection: Connection, section_id: int, position: int, **values: Any) -> None:
    params: dict[str, Any] = {
        "s": section_id, "p": position, "k": "note", "z": None, "d": 1, "w": 4,
        "m": None, "t": None,
    }
    params.update(values)
    connection.execute(text(_TILE), params)


def _count(connection: Connection, table: str) -> int:
    assert table in TABLES
    return int(connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one())  # noqa: S608


def _rejected(engine: Any, **values: Any) -> bool:
    """Eine Kachel mit diesen Werten in eigener Transaktion: wird sie abgelehnt?"""
    try:
        with engine.begin() as connection:
            if engine.dialect.name == "sqlite":
                connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            user_id = _dash_user(connection)
            connection.execute(
                text(_SECTION.replace("(:u, 0,", "(:u, 99,")),
                {"u": user_id},
            )
            section_id = connection.execute(
                text("SELECT id FROM dashboard_section WHERE position = 99")
            ).scalar_one()
            _tile(connection, section_id, 0, **values)
            raise _Rollback
    except _Rollback:
        return False
    except DBAPIError:
        # SQLite und PostgreSQL melden eine verletzte CHECK-Bedingung als
        # IntegrityError, MariaDB als OperationalError -- beides ist die Ablehnung.
        return True


class _Rollback(Exception):
    """Macht die Probe-Transaktion rückgängig, ohne dass etwas bleibt."""


@pytest.mark.migration
def test_dashboard_tables_forward_backward_forward(migrations_database_url: str) -> None:
    reset = _alembic(migrations_database_url, "downgrade", "base")
    assert reset.returncode == 0, reset.stderr
    up = _alembic(migrations_database_url, "upgrade", "head")
    assert up.returncode == 0, up.stderr

    engine = create_engine(migrations_database_url)
    try:
        inspector = inspect(engine)
        assert TABLES <= set(inspector.get_table_names())
        assert {c["name"] for c in inspector.get_columns("dashboard_tile")} == {
            "id", "section_id", "position", "kind", "zone_id", "title", "period_days",
            "width", "metric", "destination", "note",
        }
        zone_fk = next(
            fk for fk in inspector.get_foreign_keys("dashboard_tile")
            if fk["referred_table"] == "zone"
        )
        assert zone_fk["options"].get("ondelete") == "CASCADE"

        with engine.begin() as connection:
            if engine.dialect.name == "sqlite":
                connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            mode_id = connection.execute(
                text("SELECT id FROM operating_mode ORDER BY id LIMIT 1")
            ).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO zone (name, display_name, operating_mode_id, sort_order, "
                    "created_at, updated_at) VALUES ('dash-zone', 'Z', :m, 0, "
                    "'2026-10-16 10:00:00', '2026-10-16 10:00:00')"
                ),
                {"m": mode_id},
            )
            connection.execute(
                text(
                    "INSERT INTO user (username, display_name, password_hash, is_active, "
                    "created_at) VALUES ('dash', 'D', 'x', true, '2026-10-16 10:00:00')"
                )
            )

        # Jede im Modell erlaubte Ausprägung geht durch; alles andere nicht.
        for kind in TILE_KINDS:
            assert not _rejected(engine, k=kind), kind
        for width in WIDTHS:
            assert not _rejected(engine, w=width), width
        for days in PERIODS_DAYS:
            assert not _rejected(engine, d=days), days
        for metric in METRICS:
            assert not _rejected(engine, m=metric), metric
        for destination in DESTINATIONS:
            assert not _rejected(engine, t=destination), destination
        assert _rejected(engine, k="unbekannt")
        assert _rejected(engine, w=5)
        assert _rejected(engine, d=2)
        assert _rejected(engine, m="kaffee")
        assert _rejected(engine, t="/admin")
        assert _rejected(engine, p=-1)

        with engine.begin() as connection:
            if engine.dialect.name == "sqlite":
                connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            user_id = _dash_user(connection)
            zone_id = connection.execute(
                text("SELECT id FROM zone WHERE name='dash-zone'")
            ).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO dashboard_layout (user_id, version, customized, updated_at) "
                    "VALUES (:u, 1, true, '2026-10-16 10:00:00')"
                ),
                {"u": user_id},
            )
            connection.execute(text(_SECTION), {"u": user_id})
            section_id = connection.execute(text("SELECT id FROM dashboard_section")).scalar_one()
            _tile(connection, section_id, 0, k="room", z=zone_id)
            _tile(connection, section_id, 1)
            # Die Zone zu löschen nimmt ihre Kachel mit, nicht die andere.
            connection.execute(text("DELETE FROM zone WHERE id = :z"), {"z": zone_id})
            assert _count(connection, "dashboard_tile") == 1
            # Der Abschnitt nimmt seine Kacheln mit, der Benutzer Abschnitte und Layout.
            connection.execute(text("DELETE FROM user WHERE id = :u"), {"u": user_id})
            assert _count(connection, "dashboard_section") == 0
            assert _count(connection, "dashboard_tile") == 0
            assert _count(connection, "dashboard_layout") == 0
    finally:
        engine.dispose()

    down = _alembic(migrations_database_url, "downgrade", PREVIOUS)
    assert down.returncode == 0, down.stderr
    engine = create_engine(migrations_database_url)
    try:
        names = set(inspect(engine).get_table_names())
        assert not (TABLES & names)
        assert {"zone", "user"} <= names
    finally:
        engine.dispose()
    again = _alembic(migrations_database_url, "upgrade", "head")
    assert again.returncode == 0, again.stderr
