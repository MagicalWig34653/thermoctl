"""Migration `b7d2e5a40c18`: Recht `control.disarm`, versorgt alle Inhaber von `control.arm`."""

import pytest
from sqlalchemy import create_engine, text

from tests.test_migrations import _alembic


@pytest.mark.migration
def test_control_disarm_goes_to_exactly_the_holders_of_control_arm(
    migrations_database_url: str,
) -> None:
    """Wer bisher `force_dry_run` konnte (`control.arm`), behält die Fähigkeit; wer
    `control.arm` nicht hatte, bekommt nichts geschenkt. Beides gilt auch rückwärts."""
    reset = _alembic(migrations_database_url, "downgrade", "base")
    assert reset.returncode == 0, reset.stderr
    before = _alembic(migrations_database_url, "upgrade", "f6c3a8d19b42")
    assert before.returncode == 0, before.stderr

    db_engine = create_engine(migrations_database_url)
    try:
        with db_engine.begin() as connection:
            for name in ("Scharfgruppe", "Lesegruppe", "Zonengruppe"):
                connection.execute(
                    text(
                        "INSERT INTO access_group (name, description, is_builtin) "
                        "VALUES (:name, 'test', false)"
                    ),
                    {"name": name},
                )

            def grant(group: str, code: str, zone_id: int | None) -> None:
                connection.execute(
                    text(
                        "INSERT INTO group_permission (access_group_id, permission_id, zone_id) "
                        "SELECT g.id, p.id, :zone FROM access_group g, permission p "
                        "WHERE g.name = :group AND p.code = :code"
                    ),
                    {"group": group, "code": code, "zone": zone_id},
                )

            grant("Scharfgruppe", "control.arm", None)
            grant("Lesegruppe", "zone.read", None)
            # Eine zonenbezogene Zuteilung von control.arm gibt es fachlich nicht; sie
            # darf aber nicht zu einer anlagenweiten disarm-Zuteilung werden.
            zone_mode = connection.execute(
                text("SELECT id FROM operating_mode ORDER BY id LIMIT 1")
            ).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO zone (name, display_name, operating_mode_id, sort_order, "
                    "created_at, updated_at) VALUES ('disarm-z', 'Z', :m, 0, "
                    "'2026-10-11 10:00:00', '2026-10-11 10:00:00')"
                ),
                {"m": zone_mode},
            )
            zone_id = connection.execute(
                text("SELECT id FROM zone WHERE name = 'disarm-z'")
            ).scalar_one()
            grant("Zonengruppe", "control.arm", zone_id)

        up = _alembic(migrations_database_url, "upgrade", "head")
        assert up.returncode == 0, up.stderr

        def holders() -> set[str]:
            with db_engine.connect() as connection:
                return {
                    row[0]
                    for row in connection.execute(
                        text(
                            "SELECT g.name FROM group_permission gp "
                            "JOIN access_group g ON g.id = gp.access_group_id "
                            "JOIN permission p ON p.id = gp.permission_id "
                            "WHERE p.code = 'control.disarm' AND gp.zone_id IS NULL"
                        )
                    )
                }

        with db_engine.connect() as connection:
            scoped = connection.execute(
                text("SELECT is_zone_scoped FROM permission WHERE code = 'control.disarm'")
            ).scalar_one()
        assert bool(scoped) is False
        assert holders() == {"Scharfgruppe"}

        down = _alembic(migrations_database_url, "downgrade", "f6c3a8d19b42")
        assert down.returncode == 0, down.stderr
        with db_engine.connect() as connection:
            remaining = connection.execute(
                text("SELECT count(*) FROM permission WHERE code = 'control.disarm'")
            ).scalar_one()
            arm_kept = connection.execute(
                text(
                    "SELECT count(*) FROM group_permission gp "
                    "JOIN permission p ON p.id = gp.permission_id "
                    "WHERE p.code = 'control.arm'"
                )
            ).scalar_one()
        assert remaining == 0
        assert arm_kept == 2

        again = _alembic(migrations_database_url, "upgrade", "head")
        assert again.returncode == 0, again.stderr
        assert holders() == {"Scharfgruppe"}

        with db_engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM group_permission WHERE access_group_id IN "
                    "(SELECT id FROM access_group WHERE name IN "
                    "('Scharfgruppe','Lesegruppe','Zonengruppe'))"
                )
            )
            connection.execute(
                text(
                    "DELETE FROM access_group WHERE name IN "
                    "('Scharfgruppe','Lesegruppe','Zonengruppe')"
                )
            )
            connection.execute(text("DELETE FROM zone WHERE name = 'disarm-z'"))
    finally:
        db_engine.dispose()


