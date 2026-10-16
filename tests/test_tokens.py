from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from tests.helpers import user_with_permissions
from thermoctl.auth.tokens import issue_token, resolve_token, revoke_token
from thermoctl.db.base import utcnow


def test_resolving_a_token_with_invalid_format_returns_none(session: Session) -> None:
    assert resolve_token(session, "kein-gueltiges-token-format") is None
    assert resolve_token(session, "tctl") is None


def test_resolving_an_expired_token_returns_none(session: Session) -> None:
    owner = user_with_permissions(session, "abgelaufen", [("zone.read", None)])
    _token, plaintext = issue_token(
        session, owner, "abgelaufenes-token", [("zone.read", None)],
        utcnow() - timedelta(seconds=1),
    )
    assert resolve_token(session, plaintext) is None


def test_a_token_expires_at_the_exact_boundary(session: Session, monkeypatch) -> None:
    boundary = utcnow().replace(microsecond=0)
    monkeypatch.setattr("thermoctl.auth.tokens.utcnow", lambda: boundary)
    owner = user_with_permissions(session, "ablaufgrenze", [("zone.read", None)])
    _token, plaintext = issue_token(
        session, owner, "exakte-grenze", [("zone.read", None)], boundary
    )

    assert resolve_token(session, plaintext) is None


def test_revoking_a_token_sets_revoked_at(session: Session) -> None:
    owner = user_with_permissions(session, "widerruf", [("zone.read", None)])
    token, _plaintext = issue_token(
        session, owner, "zu-widerrufendes-token", [("zone.read", None)], None
    )
    assert token.revoked_at is None
    revoke_token(session, token)
    assert token.revoked_at is not None
    assert isinstance(token.revoked_at, datetime)


def test_last_used_is_written_only_after_the_resolution(session: Session, monkeypatch) -> None:
    from thermoctl.auth.tokens import LAST_USED_RESOLUTION

    owner = user_with_permissions(session, "zuletzt", [("zone.read", None)])
    token, plaintext = issue_token(session, owner, "t", [("zone.read", None)], None)
    start = utcnow().replace(microsecond=0)
    clock = [start]
    monkeypatch.setattr("thermoctl.auth.tokens.utcnow", lambda: clock[0])

    resolve_token(session, plaintext)
    assert token.last_used_at == start  # first use is always written
    clock[0] = start + LAST_USED_RESOLUTION - timedelta(seconds=1)
    resolve_token(session, plaintext)
    assert token.last_used_at == start  # too soon: no write, no row contention
    clock[0] = start + LAST_USED_RESOLUTION
    resolve_token(session, plaintext)
    assert token.last_used_at == start + LAST_USED_RESOLUTION  # exactly at the limit
