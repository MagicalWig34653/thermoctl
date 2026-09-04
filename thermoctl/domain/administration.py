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
from thermoctl.auth.sessions import revoke_all_sessions
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
    group_ids: list[int], actor_id: int | None, source: str = "web",
) -> User:
    """Creates a user and assigns them to groups."""
    if not username.strip():
        raise AdministrationError("Der Benutzername darf nicht leer sein.")
    present = session.scalar(select(User).where(User.username == username))
    if present is not None:
        raise AdministrationError(f"Den Benutzernamen '{username}' gibt es bereits.")

    # The password first — `hash_password` raises on too short an input, and an
    # aborted creation must not leave half a row behind. This exact bug used to sit
    # in the setup form.
    hash_value = hash_password(password)

    user_record = User(username=username, display_name=display_name, password_hash=hash_value)
    session.add(user_record)
    session.flush()
    for group_id in group_ids:
        session.add(UserAccessGroup(user_id=user_record.id, access_group_id=group_id))
    session.flush()
    audit.record(
        session, source=source, action="user.created", object_type="user",
        object_id=str(user_record.id), summary=f"Benutzer '{username}' angelegt",
        user_id=actor_id,
    )
    return user_record


def set_user_active(
    session: Session, user: User, active: bool, *, actor_id: int | None,
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
            "verwalten — der Zugang wäre nur noch über die Datenbank zu retten."
        )
    user.is_active = active
    session.flush()
    audit.record(
        session, source=source,
        action="user.activated" if active else "user.deactivated",
        object_type="user", object_id=str(user.id),
        summary=f"Benutzer '{user.username}' {'aktiviert' if active else 'deaktiviert'}",
        user_id=actor_id,
    )


def _grants_admin_permission(session: Session, group_id: int | None) -> bool:
    """Whether this group carries the plant-wide `user.manage` permission."""
    if group_id is None:
        return False
    return (
        session.scalar(
            select(GroupPermission.id)
            .join(Permission, Permission.id == GroupPermission.permission_id)
            .where(
                GroupPermission.access_group_id == group_id,
                Permission.code == ADMIN_PERMISSION,
                GroupPermission.zone_id.is_(None),
            )
            .limit(1)
        )
        is not None
    )


def set_user_group(
    session: Session, user: User, group_id: int | None, *, actor_id: int | None,
    source: str = "web",
) -> None:
    """Assigns a user to exactly one group, or to none at all.

    The schema allows a user to belong to several groups at once (`UserAccessGroup`
    is a plain many-to-many table), but the interface has only ever offered a single
    `<select>` -- at creation, and now here as well. Kept that way deliberately:
    "which group is this person in" stays a single, always-answerable question
    instead of a set that could disagree with itself. Multiple concurrent group
    memberships were not part of the reported gap and are not built speculatively;
    whoever needs them can widen this function and the form that calls it later.
    """
    new_group: AccessGroup | None = None
    if group_id is not None:
        new_group = session.get(AccessGroup, group_id)
        if new_group is None:
            raise AdministrationError("Die Gruppe gibt es nicht.")

    current = list(
        session.scalars(
            select(UserAccessGroup).where(UserAccessGroup.user_id == user.id)
        )
    )
    old_ids = {m.access_group_id for m in current}
    new_ids = {group_id} if group_id is not None else set()
    if old_ids == new_ids:
        return  # No change -- nothing to guard against, nothing to log.

    # Same guard as `set_user_active`: the last active user carrying `user.manage`
    # must not be moved into a group that no longer grants it -- otherwise nobody
    # would be left to hand out permissions at all, recoverable only through the
    # database.
    if user.is_active and _last_administrator(session, user) and not _grants_admin_permission(
        session, group_id
    ):
        raise AdministrationError(
            f"'{user.username}' ist der letzte aktive Benutzer mit dem Recht "
            f"{ADMIN_PERMISSION}. Diese Gruppe würde ihm das Recht nehmen, und "
            "niemand könnte mehr Benutzer verwalten."
        )

    old_names = sorted(
        name
        for (name,) in session.execute(
            select(AccessGroup.name)
            .join(UserAccessGroup, UserAccessGroup.access_group_id == AccessGroup.id)
            .where(UserAccessGroup.user_id == user.id)
        )
    )
    old_label = ", ".join(old_names) if old_names else "ohne Gruppe"
    new_label = new_group.name if new_group is not None else "ohne Gruppe"

    for membership in current:
        session.delete(membership)
    if group_id is not None:
        session.add(UserAccessGroup(user_id=user.id, access_group_id=group_id))
    session.flush()
    audit.record(
        session, source=source, action="user.group_changed", object_type="user",
        object_id=str(user.id),
        summary=f"Gruppe von '{user.username}' geändert: {old_label} -> {new_label}",
        user_id=actor_id,
    )


