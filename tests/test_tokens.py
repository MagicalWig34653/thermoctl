from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from tests.helpers import user_with_permissions
from thermoctl.auth.tokens import resolve_token, revoke_token, token_ausstellen
from thermoctl.db.base import utcnow


def test_token_aufloesen_mit_falschem_format_gibt_none(session: Session) -> None:
    assert resolve_token(session, "kein-gueltiges-token-format") is None


def test_token_aufloesen_abgelaufenes_token_gibt_none(session: Session) -> None:
    besitzer = user_with_permissions(session, "abgelaufen", [("zone.read", None)])
    _token, plaintext = token_ausstellen(
        session, besitzer, "abgelaufenes-token", [("zone.read", None)],
        utcnow() - timedelta(seconds=1),
    )
    assert resolve_token(session, plaintext) is None


def test_token_widerrufen_setzt_revoked_at(session: Session) -> None:
    besitzer = user_with_permissions(session, "widerruf", [("zone.read", None)])
    token, _plaintext = token_ausstellen(
        session, besitzer, "zu-widerrufendes-token", [("zone.read", None)], None
    )
    assert token.revoked_at is None
    revoke_token(session, token)
    assert token.revoked_at is not None
    assert isinstance(token.revoked_at, datetime)
