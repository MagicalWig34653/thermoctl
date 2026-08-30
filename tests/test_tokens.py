from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from tests.helpers import user_with_permissions
from thermoctl.auth.tokens import resolve_token, revoke_token, token_ausstellen
from thermoctl.db.base import utcnow


def test_resolving_a_token_with_invalid_format_returns_none(session: Session) -> None:
    assert resolve_token(session, "kein-gueltiges-token-format") is None


def test_resolving_an_expired_token_returns_none(session: Session) -> None:
    owner = user_with_permissions(session, "abgelaufen", [("zone.read", None)])
    _token, plaintext = token_ausstellen(
        session, owner, "abgelaufenes-token", [("zone.read", None)],
        utcnow() - timedelta(seconds=1),
    )
    assert resolve_token(session, plaintext) is None


def test_revoking_a_token_sets_revoked_at(session: Session) -> None:
    owner = user_with_permissions(session, "widerruf", [("zone.read", None)])
    token, _plaintext = token_ausstellen(
        session, owner, "zu-widerrufendes-token", [("zone.read", None)], None
    )
    assert token.revoked_at is None
    revoke_token(session, token)
    assert token.revoked_at is not None
    assert isinstance(token.revoked_at, datetime)
