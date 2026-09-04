from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.helpers import create_settings, create_zone, source, user_with_permissions
from thermoctl.auth.tokens import issue_token
from thermoctl.domain.authz import Forbidden


def test_token_klartext_erscheint_genau_einmal(session: Session) -> None:
    user_record = user_with_permissions(session, "a", [("zone.read", None), ("token.self", None)])
    token, plaintext = issue_token(session, user_record, "HA", [("zone.read", None)], None)
    assert plaintext.startswith("tctl_")
    assert plaintext not in (token.token_hash, token.prefix)


def test_a_token_with_more_permissions_than_its_owner_is_refused(session: Session) -> None:
    user_record = user_with_permissions(session, "b", [("zone.read", None), ("token.self", None)])
    with pytest.raises(Forbidden):
        issue_token(session, user_record, "Zuviel", [("zone.manage", None)], None)


def test_a_token_for_a_foreign_zone_is_refused(session: Session) -> None:
    bad = create_zone(session, "bad")
    küche = create_zone(session, "küche")
    user_record = user_with_permissions(session, "c", [("zone.read", bad.id), ("token.self", None)])
    with pytest.raises(Forbidden):
        issue_token(session, user_record, "Fremd", [("zone.read", küche.id)], None)


def test_the_user_list_needs_user_manage(client_als) -> None:
    ohne = client_als([("zone.read", None)])
    assert ohne.get("/users").status_code == 403
    mit = client_als([("user.manage", None)])
    assert mit.get("/users").status_code == 200


def test_the_password_hash_appears_in_no_view(client_als) -> None:
    response = client_als([("user.manage", None)]).get("/users")
    assert "$argon2id$" not in response.text


@pytest.fixture(autouse=True)
def _actor_source(session: Session) -> None:
    """The source `web` is created in production by the reference data migration.

    `Base.metadata.create_all()` in the `engine` fixture only creates the schema, no
    reference data -- without this line, every audit entry of the mutating views
    would fail on the NOT NULL constraint on `audit_event.source_id`.
    """
    source(session, "web")


def _with_csrf(client, session):  # type: ignore[no-untyped-def]
    """Header with a valid CSRF token for mutating requests."""
    from thermoctl.auth.csrf import csrf_token
    from thermoctl.auth.sessions import COOKIE_NAME
    from thermoctl.config import get_settings

    secret = client.cookies[COOKIE_NAME]
    return {"X-CSRF-Token": csrf_token(secret, get_settings().secret_key.get_secret_value())}


def test_creating_a_user_through_the_interface(client_als, session: Session) -> None:
    c = client_als([("user.manage", None)])
    response = c.post(
        "/users",
        data={"username": "neuling", "display_name": "Neuling",
              "password": "passwort-lang-genug", "group_id": ""},
        headers=_with_csrf(c, session),
        follow_redirects=False,
    )
    assert response.status_code == 303
    from sqlalchemy import select

    from thermoctl.db.models.identity import User

    assert session.scalar(select(User).where(User.username == "neuling")) is not None


def test_a_password_that_is_too_short_returns_to_the_form(client_als, session: Session) -> None:
    """No 500, no blank form -- and the username stays filled in."""
    c = client_als([("user.manage", None)])
    response = c.post(
        "/users",
        data={"username": "kurzpass", "display_name": "Kurz", "password": "kurz",
              "group_id": ""},
        headers=_with_csrf(c, session),
    )
    assert response.status_code == 200
    assert "mindestens 12 Zeichen" in response.text
    assert "kurzpass" in response.text
    assert "kurz\"" not in response.text.replace('value="kurzpass"', "")


def test_a_password_never_appears_in_the_response(client_als, session: Session) -> None:
    c = client_als([("user.manage", None)])
    response = c.post(
        "/users",
        data={"username": "", "display_name": "X", "password": "ein-auffälliges-geheimnis",
              "group_id": ""},
        headers=_with_csrf(c, session),
    )
    assert "ein-auffälliges-geheimnis" not in response.text


def test_the_last_administrator_cannot_deactivate_themselves(
    client_als, session: Session
) -> None:
    """The lockout guard also works via the interface, not only in the domain."""
    from sqlalchemy import select

    from thermoctl.db.models.identity import User

    c = client_als([("user.manage", None)])
    ich = session.scalar(select(User).where(User.username.like("web-%")))
    assert ich is not None
    response = c.post(
        f"/users/{ich.id}/active", data={"active": "no"},
        headers=_with_csrf(c, session),
    )
    assert response.status_code == 200
    assert "letzte aktive Benutzer" in response.text
    assert ich.is_active is True


