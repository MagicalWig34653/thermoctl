"""Ersatztests fuer Sicherheitsaussagen der entfernten Alt-Oberflaeche (Zugang/Konto).

Jede Aussage hier hatte nur einen Alt-Test gegen eine entfernte HTML-Route und keinen
Gegenpart gegen `/ui-api/access`. Siehe `docs/ui-migration/SICHERHEITSTESTS.md`.
"""

# ruff: noqa: E501

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.helpers import create_all_permissions, create_settings, source, user_with_permissions
from tests.test_ui_api_access import ADMIN, Als, _call, _csrf, _err
from thermoctl.db.models.credential import ApiToken, ApiTokenPermission
from thermoctl.db.models.identity import AccessGroup, UserAccessGroup
from thermoctl.db.models.operations import AuditEvent
from thermoctl.domain.authz import has_permission, principal_for_user


@pytest.fixture(autouse=True)
def _base(session: Session) -> None:
    create_settings(session)
    source(session, "web")
    create_all_permissions(session)


def test_a_group_change_through_the_interface_audits_before_and_after_and_removal_takes_the_rights(
    client_als: Als, session: Session
) -> None:
    person = user_with_permissions(session, "protokolliert-web", [("zone.read", None)])
    old_group = session.scalar(
        select(AccessGroup).where(AccessGroup.name == "gruppe-protokolliert-web")
    )
    assert old_group is not None
    new_group = AccessGroup(name="Neuegruppeweb")
    session.add(new_group)
    session.flush()
    client = client_als(ADMIN)
    url = f"/ui-api/access/users/{person.id}/group"

    moved = _call(client, "PUT", url, {"group_id": new_group.id})
    assert moved.status_code == 200, moved.text
    entries = session.scalars(
        select(AuditEvent).where(AuditEvent.action == "user.group_changed")
    ).all()
    assert len(entries) == 1
    assert old_group.name in entries[0].summary and new_group.name in entries[0].summary

    # Gruppe entfernen: die Rechte der Person sind weg, die Mitgliedschaft auch.
    assert has_permission(principal_for_user(session, person), "zone.read") is False
    session.add(UserAccessGroup(user_id=person.id, access_group_id=old_group.id))
    session.query(UserAccessGroup).filter_by(
        user_id=person.id, access_group_id=new_group.id
    ).delete()
    session.flush()
    assert has_permission(principal_for_user(session, person), "zone.read") is True
    removed = _call(client, "PUT", url, {"group_id": None})
    assert removed.status_code == 200, removed.text
    session.expire_all()
    assert has_permission(principal_for_user(session, person), "zone.read") is False
    assert (
        session.scalar(select(UserAccessGroup).where(UserAccessGroup.user_id == person.id))
        is None
    )
    assert (
        session.query(AuditEvent).filter_by(action="user.group_changed").count() == 2
    )


def test_a_non_numeric_validity_or_zone_is_refused_without_a_token_or_plaintext(
    client_als: Als, session: Session
) -> None:
    client = client_als(ADMIN)
    count = session.query(ApiToken).count()
    headers = _csrf(client)

    def issue(**body: Any) -> Any:
        return client.request("POST", "/ui-api/access/tokens", json=body, headers=headers)

    for body in (
        {"name": "Ungueltig", "preset": "read_only", "valid_days": "abc"},
        {"name": "Ungueltig", "permissions": [{"code": "zone.read", "zone_id": "keine-zahl"}]},
    ):
        response = issue(**body)
        _err(response, 422, "validation")
        assert "tctl_" not in response.text
    assert session.query(ApiToken).count() == count

    # Gleiches beim Aendern: der Bestand bleibt unveraendert.
    issued = issue(name="Bestand", permissions=[{"code": "zone.read", "zone_id": None}]).json()
    token_id = issued["token"]["id"]
    _err(
        _call(
            client,
            "PUT",
            f"/ui-api/access/tokens/{token_id}/permissions",
            {"permissions": [{"code": "zone.read", "zone_id": "keine-zahl"}]},
        ),
        422,
        "validation",
    )
    assert len(
        session.scalars(
            select(ApiTokenPermission).where(ApiTokenPermission.api_token_id == token_id)
        ).all()
    ) == 1


def test_a_group_permission_entry_with_an_unparsable_zone_changes_nothing(
    client_als: Als, session: Session
) -> None:
    client: TestClient = client_als(ADMIN)
    group = AccessGroup(name="Rechtegruppe", description="fuer den Test")
    session.add(group)
    session.flush()
    _err(
        _call(
            client,
            "PUT",
            f"/ui-api/access/groups/{group.id}/permissions",
            {"permissions": [{"code": "zone.read", "zone_id": "keine-zahl"}]},
        ),
        422,
        "validation",
    )
    from thermoctl.db.models.identity import GroupPermission

    assert (
        session.scalars(
            select(GroupPermission).where(GroupPermission.access_group_id == group.id)
        ).all()
        == []
    )
