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

# Examples, freely changeable after setup. Empty list means 'all permissions'.
EXAMPLE_GROUPS: dict[str, list[str]] = {
    "Verwaltung": [],
    "Bedienung": ["zone.read", "setpoint.write", "override.create", "override.cancel",
                  "token.self"],
    "Nur lesen": ["zone.read", "device.read"],
    "Integration": ["zone.read"],
}

BUILTIN_MODES = [("tag", "Tag", 0), ("nacht", "Nacht", 1), ("frostschutz", "Frostschutz", 2)]


def setup_needed(session: Session) -> bool:
    return session.scalar(select(User.id).limit(1)) is None


def create_setup_token(session: Session) -> str:
    """Generates a one-time token, stores its hash, and returns the plaintext.

    The caller writes it to the log. Without this protection, in the unfavorable
    case, whoever finds the setup page first on the network wins.
    """
    plaintext = new_secret()
    session.add(SetupToken(token_hash=hash_secret(plaintext)))
    session.flush()
    return plaintext


def run_setup(
    session: Session, *, username: str, display_name: str, password: str,
    timezone_name: str, token: str,
) -> User:
    """Creates the first administrator, the example groups, and the settings row."""
    if not setup_needed(session):
        raise PermissionError("Die Einrichtung ist bereits abgeschlossen.")
    marker = session.scalar(
        select(SetupToken).where(
            SetupToken.token_hash == hash_secret(token),
            SetupToken.consumed_at.is_(None),
        )
    )
    if marker is None:
        raise PermissionError("Ungueltiges oder verbrauchtes Einrichtungs-Token.")

    # Correctable input is validated before any part of the setup is created. The
    # view deliberately catches PasswordTooShort; later writes must therefore not
    # accidentally get committed as if the request had succeeded.
    password_hash = hash_password(password)

    for code, name, order in BUILTIN_MODES:
        if session.scalar(select(SetpointMode).where(SetpointMode.code == code)) is None:
            session.add(
                SetpointMode(code=code, name=name, sort_order=order, is_builtin=True)
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

    user_record = User(username=username, display_name=display_name, password_hash=password_hash)
    session.add(user_record)
    session.flush()
    session.add(
        UserAccessGroup(user_id=user_record.id, access_group_id=groups["Verwaltung"].id)
    )

    frost = session.scalar(select(SetpointMode).where(SetpointMode.code == "frostschutz"))
    # Cannot be None after BUILTIN_MODES is created above -- only here for mypy
    # strict, which doesn't know a narrower return type for `scalar()`.
    assert frost is not None
    session.add(Setting(id=1, timezone=timezone_name, frost_protection_mode_id=frost.id))

    marker.consumed_at = utcnow()
    session.flush()
    log.info("Einrichtung abgeschlossen", extra={"username": username})
    return user_record
