"""Migration `b3f5d7a92c61`: `audit.read` wird raumbezogen.

Vorwärts bleiben alle Vergaben erhalten (sie sind anlagenweit) und das Recht trägt das
Raumkennzeichen. Rückwärts verschwindet das Kennzeichen, und jede raumbeschränkte Vergabe
von `audit.read` wird zuvor gelöscht -- sonst stünden ungültige Vergaben in der Datenbank
(ein nicht raumbezogenes Recht darf keine Zone tragen).
"""

import pytest
from sqlalchemy import Connection, create_engine, text

from tests.test_migrations import _alembic

PREVIOUS = "a8d4f12c6e53"


def _scoped_flag(connection: Connection) -> bool:
    return bool(
        connection.execute(
            text("SELECT is_zone_scoped FROM permission WHERE code = 'audit.read'")
        ).scalar_one()
    )


def _count(connection: Connection, table: str, code: str, where: str) -> int:
    return int(
        connection.execute(
            text(
                f"SELECT count(*) FROM {table} t "  # noqa: S608
                "JOIN permission p ON p.id = t.permission_id "
                f"WHERE p.code = :code AND {where}"
            ),
            {"code": code},
        ).scalar_one()
    )


@pytest.mark.migration
def test_audit_read_becomes_zone_scoped_and_downgrade_drops_zone_grants(
    migrations_database_url: str,
) -> None:
    reset = _alembic(migrations_database_url, "downgrade", "base")
    assert reset.returncode == 0, reset.stderr
    before = _alembic(migrations_database_url, "upgrade", PREVIOUS)
    assert before.returncode == 0, before.stderr

    db_engine = create_engine(migrations_database_url)
    try:
        with db_engine.begin() as connection:
            assert _scoped_flag(connection) is False
            mode = connection.execute(
                text("SELECT id FROM operating_mode ORDER BY id LIMIT 1")
            ).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO zone (name, display_name, operating_mode_id, sort_order, "
                    "created_at, updated_at) VALUES ('audit-z', 'Z', :m, 0, "
                    "'2026-10-18 10:00:00', '2026-10-18 10:00:00')"
                ),
                {"m": mode},
            )
            zone_id = connection.execute(
                text("SELECT id FROM zone WHERE name = 'audit-z'")
            ).scalar_one()
            for name in ("Weit", "Eng"):
                connection.execute(
                    text(
                        "INSERT INTO access_group (name, description, is_builtin) "
                        "VALUES (:name, 'test', false)"
                    ),
                    {"name": name},
                )
            connection.execute(
                text(
                    "INSERT INTO group_permission (access_group_id, permission_id, zone_id) "
                    "SELECT g.id, p.id, NULL FROM access_group g, permission p "
                    "WHERE g.name = 'Weit' AND p.code = 'audit.read'"
                )
            )

        up = _alembic(migrations_database_url, "upgrade", "head")
        assert up.returncode == 0, up.stderr

        with db_engine.begin() as connection:
            # Bestehende anlagenweite Vergabe bleibt, das Kennzeichen steht.
            assert _scoped_flag(connection) is True
            assert _count(connection, "group_permission", "audit.read",
                          "t.zone_id IS NULL") == 1
            # Jetzt ist eine raumbeschränkte Vergabe gültig (Gruppe und Token).
            connection.execute(
                text(
                    "INSERT INTO group_permission (access_group_id, permission_id, zone_id) "
                    "SELECT g.id, p.id, :zone FROM access_group g, permission p "
                    "WHERE g.name = 'Eng' AND p.code = 'audit.read'"
                ),
                {"zone": zone_id},
            )

        down = _alembic(migrations_database_url, "downgrade", PREVIOUS)
        assert down.returncode == 0, down.stderr
        with db_engine.connect() as connection:
            assert _scoped_flag(connection) is False
            # Die raumbeschränkte Vergabe ist weg, die anlagenweite bleibt.
            assert _count(connection, "group_permission", "audit.read",
                          "t.zone_id IS NOT NULL") == 0
            assert _count(connection, "group_permission", "audit.read",
                          "t.zone_id IS NULL") == 1

        again = _alembic(migrations_database_url, "upgrade", "head")
        assert again.returncode == 0, again.stderr
        with db_engine.connect() as connection:
            assert _scoped_flag(connection) is True

        with db_engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM group_permission WHERE access_group_id IN "
                    "(SELECT id FROM access_group WHERE name IN ('Weit', 'Eng'))"
                )
            )
            connection.execute(text("DELETE FROM access_group WHERE name IN ('Weit', 'Eng')"))
            connection.execute(text("DELETE FROM zone WHERE name = 'audit-z'"))
    finally:
        db_engine.dispose()


@pytest.mark.migration
def test_downgrade_drops_zone_scoped_token_grants_too(migrations_database_url: str) -> None:
    """Auch ein Token mit raumbeschränktem `audit.read` verliert die Vergabe rückwärts."""
    reset = _alembic(migrations_database_url, "downgrade", "base")
    assert reset.returncode == 0, reset.stderr
    up = _alembic(migrations_database_url, "upgrade", "head")
    assert up.returncode == 0, up.stderr

    db_engine = create_engine(migrations_database_url)
    try:
        with db_engine.begin() as connection:
            mode = connection.execute(
                text("SELECT id FROM operating_mode ORDER BY id LIMIT 1")
            ).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO zone (name, display_name, operating_mode_id, sort_order, "
                    "created_at, updated_at) VALUES ('audit-t', 'Z', :m, 0, "
                    "'2026-10-18 10:00:00', '2026-10-18 10:00:00')"
                ),
                {"m": mode},
            )
            zone_id = connection.execute(
                text("SELECT id FROM zone WHERE name = 'audit-t'")
            ).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO user (username, display_name, password_hash, is_active, "
                    "created_at) VALUES ('audit-t', 'T', 'x', true, '2026-10-18 10:00:00')"
                )
            )
            user_id = connection.execute(
                text("SELECT id FROM user WHERE username = 'audit-t'")
            ).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO api_token (user_id, name, prefix, token_hash, created_at, "
                    "is_kiosk) VALUES (:u, 'audit-t', 'abc', :h, '2026-10-18 10:00:00', false)"
                ),
                {"u": user_id, "h": "h" * 64},
            )
            token_id = connection.execute(
                text("SELECT id FROM api_token WHERE name = 'audit-t'")
            ).scalar_one()
            for scope in (zone_id, None):
                connection.execute(
                    text(
                        "INSERT INTO api_token_permission (api_token_id, permission_id, zone_id) "
                        "SELECT :t, p.id, :z FROM permission p WHERE p.code = 'audit.read'"
                    ),
                    {"t": token_id, "z": scope},
                )

        down = _alembic(migrations_database_url, "downgrade", PREVIOUS)
        assert down.returncode == 0, down.stderr
        with db_engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT zone_id FROM api_token_permission t "
                    "JOIN permission p ON p.id = t.permission_id WHERE p.code = 'audit.read'"
                )
            ).all()
        assert [row[0] for row in rows] == [None]

        with db_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM api_token_permission WHERE api_token_id = :t"), {"t": token_id}
            )
            connection.execute(text("DELETE FROM api_token WHERE id = :t"), {"t": token_id})
            connection.execute(text("DELETE FROM user WHERE id = :u"), {"u": user_id})
            connection.execute(text("DELETE FROM zone WHERE name = 'audit-t'"))
    finally:
        db_engine.dispose()
