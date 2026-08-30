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
    set_password,
    set_user_active,
)
from thermoctl.domain.administration import (
    create_user as domain_create_user,
)


@pytest.fixture(autouse=True)
def _source(session: Session) -> None:
    source(session, "web")


def test_creating_a_user_writes_an_audit_entry_and_a_group_assignment(session: Session) -> None:
    group = create_group(
        session, name="Bedienung", beschreibung=None, akteur_id=None
    )
    user = domain_create_user(
        session, username="neu", display_name="Neu", password="passwort-lang-genug",
        group_ids=[group.id], akteur_id=None,
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
        group_ids=[], akteur_id=None,
    )
    with pytest.raises(AdministrationError, match="gibt es bereits"):
        domain_create_user(
            session, username="doppelt", display_name="Zweit",
            password="passwort-lang-genug", group_ids=[], akteur_id=None,
        )


def test_a_too_short_password_leaves_no_half_created_user(session: Session) -> None:
    """Exactly the bug that was in the setup form: the exception only came
    after the write had already happened."""
    from thermoctl.auth.passwords import PasswordTooShort
    from thermoctl.db.models.identity import User

    with pytest.raises(PasswordTooShort):
        domain_create_user(
            session, username="zukurz", display_name="Zu kurz", password="kurz",
            group_ids=[], akteur_id=None,
        )
    assert session.scalar(select(User).where(User.username == "zukurz")) is None


def test_the_last_administrator_cannot_be_deactivated(session: Session) -> None:
    """Without this rule, a single mistake is enough to make a running
    heating control unusable — with access left only through the database."""
    administrator = user_with_permissions(session, "einziger", [("user.manage", None)])
    with pytest.raises(AdministrationError, match="letzte aktive Benutzer"):
        set_user_active(session, administrator, False, akteur_id=None)
    assert administrator.is_active is True


def test_the_second_to_last_administrator_can_be_deactivated(session: Session) -> None:
    """The lock must only catch the truly last one — otherwise it would get in the way."""
    first = user_with_permissions(session, "erster", [("user.manage", None)])
    user_with_permissions(session, "zweiter", [("user.manage", None)])
    set_user_active(session, first, False, akteur_id=None)
    assert first.is_active is False


def test_an_already_deactivated_second_administrator_does_not_count(session: Session) -> None:
    """A deactivated administrator cannot reactivate anyone — they do not count."""
    active = user_with_permissions(session, "aktiv", [("user.manage", None)])
    inactive = user_with_permissions(session, "inaktiv", [("user.manage", None)])
    inactive.is_active = False
    session.flush()
    with pytest.raises(AdministrationError):
        set_user_active(session, active, False, akteur_id=None)


def test_a_deactivated_user_can_be_reactivated(session: Session) -> None:
    user = create_user(session, "wieder-da")
    set_user_active(session, user, False, akteur_id=None)
    set_user_active(session, user, True, akteur_id=None)
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
    group = create_group(session, name="Falsch", beschreibung=None, akteur_id=None)
    with pytest.raises(AdministrationError, match="ganze Anlage"):
        grant_permission(session, group, "user.manage", zone.id, akteur_id=None)


def test_a_zone_scoped_permission_may_carry_a_zone(session: Session) -> None:
    ensure_permission(session, "zone.read", zone_scoped=True)
    zone = create_zone(session, "kueche")
    group = create_group(session, name="Kuechenleser", beschreibung=None, akteur_id=None)
    entry = grant_permission(session, group, "zone.read", zone.id, akteur_id=None)
    assert entry.zone_id == zone.id


def test_granting_a_permission_twice_yields_one_row(session: Session) -> None:
    ensure_permission(session, "zone.read", zone_scoped=True)
    group = create_group(session, name="Doppelt", beschreibung=None, akteur_id=None)
    first = grant_permission(session, group, "zone.read", None, akteur_id=None)
    second = grant_permission(session, group, "zone.read", None, akteur_id=None)
    assert first.id == second.id


def test_an_unknown_permission_is_rejected(session: Session) -> None:
    group = create_group(session, name="Leer", beschreibung=None, akteur_id=None)
    with pytest.raises(AdministrationError, match="gibt es nicht"):
        grant_permission(session, group, "gibt.es.nicht", None, akteur_id=None)


def test_a_builtin_group_cannot_be_deleted(session: Session) -> None:
    group = AccessGroup(name="Verwaltung", is_builtin=True)
    session.add(group)
    session.flush()
    with pytest.raises(AdministrationError, match="eingebaute Gruppe"):
        delete_group(session, group, akteur_id=None)


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
        delete_group(session, group, akteur_id=None)

    permission_id = session.scalar(select(Permission.id).where(Permission.code == "user.manage"))
    entry = session.scalar(
        select(GroupPermission).where(
            GroupPermission.access_group_id == group.id,
            GroupPermission.permission_id == permission_id,
        )
    )
    assert entry is not None
    with pytest.raises(AdministrationError, match="einzige verbliebene"):
        revoke_permission(session, entry, akteur_id=None)


def test_a_group_without_the_management_permission_can_be_deleted(session: Session) -> None:
    user_with_permissions(session, "chef", [("user.manage", None)])
    dispensable = create_group(
        session, name="Entbehrlich", beschreibung=None, akteur_id=None
    )
    delete_group(session, dispensable, akteur_id=None)
    assert session.get(AccessGroup, dispensable.id) is None


def test_setting_a_password_changes_the_hash_and_logs_it(session: Session) -> None:
    from thermoctl.auth.passwords import verify_password

    user = create_user(session, "wechsler")
    set_password(session, user, "ein-neues-langes-passwort", akteur_id=None)
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
        create_group(session, name="   ", beschreibung=None, akteur_id=None)


def test_an_empty_username_is_rejected(session: Session) -> None:
    with pytest.raises(AdministrationError, match="nicht leer"):
        domain_create_user(
            session, username="  ", display_name="X", password="passwort-lang-genug",
            group_ids=[], akteur_id=None,
        )


def test_revoking_twice_does_not_change_the_timestamp(session: Session) -> None:
    """A second click must not push the revocation timestamp later — it is
    the answer to 'since when is this token no longer valid?'."""
    from thermoctl.domain.administration import revoke_token

    user = create_user(session, "tokenbesitzer")
    token = token_with_permissions(session, user, [])
    revoke_token(session, token, akteur_id=None)
    first = token.revoked_at
    revoke_token(session, token, akteur_id=None)
    assert token.revoked_at == first
