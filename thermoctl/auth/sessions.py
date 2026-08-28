from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.auth.secrets import hash_geheimnis, neues_geheimnis
from thermoctl.db.base import utcnow
from thermoctl.db.models.credential import Session_
from thermoctl.db.models.identity import User
from thermoctl.db.models.operations import Setting

COOKIE_NAME = "thermoctl_session"

# Deckungsgleich mit dem Spaltendefault von `setting.session_lifetime_seconds`: Rueckfall
# fuer den Zeitraum, in dem die Einstellungszeile noch nicht existiert (vor dem
# Setup-Assistenten aus Aufgabe 19).
STANDARD_SITZUNGS_LEBENSDAUER_S = 60 * 60 * 24 * 14


def sitzungslebensdauer_s(session: Session) -> int:
    """Liest die konfigurierte Sitzungsdauer aus der Einstellungszeile.

    Faellt auf ``STANDARD_SITZUNGS_LEBENSDAUER_S`` zurueck, solange die Einrichtung
    noch nicht gelaufen ist und die Zeile deshalb fehlt.
    """
    einstellungen = session.get(Setting, 1)
    if einstellungen is None:
        return STANDARD_SITZUNGS_LEBENSDAUER_S
    return einstellungen.session_lifetime_seconds


def sitzung_anlegen(
    session: Session, user: User, lebensdauer_s: int,
    user_agent: str | None = None, ip: str | None = None,
) -> tuple[Session_, str]:
    """Legt eine Sitzung an und liefert sie samt Klartext-Geheimnis fuer das Cookie.

    Gespeichert wird nur der Hash — wer die Datenbank liest, kann sich damit nicht anmelden.
    """
    geheimnis = neues_geheimnis()
    eintrag = Session_(
        user_id=user.id,
        token_hash=hash_geheimnis(geheimnis),
        expires_at=utcnow() + timedelta(seconds=lebensdauer_s),
        user_agent=user_agent,
        ip_address=ip,
    )
    session.add(eintrag)
    session.flush()
    return eintrag, geheimnis


def sitzung_aufloesen(session: Session, cookie_wert: str) -> Session_ | None:
    eintrag = session.scalar(
        select(Session_).where(Session_.token_hash == hash_geheimnis(cookie_wert))
    )
    if eintrag is None or eintrag.revoked_at is not None or eintrag.expires_at <= utcnow():
        return None
    eintrag.last_seen_at = utcnow()
    return eintrag


def sitzung_widerrufen(session: Session, sitzung: Session_) -> None:
    sitzung.revoked_at = utcnow()
