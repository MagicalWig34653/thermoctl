"""Migration `b721e49c6a30`: Der Downgrade lässt keine OAuth-Token als Bearer-Token übrig."""

import pytest
from sqlalchemy import create_engine, text

from tests.test_migrations import _alembic


@pytest.mark.migration
def test_downgrade_deletes_oauth_tokens_and_keeps_bearer_tokens(
    migrations_database_url: str,
) -> None:
    """Ohne die Spalte `source` wäre ein OAuth-Token nach dem Downgrade ein dauerhaft
    gültiges Bearer-Token. Der Downgrade löscht sie samt Rechten; Bearer-Token bleiben."""
    reset = _alembic(migrations_database_url, "downgrade", "base")
    assert reset.returncode == 0, reset.stderr
    up = _alembic(migrations_database_url, "upgrade", "head")
    assert up.returncode == 0, up.stderr

    db_engine = create_engine(migrations_database_url)
    try:
        with db_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO user (username, display_name, password_hash, is_active, "
                    "created_at) VALUES ('oauth-down', 'O', 'x', true, '2026-10-11 08:00:00')"
                )
            )
            user_id = connection.execute(
                text("SELECT id FROM user WHERE username = 'oauth-down'")
            ).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO oauth_client (client_id, name, created_at) "
                    "VALUES ('c-down', 'Client', '2026-10-11 08:00:00')"
                )
            )
            client_id = connection.execute(
                text("SELECT id FROM oauth_client WHERE client_id = 'c-down'")
            ).scalar_one()
            for name, source, oauth_client in (
                ("tok-oauth", "oauth", client_id),
                ("tok-bearer", "bearer", None),
            ):
                connection.execute(
                    text(
                        "INSERT INTO api_token (user_id, name, prefix, token_hash, created_at, "
                        "is_kiosk, source, oauth_client_id) VALUES (:u, :n, :n, :h, "
                        "'2026-10-11 08:00:00', false, :s, :c)"
                    ),
                    {"u": user_id, "n": name, "h": name * 4, "s": source, "c": oauth_client},
                )
                connection.execute(
                    text(
                        "INSERT INTO api_token_permission (api_token_id, permission_id, zone_id) "
                        "SELECT t.id, p.id, NULL FROM api_token t, permission p "
                        "WHERE t.name = :n AND p.code = 'zone.read'"
                    ),
                    {"n": name},
                )
            connection.execute(
                text(
                    "INSERT INTO oauth_refresh_token (token_hash, family, client_id, user_id, "
                    "api_token_id, created_at, expires_at) "
                    "SELECT 'rt-down', 'fam', :c, :u, t.id, '2026-10-11 08:00:00', "
                    "'2026-10-12 08:00:00' FROM api_token t WHERE t.name = 'tok-oauth'"
                ),
                {"c": client_id, "u": user_id},
            )

        down = _alembic(migrations_database_url, "downgrade", "b7d2e5a40c18")
        assert down.returncode == 0, down.stderr

        with db_engine.connect() as connection:
            names = sorted(
                row[0]
                for row in connection.execute(
                    text("SELECT name FROM api_token WHERE name IN ('tok-oauth','tok-bearer')")
                )
            )
            orphans = connection.execute(
                text(
                    "SELECT count(*) FROM api_token_permission WHERE api_token_id NOT IN "
                    "(SELECT id FROM api_token)"
                )
            ).scalar_one()
        assert names == ["tok-bearer"]
        assert orphans == 0

        with db_engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM api_token_permission WHERE api_token_id IN "
                    "(SELECT id FROM api_token WHERE name = 'tok-bearer')"
                )
            )
            connection.execute(text("DELETE FROM api_token WHERE name = 'tok-bearer'"))
            connection.execute(text("DELETE FROM user WHERE username = 'oauth-down'"))
        again = _alembic(migrations_database_url, "upgrade", "head")
        assert again.returncode == 0, again.stderr
    finally:
        db_engine.dispose()
