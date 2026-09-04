"""The rules behind user, group, and permission management.

Two of them decide whether a running installation stays operable: you cannot
lock yourself out, and an installation-wide permission cannot be restricted
to a zone where it would never apply.
"""

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.helpers import (
    create_user,
    create_zone,
    ensure_permission,
    source,
    token_with_permissions,
    user_with_permissions,
)
from thermoctl.db.models.identity import AccessGroup, GroupPermission, UserAccessGroup
from thermoctl.db.models.lookup import Permission
from thermoctl.db.models.operations import AuditEvent
from thermoctl.domain.administration import (
    AdministrationError,
    create_group,
    delete_group,
    grant_permission,
    revoke_permission,
    set_group_permissions,
    set_password,
    set_user_active,
    set_user_group,
)
from thermoctl.domain.administration import (
    create_user as domain_create_user,
)


@pytest.fixture(autouse=True)
def _source(session: Session) -> None:
    source(session, "web")


def test_creating_a_user_writes_an_audit_entry_and_a_group_assignment(session: Session) -> None:
    group = create_group(
        session, name="Bedienung", description=None, actor_id=None
    )
    user = domain_create_user(
        session, username="neu", display_name="Neu", password="passwort-lang-genug",
        group_ids=[group.id], actor_id=None,
    )
    assignment = session.scalar(
        select(UserAccessGroup).where(UserAccessGroup.user_id == user.id)
    )
    assert assignment is not None and assignment.access_group_id == group.id
    assert session.scalar(
        select(AuditEvent).where(AuditEvent.action == "user.created")
    ) is not None


def test_a_duplicate_username_is_rejected(session: Session) -> None:
    domain_create_user(
        session, username="doppelt", display_name="Erst", password="passwort-lang-genug",
        group_ids=[], actor_id=None,
    )
    with pytest.raises(AdministrationError, match="gibt es bereits"):
        domain_create_user(
            session, username="doppelt", display_name="Zweit",
            password="passwort-lang-genug", group_ids=[], actor_id=None,
        )


def test_a_too_short_password_leaves_no_half_created_user(session: Session) -> None:
    """Exactly the bug that was in the setup form: the exception only came
    after the write had already happened."""
    from thermoctl.auth.passwords import PasswordTooShort
    from thermoctl.db.models.identity import User

    with pytest.raises(PasswordTooShort):
        domain_create_user(
            session, username="zukurz", display_name="Zu kurz", password="kurz",
            group_ids=[], actor_id=None,
        )
    assert session.scalar(select(User).where(User.username == "zukurz")) is None


def test_the_last_administrator_cannot_be_deactivated(session: Session) -> None:
    """Without this rule, a single mistake is enough to make a running
    heating control unusable — with access left only through the database."""
    administrator = user_with_permissions(session, "einziger", [("user.manage", None)])
    with pytest.raises(AdministrationError, match="letzte aktive Benutzer"):
        set_user_active(session, administrator, False, actor_id=None)
    assert administrator.is_active is True


def test_the_second_to_last_administrator_can_be_deactivated(session: Session) -> None:
    """The lock must only catch the truly last one — otherwise it would get in the way."""
    first = user_with_permissions(session, "erster", [("user.manage", None)])
    user_with_permissions(session, "zweiter", [("user.manage", None)])
    set_user_active(session, first, False, actor_id=None)
    assert first.is_active is False


def test_an_already_deactivated_second_administrator_does_not_count(session: Session) -> None:
    """A deactivated administrator cannot reactivate anyone — they do not count."""
    active = user_with_permissions(session, "aktiv", [("user.manage", None)])
    inactive = user_with_permissions(session, "inaktiv", [("user.manage", None)])
    inactive.is_active = False
    session.flush()
    with pytest.raises(AdministrationError):
        set_user_active(session, active, False, actor_id=None)


def test_a_deactivated_user_can_be_reactivated(session: Session) -> None:
    user = create_user(session, "wieder-da")
    set_user_active(session, user, False, actor_id=None)
    set_user_active(session, user, True, actor_id=None)
    assert user.is_active is True


def test_an_installation_wide_permission_cannot_be_restricted_to_a_zone(
    session: Session,
) -> None:
    """The model has held this guarantee since subproject 1 — here it gets redeemed.

    `hat_recht()` always checks a non-zone-scoped permission without a zone.
    Granted with a zone, it would sit in the permission list and never apply.
    A grant that looks like it worked is worse than one that was rejected.
    """
    ensure_permission(session, "user.manage", zone_scoped=False)
    zone = create_zone(session, "bad")
    group = create_group(session, name="Falsch", description=None, actor_id=None)
    with pytest.raises(AdministrationError, match="ganze Anlage"):
        grant_permission(session, group, "user.manage", zone.id, actor_id=None)


