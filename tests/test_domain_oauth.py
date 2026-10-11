"""OAuth-Kern: Clients, Anfragen, Codes, Refresh-Token (Auth-Logik, Grundsatz 7)."""

import base64
import hashlib
import logging
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.helpers import user_with_permissions
from thermoctl.auth.tokens import resolve_token
from thermoctl.db.base import utcnow
from thermoctl.db.models.credential import ApiToken
from thermoctl.db.models.oauth import (
    OAuthAuthorizationCode,
    OAuthAuthorizationRequest,
    OAuthRefreshToken,
)
from thermoctl.domain import oauth
from thermoctl.domain.authz import Forbidden, principal_for_token

RESOURCE = "https://mcp.example.invalid/mcp"
REDIRECT = "https://client.example.invalid/callback"
VERIFIER = "v" * 64


def _challenge(verifier: str = VERIFIER) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _client(session: Session, uris: list[str] | None = None) -> oauth.OAuthClient:
    return oauth.register_client(session, "Testclient", uris or [REDIRECT])


def _approved_code(
    session: Session,
    client: oauth.OAuthClient,
    user,
    permissions: list[tuple[str, int | None]] | None = None,
    *,
    verifier: str = VERIFIER,
) -> str:
    request_id = oauth.start_request(
        session, client, REDIRECT, _challenge(verifier), RESOURCE, "zustand", RESOURCE
    )
    request = oauth.load_request(session, request_id)
    assert request is not None
    return oauth.approve(session, request, user, permissions or [("zone.read", None)])


def _pair(session: Session, user=None, permissions=None):
    user = user or user_with_permissions(
        session, "oauth-nutzer", [("zone.read", None), ("device.read", None)]
    )
    client = _client(session)
    code = _approved_code(session, client, user, permissions)
    pair = oauth.exchange_code(
        session, client.client_id, code, VERIFIER, REDIRECT, RESOURCE, RESOURCE
    )
    return user, client, pair


# --- register_client -------------------------------------------------------------


def test_register_client_stores_client_and_exact_uris(session: Session) -> None:
    client = oauth.register_client(session, "Claude", [REDIRECT, "http://127.0.0.1:8123/cb"])
    assert client.client_id and client.name == "Claude"
    assert oauth.client_redirect_uris(session, client) == [REDIRECT, "http://127.0.0.1:8123/cb"]


@pytest.mark.parametrize(
    "uri",
    [
        "http://client.example.invalid/cb",  # http nur auf Loopback
        "https://client.example.invalid/cb#frag",  # kein Fragment
        "/relativ",
        "javascript:alert(1)",
        "https://client.example.invalid/a b",
        "https://[::1",
        "https://nutzer:pw@client.example.invalid/cb",
        "https:///kein-host",
        "https://client.example.invalid/" + "a" * 600,
        "http://evil.example.invalid\\@localhost/cb",
        "",
    ],
)
def test_register_client_rejects_unsafe_redirect_uris(session: Session, uri: str) -> None:
    with pytest.raises(oauth.OAuthError) as exc:
        oauth.register_client(session, "Böse", [uri])
    assert exc.value.error == "invalid_redirect_uri"


def test_register_client_accepts_loopback_http_and_native_scheme(session: Session) -> None:
    oauth.register_client(session, "Nativ", ["http://localhost:9000/cb", "myapp:/callback"])


def test_register_client_needs_between_one_and_ten_uris(session: Session) -> None:
    with pytest.raises(oauth.OAuthError):
        oauth.register_client(session, "Leer", [])
    with pytest.raises(oauth.OAuthError):
        oauth.register_client(
            session, "Viele", [f"https://c.example.invalid/{i}" for i in range(11)]
        )


def test_register_client_needs_a_name(session: Session) -> None:
    with pytest.raises(oauth.OAuthError) as exc:
        oauth.register_client(session, "  ", [REDIRECT])
    assert exc.value.error == "invalid_client_metadata"


# --- start_request / load_request ------------------------------------------------