def set_password(
    session: Session, user: User, new_password: str, *, actor_id: int | None,
    source: str = "web", keep_session_id: int | None = None,
) -> None:
    """Sets a new password and ends this user's other sessions.

    The opposite stood here, with the argument that a password change is usually not a
    reaction to a suspicion, and that whoever wants to end all sessions has a dedicated
    way to do it. The second half was simply not true -- no such way existed -- and the
    first half gets the trade backwards. If someone changes their password *because*
    they suspect a stolen cookie, leaving that cookie alive defeats the whole point,
    and they get no warning that it happened. The cost of the other choice is one
    re-login on their other devices.

    `keep_session_id` spares the session making the change, so the browser in front of
    the user stays logged in. An administrator resetting someone else's password
    passes nothing, and every session of that account ends.
    """
    user.password_hash = hash_password(new_password)
    session.flush()
    beendet = revoke_all_sessions(session, user.id, keep_id=keep_session_id)
    audit.record(
        session, source=source, action="user.password_changed", object_type="user",
        object_id=str(user.id),
        summary=(
            f"Passwort von '{user.username}' geändert, "
            f"{beendet} weitere Sitzung(en) beendet"
        ), user_id=actor_id,
    )


def create_group(
    session: Session, *, name: str, description: str | None, actor_id: int | None,
    source: str = "web",
) -> AccessGroup:
    if not name.strip():
        raise AdministrationError("Der Gruppenname darf nicht leer sein.")
    if session.scalar(select(AccessGroup).where(AccessGroup.name == name)) is not None:
        raise AdministrationError(f"Die Gruppe '{name}' gibt es bereits.")
    group = AccessGroup(name=name, description=description, is_builtin=False)
    session.add(group)
    session.flush()
    audit.record(
        session, source=source, action="group.created", object_type="access_group",
        object_id=str(group.id), summary=f"Gruppe '{name}' angelegt", user_id=actor_id,
    )
    return group


def delete_group(
    session: Session, group: AccessGroup, *, actor_id: int | None, source: str = "web"
) -> None:
    if group.is_builtin:
        raise AdministrationError(
            f"'{group.name}' ist eine eingebaute Gruppe und kann nicht gelöscht werden. "
            "Ihre Rechte lassen sich aber ändern."
        )
    _without_this_group_no_administrator(session, group)
    name = group.name
    session.delete(group)
    session.flush()
    audit.record(
        session, source=source, action="group.deleted", object_type="access_group",
        object_id=str(group.id), summary=f"Gruppe '{name}' gelöscht", user_id=actor_id,
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
            f"Über '{group.name}' läuft das einzige verbliebene {ADMIN_PERMISSION}. "
            "Ohne sie kann niemand mehr Benutzer verwalten."
        )


def grant_permission(
    session: Session, group: AccessGroup, code: str, zone_id: int | None, *,
    actor_id: int | None, source: str = "web",
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
            f"Das Recht '{code}' gilt für die ganze Anlage und lässt sich nicht auf "
            "eine einzelne Zone einschränken."
        )
    present = session.scalar(
        select(GroupPermission).where(
            GroupPermission.access_group_id == group.id,
            GroupPermission.permission_id == permission.id,
            GroupPermission.zone_id.is_(None) if zone_id is None
            else GroupPermission.zone_id == zone_id,
        )
    )
    if present is not None:
        return present
    entry = GroupPermission(
        access_group_id=group.id, permission_id=permission.id, zone_id=zone_id
    )
    session.add(entry)
    session.flush()
    audit.record(
        session, source=source, action="group.permission_granted",
        object_type="access_group", object_id=str(group.id),
        summary=f"Recht {code} an '{group.name}' vergeben",
        detail=None if zone_id is None else f"eingeschränkt auf Zone {zone_id}",
        user_id=actor_id,
    )
    return entry


def revoke_permission(
    session: Session, entry: GroupPermission, *, actor_id: int | None,
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
        summary=f"Recht {permission.code} von '{group.name}' entzogen", user_id=actor_id,
    )


def revoke_token(
    session: Session, token: ApiToken, *, actor_id: int | None, source: str = "web"
) -> None:
    """Revokes a token. Rows are never deleted — they are the history."""
    if token.revoked_at is not None:
        return
    token.revoked_at = utcnow()
    session.flush()
    audit.record(
        session, source=source, action="token.revoked", object_type="api_token",
        object_id=str(token.id), summary=f"Token '{token.name}' widerrufen",
        user_id=actor_id,
    )


def set_group_permissions(
    session: Session,
    group: AccessGroup,
    wanted: set[tuple[str, int | None]],
    *,
    actor_id: int | None,
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
    present: dict[tuple[str, int | None], GroupPermission] = {}
    for entry, code in session.execute(
        select(GroupPermission, Permission.code)
        .join(Permission, Permission.id == GroupPermission.permission_id)
        .where(GroupPermission.access_group_id == group.id)
    ):
        present[(code, entry.zone_id)] = entry

    taken = 0
    for code, zone_id in sorted(wanted - set(present), key=lambda p: (p[0], p[1] or 0)):
        grant_permission(
            session, group, code, zone_id, actor_id=actor_id, source=source
        )
        taken += 1

    revoked = 0
    # Grant first, then revoke: otherwise, whoever switches the administration
    # permission from "whole plant" to individual zones would fail halfway through
    # on the administrator lock.
    for key, entry in sorted(present.items(), key=lambda p: (p[0][0], p[0][1] or 0)):
        if key not in wanted:
            revoke_permission(session, entry, actor_id=actor_id, source=source)
            revoked += 1
    return taken, revoked
