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

import logging
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError
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
from thermoctl.domain.ui_profile import DEFAULT_PROFILE, WebUiProfile, parse_profile


class AdministrationError(Exception):
    """A change that is not permitted on domain grounds — not a fault of the service."""


class ConcurrentChange(AdministrationError):
    """Someone else changed the guarded permissions at the same moment; nothing was changed.

    Not a refusal on domain grounds but the loser of a race: trying again sees the
    other change and decides on it.
    """


ADMIN_PERMISSION = "user.manage"
GROUP_PERMISSION = "group.manage"
# Rechte, deren letzter Inhaber nicht verschwinden darf (sonst ließe sich nichts mehr verwalten).
GUARDED_PERMISSIONS = (ADMIN_PERMISSION, GROUP_PERMISSION)


# MariaDB/MySQL error numbers of a statement that met another transaction: 1020 "Record has
# changed since last read" (snapshot isolation, the default since MariaDB 11.6), 1205 lock
# wait timeout, 1213 deadlock; 1451/1452 a foreign key that the other transaction just broke
# (the group a user was moved into is deleted in the same moment, or the other way round);
# 1062 a unique key the other transaction just took (the same name created twice).
_COLLISION_CODES = (1020, 1062, 1205, 1213, 1451, 1452)

# 1305 "SAVEPOINT ... does not exist" is the aftermath of the same collisions: the server has
# already rolled the whole transaction back (deadlock victim, refused overwrite), so the
# `ROLLBACK TO SAVEPOINT` of a change that runs in a savepoint (`begin_nested`) finds none
# and raises this instead of the error that caused it. The number alone is not enough -- 1305
# is also "FUNCTION ... does not exist", a programming error that must not pass as a
# collision -- so the message has to name the savepoint.
_SAVEPOINT_GONE = 1305

_COLLISION_MESSAGE = (
    "Gleichzeitig hat jemand anderes etwas an der Verwaltung geändert. "
    "Es wurde nichts geändert -- bitte die Seite neu laden und es erneut versuchen."
)

log = logging.getLogger(__name__)


def is_collision(exc: DBAPIError) -> bool:
    """Whether this database error is the aftermath of a concurrent change (see above).

    Public for the places that cannot answer "try again" with a 409 -- the login, the
    resolving of a session cookie -- and decide for themselves what a collision means there.
    """
    args = exc.orig.args if exc.orig is not None else ()
    if not args:
        return False
    if args[0] == _SAVEPOINT_GONE:
        return len(args) > 1 and "SAVEPOINT" in str(args[1]).upper()
    return bool(args[0] in _COLLISION_CODES)


def _collision(exc: DBAPIError) -> ConcurrentChange:
    """The answer to a collision -- with the database's own error in the log, since the
    caller only gets the neutral sentence."""
    log.warning(
        "Gleichzeitige Verwaltungsänderung abgewiesen",
        extra={"fehler": str(exc.orig)[:200] if exc.orig is not None else str(exc)[:200]},
    )
    return ConcurrentChange(_COLLISION_MESSAGE)


@contextmanager
def serialized_change(session: Session) -> Iterator[None]:
    """Carries an administration change through to its commit.

    The collision with another change can surface anywhere up to the commit: at a locking
    read (`_locked`), at the `UPDATE`/`DELETE`/`INSERT` of the change itself (snapshot
    isolation refuses to overwrite a row the other transaction committed meanwhile), or at
    a foreign key the other transaction broke. Every such error becomes `ConcurrentChange`
    with the transaction rolled back, nothing changed -- never a 500, and the commit is made
    here, inside the guard, not after the response.
    """
    try:
        yield
        session.flush()
        session.commit()
    except (OperationalError, IntegrityError) as exc:
        if not is_collision(exc):
            raise
        session.rollback()
        raise _collision(exc) from exc


def _locked(session: Session, statement: Select[Any]) -> Sequence[Any]:
    """Runs a read of the guard as a *locking* read.

    Every check below answers "would anybody still be able to manage afterwards?" -- and
    that answer is wrong the moment the other administrator is changed by a transaction
    that has not committed yet. Two administrators who deactivate, regroup or demote each
    other at the same time would each see the other one still in place; both checks pass,
    both commit, nobody is left. A plain `SELECT` cannot prevent that: under REPEATABLE
    READ it reads the snapshot taken at the transaction's first read, and what the other
    transaction committed since is invisible to it. A locking read waits for the other
    transaction and reads what it left (MariaDB with snapshot isolation refuses instead,
    1020, and the loser tries again). SQLite has no row locks and one writer at a time;
    there the clause is dropped.

    **Every** read that feeds a decision about the guarded permissions goes through here,
    also the ones that look harmless (`_grants_admin_permission`: does the group the user
    is moved into still carry the permission?) -- a plain read next to locking ones sees the
    old snapshot and lets the check pass on stale data.
    """
    try:
        return list(session.execute(statement.with_for_update()).scalars())
    except OperationalError as exc:
        if is_collision(exc):
            raise _collision(exc) from exc
        raise