@pytest.mark.parametrize("uri", [REDIRECT + "/", REDIRECT + "x", "https://client.example.invalid/"])
def test_start_request_compares_redirect_uri_exactly(session: Session, uri: str) -> None:
    client = _client(session)
    with pytest.raises(oauth.OAuthError) as exc:
        oauth.start_request(session, client, uri, _challenge(), RESOURCE, None, RESOURCE)
    assert exc.value.error == "invalid_request"


@pytest.mark.parametrize("challenge", ["kurz", "!" * 43, "a" * 129, ""])
def test_start_request_needs_a_well_formed_s256_challenge(session: Session, challenge: str) -> None:
    client = _client(session)
    with pytest.raises(oauth.OAuthError):
        oauth.start_request(session, client, REDIRECT, challenge, RESOURCE, None, RESOURCE)


def test_start_request_binds_to_the_mcp_resource(session: Session) -> None:
    client = _client(session)
    with pytest.raises(oauth.OAuthError) as exc:
        oauth.start_request(
            session,
            client,
            REDIRECT,
            _challenge(),
            "https://rest.example.invalid/api",
            None,
            RESOURCE,
        )
    assert exc.value.error == "invalid_target"


def test_load_request_hides_expired_and_consumed_requests(session: Session, monkeypatch) -> None:
    client = _client(session)
    request_id = oauth.start_request(
        session, client, REDIRECT, _challenge(), RESOURCE, None, RESOURCE
    )
    assert oauth.load_request(session, request_id) is not None
    assert oauth.load_request(session, "unbekannt") is None
    later = utcnow() + oauth.REQUEST_TTL + timedelta(seconds=1)
    monkeypatch.setattr("thermoctl.domain.oauth.utcnow", lambda: later)
    assert oauth.load_request(session, request_id) is None


def test_request_id_is_stored_only_as_hash(session: Session) -> None:
    client = _client(session)
    request_id = oauth.start_request(
        session, client, REDIRECT, _challenge(), RESOURCE, None, RESOURCE
    )
    row = session.scalars(select(OAuthAuthorizationRequest)).one()
    assert request_id not in (row.request_hash, row.state or "")


# --- approve / deny --------------------------------------------------------------


def test_approve_is_single_use_per_request(session: Session) -> None:
    user = user_with_permissions(session, "einmal", [("zone.read", None)])
    client = _client(session)
    request_id = oauth.start_request(
        session, client, REDIRECT, _challenge(), RESOURCE, None, RESOURCE
    )
    request = oauth.load_request(session, request_id)
    assert request is not None
    oauth.approve(session, request, user, [("zone.read", None)])
    assert oauth.load_request(session, request_id) is None
    with pytest.raises(oauth.OAuthError):
        oauth.approve(session, request, user, [("zone.read", None)])


def test_approve_refuses_permissions_the_user_lacks(session: Session) -> None:
    user = user_with_permissions(session, "klein", [("zone.read", None)])
    client = _client(session)
    request_id = oauth.start_request(
        session, client, REDIRECT, _challenge(), RESOURCE, None, RESOURCE
    )
    request = oauth.load_request(session, request_id)
    assert request is not None
    with pytest.raises(Forbidden):
        oauth.approve(session, request, user, [("zone.read", None), ("zone.manage", None)])


def test_deny_consumes_the_request(session: Session) -> None:
    client = _client(session)
    request_id = oauth.start_request(
        session, client, REDIRECT, _challenge(), RESOURCE, None, RESOURCE
    )
    request = oauth.load_request(session, request_id)
    assert request is not None
    oauth.deny(session, request)
    assert oauth.load_request(session, request_id) is None


def test_code_is_stored_only_as_hash(session: Session) -> None:
    user = user_with_permissions(session, "hash", [("zone.read", None)])
    code = _approved_code(session, _client(session), user)
    assert session.scalars(select(OAuthAuthorizationCode)).one().code_hash != code


# --- exchange_code ---------------------------------------------------------------


