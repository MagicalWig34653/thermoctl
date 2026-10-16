
import pytest
from sqlalchemy.orm import Session

from tests.helpers import create_zone, source, user_with_permissions
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






def test_a_password_never_appears_in_the_response(client_als, session: Session) -> None:
    c = client_als([("user.manage", None)])
    response = c.post(
        "/users",
        data={"username": "", "display_name": "X", "password": "ein-auffälliges-geheimnis",
              "group_id": ""},
        headers=_with_csrf(c, session),
    )
    assert "ein-auffälliges-geheimnis" not in response.text




def test_an_unknown_user_yields_404(client_als, session: Session) -> None:
    c = client_als([("user.manage", None)])
    response = c.post(
        "/users/999999/active", data={"active": "yes"}, headers=_with_csrf(c, session)
    )
    assert response.status_code == 404
















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










def test_a_password_change_for_an_unknown_user_yields_404(
    client_als, session: Session
) -> None:
    c = client_als([("user.manage", None)])
    response = c.post(
        "/users/999999/password", data={"password": "ein-langes-passwort-hier"},
        headers=_with_csrf(c, session),
    )
    assert response.status_code == 404










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












def test_an_unknown_user_yields_404_for_group_change(client_als, session: Session) -> None:
    c = client_als([("user.manage", None)])
    response = c.post(
        "/users/999999/group", data={"group_id": ""}, headers=_with_csrf(c, session)
    )
    assert response.status_code == 404