def test_a_zone_scoped_permission_may_carry_a_zone(session: Session) -> None:
    ensure_permission(session, "zone.read", zone_scoped=True)
    zone = create_zone(session, "küche")
    group = create_group(session, name="Küchenleser", description=None, actor_id=None)
    entry = grant_permission(session, group, "zone.read", zone.id, actor_id=None)
    assert entry.zone_id == zone.id


def test_granting_a_permission_twice_yields_one_row(session: Session) -> None:
    ensure_permission(session, "zone.read", zone_scoped=True)
    group = create_group(session, name="Doppelt", description=None, actor_id=None)
    first = grant_permission(session, group, "zone.read", None, actor_id=None)
    second = grant_permission(session, group, "zone.read", None, actor_id=None)
    assert first.id == second.id


def test_an_unknown_permission_is_rejected(session: Session) -> None:
    group = create_group(session, name="Leer", description=None, actor_id=None)
    with pytest.raises(AdministrationError, match="gibt es nicht"):
        grant_permission(session, group, "gibt.es.nicht", None, actor_id=None)


def test_a_builtin_group_cannot_be_deleted(session: Session) -> None:
    group = AccessGroup(name="Verwaltung", is_builtin=True)
    session.add(group)
    session.flush()
    with pytest.raises(AdministrationError, match="eingebaute Gruppe"):
        delete_group(session, group, actor_id=None)


def test_the_last_source_of_the_management_permission_cannot_be_removed(
    session: Session,
) -> None:
    """Neither by deleting the group nor by revoking the permission."""
    user_with_permissions(session, "verwalter", [("user.manage", None)])
    group = session.scalar(
        select(AccessGroup).where(AccessGroup.name == "gruppe-verwalter")
    )
    assert group is not None
    with pytest.raises(AdministrationError, match="einzige verbliebene"):
        delete_group(session, group, actor_id=None)

    permission_id = session.scalar(select(Permission.id).where(Permission.code == "user.manage"))
    entry = session.scalar(
        select(GroupPermission).where(
            GroupPermission.access_group_id == group.id,
            GroupPermission.permission_id == permission_id,
        )
    )
    assert entry is not None
    with pytest.raises(AdministrationError, match="einzige verbliebene"):
        revoke_permission(session, entry, actor_id=None)


def test_a_group_without_the_management_permission_can_be_deleted(session: Session) -> None:
    user_with_permissions(session, "chef", [("user.manage", None)])
    dispensable = create_group(
        session, name="Entbehrlich", description=None, actor_id=None
    )
    delete_group(session, dispensable, actor_id=None)
    assert session.get(AccessGroup, dispensable.id) is None


def test_setting_a_password_changes_the_hash_and_logs_it(session: Session) -> None:
    from thermoctl.auth.passwords import verify_password

    user = create_user(session, "wechsler")
    set_password(session, user, "ein-neues-langes-passwort", actor_id=None)
    assert verify_password("ein-neues-langes-passwort", user.password_hash)
    entry = session.scalar(
        select(AuditEvent).where(AuditEvent.action == "user.password_changed")
    )
    assert entry is not None
    assert "ein-neues-langes-passwort" not in (entry.summary + (entry.detail or "")), (
        "A password must never end up in the audit log."
    )


def test_an_empty_group_name_is_rejected(session: Session) -> None:
    with pytest.raises(AdministrationError, match="nicht leer"):
        create_group(session, name="   ", description=None, actor_id=None)


def test_an_empty_username_is_rejected(session: Session) -> None:
    with pytest.raises(AdministrationError, match="nicht leer"):
        domain_create_user(
            session, username="  ", display_name="X", password="passwort-lang-genug",
            group_ids=[], actor_id=None,
        )


def test_revoking_twice_does_not_change_the_timestamp(session: Session) -> None:
    """A second click must not push the revocation timestamp later — it is
    the answer to 'since when is this token no longer valid?'."""
    from thermoctl.domain.administration import revoke_token

    user = create_user(session, "tokenbesitzer")
    token = token_with_permissions(session, user, [])
    revoke_token(session, token, actor_id=None)
    first = token.revoked_at
    revoke_token(session, token, actor_id=None)
    assert token.revoked_at == first