def test_exchange_issues_a_scoped_oauth_access_token(session: Session) -> None:
    user, client, pair = _pair(session)
    token = resolve_token(session, pair.access_token)
    assert token is not None
    assert (token.source, token.oauth_client_id, token.user_id) == ("oauth", client.id, user.id)
    assert pair.expires_in == int(oauth.ACCESS_TTL.total_seconds())
    assert token.expires_at is not None
    assert principal_for_token(session, token).grants == frozenset({("zone.read", None)})
    assert pair.refresh_token and pair.refresh_token != pair.access_token


@pytest.mark.parametrize(
    "change",
    [
        {"code_verifier": "w" * 64},
        {"redirect_uri": REDIRECT + "/"},
        {"resource": "https://other.example.invalid/mcp"},
        {"client_id": "anderer-client"},
    ],
)
def test_exchange_rejects_every_mismatch(session: Session, change: dict[str, str]) -> None:
    user = user_with_permissions(session, "falsch", [("zone.read", None)])
    client = _client(session)
    other = oauth.register_client(session, "Anderer", [REDIRECT])
    code = _approved_code(session, client, user)
    args = {
        "client_id": client.client_id,
        "code": code,
        "code_verifier": VERIFIER,
        "redirect_uri": REDIRECT,
        "resource": RESOURCE,
    } | change
    if change.get("client_id") == "anderer-client":
        args["client_id"] = other.client_id
    with pytest.raises(oauth.OAuthError) as exc:
        oauth.exchange_code(session, expected_resource=RESOURCE, **args)
    assert exc.value.error in {"invalid_grant", "invalid_target"}


@pytest.mark.parametrize("verifier", ["zu-kurz", "ä" * 50, "x" * 129])
def test_exchange_rejects_malformed_verifier(session: Session, verifier: str) -> None:
    user = user_with_permissions(session, "verifier", [("zone.read", None)])
    client = _client(session)
    code = _approved_code(session, client, user)
    with pytest.raises(oauth.OAuthError):
        oauth.exchange_code(session, client.client_id, code, verifier, REDIRECT, RESOURCE, RESOURCE)


def test_exchange_rejects_unknown_and_expired_codes(session: Session, monkeypatch) -> None:
    user = user_with_permissions(session, "alt", [("zone.read", None)])
    client = _client(session)
    with pytest.raises(oauth.OAuthError):
        oauth.exchange_code(
            session, client.client_id, "gibt-es-nicht", VERIFIER, REDIRECT, RESOURCE, RESOURCE
        )
    code = _approved_code(session, client, user)
    later = utcnow() + oauth.CODE_TTL + timedelta(seconds=1)
    monkeypatch.setattr("thermoctl.domain.oauth.utcnow", lambda: later)
    with pytest.raises(oauth.OAuthError):
        oauth.exchange_code(session, client.client_id, code, VERIFIER, REDIRECT, RESOURCE, RESOURCE)


def test_second_use_of_a_code_fails_and_revokes_what_it_issued(session: Session) -> None:
    user = user_with_permissions(session, "doppelt", [("zone.read", None)])
    client = _client(session)
    code = _approved_code(session, client, user)
    first = oauth.exchange_code(
        session, client.client_id, code, VERIFIER, REDIRECT, RESOURCE, RESOURCE
    )
    assert resolve_token(session, first.access_token) is not None
    with pytest.raises(oauth.OAuthError) as exc:
        oauth.exchange_code(session, client.client_id, code, VERIFIER, REDIRECT, RESOURCE, RESOURCE)
    assert exc.value.error == "invalid_grant"
    assert resolve_token(session, first.access_token) is None
    with pytest.raises(oauth.OAuthError):
        oauth.refresh(session, client.client_id, first.refresh_token, None, RESOURCE)