def lock_guarded_permissions(session: Session) -> None:
    """Serialises every change that can take away the last holder of a guarded permission.

    First thing in each such change (and before it writes anything, or two transactions can
    wait for each other's rows): the two permission rows are locked (in the order of their
    code, the index the lookup uses), so the changes queue up one behind the other instead of
    running side by side. The rows themselves never change; they are only the common thing
    to wait on. The reads of the checks that follow are locking reads too (`_locked`), so
    they see the state the previous change left.
    """
    _locked(
        session,
        select(Permission.id)
        .where(Permission.code.in_(GUARDED_PERMISSIONS))
        .order_by(Permission.code),
    )


def _user_with_permission(
    session: Session, code: str, *, only_active: bool = True
) -> list[User]:
    """All users who hold this permission plant-wide (by default only the active ones)."""
    return list(
        _locked(
            session,
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
                User.is_active.is_(True) if only_active else User.id.is_not(None),
            )
            .distinct(),
        )
    )


def holders_of(session: Session, code: str) -> set[int]:
    """Ids aller Inhaber des anlagenweiten Rechts, inaktive Konten eingeschlossen."""
    return {u.id for u in _user_with_permission(session, code, only_active=False)}


def is_sole_holder(session: Session, user_id: int, code: str = ADMIN_PERMISSION) -> bool:
    """Ob dieser Benutzer der einzige Inhaber des anlagenweiten Rechts ist.

    Inaktive Konten zählen mit: sonst ließe sich die Eigenschaft "einziger Inhaber" durch
    kurzes Deaktivieren eines zweiten Kontos selbst herstellen.
    """
    return [b.id for b in _user_with_permission(session, code, only_active=False)] == [user_id]


