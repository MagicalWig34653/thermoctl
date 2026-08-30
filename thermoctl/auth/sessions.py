from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.auth.secrets import hash_secret, new_secret
from thermoctl.db.base import utcnow
from thermoctl.db.models.credential import Session_
from thermoctl.db.models.identity import User
from thermoctl.db.models.operations import Setting

COOKIE_NAME = "thermoctl_session"

# Deckungsgleich mit dem Spaltendefault von `setting.session_lifetime_seconds`: Rueckfall
# fuer den Zeitraum, in dem die Einstellungszeile noch nicht existiert (vor dem
# Setup-Assistenten aus Aufgabe 19).
DEFAULT_SESSION_LIFETIME_S = 60 * 60 * 24 * 14


def session_lifetime_s(session: Session) -> int:
    """Liest die konfigurierte Sitzungsdauer aus der Einstellungszeile.

    Faellt auf ``STANDARD_SITZUNGS_LEBENSDAUER_S`` zurueck, solange die Einrichtung
    noch nicht gelaufen ist und die Zeile deshalb fehlt.
    """
    settings = session.get(Setting, 1)
    if settings is None:
        return DEFAULT_SESSION_LIFETIME_S
    return settings.session_lifetime_seconds


def create_session(
    session: Session, user: User, lifetime_s: int,
    user_agent: str | None = None, ip: str | None = None,
) -> tuple[Session_, str]:
    """Legt eine Sitzung an und liefert sie samt Klartext-Geheimnis fuer das Cookie.

    Gespeichert wird nur der Hash — wer die Datenbank liest, kann sich damit nicht anmelden.
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