def test_exchange_refuses_inactive_user(session: Session) -> None:
    user = user_with_permissions(session, "gesperrt", [("zone.read", None)])
    client = _client(session)
    code = _approved_code(session, client, user)
    user.is_active = False
    session.flush()
    with pytest.raises(oauth.OAuthError) as exc:
        oauth.exchange_code(session, client.client_id, code, VERIFIER, REDIRECT, RESOURCE, RESOURCE)
    assert exc.value.error == "invalid_grant"


# --- refresh ---------------------------------------------------------------------


def test_refresh_rotates_and_replaces_the_access_token(session: Session) -> None:
    _user, client, first = _pair(session)
    second = oauth.refresh(session, client.client_id, first.refresh_token, RESOURCE, RESOURCE)
    assert second.refresh_token != first.refresh_token
    assert resolve_token(session, first.access_token) is None
    token = resolve_token(session, second.access_token)
    assert token is not None and token.source == "oauth"
    assert principal_for_token(session, token).grants == frozenset({("zone.read", None)})


def test_reusing_a_rotated_refresh_token_revokes_the_whole_family(session: Session) -> None:
    _user, client, first = _pair(session)
    second = oauth.refresh(session, client.client_id, first.refresh_token, None, RESOURCE)
    with pytest.raises(oauth.OAuthError) as exc:
        oauth.refresh(session, client.client_id, first.refresh_token, None, RESOURCE)
    assert exc.value.error == "invalid_grant"
    assert resolve_token(session, second.access_token) is None
    with pytest.raises(oauth.OAuthError):
        oauth.refresh(session, client.client_id, second.refresh_token, None, RESOURCE)


def test_refresh_is_bound_to_client_resource_and_expiry(session: Session, monkeypatch) -> None:
    _user, client, pair = _pair(session)
    other = oauth.register_client(session, "Fremd", [REDIRECT])
    with pytest.raises(oauth.OAuthError):
        oauth.refresh(session, other.client_id, pair.refresh_token, None, RESOURCE)
    with pytest.raises(oauth.OAuthError) as exc:
        oauth.refresh(
            session, client.client_id, pair.refresh_token, "https://x.example.invalid", RESOURCE
        )
    assert exc.value.error == "invalid_target"
    with pytest.raises(oauth.OAuthError):
        oauth.refresh(session, client.client_id, "unbekannt", None, RESOURCE)
    monkeypatch.setattr(
        "thermoctl.domain.oauth.utcnow", lambda: utcnow() + oauth.REFRESH_TTL + timedelta(seconds=1)
    )
    with pytest.raises(oauth.OAuthError):
        oauth.refresh(session, client.client_id, pair.refresh_token, None, RESOURCE)


def test_refreshed_rights_are_cut_back_to_the_owners_current_rights(session: Session) -> None:
    user = user_with_permissions(session, "verliert", [("zone.read", None), ("device.read", None)])
    client = _client(session)
    code = _approved_code(session, client, user, [("zone.read", None), ("device.read", None)])
    first = oauth.exchange_code(
        session, client.client_id, code, VERIFIER, REDIRECT, RESOURCE, RESOURCE
    )
    revoke_permission(session, user, "device.read")
    second = oauth.refresh(session, client.client_id, first.refresh_token, None, RESOURCE)
    token = resolve_token(session, second.access_token)
    assert token is not None
    rows = {code_ for code_, _z in _stored_grants(session, token)}
    assert rows == {"zone.read"}


def revoke_permission(session: Session, user, code: str) -> None:
    from sqlalchemy import delete

    from thermoctl.db.models.identity import GroupPermission
    from thermoctl.db.models.lookup import Permission

    pid = session.scalar(select(Permission.id).where(Permission.code == code))
    session.execute(delete(GroupPermission).where(GroupPermission.permission_id == pid))
    session.flush()


def _stored_grants(session: Session, token: ApiToken):
    from thermoctl.db.models.credential import ApiTokenPermission
    from thermoctl.db.models.lookup import Permission

    return session.execute(
        select(Permission.code, ApiTokenPermission.zone_id)
        .join(ApiTokenPermission, ApiTokenPermission.permission_id == Permission.id)
        .where(ApiTokenPermission.api_token_id == token.id)
    ).all()


