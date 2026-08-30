import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.auth.passwords import hash_password
from thermoctl.auth.secrets import hash_secret, new_secret
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
EXAMPLE_GROUPS: dict[str, list[str]] = {
    "Verwaltung": [],
    "Bedienung": ["zone.read", "setpoint.write", "override.create", "override.cancel",
                  "token.self"],
    "Nur lesen": ["zone.read", "device.read"],
    "Integration": ["zone.read"],
}

BUILTIN_MODES = [("tag", "Tag", 0), ("nacht", "Nacht", 1), ("frostschutz", "Frostschutz", 2)]


def einrichtung_noetig(session: Session) -> bool:
    return session.scalar(select(User.id).limit(1)) is None


def setup_token_erzeugen(session: Session) -> str:
    """Erzeugt ein Einmal-Token, legt seinen Hash ab und gibt den Klartext zurueck.

    Der Aufrufer schreibt ihn ins Log. Ohne diesen Schutz gewinnt im unguenstigen Fall
    der Erste im Netz, der die Einrichtungsseite findet.
    """
    plaintext = new_secret()
    session.add(SetupToken(token_hash=hash_secret(plaintext)))
    session.flush()
    return plaintext


def einrichtung_durchfuehren(
    session: Session, *, username: str, display_name: str, password: str,
    timezone_name: str, token: str,
) -> User:
    """Legt den ersten Verwalter, die Beispielgruppen und die Einstellungszeile an."""
    if not einrichtung_noetig(session):
        raise PermissionError("Die Einrichtung ist bereits abgeschlossen.")
    marker = session.scalar(
        select(SetupToken).where(
            SetupToken.token_hash == hash_secret(token),
            SetupToken.consumed_at.is_(None),
        )
    )
    if marker is None:
        raise PermissionError("Ungueltiges oder verbrauchtes Einrichtungs-Token.")

    # Korrigierbare Eingaben werden geprueft, bevor Teile der Einrichtung angelegt
    # werden. Die View faengt PasswordTooShort bewusst ab; spaetere Schreibzugriffe
    # duerfen deshalb nicht versehentlich als erfolgreicher Request committet werden.
    password_hash = hash_password(password)

    for code, name, reihenfolge in BUILTIN_MODES:
        if session.scalar(select(SetpointMode).where(SetpointMode.code == code)) is None:
            session.add(
                SetpointMode(code=code, name=name, sort_order=reihenfolge, is_builtin=True)
            )
    session.flush()

    alle = {p.code: p for p in session.scalars(select(Permission))}
    groups: dict[str, AccessGroup] = {}
    for name, codes in EXAMPLE_GROUPS.items():
        group = AccessGroup(name=name, is_builtin=True)
        session.add(group)
        session.flush()
        groups[name] = group
        for code in codes or alle:
            session.add(
                GroupPermission(access_group_id=group.id, permission_id=alle[code].id,
                                zone_id=None)
            )

    nutzer = User(username=username, display_name=display_name, password_hash=password_hash)
    session.add(nutzer)
    session.flush()
    session.add(
        UserAccessGroup(user_id=nutzer.id, access_group_id=groups["Verwaltung"].id)
    )

    frost = session.scalar(select(SetpointMode).where(SetpointMode.code == "frostschutz"))
    # Kann nach der Anlage der EINGEBAUTE_MODI oben nicht None sein -- nur fuer mypy
    # strict, das den Rueckgabetyp von `scalar()` nicht enger kennt.
    assert frost is not None
    session.add(Setting(id=1, timezone=timezone_name, frost_protection_mode_id=frost.id))

    marker.consumed_at = utcnow()
    session.flush()
    log.info("Einrichtung abgeschlossen", extra={"username": username})
    return nutzer