def _last_holder_of(session: Session, user: User) -> list[str]:
    """Alle geschützten Rechte, deren letzter aktiver Inhaber dieser Benutzer ist."""
    return [
        code
        for code in GUARDED_PERMISSIONS
        if [b.id for b in _user_with_permission(session, code)] == [user.id]
    ]


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
    if not active:
        lock_guarded_permissions(session)
    held = _last_holder_of(session, user) if not active else []
    if held:
        raise AdministrationError(
            f"'{user.username}' ist der letzte aktive Benutzer mit dem Recht "
            f"{held[0]}. Wird er deaktiviert, kann niemand mehr {_what_it_manages(held[0])} "
            "— der Zugang wäre nur noch über die Datenbank zu retten."
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


def _what_it_manages(code: str) -> str:
    return "Benutzer verwalten" if code == ADMIN_PERMISSION else "Gruppen und Rechte verwalten"


def _grants_admin_permission(
    session: Session, group_id: int | None, code: str = ADMIN_PERMISSION
) -> bool:
    """Whether this group carries this plant-wide permission (a locking read, see `_locked`)."""
    if group_id is None:
        return False
    return bool(
        _locked(
            session,
            select(GroupPermission.id)
            .join(Permission, Permission.id == GroupPermission.permission_id)
            .where(
                GroupPermission.access_group_id == group_id,
                Permission.code == code,
                GroupPermission.zone_id.is_(None),
            )
            .limit(1),
        )
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
    lock_guarded_permissions(session)
    new_group: AccessGroup | None = None
    if group_id is not None:
        new_group = session.get(AccessGroup, group_id)
        if new_group is None:
            raise AdministrationError("Die Gruppe gibt es nicht.")

    current = list(
        _locked(session, select(UserAccessGroup).where(UserAccessGroup.user_id == user.id))
    )
    old_ids = {m.access_group_id for m in current}
    new_ids = {group_id} if group_id is not None else set()
    if old_ids == new_ids:
        return  # No change -- nothing to guard against, nothing to log.

    # Same guard as `set_user_active`: the last active user carrying `user.manage`
    # must not be moved into a group that no longer grants it -- otherwise nobody
    # would be left to hand out permissions at all, recoverable only through the
    # database.
    for held in _last_holder_of(session, user) if user.is_active else []:
        if not _grants_admin_permission(session, group_id, held):
            raise AdministrationError(
                f"'{user.username}' ist der letzte aktive Benutzer mit dem Recht "
                f"{held}. Diese Gruppe würde ihm das Recht nehmen, und "
                f"niemand könnte mehr {_what_it_manages(held)}."
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
    source: str = "web", ui_profile: WebUiProfile = DEFAULT_PROFILE,
) -> AccessGroup:
    if not name.strip():
        raise AdministrationError("Der Gruppenname darf nicht leer sein.")
    if session.scalar(select(AccessGroup).where(AccessGroup.name == name)) is not None:
        raise AdministrationError(f"Die Gruppe '{name}' gibt es bereits.")
    group = AccessGroup(
        name=name, description=description, is_builtin=False,
        ui_profile=ui_profile.value,
    )
    session.add(group)
    session.flush()
    audit.record(
        session, source=source, action="group.created", object_type="access_group",
        object_id=str(group.id),
        summary=f"Gruppe '{name}' angelegt ({ui_profile.value})", user_id=actor_id,
    )
    return group


def set_group_ui_profile(
    session: Session, group: AccessGroup, ui_profile: WebUiProfile, *,
    actor_id: int | None, source: str = "web",
) -> None:
    """Stellt die Oberfläche einer Gruppe um -- Anlagensicht oder Wohnungssicht.

    Ausdrücklich keine Rechteänderung: die Mitglieder behalten jedes Recht, das sie
    hatten. Das Profil steuert nur noch die Darstellung (Wortwahl, Wohnungsstart); Sichtbarkeit
    und Zugriff folgen allein den Rechten, deshalb gibt es hier keine Sperre.
    """
    if parse_profile(group.ui_profile) is ui_profile:
        return
    group.ui_profile = ui_profile.value
    session.flush()
    audit.record(
        session, source=source, action="group.ui_profile_changed",
        object_type="access_group", object_id=str(group.id),
        summary=f"Oberfläche von '{group.name}' auf {ui_profile.value} gestellt",
        user_id=actor_id,
    )


def delete_group(
    session: Session, group: AccessGroup, *, actor_id: int | None, source: str = "web"
) -> None:
    if group.is_builtin:
        raise AdministrationError(
            f"'{group.name}' ist eine eingebaute Gruppe und kann nicht gelöscht werden. "
            "Ihre Rechte lassen sich aber ändern."
        )
    lock_guarded_permissions(session)
    _without_this_group_no_administrator(session, group)
    name = group.name
    session.delete(group)
    session.flush()
    audit.record(
        session, source=source, action="group.deleted", object_type="access_group",
        object_id=str(group.id), summary=f"Gruppe '{name}' gelöscht", user_id=actor_id,
    )


def _without_this_group_no_administrator(
    session: Session, group: AccessGroup, only: str | None = None
) -> None:
    """Prevents the last source of a guarded permission from disappearing.

    Guarded are `user.manage` and `group.manage` (`GUARDED_PERMISSIONS`); `only` limits
    the check to one of them (revoking a single permission).
    """
    for code in (only,) if only is not None else GUARDED_PERMISSIONS:
        if not _user_with_permission(session, code):
            continue
        other_source = _locked(
            session,
            select(GroupPermission.id)
            .join(Permission, Permission.id == GroupPermission.permission_id)
            .join(
                UserAccessGroup,
                UserAccessGroup.access_group_id == GroupPermission.access_group_id,
            )
            .join(User, User.id == UserAccessGroup.user_id)
            .where(
                Permission.code == code,
                GroupPermission.zone_id.is_(None),
                GroupPermission.access_group_id != group.id,
                User.is_active.is_(True),
            )
            .limit(1),
        )
        if not other_source:
            raise AdministrationError(
                f"Über '{group.name}' läuft das einzige verbliebene {code}. "
                f"Ohne sie kann niemand mehr {_what_it_manages(code)}."
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
    if permission.code in GUARDED_PERMISSIONS and entry.zone_id is None:
        lock_guarded_permissions(session)
        _without_this_group_no_administrator(session, group, only=permission.code)
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
    # Before the first write: a transaction that already wrote and then waits for the lock,
    # while another one holding it waits for those rows, would be a deadlock.
    lock_guarded_permissions(session)
    # A locking read, like every read that feeds the checks below: a plain one would show the
    # snapshot from before the lock, with entries another change has deleted meanwhile.
    codes = {pid: code for pid, code in session.execute(select(Permission.id, Permission.code))}
    present: dict[tuple[str, int | None], GroupPermission] = {}
    for entry in _locked(
        session, select(GroupPermission).where(GroupPermission.access_group_id == group.id)
    ):
        present[(codes[entry.permission_id], entry.zone_id)] = entry

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