def test_refresh_refuses_inactive_user(session: Session) -> None:
    user, client, pair = _pair(session)
    user.is_active = False
    session.flush()
    with pytest.raises(oauth.OAuthError) as exc:
        oauth.refresh(session, client.client_id, pair.refresh_token, None, RESOURCE)
    assert exc.value.error == "invalid_grant"


def test_refresh_without_remaining_rights_fails(session: Session) -> None:
    user = user_with_permissions(session, "nichts", [("zone.read", None)])
    client = _client(session)
    code = _approved_code(session, client, user)
    pair = oauth.exchange_code(
        session, client.client_id, code, VERIFIER, REDIRECT, RESOURCE, RESOURCE
    )
    revoke_permission(session, user, "zone.read")
    with pytest.raises(oauth.OAuthError) as exc:
        oauth.refresh(session, client.client_id, pair.refresh_token, None, RESOURCE)
    assert exc.value.error == "invalid_grant"


# --- revoke ----------------------------------------------------------------------


def test_revoking_the_access_token_invalidates_it(session: Session) -> None:
    _user, _client_, pair = _pair(session)
    oauth.revoke(session, pair.access_token)
    assert resolve_token(session, pair.access_token) is None


def test_revoking_the_refresh_token_revokes_the_family(session: Session) -> None:
    _user, client, pair = _pair(session)
    oauth.revoke(session, pair.refresh_token)
    assert resolve_token(session, pair.access_token) is None
    with pytest.raises(oauth.OAuthError):
        oauth.refresh(session, client.client_id, pair.refresh_token, None, RESOURCE)
    assert all(r.revoked_at for r in session.scalars(select(OAuthRefreshToken)))


def test_revoking_garbage_or_a_bearer_token_is_silent_and_harmless(session: Session) -> None:
    from thermoctl.auth.tokens import issue_token

    owner = user_with_permissions(session, "bearer", [("zone.read", None)])
    bearer, plaintext = issue_token(session, owner, "b", [("zone.read", None)], None)
    oauth.revoke(session, "irgendwas")
    oauth.revoke(session, plaintext)  # kein OAuth-Token: bleibt gültig (eigener Widerrufsweg)
    assert bearer.revoked_at is None


# --- Protokoll ohne Klartext -----------------------------------------------------


