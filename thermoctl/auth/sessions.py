from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.auth.secrets import hash_secret, new_secret
from thermoctl.db.base import utcnow
from thermoctl.db.models.credential import Session_
from thermoctl.db.models.identity import User
from thermoctl.db.models.operations import Setting

COOKIE_NAME = "thermoctl_session"

# Matches the column default of `setting.session_lifetime_seconds`: fallback for
# the period during which the settings row does not exist yet (before the setup
# wizard from task 19).
DEFAULT_SESSION_LIFETIME_S = 60 * 60 * 24 * 14


def session_lifetime_s(session: Session) -> int:
    """Reads the configured session lifetime from the settings row.

    Falls back to ``DEFAULT_SESSION_LIFETIME_S`` as long as setup has not run yet
    and the row is therefore missing.
    """
    settings = session.get(Setting, 1)
    if settings is None:
        return DEFAULT_SESSION_LIFETIME_S
    return settings.session_lifetime_seconds


def create_session(
    session: Session, user: User, lifetime_s: int,
    user_agent: str | None = None, ip: str | None = None,
) -> tuple[Session_, str]:
    """Creates a session and returns it along with the plaintext secret for the cookie.

    Only the hash is stored — reading the database does not let you log in with it.
    """
    geheimnis = new_secret()
    entry = Session_(
        user_id=user.id,
        token_hash=hash_secret(geheimnis),
        expires_at=utcnow() + timedelta(seconds=lifetime_s),
        user_agent=user_agent,
        ip_address=ip,
    )
    session.add(entry)
    session.flush()
    return entry, geheimnis


def resolve_session(session: Session, cookie_value: str) -> Session_ | None:
    entry = session.scalar(
        select(Session_).where(Session_.token_hash == hash_secret(cookie_value))
    )
    if entry is None or entry.revoked_at is not None or entry.expires_at <= utcnow():
        return None
    entry.last_seen_at = utcnow()
    return entry


def revoke_session(session: Session, http_session: Session_) -> None:
    http_session.revoked_at = utcnow()