def test_changing_a_users_group_changes_their_rights(session: Session) -> None:
    """This is the reported gap itself: the group of an existing user can change,
    and the change actually takes effect, not just the row in `user_access_group`."""
    from thermoctl.domain.authz import has_permission, principal_for_user

    user = user_with_permissions(session, "umgehängt", [("zone.read", None)])
    old_group = session.scalar(select(AccessGroup).where(AccessGroup.name == "gruppe-umgehängt"))
    assert old_group is not None
    new_group = _group_with_permissions_helper(session, "ziel", [("token.self", None)])

    set_user_group(session, user, new_group.id, actor_id=None)

    assert has_permission(principal_for_user(session, user), "token.self") is True
    assert has_permission(principal_for_user(session, user), "zone.read") is False
    membership = list(
        session.scalars(select(UserAccessGroup).where(UserAccessGroup.user_id == user.id))
    )
    assert [m.access_group_id for m in membership] == [new_group.id]


def test_assigning_no_group_removes_all_rights(session: Session) -> None:
    from thermoctl.domain.authz import has_permission, principal_for_user

    user = user_with_permissions(session, "entrechtet", [("zone.read", None)])
    set_user_group(session, user, None, actor_id=None)
    assert has_permission(principal_for_user(session, user), "zone.read") is False
    assert (
        session.scalar(select(UserAccessGroup).where(UserAccessGroup.user_id == user.id))
        is None
    )


def test_changing_a_users_group_writes_an_audit_entry_with_before_and_after(
    session: Session,
) -> None:
    user = user_with_permissions(session, "protokolliert", [("zone.read", None)])
    new_group = _group_with_permissions_helper(session, "neuegruppe", [])
    set_user_group(session, user, new_group.id, actor_id=None)
    entry = session.scalar(
        select(AuditEvent).where(AuditEvent.action == "user.group_changed")
    )
    assert entry is not None
    assert "gruppe-protokolliert" in entry.summary
    assert "neuegruppe" in entry.summary


def test_the_last_administrator_cannot_be_moved_out_of_the_management_group(
    session: Session,
) -> None:
    administrator = user_with_permissions(session, "einzigerverwalter", [("user.manage", None)])
    harmless = _group_with_permissions_helper(session, "harmlos", [("zone.read", None)])
    with pytest.raises(AdministrationError, match="letzte aktive Benutzer"):
        set_user_group(session, administrator, harmless.id, actor_id=None)
    membership = session.scalar(
        select(UserAccessGroup).where(UserAccessGroup.user_id == administrator.id)
    )
    assert membership is not None and membership.access_group_id != harmless.id


def test_the_last_administrator_cannot_be_moved_to_no_group(session: Session) -> None:
    """"Ohne Gruppe" is the other way to take `user.manage` away from the last
    active administrator, not just moving them into some other group."""
    administrator = user_with_permissions(session, "einzigohnegruppe", [("user.manage", None)])
    with pytest.raises(AdministrationError, match="letzte aktive Benutzer"):
        set_user_group(session, administrator, None, actor_id=None)
    assert (
        session.scalar(
            select(UserAccessGroup).where(UserAccessGroup.user_id == administrator.id)
        )
        is not None
    )


def test_the_second_to_last_administrator_can_change_group(session: Session) -> None:
    """The lock must only catch the truly last one -- otherwise it gets in the way."""
    first = user_with_permissions(session, "verwaltera", [("user.manage", None)])
    user_with_permissions(session, "verwalterb", [("user.manage", None)])
    harmless = _group_with_permissions_helper(session, "harmlos2", [("zone.read", None)])
    set_user_group(session, first, harmless.id, actor_id=None)
    membership = session.scalar(
        select(UserAccessGroup).where(UserAccessGroup.user_id == first.id)
    )
    assert membership is not None and membership.access_group_id == harmless.id


def test_the_last_administrator_can_move_to_another_management_group(
    session: Session,
) -> None:
    """The guard protects the permission, not the identity of the old group."""
    administrator = user_with_permissions(session, "wechseladmin", [("user.manage", None)])
    replacement = _group_with_permissions_helper(
        session, "andereverwaltung", [("user.manage", None)]
    )

    set_user_group(session, administrator, replacement.id, actor_id=None)

    membership = session.scalar(
        select(UserAccessGroup).where(UserAccessGroup.user_id == administrator.id)
    )
    assert membership is not None and membership.access_group_id == replacement.id


def test_reassigning_the_same_group_is_a_no_op(session: Session) -> None:
    """Resubmitting the group a user already has must not write a spurious audit
    entry and must not trip the lockout guard either."""
    administrator = user_with_permissions(session, "unverändert", [("user.manage", None)])
    current_group = session.scalar(
        select(AccessGroup).where(AccessGroup.name == "gruppe-unverändert")
    )
    assert current_group is not None
    set_user_group(session, administrator, current_group.id, actor_id=None)
    assert (
        session.scalar(select(AuditEvent).where(AuditEvent.action == "user.group_changed"))
        is None
    )