def test_an_unknown_user_yields_404(client_als, session: Session) -> None:
    c = client_als([("user.manage", None)])
    response = c.post(
        "/users/999999/active", data={"active": "yes"}, headers=_with_csrf(c, session)
    )
    assert response.status_code == 404


def test_creating_a_group_and_granting_a_permission(client_als, session: Session) -> None:
    from sqlalchemy import select

    from tests.helpers import ensure_permission
    from thermoctl.db.models.identity import AccessGroup, GroupPermission

    ensure_permission(session, "zone.read", zone_scoped=True)
    c = client_als([("group.manage", None)])
    assert c.post(
        "/groups", data={"name": "Gäste", "description": "Nur schauen"},
        headers=_with_csrf(c, session), follow_redirects=False,
    ).status_code == 303
    group = session.scalar(select(AccessGroup).where(AccessGroup.name == "Gäste"))
    assert group is not None

    # The endpoint accepts the entire desired state, not a single permission:
    # `permission=<code>` for the whole installation, `permission=<code>:<zone>` for a zone.
    assert c.post(
        f"/groups/{group.id}/permissions", data={"permission": ["zone.read"]},
        headers=_with_csrf(c, session), follow_redirects=False,
    ).status_code == 303
    assert session.scalar(
        select(GroupPermission).where(GroupPermission.access_group_id == group.id)
    ) is not None


def test_an_installation_wide_permission_on_one_zone_is_refused_in_the_view(
    client_als, session: Session
) -> None:
    from sqlalchemy import select

    from tests.helpers import create_zone, ensure_permission
    from thermoctl.db.models.identity import AccessGroup

    ensure_permission(session, "user.manage", zone_scoped=False)
    zone = create_zone(session, "bad-für-rechte")
    c = client_als([("group.manage", None)])
    c.post("/groups", data={"name": "Falsch", "description": ""},
           headers=_with_csrf(c, session))
    group = session.scalar(select(AccessGroup).where(AccessGroup.name == "Falsch"))
    assert group is not None
    response = c.post(
        f"/groups/{group.id}/permissions", data={"permission": [f"user.manage:{zone.id}"]},
        headers=_with_csrf(c, session),
    )
    assert response.status_code == 200
    assert "ganze Anlage" in response.text


def test_issuing_a_token_shows_the_plaintext_exactly_once(client_als, session: Session) -> None:
    c = client_als([("token.self", None), ("zone.read", None)])
    response = c.post(
        "/tokens", data={"name": "Anzeigetafel", "code": "zone.read", "valid_days": ""},
        headers=_with_csrf(c, session),
    )
    assert response.status_code == 200
    assert "tctl_" in response.text
    # On the next call to the page it is gone -- only the hash is stored.
    assert "tctl_" not in c.get("/tokens").text