def test_no_plaintext_secret_reaches_the_log(session: Session, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    user = user_with_permissions(session, "log", [("zone.read", None)])
    client = _client(session)
    code = _approved_code(session, client, user)
    pair = oauth.exchange_code(
        session, client.client_id, code, VERIFIER, REDIRECT, RESOURCE, RESOURCE
    )
    oauth.refresh(session, client.client_id, pair.refresh_token, None, RESOURCE)
    text = " ".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    for secret in (code, pair.access_token, pair.refresh_token, VERIFIER):
        assert secret not in text
    assert any(getattr(r, "event", "").startswith("oauth.") for r in caplog.records)


def test_start_request_rejects_overlong_state(session: Session) -> None:
    client = _client(session)
    with pytest.raises(oauth.OAuthError):
        oauth.start_request(session, client, REDIRECT, _challenge(), RESOURCE, "s" * 513, RESOURCE)


def test_exchange_rejects_a_code_when_the_server_resource_changed(session: Session) -> None:
    user = user_with_permissions(session, "umgezogen", [("zone.read", None)])
    client = _client(session)
    code = _approved_code(session, client, user)
    with pytest.raises(oauth.OAuthError) as exc:
        oauth.exchange_code(
            session,
            client.client_id,
            code,
            VERIFIER,
            REDIRECT,
            None,
            "https://neu.example.invalid/mcp",
        )
    assert exc.value.error == "invalid_target"


def _lose_the_race(monkeypatch, column: str) -> None:
    """Simuliert den gleichzeitigen Gewinner: Die Zeile wird vor der Beanspruchung belegt."""
    real = oauth._claim

    def racing(session, row, col, *extra):
        if col == column:
            from sqlalchemy import update

            session.execute(
                update(type(row)).where(type(row).id == row.id).values({col: utcnow()})
            )
        return real(session, row, col, *extra)

    monkeypatch.setattr("thermoctl.domain.oauth._claim", racing)


def test_claim_is_won_by_exactly_one_caller(session: Session) -> None:
    client = _client(session)
    request_id = oauth.start_request(
        session, client, REDIRECT, _challenge(), RESOURCE, None, RESOURCE
    )
    request = oauth.load_request(session, request_id)
    assert request is not None
    assert oauth._claim(session, request, "consumed_at") is True
    assert oauth._claim(session, request, "consumed_at") is False


def test_concurrent_code_redemption_lets_only_one_win(session: Session, monkeypatch) -> None:
    user = user_with_permissions(session, "rennen-code", [("zone.read", None)])
    client = _client(session)
    code = _approved_code(session, client, user)
    _lose_the_race(monkeypatch, "consumed_at")
    with pytest.raises(oauth.OAuthError) as exc:
        oauth.exchange_code(
            session, client.client_id, code, VERIFIER, REDIRECT, RESOURCE, RESOURCE
        )
    assert exc.value.error == "invalid_grant"
    assert not session.scalars(select(OAuthRefreshToken)).all()  # kein Paar ausgestellt


def test_concurrent_refresh_lets_only_one_win(session: Session, monkeypatch) -> None:
    _user, client, pair = _pair(session)
    _lose_the_race(monkeypatch, "revoked_at")
    with pytest.raises(oauth.OAuthError) as exc:
        oauth.refresh(session, client.client_id, pair.refresh_token, None, RESOURCE)
    assert exc.value.error == "invalid_grant"
    assert resolve_token(session, pair.access_token) is None  # Familie gesperrt
    assert len(session.scalars(select(OAuthRefreshToken)).all()) == 1  # kein zweites Paar


def test_concurrent_approval_of_one_request_lets_only_one_win(
    session: Session, monkeypatch
) -> None:
    user = user_with_permissions(session, "rennen-zustimmung", [("zone.read", None)])
    client = _client(session)
    request_id = oauth.start_request(
        session, client, REDIRECT, _challenge(), RESOURCE, None, RESOURCE
    )
    request = oauth.load_request(session, request_id)
    assert request is not None
    _lose_the_race(monkeypatch, "consumed_at")
    with pytest.raises(oauth.OAuthError):
        oauth.approve(session, request, user, [("zone.read", None)])
    assert not session.scalars(select(OAuthAuthorizationCode)).all()


def test_refresh_after_manual_revocation_of_the_access_token_fails(session: Session) -> None:
    _user, client, pair = _pair(session)
    token = resolve_token(session, pair.access_token)
    assert token is not None
    from thermoctl.auth.tokens import revoke_token

    revoke_token(session, token)
    with pytest.raises(oauth.OAuthError) as exc:
        oauth.refresh(session, client.client_id, pair.refresh_token, None, RESOURCE)
    assert exc.value.error == "invalid_grant"
    assert all(r.revoked_at for r in session.scalars(select(OAuthRefreshToken)))


def test_revoking_the_access_token_also_blocks_its_refresh_token(session: Session) -> None:
    _user, client, pair = _pair(session)
    oauth.revoke(session, pair.access_token)
    with pytest.raises(oauth.OAuthError):
        oauth.refresh(session, client.client_id, pair.refresh_token, None, RESOURCE)


def test_revoking_an_oauth_access_token_without_refresh_row_still_revokes_it(
    session: Session,
) -> None:
    from thermoctl.auth.tokens import issue_token

    owner = user_with_permissions(session, "verwaist", [("zone.read", None)])
    token, plaintext = issue_token(session, owner, "o", [("zone.read", None)], None)
    token.source = "oauth"
    session.flush()
    oauth.revoke(session, plaintext)
    assert token.revoked_at is not None
