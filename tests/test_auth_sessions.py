from datetime import UTC, datetime

from sqlalchemy.orm import Session

from thermoctl.auth.sessions import (
    DEFAULT_SESSION_LIFETIME_S,
    create_session,
    resolve_session,
    session_lifetime_s,
)


def test_the_missing_settings_fallback_is_exactly_fourteen_days(session: Session) -> None:
    assert DEFAULT_SESSION_LIFETIME_S == 14 * 24 * 60 * 60
    assert session_lifetime_s(session) == 1_209_600


def test_a_session_expires_at_the_exact_boundary(
    session: Session, user, monkeypatch
) -> None:
    boundary = datetime(2026, 9, 2, 12, tzinfo=UTC)
    _entry, secret = create_session(session, user, 60)
    _entry.expires_at = boundary
    session.flush()
    monkeypatch.setattr("thermoctl.auth.sessions.utcnow", lambda: boundary)

    assert resolve_session(session, secret) is None


def test_unknown_and_revoked_sessions_are_both_rejected(session: Session, user) -> None:
    assert resolve_session(session, "unknown-session-secret") is None

    entry, secret = create_session(session, user, 60)
    entry.revoked_at = datetime.now(UTC)
    session.flush()

    assert resolve_session(session, secret) is None
