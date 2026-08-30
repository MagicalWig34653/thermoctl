"""Changing users, groups and permissions — the rules for it, not the forms.

Lives in the domain so that the interface, REST and MCP use the same rules. Two of
those rules are the actual reason for this module:

- **You cannot lock yourself out.** The last active user with `user.manage` can
  neither be deactivated nor removed from their group. Without this rule, a single
  mistake would be enough to make a running heating control system unmanageable --
  with access recoverable only through the database.
- **A permission that is not zone-scoped must not carry a zone.** `hat_recht()` always
  queries such permissions without a zone reference; a `user.manage` granted with a
  zone would sit in the list but never take effect. The model has held this as a
  guarantee of the domain logic since sub-project 1 -- up to now nobody had actually
  enforced it.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl import audit
from thermoctl.auth.passwords import hash_password
from thermoctl.db.base import utcnow
from thermoctl.db.models.credential import ApiToken
from thermoctl.db.models.identity import (
    AccessGroup,
    GroupPermission,
    User,
    UserAccessGroup,
)
from thermoctl.db.models.lookup import Permission


class AdministrationError(Exception):
    """A change that is not permitted on domain grounds — not a fault of the service."""


ADMIN_PERMISSION = "user.manage"


def _user_with_permission(session: Session, code: str) -> list[User]:
    """All active users who hold this permission plant-wide."""
    return list(
        session.scalars(
            select(User)
            .join(UserAccessGroup, UserAccessGroup.user_id == User.id)
            .join(
                GroupPermission,
                GroupPermission.access_group_id == UserAccessGroup.access_group_id,
            )
            .join(Permission, Permission.id == GroupPermission.permission_id)
            .where(
                Permission.code == code,
                GroupPermission.zone_id.is_(None),
                User.is_active.is_(True),
            )
            .distinct()
        )
    )


def _last_administrator(session: Session, user: User) -> bool:
    administrator = _user_with_permission(session, ADMIN_PERMISSION)
    return [b.id for b in administrator] == [user.id]


def create_user(
    session: Session, *, username: str, display_name: str, password: str,
    group_ids: list[int], akteur_id: int | None, source: str = "web",
) -> User:
    """Creates a user and assigns them to groups."""
    if not username.strip():
        raise AdministrationError("Der Benutzername darf nicht leer sein.")
    vorhanden = session.scalar(select(User).where(User.username == username))
    if vorhanden is not None:
        raise AdministrationError(f"Den Benutzernamen '{username}' gibt es bereits.")

    # The password first — `hash_password` raises on too short an input, and an
    # aborted creation must not leave half a row behind. This exact bug used to sit
    # in the setup form.
    hash_value = hash_password(password)

    nutzer = User(username=username, display_name=display_name, password_hash=hash_value)
    session.add(nutzer)
    session.flush()
    for group_id in group_ids:
        session.add(UserAccessGroup(user_id=nutzer.id, access_group_id=group_id))
    session.flush()
    audit.record(
        session, source=source, action="user.created", object_type="user",
        object_id=str(nutzer.id), summary=f"Benutzer '{username}' angelegt",
        user_id=akteur_id,
    )
    return nutzer


def set_user_active(
    session: Session, user: User, active: bool, *, akteur_id: int | None,
    source: str = "web",
) -> None:
    """Deactivates or reactivates a user. Never deleted.

    A deleted user would tear their audit entries down with them, or leave them
    without a name. Deactivated, it stays traceable who did what and when.
    """
    if not active and _last_administrator(session, user):
        raise AdministrationError(
            f"'{user.username}' ist der letzte aktive Benutzer mit dem Recht "
            f"{ADMIN_PERMISSION}. Wird er deaktiviert, kann niemand mehr Benutzer "
            "verwalten — der Zugang waere nur noch ueber die Datenbank zu retten."
        )
    user.is_active = active
    session.flush()
    audit.record(
        session, source=source,
        action="user.activated" if active else "user.deactivated",
        object_type="user", object_id=str(user.id),
        summary=f"Benutzer '{user.username}' {'aktiviert' if active else 'deaktiviert'}",
        user_id=akteur_id,
    )


def set_password(
    session: Session, user: User, new_password: str, *, akteur_id: int | None,
    source: str = "web",
) -> None:
    """Sets a new password. Existing sessions remain valid.

    Deliberately so: in everyday use, a password change is usually not a reaction to a
    suspicion. Whoever wants to end all sessions revokes them explicitly -- there is a
    dedicated way to do that, instead of conflating two intents into one action.
    """
    user.password_hash = hash_password(new_password)
    session.flush()
    audit.record(
        session, source=source, action="user.password_changed", object_type="user",
        object_id=str(user.id),
        summary=f"Passwort von '{user.username}' geaendert", user_id=akteur_id,
    )


def create_group(
    session: Session, *, name: str, beschreibung: str | None, akteur_id: int | None,
    source: str = "web",
) -> AccessGroup:
    if not name.strip():
        raise AdministrationError("Der Gruppenname darf nicht leer sein.")
    if session.scalar(select(AccessGroup).where(AccessGroup.name == name)) is not None:
        raise AdministrationError(f"Die Gruppe '{name}' gibt es bereits.")
    group = AccessGroup(name=name, description=beschreibung, is_builtin=False)
    session.add(group)
    session.flush()
    audit.record(
        session, source=source, action="group.created", object_type="access_group",
        object_id=str(group.id), summary=f"Gruppe '{name}' angelegt", user_id=akteur_id,
    )
    return group


def delete_group(
    session: Session, group: AccessGroup, *, akteur_id: int | None, source: str = "web"
) -> None:
    if group.is_builtin:
        raise AdministrationError(
            f"'{group.name}' ist eine eingebaute Gruppe und kann nicht geloescht werden. "
            "Ihre Rechte lassen sich aber aendern."
        )
    _without_this_group_no_administrator(session, group)
    name = group.name
    session.delete(group)
    session.flush()
    audit.record(
        session, source=source, action="group.deleted", object_type="access_group",
        object_id=str(group.id), summary=f"Gruppe '{name}' geloescht", user_id=akteur_id,
    )


def _without_this_group_no_administrator(session: Session, group: AccessGroup) -> None:
    """Prevents the last source of the administration permission from disappearing."""
    administrator = _user_with_permission(session, ADMIN_PERMISSION)
    if not administrator:
        return
    other_source = session.scalar(
        select(GroupPermission.id)
        .join(Permission, Permission.id == GroupPermission.permission_id)
        .join(
            UserAccessGroup,
            UserAccessGroup.access_group_id == GroupPermission.access_group_id,
        )
        .join(User, User.id == UserAccessGroup.user_id)
        .where(
            Permission.code == ADMIN_PERMISSION,
            GroupPermission.zone_id.is_(None),
            GroupPermission.access_group_id != group.id,
            User.is_active.is_(True),
        )
        .limit(1)
    )
    if other_source is None:
        raise AdministrationError(
            f"Ueber '{group.name}' laeuft das einzige verbliebene {ADMIN_PERMISSION}. "
            "Ohne sie kann niemand mehr Benutzer verwalten."
        )


def grant_permission(
    session: Session, group: AccessGroup, code: str, zone_id: int | None, *,
    akteur_id: int | None, source: str = "web",
) -> GroupPermission:
    """Grants a permission to a group, optionally restricted to a zone."""
    permission = session.scalar(select(Permission).where(Permission.code == code))
    if permission is None:
        raise AdministrationError(f"Das Recht '{code}' gibt es nicht.")
    if not permission.is_zone_scoped and zone_id is not None:
        # `hat_recht()` always queries such a permission without a zone reference.
        # Granted with a zone, it would sit in the list and never take effect — a
        # grant that looks like it worked is worse than a rejected one.
        raise AdministrationError(
            f"Das Recht '{code}' gilt fuer die ganze Anlage und laesst sich nicht auf "
            "eine einzelne Zone einschraenken."
        )
    vorhanden = session.scalar(
        select(GroupPermission).where(
            GroupPermission.access_group_id == group.id,
            GroupPermission.permission_id == permission.id,
            GroupPermission.zone_id.is_(None) if zone_id is None
            else GroupPermission.zone_id == zone_id,
        )
    )
    if vorhanden is not None:
        return vorhanden
    entry = GroupPermission(
        access_group_id=group.id, permission_id=permission.id, zone_id=zone_id
    )
    session.add(entry)
    session.flush()
    audit.record(
        session, source=source, action="group.permission_granted",
        object_type="access_group", object_id=str(group.id),
        summary=f"Recht {code} an '{group.name}' vergeben",
        detail=None if zone_id is None else f"eingeschraenkt auf Zone {zone_id}",
        user_id=akteur_id,
    )
    return entry


def revoke_permission(
    session: Session, entry: GroupPermission, *, akteur_id: int | None,
    source: str = "web",
) -> None:
    group = session.get(AccessGroup, entry.access_group_id)
    permission = session.get(Permission, entry.permission_id)
    assert group is not None and permission is not None
    if permission.code == ADMIN_PERMISSION and entry.zone_id is None:
        _without_this_group_no_administrator(session, group)
    session.delete(entry)
    session.flush()
    audit.record(
        session, source=source, action="group.permission_revoked",
        object_type="access_group", object_id=str(group.id),
        summary=f"Recht {permission.code} von '{group.name}' entzogen", user_id=akteur_id,
    )


def revoke_token(
    session: Session, token: ApiToken, *, akteur_id: int | None, source: str = "web"
) -> None:
    """Revokes a token. Rows are never deleted — they are the history."""
    if token.revoked_at is not None:
        return
    token.revoked_at = utcnow()
    session.flush()
    audit.record(
        session, source=source, action="token.revoked", object_type="api_token",
        object_id=str(token.id), summary=f"Token '{token.name}' widerrufen",
        user_id=akteur_id,
    )


def set_group_permissions(
    session: Session,
    group: AccessGroup,
    gewuenscht: set[tuple[str, int | None]],
    *,
    akteur_id: int | None,
    source: str = "web",
) -> tuple[int, int]:
    """Brings a group's permissions to the desired state. Returns (granted, revoked).

    Instead of individual grant and revoke clicks: the interface sends the whole
    desired state, and the difference is computed here. Whoever sets up a group thinks
    in terms of "this is what it should be allowed to do", not a sequence of sixteen
    individual steps.

    Deliberately calls `recht_vergeben` and `recht_entziehen` instead of touching rows
    itself: that is where the check for zone-less permissions, the lock against losing
    the last administrator, and the audit entries all live. A second version of that
    logic would be exactly the kind of shortcut that later locks someone out.
    """
    vorhanden: dict[tuple[str, int | None], GroupPermission] = {}
    for entry, code in session.execute(
        select(GroupPermission, Permission.code)
        .join(Permission, Permission.id == GroupPermission.permission_id)
        .where(GroupPermission.access_group_id == group.id)
    ):
        vorhanden[(code, entry.zone_id)] = entry

    vergeben = 0
    for code, zone_id in sorted(gewuenscht - set(vorhanden), key=lambda p: (p[0], p[1] or 0)):
        grant_permission(
            session, group, code, zone_id, akteur_id=akteur_id, source=source
        )
        vergeben += 1

    entzogen = 0
    # Grant first, then revoke: otherwise, whoever switches the administration
    # permission from "whole plant" to individual zones would fail halfway through
    # on the administrator lock.
    for schluessel, entry in sorted(vorhanden.items(), key=lambda p: (p[0][0], p[0][1] or 0)):
        if schluessel not in gewuenscht:
            revoke_permission(session, entry, akteur_id=akteur_id, source=source)
            entzogen += 1
    return vergeben, entzogen