def test_the_rendered_token_expiry_uses_the_configured_timezone(
    client_als, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    c = client_als([("token.self", None)])
    settings = create_settings(session)
    settings.timezone = "America/New_York"
    monkeypatch.setattr(
        "thermoctl.web.admin_views.utcnow", lambda: datetime(2026, 8, 15, 12, 5)
    )
    response = c.post(
        "/tokens", data={"name": "Zeitzone", "code": "", "valid_days": "1"},
        headers=_with_csrf(c, session),
    )

    assert "16.08.2026 08:05" in response.text


def test_a_token_without_a_name_is_refused(client_als, session: Session) -> None:
    c = client_als([("token.self", None)])
    response = c.post(
        "/tokens", data={"name": "  ", "code": "", "valid_days": ""},
        headers=_with_csrf(c, session),
    )
    assert response.status_code == 200
    assert "braucht einen Namen" in response.text


def test_a_token_with_too_many_permissions_is_refused_understandably(
    client_als, session: Session
) -> None:
    from tests.helpers import ensure_permission

    ensure_permission(session, "zone.manage", zone_scoped=True)
    c = client_als([("token.self", None)])
    response = c.post(
        "/tokens", data={"name": "Zuviel", "code": "zone.manage", "valid_days": ""},
        headers=_with_csrf(c, session),
    )
    assert response.status_code == 200
    assert "kann kein Token" in response.text


def test_a_foreign_token_cannot_be_found(client_als, session: Session) -> None:
    """404 instead of 403 -- otherwise the response would reveal which ids exist."""
    from tests.helpers import token_with_permissions, user_with_permissions

    fremder = user_with_permissions(session, "fremder", [("token.self", None)])
    fremdes = token_with_permissions(session, fremder, [])
    c = client_als([("token.self", None)])
    response = c.post(
        f"/tokens/{fremdes.id}/revoke", headers=_with_csrf(c, session)
    )
    assert response.status_code == 404
    assert fremdes.revoked_at is None


def test_your_own_token_can_be_revoked(client_als, session: Session) -> None:
    c = client_als([("token.self", None)])
    c.post("/tokens", data={"name": "Weg damit", "code": "", "valid_days": "30"},
           headers=_with_csrf(c, session))
    from sqlalchemy import select

    from thermoctl.db.models.credential import ApiToken

    token = session.scalar(select(ApiToken).where(ApiToken.name == "Weg damit"))
    assert token is not None
    assert c.post(
        f"/tokens/{token.id}/revoke", headers=_with_csrf(c, session),
        follow_redirects=False,
    ).status_code == 303
    assert token.revoked_at is not None


def test_changing_your_own_password_without_user_manage(client_als, session: Session) -> None:
    """Anyone who only affects themselves needs no administration permission --
    otherwise nobody could change their own password without being an administrator."""
    from sqlalchemy import select

    from thermoctl.auth.passwords import verify_password
    from thermoctl.db.models.identity import User

    c = client_als([("zone.read", None)])
    ich = session.scalar(select(User).where(User.username.like("web-%")).order_by(User.id.desc()))
    assert ich is not None
    response = c.post(
        f"/users/{ich.id}/password", data={"password": "mein-neues-langes-passwort"},
        headers=_with_csrf(c, session), follow_redirects=False,
    )
    assert response.status_code == 303
    assert verify_password("mein-neues-langes-passwort", ich.password_hash)


def test_changing_someone_elses_password_needs_user_manage(client_als, session: Session) -> None:
    from tests.helpers import create_user

    fremder = create_user(session, "fremdes-passwort")
    c = client_als([("zone.read", None)])
    response = c.post(
        f"/users/{fremder.id}/password", data={"password": "egal-was-hier-steht"},
        headers=_with_csrf(c, session),
    )
    assert response.status_code == 403


def test_deleting_a_group_through_the_interface(client_als, session: Session) -> None:
    from sqlalchemy import select

    from thermoctl.db.models.identity import AccessGroup

    c = client_als([("group.manage", None)])
    c.post("/groups", data={"name": "Entbehrlich", "description": ""},
           headers=_with_csrf(c, session))
    group = session.scalar(select(AccessGroup).where(AccessGroup.name == "Entbehrlich"))
    assert group is not None
    assert c.post(
        f"/groups/{group.id}/delete", headers=_with_csrf(c, session),
        follow_redirects=False,
    ).status_code == 303
    assert session.get(AccessGroup, group.id) is None


def test_revoking_a_permission_through_the_interface(client_als, session: Session) -> None:
    from sqlalchemy import select

    from tests.helpers import ensure_permission
    from thermoctl.db.models.identity import AccessGroup, GroupPermission

    ensure_permission(session, "device.read", zone_scoped=True)
    c = client_als([("group.manage", None)])
    c.post("/groups", data={"name": "Rechteweg", "description": ""},
           headers=_with_csrf(c, session))
    group = session.scalar(select(AccessGroup).where(AccessGroup.name == "Rechteweg"))
    assert group is not None
    c.post(f"/groups/{group.id}/permissions", data={"permission": ["device.read"]},
           headers=_with_csrf(c, session))
    entry = session.scalar(
        select(GroupPermission).where(GroupPermission.access_group_id == group.id)
    )
    assert entry is not None
    # Revoking happens by omission: the form sends the entire desired state, and
    # whatever is not in it falls away. An empty form takes everything from the group.
    assert c.post(
        f"/groups/{group.id}/permissions", data={},
        headers=_with_csrf(c, session), follow_redirects=False,
    ).status_code == 303
    assert session.get(GroupPermission, entry.id) is None


def test_permissions_of_an_unknown_group_yield_404(client_als, session: Session) -> None:
    """The permission entry's id used to be in the path, and the test checked that an
    entry belonging to another group could not be revoked. That path no longer exists
    -- the collective endpoint only knows the group, and it has to exist."""
    from tests.helpers import ensure_permission

    ensure_permission(session, "device.read", zone_scoped=True)
    c = client_als([("group.manage", None)])
    response = c.post(
        "/groups/999999/permissions", data={"permission": ["device.read"]},
        headers=_with_csrf(c, session),
    )
    assert response.status_code == 404


def test_an_unknown_group_yields_404(client_als, session: Session) -> None:
    c = client_als([("group.manage", None)])
    assert c.post("/groups/999999/delete", headers=_with_csrf(c, session)).status_code == 404
    assert c.post(
        "/groups/999999/permissions", data={"code": "zone.read", "zone_id": ""},
        headers=_with_csrf(c, session),
    ).status_code == 404


def test_a_duplicate_group_name_stays_in_the_form(client_als, session: Session) -> None:
    c = client_als([("group.manage", None)])
    c.post("/groups", data={"name": "Zweimal", "description": ""},
           headers=_with_csrf(c, session))
    response = c.post("/groups", data={"name": "Zweimal", "description": ""},
                     headers=_with_csrf(c, session))
    assert response.status_code == 200
    assert "gibt es bereits" in response.text


def test_the_group_holding_the_last_admin_permission_stays(
    client_als, session: Session
) -> None:
    """The last group holding the admin permission cannot be deleted here either.

    The German original said "the last source" -- vocabulary from the audit log,
    where a source is something else entirely. The test always checked the group.
    """
    from sqlalchemy import select

    from thermoctl.db.models.identity import AccessGroup

    c = client_als([("group.manage", None), ("user.manage", None)])
    group = session.scalar(
        select(AccessGroup).where(AccessGroup.name.like("gruppe-web-%")).order_by(
            AccessGroup.id.desc()
        )
    )
    assert group is not None
    response = c.post(f"/groups/{group.id}/delete", headers=_with_csrf(c, session))
    assert response.status_code == 200
    assert "einzige verbliebene" in response.text
    assert session.get(AccessGroup, group.id) is not None


def test_a_too_short_password_on_your_own_change_stays_in_the_form(
    client_als, session: Session
) -> None:
    from sqlalchemy import select

    from thermoctl.db.models.identity import User

    c = client_als([("user.manage", None)])
    ich = session.scalar(select(User).where(User.username.like("web-%")).order_by(User.id.desc()))
    assert ich is not None
    response = c.post(
        f"/users/{ich.id}/password", data={"password": "kurz"},
        headers=_with_csrf(c, session),
    )
    assert response.status_code == 200
    assert "mindestens 12 Zeichen" in response.text


def test_deactivating_and_reactivating_a_user(client_als, session: Session) -> None:
    from tests.helpers import create_user

    anderer = create_user(session, "kommt-und-geht")
    c = client_als([("user.manage", None)])
    assert c.post(
        f"/users/{anderer.id}/active", data={"active": "no"},
        headers=_with_csrf(c, session), follow_redirects=False,
    ).status_code == 303
    assert anderer.is_active is False
    assert c.post(
        f"/users/{anderer.id}/active", data={"active": "yes"},
        headers=_with_csrf(c, session), follow_redirects=False,
    ).status_code == 303
    assert anderer.is_active is True


def test_a_password_change_for_an_unknown_user_yields_404(
    client_als, session: Session
) -> None:
    c = client_als([("user.manage", None)])
    response = c.post(
        "/users/999999/password", data={"password": "ein-langes-passwort-hier"},
        headers=_with_csrf(c, session),
    )
    assert response.status_code == 404


def test_the_last_admin_permission_cannot_be_revoked(
    client_als, session: Session
) -> None:
    """The second path into the same dead end: not deleting the group, but taking
    the permission away from it. Both must be blocked, or the guard can be sidestepped."""
    from sqlalchemy import select

    from thermoctl.db.models.identity import AccessGroup, GroupPermission
    from thermoctl.db.models.lookup import Permission

    c = client_als([("group.manage", None), ("user.manage", None)])
    group = session.scalar(
        select(AccessGroup).where(AccessGroup.name.like("gruppe-web-%")).order_by(
            AccessGroup.id.desc()
        )
    )
    assert group is not None
    permission_id = session.scalar(select(Permission.id).where(Permission.code == "user.manage"))
    entry = session.scalar(
        select(GroupPermission).where(
            GroupPermission.access_group_id == group.id,
            GroupPermission.permission_id == permission_id,
        )
    )
    assert entry is not None
    # Omission is the new way to revoke -- the guard has to hold there too.
    response = c.post(
        f"/groups/{group.id}/permissions", data={"permission": ["group.manage"]},
        headers=_with_csrf(c, session),
    )
    assert response.status_code == 200
    assert "einzige verbliebene" in response.text
    assert session.get(GroupPermission, entry.id) is not None


def test_setting_permissions_grants_and_revokes_in_one_step(
    client_als, session: Session
) -> None:
    """The real benefit of the collective endpoint: converting a group is one
    operation, not a sequence of individual steps in between which it sits
    half-configured."""
    from sqlalchemy import select

    from tests.helpers import create_zone, ensure_permission
    from thermoctl.db.models.identity import AccessGroup, GroupPermission
    from thermoctl.db.models.lookup import Permission

    ensure_permission(session, "zone.read", zone_scoped=True)
    ensure_permission(session, "device.read", zone_scoped=True)
    bad = create_zone(session, "bad-umstellen")
    c = client_als([("group.manage", None)])
    c.post("/groups", data={"name": "Umbau", "description": ""}, headers=_with_csrf(c, session))
    group = session.scalar(select(AccessGroup).where(AccessGroup.name == "Umbau"))
    assert group is not None

    c.post(f"/groups/{group.id}/permissions", data={"permission": ["zone.read"]},
           headers=_with_csrf(c, session))
    c.post(
        f"/groups/{group.id}/permissions",
        data={"permission": [f"zone.read:{bad.id}", "device.read"]},
        headers=_with_csrf(c, session),
    )

    state = {
        (code, zone_id)
        for code, zone_id in session.execute(
            select(Permission.code, GroupPermission.zone_id)
            .join(Permission, Permission.id == GroupPermission.permission_id)
            .where(GroupPermission.access_group_id == group.id)
        )
    }
    assert state == {("zone.read", bad.id), ("device.read", None)}


def test_the_group_page_shows_permissions_by_area_in_plain_words(
    client_als, session: Session
) -> None:
    """Before, there was a flat list of codes there. The code stays visible -- it
    appears in error messages and in the documentation -- but it is no longer the
    only thing shown."""
    from tests.helpers import create_all_permissions
    from thermoctl.domain.authz import PERMISSION_AREAS

    create_all_permissions(session)
    page = client_als([("group.manage", None)]).get("/groups")
    assert page.status_code == 200
    for name, _hint, _codes in PERMISSION_AREAS:
        assert name in page.text, f"area '{name}' is missing from the page"
    assert "Zonen und ihren Zustand sehen" in page.text
    assert "zone.read" in page.text


def test_an_unparsable_zone_in_a_permission_entry_is_a_bad_request(
    angemeldeter_client: TestClient, session: Session
) -> None:
    """The checkboxes send `code:zone_id`; both halves come from the browser.

    An empty code is skipped -- a stray separator should not decide anything -- but a
    zone that is not a number is refused outright. Guessing what was meant would be
    the one thing a permission form must never do.
    """
    from thermoctl.db.models.identity import AccessGroup

    group = AccessGroup(name="Rechtegruppe", description="für den Test")
    session.add(group)
    session.flush()
    response = angemeldeter_client.post(
        f"/groups/{group.id}/permissions",
        data={"permission": ["zone.read:keine-zahl"]},
        headers=_with_csrf(angemeldeter_client, session),
        follow_redirects=False,
    )
    assert response.status_code == 400

    nur_trenner = angemeldeter_client.post(
        f"/groups/{group.id}/permissions",
        data={"permission": [":", "zone.read:"]},
        headers=_with_csrf(angemeldeter_client, session),
        follow_redirects=False,
    )
    assert nur_trenner.status_code in (200, 303)


def _rendered_group_form_fields(html: str, user_id: int) -> dict[str, str]:
    """Fills the per-row group form for `user_id` exactly as a browser would.

    Field **names** and the `<select>`'s **available options** come out of the
    rendered markup and are never invented by the test. A `<select>` yields whatever
    option carries `selected`, falling back to the first one, the way a real browser
    resolves the control it shows -- so a test can still pick a *different* option
    from the ones actually offered, the way clicking a dropdown does.
    """
    import re

    action = f"/users/{user_id}/group"
    for form in re.findall(r"<form\b[^>]*>.*?</form>", html, re.DOTALL):
        if f'action="{action}"' not in form:
            continue
        values: dict[str, str] = {}
        for field in re.findall(r"<input\b[^>]*>", form):
            name = re.search(r'name="([^"]+)"', field)
            if name is None:
                continue
            rendered = re.search(r'value="([^"]*)"', field)
            values[name.group(1)] = rendered.group(1) if rendered else ""
        for select in re.findall(r"<select\b[^>]*>.*?</select>", form, re.DOTALL):
            name = re.search(r'name="([^"]+)"', select)
            if name is None:
                continue
            options = re.findall(r'<option value="([^"]*)"(.*?)>', select, re.DOTALL)
            selected = next((value for value, attrs in options if "selected" in attrs), None)
            values[name.group(1)] = (
                selected if selected is not None else (options[0][0] if options else "")
            )
            values[f"{name.group(1)}__options"] = ",".join(value for value, _ in options)
        return values
    raise AssertionError(f"Kein Formular mit action={action!r} gefunden")


def test_the_rendered_group_form_changes_the_group_and_the_rights(
    client_als, session: Session
) -> None:
    """Submits the per-row form exactly as the browser rendered it -- field names
    and the chosen option both come out of the markup, none of them are supplied by
    the test. This is the reported gap: an existing user's group could not be
    changed through the interface at all.
    """
    from tests.helpers import create_user, ensure_permission
    from thermoctl.db.models.identity import AccessGroup
    from thermoctl.domain.authz import has_permission, principal_for_user

    ensure_permission(session, "zone.read", zone_scoped=False)
    ziel_gruppe = AccessGroup(name="Zielgruppe")
    session.add(ziel_gruppe)
    session.flush()
    from thermoctl.domain.administration import grant_permission

    grant_permission(session, ziel_gruppe, "zone.read", None, actor_id=None)

    zielperson = create_user(session, "wechselperson")
    c = client_als([("user.manage", None)])

    page = c.get("/users")
    assert page.status_code == 200
    fields = _rendered_group_form_fields(page.text, zielperson.id)
    options = fields.pop("group_id__options").split(",")
    assert str(ziel_gruppe.id) in options, (
        "The target group does not even appear as an option in the rendered form"
    )
    fields["group_id"] = str(ziel_gruppe.id)

    response = c.post(f"/users/{zielperson.id}/group", data=fields, follow_redirects=False)
    assert response.status_code == 303
    assert has_permission(principal_for_user(session, zielperson), "zone.read") is True


def test_removing_the_group_through_the_interface_removes_the_rights(
    client_als, session: Session
) -> None:
    from sqlalchemy import select

    from tests.helpers import ensure_permission, user_with_permissions
    from thermoctl.db.models.identity import UserAccessGroup
    from thermoctl.domain.authz import has_permission, principal_for_user

    ensure_permission(session, "zone.read", zone_scoped=False)
    person = user_with_permissions(session, "hatrechte", [("zone.read", None)])
    c = client_als([("user.manage", None)])

    response = c.post(
        f"/users/{person.id}/group", data={"group_id": ""},
        headers=_with_csrf(c, session), follow_redirects=False,
    )
    assert response.status_code == 303
    assert has_permission(principal_for_user(session, person), "zone.read") is False
    assert (
        session.scalar(select(UserAccessGroup).where(UserAccessGroup.user_id == person.id))
        is None
    )


def test_changing_a_group_needs_user_manage(client_als, session: Session) -> None:
    from tests.helpers import create_user

    person = create_user(session, "ohnerecht-ziel")
    c = client_als([("zone.read", None)])
    response = c.post(
        f"/users/{person.id}/group", data={"group_id": ""},
        headers=_with_csrf(c, session),
    )
    assert response.status_code == 403


def test_changing_a_group_needs_a_csrf_token(client_als, session: Session) -> None:
    from tests.helpers import create_user

    person = create_user(session, "ohnetoken-ziel")
    c = client_als([("user.manage", None)])
    response = c.post(f"/users/{person.id}/group", data={"group_id": ""})
    assert response.status_code == 403


def test_changing_a_group_writes_an_audit_entry_naming_before_and_after(
    client_als, session: Session
) -> None:
    from sqlalchemy import select

    from tests.helpers import user_with_permissions
    from thermoctl.db.models.identity import AccessGroup
    from thermoctl.db.models.operations import AuditEvent

    person = user_with_permissions(session, "protokolliert-web", [("zone.read", None)])
    old_group = session.scalar(
        select(AccessGroup).where(AccessGroup.name == "gruppe-protokolliert-web")
    )
    assert old_group is not None
    neue_gruppe = AccessGroup(name="Neuegruppeweb")
    session.add(neue_gruppe)
    session.flush()

    c = client_als([("user.manage", None)])
    response = c.post(
        f"/users/{person.id}/group", data={"group_id": str(neue_gruppe.id)},
        headers=_with_csrf(c, session), follow_redirects=False,
    )
    assert response.status_code == 303

    entry = session.scalar(
        select(AuditEvent).where(AuditEvent.action == "user.group_changed")
    )
    assert entry is not None
    assert old_group.name in entry.summary
    assert neue_gruppe.name in entry.summary


def test_an_unknown_user_yields_404_for_group_change(client_als, session: Session) -> None:
    c = client_als([("user.manage", None)])
    response = c.post(
        "/users/999999/group", data={"group_id": ""}, headers=_with_csrf(c, session)
    )
    assert response.status_code == 404


def test_an_unparsable_group_id_is_a_bad_request(client_als, session: Session) -> None:
    """The field comes from a rendered `<select>` and should always be a number or
    empty -- but a request need not come from that rendered form."""
    from tests.helpers import create_user

    person = create_user(session, "unparsierbar-ziel")
    c = client_als([("user.manage", None)])
    response = c.post(
        f"/users/{person.id}/group", data={"group_id": "keine-zahl"},
        headers=_with_csrf(c, session),
    )
    assert response.status_code == 400


def test_the_last_administrator_cannot_lock_themselves_out_via_group_change(
    client_als, session: Session
) -> None:
    """The interface-level twin of the domain guard: a group change that would take
    the plant's only `user.manage` away must be refused, exactly like deactivation."""
    from sqlalchemy import select as _select

    from thermoctl.db.models.identity import AccessGroup, User

    c = client_als([("user.manage", None)])
    ich = session.scalar(_select(User).where(User.username.like("web-%")).order_by(User.id.desc()))
    assert ich is not None
    harmlos = AccessGroup(name="Harmlosweb")
    session.add(harmlos)
    session.flush()

    response = c.post(
        f"/users/{ich.id}/group", data={"group_id": str(harmlos.id)},
        headers=_with_csrf(c, session),
    )
    assert response.status_code == 200
    assert "letzte aktive Benutzer" in response.text


def test_a_rejected_creation_keeps_the_chosen_group_preselected(
    client_als, session: Session
) -> None:
    """The template bug: `values.get("gruppe_id")` never matched what the view
    actually stored under `group_id`, so a rejected form always lost the choice."""
    from thermoctl.db.models.identity import AccessGroup

    gruppe = AccessGroup(name="Vorausgewählt")
    session.add(gruppe)
    session.flush()
    c = client_als([("user.manage", None)])

    response = c.post(
        "/users",
        data={"username": "kurzpass2", "display_name": "Kurz", "password": "kurz",
              "group_id": str(gruppe.id)},
        headers=_with_csrf(c, session),
    )
    assert response.status_code == 200
    import re

    # Scoped to the creation form specifically -- the page also carries one group
    # `<select>` per existing user, and one of those could easily contain an
    # `<option value="…">` matching the same group id by coincidence.
    creation_forms = [
        form for form in re.findall(r"<form\b[^>]*>.*?</form>", response.text, re.DOTALL)
        if 'action="/users"' in form
    ]
    assert len(creation_forms) == 1, "Expected exactly one user-creation form on the page"
    match = re.search(rf'<option value="{gruppe.id}"(.*?)>', creation_forms[0], re.DOTALL)
    assert match is not None and "selected" in match.group(1), (
        "The chosen group is no longer preselected after the rejected submission"
    )
