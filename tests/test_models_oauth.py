"""Verbindliche Schema- und Migrationsregeln für OAuth-Zugangsdaten."""

from datetime import timedelta

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tests.helpers import create_user, ensure_permission
from tests.test_migrations import _alembic
from thermoctl.db.base import Base, utcnow
from thermoctl.db.models.credential import API_TOKEN_SOURCES, ApiToken
from thermoctl.db.models.oauth import (
    OAuthAuthorizationCode,
    OAuthAuthorizationRequest,
    OAuthClient,
    OAuthCodePermission,
    OAuthRedirectUri,
    OAuthRefreshToken,
)


def _client(session: Session, client_id: str = "client-a") -> OAuthClient:
    client = OAuthClient(client_id=client_id, name="Testclient")
    session.add(client)
    session.flush()
    return client


def test_oauth_columns_and_lengths_match_contract() -> None:
    expected = {
        OAuthClient: {"client_id": 64, "name": 128},
        OAuthRedirectUri: {"uri": 512},
        OAuthAuthorizationRequest: {
            "state": 512,
            "code_challenge": 128,
            "resource": 1024,
        },
        OAuthAuthorizationCode: {"code_challenge": 128},
        OAuthRefreshToken: {"family": 32},
    }
    for model, lengths in expected.items():
        for name, length in lengths.items():
            assert model.__table__.c[name].type.length == length
    for model in expected:
        assert model.__table__.name in Base.metadata.tables
    assert {"source", "oauth_client_id"} <= set(ApiToken.__table__.c.keys())
    assert OAuthRefreshToken.__table__.c.family.index is True
    assert OAuthAuthorizationCode.__table__.c.get("permission_set_id") is None
    assert API_TOKEN_SOURCES == {"bearer", "oauth"}
    expected_columns = {
        OAuthClient: {"id", "client_id", "name", "created_at", "last_used_at"},
        OAuthRedirectUri: {"id", "client_id", "uri"},
        OAuthAuthorizationRequest: {
            "id",
            "request_hash",
            "client_id",
            "redirect_uri",
            "state",
            "code_challenge",
            "resource",
            "scope_preset",
            "created_at",
            "expires_at",
            "consumed_at",
        },
        OAuthAuthorizationCode: {
            "id",
            "code_hash",
            "client_id",
            "user_id",
            "redirect_uri",
            "code_challenge",
            "resource",
            "expires_at",
            "consumed_at",
        },
        OAuthCodePermission: {"id", "code_id", "permission_id", "zone_id"},
        OAuthRefreshToken: {
            "id",
            "token_hash",
            "family",
            "client_id",
            "user_id",
            "api_token_id",
            "created_at",
            "expires_at",
            "replaced_by_id",
            "revoked_at",
        },
    }
    for model, columns in expected_columns.items():
        assert set(model.__table__.c.keys()) == columns
    expected_deletions = {
        (OAuthRedirectUri, "client_id"): "CASCADE",
        (OAuthAuthorizationRequest, "client_id"): "CASCADE",
        (OAuthAuthorizationCode, "client_id"): "CASCADE",
        (OAuthAuthorizationCode, "user_id"): "CASCADE",
        (OAuthCodePermission, "code_id"): "CASCADE",
        (OAuthRefreshToken, "client_id"): "CASCADE",
        (OAuthRefreshToken, "user_id"): "CASCADE",
        (OAuthRefreshToken, "api_token_id"): "SET NULL",
        (OAuthRefreshToken, "replaced_by_id"): "SET NULL",
        (ApiToken, "oauth_client_id"): "SET NULL",
    }
    for (model, column), action in expected_deletions.items():
        foreign_key = next(iter(model.__table__.c[column].foreign_keys))
        assert foreign_key.ondelete == action


def test_client_identifiers_and_redirect_uris_are_unique(session: Session) -> None:
    client = _client(session)
    session.add(OAuthRedirectUri(client_id=client.id, uri="https://app.example/callback"))
    session.flush()
    with pytest.raises(IntegrityError):
        with session.begin_nested():
            session.add(OAuthClient(client_id=client.client_id, name="Doppelt"))
            session.flush()
    with pytest.raises(IntegrityError):
        with session.begin_nested():
            session.add(OAuthRedirectUri(client_id=client.id, uri="https://app.example/callback"))
            session.flush()
    other = _client(session, "client-b")
    session.add(OAuthRedirectUri(client_id=other.id, uri="https://app.example/callback"))
    session.flush()


def test_request_code_and_refresh_hashes_are_unique(session: Session) -> None:
    client = _client(session)
    user = create_user(session, "oauth-hashes")
    now = utcnow()
    request = dict(
        client_id=client.id,
        redirect_uri="https://app.example/callback",
        code_challenge="x" * 43,
        resource="https://mcp.example",
        expires_at=now + timedelta(minutes=10),
    )
    code = dict(
        client_id=client.id,
        user_id=user.id,
        redirect_uri="https://app.example/callback",
        code_challenge="x" * 43,
        resource="https://mcp.example",
        expires_at=now + timedelta(minutes=5),
    )
    refresh = dict(
        client_id=client.id, user_id=user.id, family="family-a", expires_at=now + timedelta(days=30)
    )
    session.add_all(
        [
            OAuthAuthorizationRequest(request_hash="r" * 64, **request),
            OAuthAuthorizationCode(code_hash="c" * 64, **code),
            OAuthRefreshToken(token_hash="t" * 64, **refresh),
        ]
    )
    session.flush()
    for duplicate in (
        OAuthAuthorizationRequest(request_hash="r" * 64, **request),
        OAuthAuthorizationCode(code_hash="c" * 64, **code),
        OAuthRefreshToken(token_hash="t" * 64, **refresh),
    ):
        with pytest.raises(IntegrityError):
            with session.begin_nested():
                session.add(duplicate)
                session.flush()


