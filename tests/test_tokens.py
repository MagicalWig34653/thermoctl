from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from tests.hilfen import benutzer_mit_rechten
from thermoctl.auth.tokens import token_aufloesen, token_ausstellen, token_widerrufen
from thermoctl.db.base import utcnow


def test_token_aufloesen_mit_falschem_format_gibt_none(session: Session) -> None:
    assert token_aufloesen(session, "kein-gueltiges-token-format") is None


def test_token_aufloesen_abgelaufenes_token_gibt_none(session: Session) -> None:
    besitzer = benutzer_mit_rechten(session, "abgelaufen", [("zone.read", None)])
    _token, klartext = token_ausstellen(
        session, besitzer, "abgelaufenes-token", [("zone.read", None)],
        utcnow() - timedelta(seconds=1),
    )
    assert token_aufloesen(session, klartext) is None


def test_token_widerrufen_setzt_revoked_at(session: Session) -> None:
    besitzer = benutzer_mit_rechten(session, "widerruf", [("zone.read", None)])
    token, _klartext = token_ausstellen(
        session, besitzer, "zu-widerrufendes-token", [("zone.read", None)], None
    )
    assert token.revoked_at is None
    token_widerrufen(session, token)
    assert token.revoked_at is not None
    assert isinstance(token.revoked_at, datetime)
