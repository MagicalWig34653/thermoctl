import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.auth.passwords import hash_password
from thermoctl.auth.secrets import hash_geheimnis, neues_geheimnis
from thermoctl.db.base import utcnow
from thermoctl.db.models.credential import SetupToken
from thermoctl.db.models.identity import (
    AccessGroup,
    GroupPermission,
    User,
    UserAccessGroup,
)
from thermoctl.db.models.lookup import Permission
from thermoctl.db.models.operations import Setting
from thermoctl.db.models.zone import SetpointMode

log = logging.getLogger(__name__)

# Beispiele, nach der Einrichtung frei aenderbar. Leere Liste heisst 'alle Rechte'.
BEISPIELGRUPPEN: dict[str, list[str]] = {
    "Verwaltung": [],
    "Bedienung": ["zone.read", "setpoint.write", "override.create", "override.cancel",
                  "token.self"],
    "Nur lesen": ["zone.read", "device.read"],
    "Integration": ["zone.read"],
}

EINGEBAUTE_MODI = [("tag", "Tag", 0), ("nacht", "Nacht", 1), ("frostschutz", "Frostschutz", 2)]


def einrichtung_noetig(session: Session) -> bool:
    return session.scalar(select(User.id).limit(1)) is None


def setup_token_erzeugen(session: Session) -> str:
    """Erzeugt ein Einmal-Token, legt seinen Hash ab und gibt den Klartext zurueck.

    Der Aufrufer schreibt ihn ins Log. Ohne diesen Schutz gewinnt im unguenstigen Fall
    der Erste im Netz, der die Einrichtungsseite findet.
    """
    klartext = neues_geheimnis()
    session.add(SetupToken(token_hash=hash_geheimnis(klartext)))
    session.flush()
    return klartext


def setup_token_pruefen(session: Session, klartext: str) -> bool:
    marke = session.scalar(
        select(SetupToken).where(
            SetupToken.token_hash == hash_geheimnis(klartext),
            SetupToken.consumed_at.is_(None),
        )
    )
    return marke is not None


def einrichtung_durchfuehren(
    session: Session, *, username: str, display_name: str, passwort: str,
    zeitzone: str, token: str,
) -> User:
    """Legt den ersten Verwalter, die Beispielgruppen und die Einstellungszeile an."""
    if not einrichtung_noetig(session):
        raise PermissionError("Die Einrichtung ist bereits abgeschlossen.")
    marke = session.scalar(
        select(SetupToken).where(
            SetupToken.token_hash == hash_geheimnis(token),
            SetupToken.consumed_at.is_(None),
        )
    )
    if marke is None:
        raise PermissionError("Ungueltiges oder verbrauchtes Einrichtungs-Token.")

    for code, name, reihenfolge in EINGEBAUTE_MODI:
        if session.scalar(select(SetpointMode).where(SetpointMode.code == code)) is None:
            session.add(
                SetpointMode(code=code, name=name, sort_order=reihenfolge, is_builtin=True)
            )
    session.flush()

    alle = {p.code: p for p in session.scalars(select(Permission))}
    gruppen: dict[str, AccessGroup] = {}
    for name, codes in BEISPIELGRUPPEN.items():
        gruppe = AccessGroup(name=name, is_builtin=True)
        session.add(gruppe)
        session.flush()
        gruppen[name] = gruppe
        for code in codes or alle:
            session.add(
                GroupPermission(access_group_id=gruppe.id, permission_id=alle[code].id,
                                zone_id=None)
            )

    nutzer = User(username=username, display_name=display_name,
                  password_hash=hash_password(passwort))
    session.add(nutzer)
    session.flush()
    session.add(
        UserAccessGroup(user_id=nutzer.id, access_group_id=gruppen["Verwaltung"].id)
    )

    frost = session.scalar(select(SetpointMode).where(SetpointMode.code == "frostschutz"))
    # Kann nach der Anlage der EINGEBAUTE_MODI oben nicht None sein -- nur fuer mypy
    # strict, das den Rueckgabetyp von `scalar()` nicht enger kennt.
    assert frost is not None
    session.add(Setting(id=1, timezone=zeitzone, frost_protection_mode_id=frost.id))

    marke.consumed_at = utcnow()
    session.flush()
    log.info("Einrichtung abgeschlossen", extra={"username": username})
    return nutzer