def test_an_unknown_group_id_is_rejected(session: Session) -> None:
    user = create_user(session, "fürsonstwas")
    with pytest.raises(AdministrationError, match="gibt es nicht"):
        set_user_group(session, user, 999999, actor_id=None)


def test_activation_audit_names_the_actual_new_state(session: Session) -> None:
    """The audit trail must not claim the opposite account state."""
    user = create_user(session, "audit-state")
    set_user_active(session, user, False, actor_id=None)
    set_user_active(session, user, True, actor_id=None)

    events = list(
        session.scalars(
            select(AuditEvent)
            .where(AuditEvent.object_id == str(user.id))
            .order_by(AuditEvent.id)
        )
    )
    assert [(event.action, event.summary) for event in events[-2:]] == [
        ("user.deactivated", "Benutzer 'audit-state' deaktiviert"),
        ("user.activated", "Benutzer 'audit-state' aktiviert"),
    ]


def test_zone_grants_are_distinct_and_the_audit_names_the_zone(session: Session) -> None:
    """A grant for one room must neither mask nor misdescribe a grant for another."""
    ensure_permission(session, "zone.read", zone_scoped=True)
    first_zone = create_zone(session, "grant-first")
    second_zone = create_zone(session, "grant-second")
    group = create_group(session, name="zone-grants", description=None, actor_id=None)

    first = grant_permission(session, group, "zone.read", first_zone.id, actor_id=None)
    second = grant_permission(session, group, "zone.read", second_zone.id, actor_id=None)

    assert first.id != second.id
    event = session.scalar(
        select(AuditEvent)
        .where(AuditEvent.action == "group.permission_granted")
        .order_by(AuditEvent.id.desc())
    )
    assert event is not None
    assert event.detail == f"eingeschränkt auf Zone {second_zone.id}"


def test_revoking_a_non_admin_permission_from_the_only_admin_group_is_allowed(
    session: Session,
) -> None:
    """The lockout guard applies only when the administration grant itself is removed."""
    user_with_permissions(
        session, "multi-right-admin", [("user.manage", None), ("zone.read", None)]
    )
    group = session.scalar(
        select(AccessGroup).where(AccessGroup.name == "gruppe-multi-right-admin")
    )
    permission = session.scalar(select(Permission).where(Permission.code == "zone.read"))
    assert group is not None and permission is not None
    entry = session.scalar(
        select(GroupPermission).where(
            GroupPermission.access_group_id == group.id,
            GroupPermission.permission_id == permission.id,
        )
    )
    assert entry is not None

    revoke_permission(session, entry, actor_id=None)

    assert session.get(GroupPermission, entry.id) is None


def test_an_admin_group_can_be_deleted_when_another_admin_source_remains(
    session: Session,
) -> None:
    """The lockout guard must not prevent safe administrative cleanup."""
    user_with_permissions(session, "admin-source-one", [("user.manage", None)])
    user_with_permissions(session, "admin-source-two", [("user.manage", None)])
    first_group = session.scalar(
        select(AccessGroup).where(AccessGroup.name == "gruppe-admin-source-one")
    )
    assert first_group is not None

    delete_group(session, first_group, actor_id=None)

    assert session.get(AccessGroup, first_group.id) is None


def test_setting_group_permissions_reports_and_applies_the_exact_difference(
    session: Session,
) -> None:
    """Bulk editing grants and revokes each requested row exactly once."""
    ensure_permission(session, "zone.read", zone_scoped=True)
    ensure_permission(session, "device.read", zone_scoped=False)
    zone = create_zone(session, "permission-difference")
    group = create_group(session, name="permission-difference", description=None, actor_id=None)
    grant_permission(session, group, "device.read", None, actor_id=None)

    changed = set_group_permissions(
        session,
        group,
        {("zone.read", zone.id)},
        actor_id=None,
    )

    assert changed == (1, 1)
    rows = list(
        session.execute(
            select(Permission.code, GroupPermission.zone_id)
            .join(GroupPermission, GroupPermission.permission_id == Permission.id)
            .where(GroupPermission.access_group_id == group.id)
        )
    )
    assert rows == [("zone.read", zone.id)]


def _group_with_permissions_helper(
    session: Session, name: str, permissions: list[tuple[str, int | None]]
) -> AccessGroup:
    """Local wrapper so this file does not need to import the private helper
    `tests.helpers._group_with_permissions` under its underscored name."""
    group = create_group(session, name=name, description=None, actor_id=None)
    for code, zone_id in permissions:
        ensure_permission(session, code, zone_scoped=zone_id is not None)
        grant_permission(session, group, code, zone_id, actor_id=None)
    return group