def test_user_deletion_cascades_codes_refresh_and_permissions(session: Session) -> None:
    client = _client(session)
    user = create_user(session, "oauth-cascade")
    permission = ensure_permission(session, "zone.read", True)
    expires = utcnow() + timedelta(days=1)
    code = OAuthAuthorizationCode(
        code_hash="code-cascade",
        client_id=client.id,
        user_id=user.id,
        redirect_uri="https://app.example/callback",
        code_challenge="x" * 43,
        resource="https://mcp.example",
        expires_at=expires,
    )
    refresh = OAuthRefreshToken(
        token_hash="refresh-cascade",
        family="family-b",
        client_id=client.id,
        user_id=user.id,
        expires_at=expires,
    )
    session.add_all([code, refresh])
    session.flush()
    session.add(OAuthCodePermission(code_id=code.id, permission_id=permission.id))
    session.flush()
    session.delete(user)
    session.flush()
    assert session.query(OAuthAuthorizationCode).filter_by(id=code.id).count() == 0
    assert session.query(OAuthCodePermission).filter_by(code_id=code.id).count() == 0
    assert session.query(OAuthRefreshToken).filter_by(id=refresh.id).count() == 0


def test_oauth_client_deletion_nulls_token_reference(session: Session) -> None:
    client = _client(session)
    user = create_user(session, "oauth-token")
    token = ApiToken(
        user_id=user.id,
        name="OAuth",
        prefix="oauth-c",
        token_hash="token-c",
        source="oauth",
        oauth_client_id=client.id,
    )
    session.add(token)
    session.flush()
    session.delete(client)
    session.flush()
    session.refresh(token)
    assert token.oauth_client_id is None
    assert token.source == "oauth"


def test_new_api_token_defaults_to_bearer(session: Session) -> None:
    user = create_user(session, "oauth-bearer-default")
    token = ApiToken(
        user_id=user.id, name="Persönlich", prefix="bearer-d", token_hash="bearer-default"
    )
    session.add(token)
    session.flush()
    assert token.source == "bearer"
    assert token.oauth_client_id is None


def test_refresh_token_can_reference_access_token_and_replacement(session: Session) -> None:
    client = _client(session)
    user = create_user(session, "oauth-refresh-chain")
    token = ApiToken(
        user_id=user.id,
        name="OAuth",
        prefix="refresh-r",
        token_hash="access-r",
        source="oauth",
        oauth_client_id=client.id,
    )
    session.add(token)
    session.flush()
    expires = utcnow() + timedelta(days=30)
    old = OAuthRefreshToken(
        token_hash="refresh-old",
        family="family-r",
        client_id=client.id,
        user_id=user.id,
        api_token_id=token.id,
        expires_at=expires,
    )
    new = OAuthRefreshToken(
        token_hash="refresh-new",
        family="family-r",
        client_id=client.id,
        user_id=user.id,
        expires_at=expires,
    )
    session.add_all([old, new])
    session.flush()
    old.replaced_by_id = new.id
    session.flush()
    assert old.api_token_id == token.id
    assert old.replaced_by_id == new.id
    session.delete(token)
    session.flush()
    session.refresh(old)
    assert old.api_token_id is None


@pytest.mark.migration
def test_oauth_migration_preserves_existing_tokens_and_downgrades(
    migrations_database_url: str,
) -> None:
    assert _alembic(migrations_database_url, "downgrade", "base").returncode == 0
    before = _alembic(migrations_database_url, "upgrade", "b7d2e5a40c18")
    assert before.returncode == 0, before.stderr
    engine = create_engine(migrations_database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO user (username, display_name, password_hash, "
                    "is_active, created_at) VALUES ('oauth-old', 'Alt', 'hash', "
                    "true, '2026-10-11 10:00:00')"
                )
            )
            user_id = connection.execute(text("SELECT id FROM user WHERE username='oauth-old'"))
            connection.execute(
                text(
                    "INSERT INTO api_token (user_id, name, prefix, token_hash, "
                    "created_at, is_kiosk) VALUES (:user_id, 'Alt', 'old-oauth', "
                    "'old-hash', '2026-10-11 10:00:00', false)"
                ),
                {"user_id": user_id.scalar_one()},
            )
        up = _alembic(migrations_database_url, "upgrade", "head")
        assert up.returncode == 0, up.stderr
        tables = set(inspect(engine).get_table_names())
        assert {
            "oauth_client",
            "oauth_redirect_uri",
            "oauth_authorization_request",
            "oauth_authorization_code",
            "oauth_code_permission",
            "oauth_refresh_token",
        } <= tables
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT source FROM api_token WHERE prefix='old-oauth'")
                ).scalar_one()
                == "bearer"
            )
        # Ausdrücklich die Revision davor und nicht „-1“: Auf den OAuth-Stand setzen
        # später weitere Migrationen auf (`c4a9e2f71b35`), „-1“ träfe dann die falsche.
        down = _alembic(migrations_database_url, "downgrade", "b7d2e5a40c18")
        assert down.returncode == 0, down.stderr
        assert "oauth_client" not in inspect(engine).get_table_names()
        assert "source" not in {c["name"] for c in inspect(engine).get_columns("api_token")}
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT COUNT(*) FROM api_token WHERE prefix='old-oauth'")
                ).scalar_one()
                == 1
            )
        again = _alembic(migrations_database_url, "upgrade", "head")
        assert again.returncode == 0, again.stderr
    finally:
        engine.dispose()
