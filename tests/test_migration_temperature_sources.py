"""Migration `c7e2a4f91d38`: mehrere Temperaturfühler je Zone.

Vorwärts wird jeder `zone.temperature_source_device_id` eine Zeile in
`zone_temperature_source` (Zonen ohne Quelle bekommen keine), die Spalte entfällt.
Rückwärts bleibt je Zone der Fühler mit kleinster `sort_order` -- verlustbehaftet.
"""

from typing import Any

import pytest
from sqlalchemy import Connection, create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from tests.test_migrations import _alembic

PREVIOUS = "b3f5d7a92c61"


def _engine(url: str) -> Engine:
    engine = create_engine(url)
    if engine.dialect.name == "sqlite":

        @event.listens_for(engine, "connect")
        def _foreign_keys_on(dbapi_connection: Any, _record: Any) -> None:
            dbapi_connection.execute("PRAGMA foreign_keys=ON")

    return engine


def _zone(connection: Connection, name: str, source: int | None) -> int:
    mode = connection.execute(
        text("SELECT id FROM operating_mode ORDER BY id LIMIT 1")
    ).scalar_one()
    columns = "name, display_name, operating_mode_id, sort_order, created_at, updated_at"
    values = ":n, 'Z', :m, 0, '2026-11-07 10:00:00', '2026-11-07 10:00:00'"
    params: dict[str, Any] = {"n": name, "m": mode}
    if source is not None:
        columns += ", temperature_source_device_id"
        values += ", :s"
        params["s"] = source
    connection.execute(text(f"INSERT INTO zone ({columns}) VALUES ({values})"), params)  # noqa: S608
    return int(
        connection.execute(text("SELECT id FROM zone WHERE name = :n"), {"n": name}).scalar_one()
    )


def _device(connection: Connection, name: str) -> int:
    integration = connection.execute(
        text("SELECT id FROM integration ORDER BY id LIMIT 1")
    ).scalar_one()
    connection.execute(
        text(
            "INSERT INTO device (integration_id, external_id, display_name, is_enabled) "
            "VALUES (:i, :e, :e, :t)"
        ),
        {"i": integration, "e": name, "t": True},
    )
    return int(
        connection.execute(
            text("SELECT id FROM device WHERE external_id = :e"), {"e": name}
        ).scalar_one()
    )


def _rows(connection: Connection) -> list[tuple[int, int, int]]:
    return [
        (int(r[0]), int(r[1]), int(r[2]))
        for r in connection.execute(
            text(
                "SELECT zone_id, device_id, sort_order FROM zone_temperature_source "
                "ORDER BY zone_id, sort_order, id"
            )
        )
    ]


@pytest.mark.migration
def test_upgrade_takes_over_sources_and_downgrade_keeps_first(
    migrations_database_url: str,
) -> None:
    reset = _alembic(migrations_database_url, "downgrade", "base")
    assert reset.returncode == 0, reset.stderr
    before = _alembic(migrations_database_url, "upgrade", PREVIOUS)
    assert before.returncode == 0, before.stderr

    engine = _engine(migrations_database_url)
    try:
        with engine.begin() as connection:
            dev1 = _device(connection, "ts-1")
            dev2 = _device(connection, "ts-2")
            with_source = _zone(connection, "ts-mit", dev1)
            without = _zone(connection, "ts-ohne", None)

        up = _alembic(migrations_database_url, "upgrade", "head")
        assert up.returncode == 0, up.stderr

        columns = {c["name"] for c in inspect(engine).get_columns("zone")}
        assert "temperature_source_device_id" not in columns
        with engine.begin() as connection:
            assert _rows(connection) == [(with_source, dev1, 0)]
            assert without not in {r[0] for r in _rows(connection)}

            # Eindeutigkeit: derselbe Fühler nicht zweimal in einer Zone.
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(
                    text(
                        "INSERT INTO zone_temperature_source (zone_id, device_id, sort_order) "
                        "VALUES (:z, :d, 5)"
                    ),
                    {"z": with_source, "d": dev1},
                )
            # Ein zweiter Fühler ist erlaubt; er steht vor dem ersten.
            connection.execute(
                text(
                    "INSERT INTO zone_temperature_source (zone_id, device_id, sort_order) "
                    "VALUES (:z, :d, -1)"
                ),
                {"z": with_source, "d": dev2},
            )

        down = _alembic(migrations_database_url, "downgrade", PREVIOUS)
        assert down.returncode == 0, down.stderr
        inspector = inspect(engine)
        assert "zone_temperature_source" not in inspector.get_table_names()
        assert "temperature_source_device_id" in {c["name"] for c in inspector.get_columns("zone")}
        with engine.connect() as connection:
            got = dict(
                connection.execute(
                    text("SELECT id, temperature_source_device_id FROM zone WHERE name LIKE 'ts-%'")
                ).all()
            )
        # Kleinste `sort_order` gewinnt (dev2 mit -1); Zone ohne Quelle bleibt leer.
        assert got == {with_source: dev2, without: None}

        again = _alembic(migrations_database_url, "upgrade", "head")
        assert again.returncode == 0, again.stderr
        with engine.begin() as connection:
            assert _rows(connection) == [(with_source, dev2, 0)]
            connection.execute(text("DELETE FROM zone_temperature_source"))
            connection.execute(text("DELETE FROM zone WHERE name LIKE 'ts-%'"))
            connection.execute(text("DELETE FROM device WHERE external_id LIKE 'ts-%'"))
    finally:
        engine.dispose()


@pytest.mark.migration
def test_sources_cascade_with_zone_and_device(migrations_database_url: str) -> None:
    reset = _alembic(migrations_database_url, "downgrade", "base")
    assert reset.returncode == 0, reset.stderr
    up = _alembic(migrations_database_url, "upgrade", "head")
    assert up.returncode == 0, up.stderr

    engine = _engine(migrations_database_url)
    try:
        with engine.begin() as connection:
            dev1 = _device(connection, "tc-1")
            dev2 = _device(connection, "tc-2")
            zone_a = _zone(connection, "tc-a", None)
            zone_b = _zone(connection, "tc-b", None)
            for zone, device in ((zone_a, dev1), (zone_a, dev2), (zone_b, dev2)):
                connection.execute(
                    text(
                        "INSERT INTO zone_temperature_source (zone_id, device_id, sort_order) "
                        "VALUES (:z, :d, 0)"
                    ),
                    {"z": zone, "d": device},
                )
            # Gerät löschen: der Fühler fällt aus allen Listen.
            connection.execute(text("DELETE FROM device WHERE id = :d"), {"d": dev2})
            assert _rows(connection) == [(zone_a, dev1, 0)]
            # Zone löschen: ihre Fühlerzeilen gehen mit.
            connection.execute(text("DELETE FROM zone WHERE id = :z"), {"z": zone_a})
            assert _rows(connection) == []
            connection.execute(text("DELETE FROM zone WHERE id = :z"), {"z": zone_b})
            connection.execute(text("DELETE FROM device WHERE id = :d"), {"d": dev1})
    finally:
        engine.dispose()