@pytest.mark.migration
def test_control_disarm_goes_to_api_tokens_holding_control_arm(
    migrations_database_url: str,
) -> None:
    """Auch ein bestehendes API-Token mit anlagenweitem `control.arm` behält `force_dry_run`;
    ein Token ohne `control.arm` bekommt nichts. Rückwärts fällt die Zuteilung weg, und ein
    erneutes Hochziehen stellt sie wieder her (ohne Doppelzeilen)."""
    reset = _alembic(migrations_database_url, "downgrade", "base")
    assert reset.returncode == 0, reset.stderr
    before = _alembic(migrations_database_url, "upgrade", "f6c3a8d19b42")
    assert before.returncode == 0, before.stderr

    db_engine = create_engine(migrations_database_url)
    try:
        with db_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO user (username, display_name, password_hash, is_active, "
                    "created_at) VALUES ('disarm-tok', 'T', 'x', true, '2026-10-11 08:00:00')"
                )
            )
            user_id = connection.execute(
                text("SELECT id FROM user WHERE username = 'disarm-tok'")
            ).scalar_one()
            for name in ("tok-arm", "tok-read"):
                connection.execute(
                    text(
                        "INSERT INTO api_token (user_id, name, prefix, token_hash, created_at, "
                        "is_kiosk) VALUES (:u, :n, :p, :h, '2026-10-11 08:00:00', false)"
                    ),
                    {"u": user_id, "n": name, "p": name, "h": name * 4},
                )
            for name, code in (("tok-arm", "control.arm"), ("tok-read", "zone.read")):
                connection.execute(
                    text(
                        "INSERT INTO api_token_permission (api_token_id, permission_id, zone_id) "
                        "SELECT t.id, p.id, NULL FROM api_token t, permission p "
                        "WHERE t.name = :n AND p.code = :c"
                    ),
                    {"n": name, "c": code},
                )

        def disarm_holders() -> list[str]:
            with db_engine.connect() as connection:
                return sorted(
                    row[0]
                    for row in connection.execute(
                        text(
                            "SELECT t.name FROM api_token_permission atp "
                            "JOIN api_token t ON t.id = atp.api_token_id "
                            "JOIN permission p ON p.id = atp.permission_id "
                            "WHERE p.code = 'control.disarm' AND atp.zone_id IS NULL"
                        )
                    )
                )

        up = _alembic(migrations_database_url, "upgrade", "b7d2e5a40c18")
        assert up.returncode == 0, up.stderr
        assert disarm_holders() == ["tok-arm"]

        down = _alembic(migrations_database_url, "downgrade", "f6c3a8d19b42")
        assert down.returncode == 0, down.stderr
        with db_engine.connect() as connection:
            assert connection.execute(
                text("SELECT count(*) FROM api_token_permission")
            ).scalar_one() == 2

        again = _alembic(migrations_database_url, "upgrade", "b7d2e5a40c18")
        assert again.returncode == 0, again.stderr
        assert disarm_holders() == ["tok-arm"]

        with db_engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM api_token_permission WHERE api_token_id IN "
                    "(SELECT id FROM api_token WHERE name IN ('tok-arm','tok-read'))"
                )
            )
            connection.execute(text("DELETE FROM api_token WHERE name IN ('tok-arm','tok-read')"))
            connection.execute(text("DELETE FROM user WHERE username = 'disarm-tok'"))
    finally:
        db_engine.dispose()
