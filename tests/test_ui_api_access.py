"""Personen, Zugänge und Konto unter `/ui-api/access` und `/ui-api/account`.

Hochsicherheitsrelevant: geprüft wird, dass nur die Cookie-Sitzung hineinkommt, dass
Profil vor Recht vor Existenz geprüft wird, dass kein Klartext-Token außerhalb der
einen Ausstellungsantwort auftaucht, dass kein Weg über die Rechte des Handelnden
hinausführt und dass der letzte Verwalter nicht verloren geht.
"""

# ruff: noqa: E501

import logging
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.helpers import (
    create_all_permissions,
    create_passkey,
    create_settings,
    create_user,
    create_zone,
    source,
    token_with_permissions,
    user_with_permissions,
)
from thermoctl.auth.csrf import CSRF_HEADER, csrf_token
from thermoctl.auth.kiosk import KIOSK_COOKIE_NAME
from thermoctl.auth.passwords import hash_password, verify_password
from thermoctl.auth.sessions import COOKIE_NAME, create_session, resolve_session
from thermoctl.auth.tokens import issue_token
from thermoctl.config import get_settings
from thermoctl.db.base import utcnow
from thermoctl.db.models.credential import ApiToken, ApiTokenPermission, Session_
from thermoctl.db.models.identity import AccessGroup, GroupPermission, User, UserAccessGroup
from thermoctl.db.models.lookup import Permission
from thermoctl.db.models.oauth import OAuthRefreshToken
from thermoctl.db.models.operations import AuditEvent, Setting
from thermoctl.db.models.passkey import UserPasskey
from thermoctl.domain.kiosk import issue_kiosk_token
from thermoctl.domain.oauth import REFRESH_TTL, register_client

Als = Callable[[list[tuple[str, int | None]]], TestClient]

ADMIN: list[tuple[str, int | None]] = [
    (code, None)
    for code in (
        "user.manage", "group.manage", "token.self", "token.manage", "zone.read",
        "setpoint.write", "override.create", "override.cancel",
    )
]
LONG_PASSWORD = "ein-langes-passwort-1"
GHOST = 99999


@pytest.fixture(autouse=True)
def _base(session: Session) -> None:
    create_settings(session)
    source(session, "web")
    create_all_permissions(session)


def _csrf(client: TestClient) -> dict[str, str]:
    cookie = client.cookies.get(COOKIE_NAME)
    assert cookie is not None
    return {CSRF_HEADER: csrf_token(cookie, get_settings().secret_key.get_secret_value())}


def _call(client: TestClient, method: str, url: str, body: dict[str, Any] | None = None) -> Any:
    return client.request(method, url, json=body, headers=_csrf(client))


def _err(response: Any, status: int, code: str) -> dict[str, Any]:
    assert response.status_code == status, response.text
    body: dict[str, Any] = response.json()
    assert body["code"] == code
    assert isinstance(body["detail"], str) and body["detail"]
    return body


def _me(client: TestClient) -> int:
    user_id: int = client.get("/ui-api/bootstrap").json()["user_id"]
    return user_id


def _actions(session: Session) -> list[str]:
    return [row.action for row in session.scalars(select(AuditEvent).order_by(AuditEvent.id))]


# Jeder Endpunkt mit gültiger Anfrage und dem Recht, das er verlangt (None: nur Anmeldung).
# Die Ids existieren nicht: Profil und Recht müssen vor der Existenzprüfung greifen.
ENDPOINTS: list[tuple[str, str, dict[str, Any] | None, str | None]] = [
    ("GET", "/ui-api/access/users", None, "user.manage"),
    ("POST", "/ui-api/access/users", {"username": "x", "display_name": "X", "password": LONG_PASSWORD}, "user.manage"),
    ("PUT", f"/ui-api/access/users/{GHOST}/active", {"active": False}, "user.manage"),
    ("PUT", f"/ui-api/access/users/{GHOST}/group", {"group_id": None}, "user.manage"),
    ("PUT", f"/ui-api/access/users/{GHOST}/password", {"password": LONG_PASSWORD}, "user.manage"),
    ("GET", "/ui-api/access/groups", None, "group.manage"),
    ("POST", "/ui-api/access/groups", {"name": "g"}, "group.manage"),
    ("PUT", f"/ui-api/access/groups/{GHOST}/ui-profile", {"ui_profile": "admin"}, "group.manage"),
    ("DELETE", f"/ui-api/access/groups/{GHOST}", None, "group.manage"),
    ("PUT", f"/ui-api/access/groups/{GHOST}/permissions", {"permissions": []}, "group.manage"),
    ("GET", "/ui-api/access/tokens", None, "token.self"),
    ("POST", "/ui-api/access/tokens", {"name": "t", "preset": "read_only"}, "token.self"),
    ("PUT", f"/ui-api/access/tokens/{GHOST}/permissions", {"permissions": []}, "token.self"),
    ("POST", f"/ui-api/access/tokens/{GHOST}/revoke", None, "token.self"),
    ("GET", "/ui-api/access/kiosk-tokens", None, "token.manage"),
    ("POST", "/ui-api/access/kiosk-tokens", {"name": "k", "zone_ids": [GHOST]}, "token.manage"),
    ("POST", f"/ui-api/access/kiosk-tokens/{GHOST}/revoke", None, "token.manage"),
]
ACCOUNT: list[tuple[str, str, dict[str, Any] | None]] = [
    ("GET", "/ui-api/account", None),
    ("PUT", "/ui-api/account/password", {"current_password": "a", "new_password": LONG_PASSWORD}),
    ("GET", "/ui-api/account/sessions", None),
    ("POST", "/ui-api/account/sessions/revoke-others", None),
    ("POST", f"/ui-api/account/sessions/{GHOST}/revoke", None),
    ("GET", "/ui-api/account/passkeys", None),
    ("DELETE", f"/ui-api/account/passkeys/{GHOST}", None),
]
EVERYTHING = [(m, u, b) for m, u, b, _p in ENDPOINTS] + ACCOUNT


# -- Zugang: nur die Cookie-Sitzung -----------------------------------------------------


def test_every_endpoint_without_session_is_401(client: TestClient) -> None:
    for method, url, body in EVERYTHING:
        response = client.request(method, url, json=body)
        assert response.status_code == 401, url
        assert response.json() == {"detail": "Nicht angemeldet"}


def test_bearer_token_never_reaches_the_ui_api(client: TestClient, session: Session) -> None:
    """Ein Token mit allen Rechten des Besitzers öffnet hier nichts (Bearer-Semantik unberührt)."""
    owner = user_with_permissions(session, "besitzer", ADMIN)
    _token, plaintext = issue_token(session, owner, "alles", ADMIN, None)
    client.cookies.clear()
    for method, url, body in EVERYTHING:
        response = client.request(
            method, url, json=body, headers={"Authorization": f"Bearer {plaintext}"}
        )
        assert response.status_code == 401, url


def test_kiosk_cookie_never_reaches_the_ui_api(client: TestClient, session: Session) -> None:
    zone = create_zone(session, "Wohnen")
    owner = user_with_permissions(session, "besitzer", ADMIN)
    _token, plaintext = issue_kiosk_token(
        session, owner, "tablet", [zone.id], control_allowed=True, expires_at=None
    )
    client.cookies.clear()
    client.cookies.set(KIOSK_COOKIE_NAME, plaintext)
    for method, url, body in EVERYTHING:
        assert client.request(method, url, json=body).status_code == 401, url
    # Auch als Bearer-Header zusätzlich zum Cookie:
    for method, url, body in EVERYTHING:
        response = client.request(
            method, url, json=body, headers={"Authorization": f"Bearer {plaintext}"}
        )
        assert response.status_code == 401, url


def test_revoked_or_inactive_session_is_401(client_als: Als, session: Session) -> None:
    client = client_als(ADMIN)
    me = session.get(User, _me(client))
    assert me is not None
    me.is_active = False
    session.flush()
    assert client.get("/ui-api/access/users").status_code == 401
    me.is_active = True
    cookie = client.cookies.get(COOKIE_NAME)
    assert cookie is not None
    entry = resolve_session(session, cookie)
    assert entry is not None
    entry.revoked_at = utcnow()
    session.flush()
    assert client.get("/ui-api/account").status_code == 401


def test_writes_without_csrf_are_403_stale_and_change_nothing(client_als: Als, session: Session) -> None:
    client = client_als(ADMIN)
    before = _actions(session)
    for method, url, body in EVERYTHING:
        if method == "GET":
            continue
        response = client.request(method, url, json=body, headers={CSRF_HEADER: "falsch"})
        assert response.status_code == 403 and response.json()["stale"] is True, url
        response = client.request(method, url, json=body)
        assert response.status_code == 403 and response.json()["stale"] is True, url
    assert _actions(session) == before


def test_router_is_hidden_and_csrf_protected() -> None:
    from thermoctl.auth.dependencies import csrf_protection
    from thermoctl.web.ui_api_access import router

    assert csrf_protection in [d.dependency for d in router.dependencies]
    assert not router.include_in_schema


# -- Rechte, Profil, Existenz -----------------------------------------------------------


def test_admin_endpoints_without_rights_are_403_and_account_stays_open(
    client_als: Als, session: Session
) -> None:
    client = client_als([])
    for method, url, body, _perm in ENDPOINTS:
        _err(_call(client, method, url, body), 403, "forbidden")
    assert session.scalar(select(AuditEvent.id)) is None
    for method, url, body in ACCOUNT:
        assert _call(client, method, url, body).status_code != 403 or url.endswith("password"), url


@pytest.mark.parametrize("missing", ["user.manage", "group.manage", "token.self", "token.manage"])
def test_each_endpoint_demands_exactly_its_own_permission(
    client_als: Als, missing: str
) -> None:
    client = client_als([g for g in ADMIN if g[0] != missing])
    for method, url, body, perm in ENDPOINTS:
        response = _call(client, method, url, body)
        if perm == missing:
            _err(response, 403, "forbidden")
        else:
            assert response.status_code != 403, (method, url)


def test_zone_scoped_right_does_not_open_the_plant_wide_endpoints(client_als: Als, session: Session) -> None:
    zone = create_zone(session, "Bad")
    client = client_als([("zone.read", zone.id), ("setpoint.write", zone.id)])
    for method, url, body, _perm in ENDPOINTS:
        _err(_call(client, method, url, body), 403, "forbidden")


def test_tenant_profile_is_refused_before_the_right_is_checked(
    tenant_client: Als, client_als: Als
) -> None:
    client = tenant_client(ADMIN)
    for method, url, body, _perm in ENDPOINTS:
        _err(_call(client, method, url, body), 403, "wrong_profile")
    # Das Konto gehört beiden Oberflächen.
    assert client.get("/ui-api/account").status_code == 200
    assert client.get("/ui-api/account/sessions").status_code == 200
    assert client.get("/ui-api/account/passkeys").status_code == 200


def test_unknown_ids_are_404_with_identical_bodies(client_als: Als, session: Session) -> None:
    client = client_als(ADMIN)
    foreign = create_user(session, "fremd")
    foreign_token = token_with_permissions(session, foreign, [])
    foreign_session, _secret = create_session(session, foreign, 3600)
    foreign_key = create_passkey(session, foreign, "fremd-kennung")
    probes = [
        ("PUT", "/ui-api/access/users/{}/active", {"active": False}),
        ("PUT", "/ui-api/access/users/{}/group", {"group_id": None}),
        ("PUT", "/ui-api/access/users/{}/password", {"password": LONG_PASSWORD}),
        ("PUT", "/ui-api/access/groups/{}/ui-profile", {"ui_profile": "admin"}),
        ("DELETE", "/ui-api/access/groups/{}", None),
        ("PUT", "/ui-api/access/groups/{}/permissions", {"permissions": []}),
    ]
    for method, template, body in probes:
        _err(_call(client, method, template.format(GHOST), body), 404, "not_found")
    # Fremde Tokens, Sitzungen, Passkeys: nicht auffindbar, nicht verboten.
    for method, template, body, foreign_id in [
        ("PUT", "/ui-api/access/tokens/{}/permissions", {"permissions": []}, foreign_token.id),
        ("POST", "/ui-api/access/tokens/{}/revoke", None, foreign_token.id),
        ("POST", "/ui-api/account/sessions/{}/revoke", None, foreign_session.id),
        ("DELETE", "/ui-api/account/passkeys/{}", None, foreign_key.id),
    ]:
        unknown = _err(_call(client, method, template.format(GHOST), body), 404, "not_found")
        other = _err(_call(client, method, template.format(foreign_id), body), 404, "not_found")
        assert unknown == other
    session.expire_all()
    assert foreign_token.revoked_at is None and foreign_session.revoked_at is None
    assert session.get(UserPasskey, foreign_key.id) is not None


def test_malformed_bodies_are_422(client_als: Als) -> None:
    client = client_als(ADMIN)
    for method, url, body, _perm in ENDPOINTS:
        if method in {"POST", "PUT"} and body is not None:
            _err(_call(client, method, url, {"unerwartet": 1}), 422, "validation")
    _err(_call(client, "PUT", "/ui-api/account/password", {"unerwartet": 1}), 422, "validation")


# -- Benutzer ---------------------------------------------------------------------------


def test_user_list_has_groups_and_marks_self(client_als: Als, session: Session) -> None:
    client = client_als(ADMIN)
    other = create_user(session, "anna")
    group = AccessGroup(name="Mieter")
    session.add(group)
    session.flush()
    session.add(UserAccessGroup(user_id=other.id, access_group_id=group.id))
    session.flush()
    body = client.get("/ui-api/access/users").json()
    by_name = {u["username"]: u for u in body["users"]}
    assert by_name["anna"]["group_id"] == group.id and by_name["anna"]["is_self"] is False
    assert by_name["anna"]["is_active"] is True and by_name["anna"]["last_login_at"] is None
    me = next(u for u in body["users"] if u["is_self"])
    assert me["id"] == _me(client)
    assert group.id in {g["id"] for g in body["groups"]}
    assert "password" not in str(body) and "platzhalter" not in str(body)


def test_create_user_writes_user_membership_hash_and_audit(client_als: Als, session: Session) -> None:
    client = client_als(ADMIN)
    group = AccessGroup(name="Team")
    session.add(group)
    session.flush()
    response = _call(client, "POST", "/ui-api/access/users", {
        "username": "neu", "display_name": "Neu", "password": LONG_PASSWORD, "group_id": group.id,
    })
    assert response.status_code == 201, response.text
    created = session.scalar(select(User).where(User.username == "neu"))
    assert created is not None
    assert response.json()["id"] == created.id and response.json()["group_id"] == group.id
    assert created.password_hash != LONG_PASSWORD and verify_password(LONG_PASSWORD, created.password_hash)
    assert LONG_PASSWORD not in response.text
    assert session.scalar(select(UserAccessGroup).where(UserAccessGroup.user_id == created.id)) is not None
    assert "user.created" in _actions(session)
    # ohne Gruppe:
    bare = _call(client, "POST", "/ui-api/access/users", {
        "username": "ohne", "display_name": "Ohne", "password": LONG_PASSWORD,
    })
    assert bare.status_code == 201 and bare.json()["group_id"] is None


def test_create_user_errors_leave_nothing_behind(client_als: Als, session: Session) -> None:
    client = client_als(ADMIN)
    count = session.query(User).count()
    short = _err(_call(client, "POST", "/ui-api/access/users", {
        "username": "kurz", "display_name": "K", "password": "zu-kurz"}), 422, "password_too_short")
    assert short["field"] == "password"
    _err(_call(client, "POST", "/ui-api/access/users", {
        "username": "  ", "display_name": "K", "password": LONG_PASSWORD}), 422, "invalid_username")
    _err(_call(client, "POST", "/ui-api/access/users", {
        "username": "x", "display_name": "K", "password": LONG_PASSWORD, "group_id": GHOST}), 422, "unknown_group")
    _err(_call(client, "POST", "/ui-api/access/users", {
        "username": "x" * 65, "display_name": "K", "password": LONG_PASSWORD}), 422, "validation")
    _err(_call(client, "POST", "/ui-api/access/users", {
        "username": "web-1", "display_name": "K", "password": LONG_PASSWORD}), 409, "username_taken")
    assert session.query(User).count() == count


def test_set_active_blocks_and_unblocks_and_logs(client_als: Als, session: Session) -> None:
    client = client_als(ADMIN)
    other = create_user(session, "anna")
    off = _call(client, "PUT", f"/ui-api/access/users/{other.id}/active", {"active": False})
    assert off.status_code == 200 and off.json()["is_active"] is False
    session.refresh(other)
    assert other.is_active is False
    on = _call(client, "PUT", f"/ui-api/access/users/{other.id}/active", {"active": True})
    assert on.json()["is_active"] is True
    assert _actions(session) == ["user.deactivated", "user.activated"]


def test_last_administrator_cannot_be_locked_out_by_any_path(client_als: Als, session: Session) -> None:
    client = client_als(ADMIN)
    me_id = _me(client)
    me = session.get(User, me_id)
    assert me is not None
    group = session.scalar(select(AccessGroup).join(UserAccessGroup).where(UserAccessGroup.user_id == me_id))
    assert group is not None
    other = AccessGroup(name="ohne-rechte")
    session.add(other)
    session.flush()
    before = _actions(session)
    # 1. sich selbst sperren: am eigenen Konto verwaltet man nichts (own_account)
    _err(_call(client, "PUT", f"/ui-api/access/users/{me_id}/active", {"active": False}), 409, "own_account")
    # 2. sich umgruppieren oder gruppenlos machen: ebenso
    _err(_call(client, "PUT", f"/ui-api/access/users/{me_id}/group", {"group_id": other.id}), 409, "own_account")
    _err(_call(client, "PUT", f"/ui-api/access/users/{me_id}/group", {"group_id": None}), 409, "own_account")
    # 3. die Gruppe löschen
    _err(_call(client, "DELETE", f"/ui-api/access/groups/{group.id}"), 409, "last_administrator")
    # 4. user.manage aus der Gruppe nehmen
    kept = [{"code": c, "zone_id": None} for c, _z in ADMIN if c != "user.manage"]
    _err(_call(client, "PUT", f"/ui-api/access/groups/{group.id}/permissions", {"permissions": kept}), 409, "last_administrator")
    # 5. die Gruppe auf Wohnungssicht stellen
    _err(_call(client, "PUT", f"/ui-api/access/groups/{group.id}/ui-profile", {"ui_profile": "tenant"}), 409, "last_administrator")
    session.expire_all()
    assert me.is_active is True
    assert session.get(AccessGroup, group.id) is not None
    assert group.ui_profile == "admin"
    assert session.scalar(
        select(GroupPermission.id).join(Permission).where(
            GroupPermission.access_group_id == group.id, Permission.code == "user.manage"
        )
    ) is not None
    assert _actions(session) == before


def test_a_second_administrator_lifts_the_guard(client_als: Als, session: Session) -> None:
    client = client_als(ADMIN)
    second = user_with_permissions(session, "zweiter", ADMIN)
    assert _call(client, "PUT", f"/ui-api/access/users/{second.id}/active", {"active": False}).status_code == 200
    assert _call(client, "PUT", f"/ui-api/access/users/{second.id}/active", {"active": True}).status_code == 200
    empty = AccessGroup(name="leer")
    session.add(empty)
    session.flush()
    moved = _call(client, "PUT", f"/ui-api/access/users/{second.id}/group", {"group_id": empty.id})
    assert moved.status_code == 200 and moved.json()["group_id"] == empty.id
    assert session.scalars(select(UserAccessGroup.access_group_id).where(UserAccessGroup.user_id == second.id)).all() == [empty.id]
    assert "user.group_changed" in _actions(session)


def test_set_user_group_unknown_group_is_422(client_als: Als, session: Session) -> None:
    client = client_als(ADMIN)
    other = create_user(session, "anna")
    _err(_call(client, "PUT", f"/ui-api/access/users/{other.id}/group", {"group_id": GHOST}), 422, "unknown_group")


def test_admin_password_reset_ends_all_sessions_of_the_target(client_als: Als, session: Session) -> None:
    client = client_als(ADMIN)
    target = create_user(session, "anna")
    first, _a = create_session(session, target, 3600)
    second, _b = create_session(session, target, 3600)
    mine = resolve_session(session, client.cookies[COOKIE_NAME])
    assert mine is not None
    response = _call(client, "PUT", f"/ui-api/access/users/{target.id}/password", {"password": LONG_PASSWORD})
    assert response.status_code == 200 and response.json() == {"ok": True}
    session.refresh(target)
    assert verify_password(LONG_PASSWORD, target.password_hash)
    assert first.revoked_at is not None and second.revoked_at is not None
    assert mine.revoked_at is None
    assert LONG_PASSWORD not in response.text
    assert "user.password_changed" in _actions(session)
    short = _err(_call(client, "PUT", f"/ui-api/access/users/{target.id}/password", {"password": "kurz"}), 422, "password_too_short")
    assert short["field"] == "password"
    session.refresh(target)
    assert verify_password(LONG_PASSWORD, target.password_hash)


def test_own_password_goes_only_through_the_account_endpoint(client_als: Als, session: Session) -> None:
    """Sonst wäre die Verwaltungsroute ein Weg an der Abfrage des alten Passworts vorbei."""
    client = client_als(ADMIN)
    me = session.get(User, _me(client))
    assert me is not None
    before = me.password_hash
    _err(_call(client, "PUT", f"/ui-api/access/users/{me.id}/password", {"password": LONG_PASSWORD}), 409, "own_account")
    session.refresh(me)
    assert me.password_hash == before


# -- Gruppen ----------------------------------------------------------------------------


def test_group_list_gives_areas_zones_and_permissions(client_als: Als, session: Session) -> None:
    zone = create_zone(session, "Wohnen")
    client = client_als(ADMIN)
    mine = session.scalar(select(AccessGroup).join(UserAccessGroup).where(UserAccessGroup.user_id == _me(client)))
    assert mine is not None
    session.add(GroupPermission(
        access_group_id=mine.id, permission_id=session.scalar(select(Permission.id).where(Permission.code == "device.read")) or 0,
        zone_id=zone.id,
    ))
    session.flush()
    body = client.get("/ui-api/access/groups").json()
    group = next(g for g in body["groups"] if g["id"] == mine.id)
    assert {"code": "device.read", "zone_id": zone.id} in group["permissions"]
    assert group["member_count"] == 1 and group["ui_profile"] == "admin"
    assert any("Wohnen" in line for line in group["summary"])
    titles = [a["title"] for a in body["areas"]]
    assert titles[0] == "Sehen und bedienen" and "Benutzer und Zugang" in titles
    flat = {p["code"]: p for a in body["areas"] for p in a["permissions"]}
    assert flat["zone.read"]["zone_scoped"] is True and flat["user.manage"]["zone_scoped"] is False
    assert {z["name"] for z in body["zones"]} == {"Wohnen"}
    assert body["ui_profiles"] == ["admin", "tenant"]


def test_create_and_delete_group(client_als: Als, session: Session) -> None:
    client = client_als(ADMIN)
    response = _call(client, "POST", "/ui-api/access/groups", {"name": "Mieter", "description": "Wohnung", "ui_profile": "tenant"})
    assert response.status_code == 201, response.text
    group = session.scalar(select(AccessGroup).where(AccessGroup.name == "Mieter"))
    assert group is not None and group.ui_profile == "tenant" and group.is_builtin is False
    assert response.json()["id"] == group.id and response.json()["description"] == "Wohnung"
    deleted = _call(client, "DELETE", f"/ui-api/access/groups/{group.id}")
    assert deleted.status_code == 200 and deleted.json() == {"ok": True}
    session.expire_all()
    assert session.get(AccessGroup, group.id) is None
    assert _actions(session) == ["group.created", "group.deleted"]


def test_create_group_errors(client_als: Als, session: Session) -> None:
    client = client_als(ADMIN)
    count = session.query(AccessGroup).count()
    _err(_call(client, "POST", "/ui-api/access/groups", {"name": " "}), 422, "invalid_name")
    _err(_call(client, "POST", "/ui-api/access/groups", {"name": "x", "ui_profile": "root"}), 422, "unknown_ui_profile")
    _err(_call(client, "POST", "/ui-api/access/groups", {"name": "x" * 65}), 422, "validation")
    existing = session.scalar(select(AccessGroup.name))
    assert existing is not None
    taken = _err(_call(client, "POST", "/ui-api/access/groups", {"name": existing}), 409, "name_taken")
    assert taken["field"] == "name"
    assert session.query(AccessGroup).count() == count


def test_builtin_group_cannot_be_deleted(client_als: Als, session: Session) -> None:
    client = client_als(ADMIN)
    group = AccessGroup(name="Eingebaut", is_builtin=True)
    session.add(group)
    session.flush()
    _err(_call(client, "DELETE", f"/ui-api/access/groups/{group.id}"), 409, "builtin_group")
    assert session.get(AccessGroup, group.id) is not None


def test_ui_profile_switch_is_strict(client_als: Als, session: Session) -> None:
    client = client_als(ADMIN)
    group = AccessGroup(name="Haus")
    session.add(group)
    session.flush()
    response = _call(client, "PUT", f"/ui-api/access/groups/{group.id}/ui-profile", {"ui_profile": "tenant"})
    assert response.status_code == 200 and response.json()["ui_profile"] == "tenant"
    session.refresh(group)
    assert group.ui_profile == "tenant"
    # Kein stiller Rückfall auf die Anlagensicht wie bei `parse_profile` in den Views:
    _err(_call(client, "PUT", f"/ui-api/access/groups/{group.id}/ui-profile", {"ui_profile": ""}), 422, "unknown_ui_profile")
    session.refresh(group)
    assert group.ui_profile == "tenant"
    assert "group.ui_profile_changed" in _actions(session)


def test_group_permissions_replace_the_whole_state(client_als: Als, session: Session) -> None:
    zone = create_zone(session, "Bad")
    client = client_als(ADMIN)
    group = AccessGroup(name="Haus")
    session.add(group)
    session.flush()
    wanted = [{"code": "zone.read", "zone_id": zone.id}, {"code": "audit.read", "zone_id": None}]
    response = _call(client, "PUT", f"/ui-api/access/groups/{group.id}/permissions", {"permissions": wanted})
    assert response.status_code == 200, response.text
    assert {(p["code"], p["zone_id"]) for p in response.json()["permissions"]} == {("zone.read", zone.id), ("audit.read", None)}
    again = _call(client, "PUT", f"/ui-api/access/groups/{group.id}/permissions", {"permissions": wanted[:1]})
    assert [(p["code"], p["zone_id"]) for p in again.json()["permissions"]] == [("zone.read", zone.id)]
    rows = session.scalars(select(GroupPermission).where(GroupPermission.access_group_id == group.id)).all()
    assert len(rows) == 1
    actions = _actions(session)
    assert actions.count("group.permission_granted") == 2 and actions.count("group.permission_revoked") == 1


def test_group_permissions_input_errors_change_nothing(client_als: Als, session: Session) -> None:
    zone = create_zone(session, "Bad")
    client = client_als(ADMIN)
    group = AccessGroup(name="Haus")
    session.add(group)
    session.flush()
    url = f"/ui-api/access/groups/{group.id}/permissions"
    _err(_call(client, "PUT", url, {"permissions": [{"code": "gibt.es.nicht", "zone_id": None}]}), 422, "unknown_permission")
    _err(_call(client, "PUT", url, {"permissions": [{"code": "user.manage", "zone_id": zone.id}]}), 422, "invalid_scope")
    _err(_call(client, "PUT", url, {"permissions": [{"code": "zone.read", "zone_id": GHOST}]}), 422, "invalid_scope")
    assert session.scalars(select(GroupPermission).where(GroupPermission.access_group_id == group.id)).all() == []


# -- Eigene API-Tokens ------------------------------------------------------------------


def _issue(client: TestClient, **body: Any) -> Any:
    return _call(client, "POST", "/ui-api/access/tokens", body)


def test_token_list_shows_state_grants_presets_and_never_secrets(client_als: Als, session: Session) -> None:
    client = client_als([("token.self", None), ("zone.read", None), ("setpoint.write", None)])
    me = session.get(User, _me(client))
    assert me is not None
    active = token_with_permissions(session, me, [("zone.read", None)])
    revoked = token_with_permissions(session, create_user(session, "x"), [])
    revoked.revoked_at = utcnow()
    mine_revoked = ApiToken(user_id=me.id, name="alt", prefix="alt", token_hash="a" * 64, revoked_at=utcnow())
    mine_expired = ApiToken(user_id=me.id, name="abgelaufen", prefix="abg", token_hash="b" * 64, expires_at=utcnow())
    kiosk = ApiToken(user_id=me.id, name="tablet", prefix="kio", token_hash="c" * 64, is_kiosk=True)
    session.add_all([mine_revoked, mine_expired, kiosk])
    session.flush()
    body = client.get("/ui-api/access/tokens").json()
    names = {t["name"]: t for t in body["tokens"]}
    assert set(names) == {active.name, "alt", "abgelaufen"}  # weder fremde noch Kiosk-Token
    assert names["alt"]["state"] == "revoked" and names["abgelaufen"]["state"] == "expired"
    assert names[active.name]["state"] == "active"
    assert names[active.name]["permissions"] == [{"code": "zone.read", "zone_id": None}]
    assert names[active.name]["effective"] == [{"code": "zone.read", "zone_id": None}]
    assert {(a["code"]) for a in body["available"]} == {"token.self", "zone.read", "setpoint.write"}
    assert body["presets"]["read_only"] == ["zone.read"]
    assert body["presets"]["read_write"] == ["zone.read"]  # nur, was der Besitzer selbst hält
    text = client.get("/ui-api/access/tokens").text
    assert "token_hash" not in text and "hash-" not in text and "a" * 64 not in text


def test_issue_token_shows_plaintext_once_and_only_once(
    client_als: Als, session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    client = client_als(ADMIN)
    with caplog.at_level(logging.DEBUG):
        response = _issue(client, name="Skript", valid_days=30, permissions=[{"code": "zone.read", "zone_id": None}])
    assert response.status_code == 201, response.text
    assert response.headers["cache-control"] == "no-store"
    plaintext = response.json()["plaintext"]
    secret = plaintext.split("_", 2)[2]
    token = session.scalar(select(ApiToken).where(ApiToken.name == "Skript"))
    assert token is not None and plaintext.startswith("tctl_")
    assert response.json()["token"]["id"] == token.id and response.json()["token"]["state"] == "active"
    assert token.expires_at is not None and 29 <= (token.expires_at - utcnow()).days <= 30
    assert token.is_kiosk is False and token.user_id == _me(client)
    assert {"plaintext", "token_hash"} & set(response.json()["token"]) == set()
    # Klartext nirgends gespeichert: nicht als Hash-Spalte, nicht im Protokoll, nicht im Log.
    assert token.token_hash != secret and token.token_hash != plaintext
    for event in session.scalars(select(AuditEvent)):
        assert secret not in f"{event.summary} {event.detail} {event.object_id}"
    assert secret not in caplog.text and plaintext not in caplog.text
    issued = session.scalars(select(AuditEvent).where(AuditEvent.action == "token.issued")).one()
    assert (issued.object_type, issued.object_id) == ("api_token", str(token.id))
    assert issued.actor_user_id == _me(client) and "Skript" in issued.summary
    assert "zone.read" in (issued.detail or "") and "Ablauf" in (issued.detail or "")
    assert plaintext not in f"{issued.summary} {issued.detail}"
    # Nie wieder lesbar:
    for url in ("/ui-api/access/tokens", "/ui-api/access/kiosk-tokens"):
        assert secret not in client.get(url).text
    revoked = _call(client, "POST", f"/ui-api/access/tokens/{token.id}/revoke")
    assert secret not in revoked.text
    edited = _call(client, "PUT", f"/ui-api/access/tokens/{token.id}/permissions", {"permissions": []})
    assert secret not in edited.text
    # Der Klartext ist ein brauchbares Bearer-Token (REST-Semantik unberührt), bis er widerrufen ist.
    assert revoked.json()["state"] == "revoked"


def test_issue_token_errors_never_echo_input_and_create_nothing(client_als: Als, session: Session) -> None:
    client = client_als(ADMIN)
    count = session.query(ApiToken).count()
    secretive = "geheimer-name-" * 3
    for body, status, code in [
        ({"name": " ", "preset": "read_only"}, 422, "invalid_name"),
        ({"name": secretive}, 422, "missing_selection"),
        ({"name": secretive, "preset": "gibtsnicht"}, 422, "unknown_preset"),
        ({"name": secretive, "preset": "read_only", "valid_days": 0}, 422, "validation"),
        ({"name": secretive, "preset": "read_only", "valid_days": 99999}, 422, "validation"),
        ({"name": "x" * 129, "preset": "read_only"}, 422, "validation"),
    ]:
        response = _err(_issue(client, **body), status, code)
        assert secretive not in str(response)
    assert session.query(ApiToken).count() == count


def test_preset_is_limited_to_the_rights_the_issuer_holds(client_als: Als, session: Session) -> None:
    client = client_als([("token.self", None), ("zone.read", None)])
    response = _issue(client, name="vorlage", preset="read_write")
    assert response.status_code == 201, response.text
    assert [(g["code"], g["zone_id"]) for g in response.json()["token"]["permissions"]] == [("zone.read", None)]


def test_a_token_can_never_carry_more_than_its_issuer(client_als: Als, session: Session) -> None:
    zone = create_zone(session, "Bad")
    other = create_zone(session, "Flur")
    client = client_als([("token.self", None), ("zone.read", zone.id)])
    count = session.query(ApiToken).count()
    bodies = []
    for grant in (
        {"code": "user.manage", "zone_id": None},        # nicht gehalten
        {"code": "gibt.es.nicht", "zone_id": None},       # unbekannt
        {"code": "zone.read", "zone_id": None},           # Zone -> ganze Anlage: Ausweitung
        {"code": "zone.read", "zone_id": other.id},       # andere Zone
        {"code": "zone.read", "zone_id": GHOST},          # Zone gibt es nicht
        {"code": "user.manage", "zone_id": zone.id},      # nicht zonenbezogen
    ):
        bodies.append(_err(_issue(client, name="x", permissions=[grant]), 403, "permission_not_held"))
    assert all(b == bodies[0] for b in bodies)  # Gründe ununterscheidbar
    assert session.query(ApiToken).count() == count
    ok = _issue(client, name="bad", permissions=[{"code": "zone.read", "zone_id": zone.id}])
    assert ok.status_code == 201


def test_plant_wide_right_may_be_narrowed_to_one_zone(client_als: Als, session: Session) -> None:
    zone = create_zone(session, "Bad")
    client = client_als([("token.self", None), ("zone.read", None)])
    response = _issue(client, name="eng", permissions=[{"code": "zone.read", "zone_id": zone.id}])
    assert response.status_code == 201
    assert response.json()["token"]["permissions"] == [{"code": "zone.read", "zone_id": zone.id}]


def test_change_token_permissions_is_a_subset_and_logged(client_als: Als, session: Session) -> None:
    client = client_als([("token.self", None), ("zone.read", None), ("setpoint.write", None)])
    issued = _issue(client, name="t", permissions=[{"code": "zone.read", "zone_id": None}]).json()["token"]
    url = f"/ui-api/access/tokens/{issued['id']}/permissions"
    ok = _call(client, "PUT", url, {"permissions": [{"code": "setpoint.write", "zone_id": None}]})
    assert ok.status_code == 200 and ok.json()["permissions"] == [{"code": "setpoint.write", "zone_id": None}]
    stored = session.execute(
        select(Permission.code).join(ApiTokenPermission).where(ApiTokenPermission.api_token_id == issued["id"])
    ).scalars().all()
    assert stored == ["setpoint.write"]
    event = session.scalar(select(AuditEvent).where(AuditEvent.action == "token.permissions_changed"))
    assert event is not None and event.detail == "alt: zone.read; neu: setpoint.write"
    # Ausweitung: abgewiesen, Bestand unverändert
    _err(_call(client, "PUT", url, {"permissions": [{"code": "user.manage", "zone_id": None}]}), 403, "permission_not_held")
    assert session.execute(
        select(Permission.code).join(ApiTokenPermission).where(ApiTokenPermission.api_token_id == issued["id"])
    ).scalars().all() == ["setpoint.write"]
    # leere Auswahl ist erlaubt (Token ohne Rechte), doppelte Einträge werden zusammengeführt
    dup = {"code": "zone.read", "zone_id": None}
    assert len(_call(client, "PUT", url, {"permissions": [dup, dup]}).json()["permissions"]) == 1
    assert _call(client, "PUT", url, {"permissions": []}).json()["permissions"] == []


def test_revoked_or_expired_token_cannot_be_edited(client_als: Als, session: Session) -> None:
    client = client_als(ADMIN)
    me = session.get(User, _me(client))
    assert me is not None
    revoked = token_with_permissions(session, me, [])
    revoked.revoked_at = utcnow()
    expired = ApiToken(user_id=me.id, name="abg", prefix="abg", token_hash="d" * 64, expires_at=utcnow())
    session.add(expired)
    session.flush()
    for token in (revoked, expired):
        _err(_call(client, "PUT", f"/ui-api/access/tokens/{token.id}/permissions", {"permissions": []}), 409, "token_not_active")


def test_revoke_token_and_twice(client_als: Als, session: Session) -> None:
    client = client_als(ADMIN)
    issued = _issue(client, name="t", preset="read_only").json()["token"]
    first = _call(client, "POST", f"/ui-api/access/tokens/{issued['id']}/revoke")
    assert first.status_code == 200 and first.json()["state"] == "revoked"
    token = session.get(ApiToken, issued["id"])
    assert token is not None and token.revoked_at is not None
    stamp = token.revoked_at
    assert _call(client, "POST", f"/ui-api/access/tokens/{issued['id']}/revoke").status_code == 200
    assert token.revoked_at == stamp
    assert _actions(session).count("token.revoked") == 1


def test_revoking_an_oauth_token_blocks_its_refresh_family(client_als: Als, session: Session) -> None:
    client = client_als(ADMIN)
    me = session.get(User, _me(client))
    assert me is not None
    token = token_with_permissions(session, me, [])
    token.source = "oauth"
    oauth_client = register_client(session, "Anwendung", ["https://client.example/return"])
    token.oauth_client_id = oauth_client.id
    row = OAuthRefreshToken(
        token_hash="h" * 64, family="f" * 32, client_id=oauth_client.id, user_id=me.id,
        api_token_id=token.id, expires_at=utcnow() + REFRESH_TTL,
    )
    session.add(row)
    session.flush()
    listed = client.get("/ui-api/access/tokens").json()["tokens"]
    assert listed[0]["source"] == "oauth" and listed[0]["client_name"] == "Anwendung"
    response = _call(client, "POST", f"/ui-api/access/tokens/{token.id}/revoke")
    assert response.status_code == 200 and response.json()["client_name"] == "Anwendung"
    assert token.revoked_at is not None and row.revoked_at is not None


def test_tokens_and_kiosk_tokens_do_not_cross(client_als: Als, session: Session) -> None:
    zone = create_zone(session, "Wohnen")
    client = client_als(ADMIN)
    kiosk = _call(client, "POST", "/ui-api/access/kiosk-tokens", {"name": "tablet", "zone_ids": [zone.id]}).json()["token"]
    own = _issue(client, name="eigen", preset="read_only").json()["token"]
    _err(_call(client, "PUT", f"/ui-api/access/tokens/{kiosk['id']}/permissions", {"permissions": []}), 404, "not_found")
    _err(_call(client, "POST", f"/ui-api/access/tokens/{kiosk['id']}/revoke"), 404, "not_found")
    _err(_call(client, "POST", f"/ui-api/access/kiosk-tokens/{own['id']}/revoke"), 404, "not_found")
    assert session.get(ApiToken, kiosk["id"]).revoked_at is None  # type: ignore[union-attr]
    assert session.get(ApiToken, own["id"]).revoked_at is None  # type: ignore[union-attr]


def test_token_of_another_user_with_token_self_is_not_manageable(client_als: Als, session: Session) -> None:
    client = client_als(ADMIN)  # auch token.manage ändert daran nichts: das sind persönliche Token
    foreign = token_with_permissions(session, create_user(session, "fremd"), [("zone.read", None)])
    _err(_call(client, "POST", f"/ui-api/access/tokens/{foreign.id}/revoke"), 404, "not_found")
    assert foreign.revoked_at is None
    assert foreign.name not in client.get("/ui-api/access/tokens").text


# -- Kiosk-Zugänge ----------------------------------------------------------------------


def test_kiosk_list_carries_the_plant_timezone_not_the_browsers(
    client_als: Als, session: Session
) -> None:
    """Der Ablauf eines Zugangs wird in der Anlagen-Zeitzone angezeigt; ohne Einrichtung gilt UTC."""
    client = client_als(ADMIN)
    row = session.get(Setting, 1)
    assert row is not None
    row.timezone = "Pacific/Auckland"
    session.flush()
    assert client.get("/ui-api/access/kiosk-tokens").json()["timezone"] == "Pacific/Auckland"
    session.delete(row)
    session.flush()
    assert client.get("/ui-api/access/kiosk-tokens").json()["timezone"] == "UTC"


def test_kiosk_issue_list_and_plaintext_once(
    client_als: Als, session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    a, b = create_zone(session, "Wohnen"), create_zone(session, "Bad")
    client = client_als(ADMIN)
    with caplog.at_level(logging.DEBUG):
        response = _call(client, "POST", "/ui-api/access/kiosk-tokens", {
            "name": "Flur-Tablet", "zone_ids": [a.id, a.id, b.id], "control_allowed": True, "valid_days": 7,
        })
    assert response.status_code == 201, response.text
    assert response.headers["cache-control"] == "no-store"
    plaintext = response.json()["plaintext"]
    secret = plaintext.split("_", 2)[2]
    token = session.scalar(select(ApiToken).where(ApiToken.name == "Flur-Tablet"))
    assert token is not None and token.is_kiosk is True and token.expires_at is not None
    out = response.json()["token"]
    assert out["control_allowed"] is True and {z["id"] for z in out["zones"]} == {a.id, b.id}
    codes = set(session.execute(
        select(Permission.code, ApiTokenPermission.zone_id).join(ApiTokenPermission).where(ApiTokenPermission.api_token_id == token.id)
    ).all())
    assert ("setpoint.write", a.id) in codes and ("zone.read", b.id) in codes and ("user.manage", None) not in codes
    assert secret not in caplog.text and plaintext not in caplog.text
    for event in session.scalars(select(AuditEvent)):
        assert secret not in f"{event.summary} {event.detail}"
    issued = session.scalars(select(AuditEvent).where(AuditEvent.action == "kiosk_token.issued")).one()
    assert (issued.object_type, issued.object_id) == ("api_token", str(token.id))
    assert issued.actor_user_id == _me(client) and "Flur-Tablet" in issued.summary
    assert "Wohnen" in (issued.detail or "") and "Bad" in (issued.detail or "")
    assert "Bedienung: ja" in (issued.detail or "")
    assert plaintext not in f"{issued.summary} {issued.detail}"
    listing = client.get("/ui-api/access/kiosk-tokens")
    assert secret not in listing.text and "token_hash" not in listing.text and plaintext not in listing.text
    names = {t["name"]: t for t in listing.json()["tokens"]}
    assert names["Flur-Tablet"]["state"] == "active" and names["Flur-Tablet"]["control_allowed"] is True
    assert {z["name"] for z in listing.json()["zones"]} == {"Wohnen", "Bad"}
    # Nur ansehen:
    view = _call(client, "POST", "/ui-api/access/kiosk-tokens", {"name": "Anzeige", "zone_ids": [a.id]}).json()["token"]
    assert view["control_allowed"] is False


def test_kiosk_issue_errors_create_nothing(client_als: Als, session: Session) -> None:
    zone = create_zone(session, "Wohnen")
    client = client_als(ADMIN)
    count = session.query(ApiToken).count()
    url = "/ui-api/access/kiosk-tokens"
    _err(_call(client, "POST", url, {"name": " ", "zone_ids": [zone.id]}), 422, "invalid_name")
    _err(_call(client, "POST", url, {"name": "k", "zone_ids": []}), 422, "kiosk_scope")
    _err(_call(client, "POST", url, {"name": "k", "zone_ids": [GHOST]}), 422, "unknown_zone")
    _err(_call(client, "POST", url, {"name": "k", "zone_ids": [zone.id], "valid_days": 0}), 422, "validation")
    assert session.query(ApiToken).count() == count


def test_kiosk_cannot_exceed_the_issuer_rights(client_als: Als, session: Session) -> None:
    zone, other = create_zone(session, "Wohnen"), create_zone(session, "Bad")
    client = client_als([("token.manage", None), ("zone.read", zone.id)])
    count = session.query(ApiToken).count()
    denied = _err(_call(client, "POST", "/ui-api/access/kiosk-tokens", {"name": "k", "zone_ids": [other.id]}), 422, "kiosk_scope")
    assert "zone.read" in denied["detail"]
    # Bedienen verlangt setpoint.write & Co. in der Zone -- die fehlen dem Aussteller:
    _err(_call(client, "POST", "/ui-api/access/kiosk-tokens", {"name": "k", "zone_ids": [zone.id], "control_allowed": True}), 422, "kiosk_scope")
    assert session.query(ApiToken).count() == count
    assert _call(client, "POST", "/ui-api/access/kiosk-tokens", {"name": "k", "zone_ids": [zone.id]}).status_code == 201


def test_kiosk_revoke(client_als: Als, session: Session) -> None:
    zone = create_zone(session, "Wohnen")
    client = client_als(ADMIN)
    issued = _call(client, "POST", "/ui-api/access/kiosk-tokens", {"name": "k", "zone_ids": [zone.id]}).json()["token"]
    response = _call(client, "POST", f"/ui-api/access/kiosk-tokens/{issued['id']}/revoke")
    assert response.status_code == 200 and response.json()["state"] == "revoked"
    token = session.get(ApiToken, issued["id"])
    assert token is not None and token.revoked_at is not None
    assert "token.revoked" in _actions(session)


# -- Konto ------------------------------------------------------------------------------


def test_account_profile(client_als: Als, session: Session, tenant_client: Als) -> None:
    client = client_als([])
    body = client.get("/ui-api/account").json()
    assert body["username"].startswith("web-") and body["ui_profile"] == "admin"
    assert body["user_id"] == _me(client) and body["passkeys_available"] is False
    assert "password" not in str(body)
    assert tenant_client([]).get("/ui-api/account").json()["ui_profile"] == "tenant"


def test_account_reports_passkey_availability(
    client_als: Als, monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    client_als([])
    monkeypatch.setenv("THERMOCTL_PASSKEY_RP_ID", "heizung.example")
    get_settings.cache_clear()
    assert client.get("/ui-api/account").json()["passkeys_available"] is True
    assert client.get("/ui-api/account/passkeys").json()["available"] is True


def test_change_own_password_needs_the_old_one_and_keeps_only_this_session(
    client_als: Als, session: Session
) -> None:
    client = client_als([])
    me = session.get(User, _me(client))
    assert me is not None
    me.password_hash = hash_password("das-alte-passwort-1")
    other, _s = create_session(session, me, 3600)
    mine = resolve_session(session, client.cookies[COOKIE_NAME])
    assert mine is not None
    url = "/ui-api/account/password"
    # falsches Passwort: nichts ändert sich, nichts im Protokoll
    wrong = _err(_call(client, "PUT", url, {"current_password": "falsch-falsch-1", "new_password": LONG_PASSWORD}), 403, "wrong_password")
    assert wrong["field"] == "current_password"
    session.refresh(me)
    assert verify_password("das-alte-passwort-1", me.password_hash) and other.revoked_at is None
    assert "user.password_changed" not in _actions(session)
    # zu kurzes neues Passwort
    short = _err(_call(client, "PUT", url, {"current_password": "das-alte-passwort-1", "new_password": "kurz"}), 422, "password_too_short")
    assert short["field"] == "new_password"
    session.refresh(me)
    assert verify_password("das-alte-passwort-1", me.password_hash)
    # richtig
    response = _call(client, "PUT", url, {"current_password": "das-alte-passwort-1", "new_password": LONG_PASSWORD})
    assert response.status_code == 200 and response.json() == {"ok": True}
    session.refresh(me)
    assert verify_password(LONG_PASSWORD, me.password_hash)
    assert other.revoked_at is not None and mine.revoked_at is None
    assert client.get("/ui-api/account").status_code == 200
    assert LONG_PASSWORD not in response.text and "user.password_changed" in _actions(session)


def test_sessions_list_revoke_one_and_others(client_als: Als, session: Session) -> None:
    client = client_als([])
    me = session.get(User, _me(client))
    assert me is not None
    other, _s = create_session(session, me, 3600, user_agent="Firefox", ip="10.0.0.2")
    third, _t = create_session(session, me, 3600)
    gone, _g = create_session(session, me, 3600)
    gone.revoked_at = utcnow()
    stranger, _x = create_session(session, create_user(session, "fremd"), 3600)
    session.flush()
    rows = client.get("/ui-api/account/sessions").json()
    ids = {r["id"] for r in rows}
    assert ids == {other.id, third.id, next(r["id"] for r in rows if r["current"])}
    assert stranger.id not in ids and gone.id not in ids
    assert [r["current"] for r in rows].count(True) == 1
    listed = next(r for r in rows if r["id"] == other.id)
    assert listed["user_agent"] == "Firefox" and listed["ip_address"] == "10.0.0.2"
    assert "token_hash" not in str(rows)
    # eine beenden
    response = _call(client, "POST", f"/ui-api/account/sessions/{other.id}/revoke")
    assert response.status_code == 200 and other.revoked_at is not None and third.revoked_at is None
    _err(_call(client, "POST", f"/ui-api/account/sessions/{other.id}/revoke"), 404, "not_found")
    # die eigene nicht über diese Route
    current = next(r["id"] for r in client.get("/ui-api/account/sessions").json() if r["current"])
    _err(_call(client, "POST", f"/ui-api/account/sessions/{current}/revoke"), 409, "current_session")
    # alle anderen
    ended = _call(client, "POST", "/ui-api/account/sessions/revoke-others")
    assert ended.json() == {"ended": 1} and third.revoked_at is not None and stranger.revoked_at is None
    assert client.get("/ui-api/account").status_code == 200
    actions = _actions(session)
    assert "session.revoked" in actions and "session.revoked_others" in actions


def test_expired_session_is_not_listed_nor_revocable(client_als: Als, session: Session) -> None:
    client = client_als([])
    me = session.get(User, _me(client))
    assert me is not None
    old, _s = create_session(session, me, 3600)
    old.expires_at = utcnow()
    session.flush()
    assert old.id not in {r["id"] for r in client.get("/ui-api/account/sessions").json()}
    _err(_call(client, "POST", f"/ui-api/account/sessions/{old.id}/revoke"), 404, "not_found")


def test_passkeys_list_and_remove_own_only(client_als: Als, session: Session) -> None:
    client = client_als([])
    me = session.get(User, _me(client))
    assert me is not None
    mine = create_passkey(session, me, "meins")
    theirs = create_passkey(session, create_user(session, "fremd"), "deins")
    body = client.get("/ui-api/account/passkeys").json()
    assert body["available"] is False
    assert [p["id"] for p in body["passkeys"]] == [mine.id]
    assert body["passkeys"][0]["label"] == "Passkey meins"
    assert "public_key" not in str(body) and "credential" not in str(body) and "platzhalter" not in str(body)
    _err(_call(client, "DELETE", f"/ui-api/account/passkeys/{theirs.id}"), 404, "not_found")
    assert session.get(UserPasskey, theirs.id) is not None
    response = _call(client, "DELETE", f"/ui-api/account/passkeys/{mine.id}")
    assert response.status_code == 200 and response.json() == {"ok": True}
    session.expire_all()
    assert session.get(UserPasskey, mine.id) is None
    assert "passkey.removed" in _actions(session)


def test_sessions_left_untouched_by_listing(client_als: Als, session: Session) -> None:
    client = client_als([])
    before = session.query(Session_).filter(Session_.revoked_at.is_not(None)).count()
    client.get("/ui-api/account/sessions")
    assert session.query(Session_).filter(Session_.revoked_at.is_not(None)).count() == before


def test_password_change_throttles_wrong_attempts_and_success_resets(
    client_als: Als, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from thermoctl.web.shared.login import FEHLVERSUCHE

    delays: list[float] = []

    async def merken(seconds: float) -> None:
        delays.append(seconds)

    monkeypatch.setattr("thermoctl.web.shared.login.sleep", merken)
    client = client_als([])
    me = session.get(User, _me(client))
    assert me is not None
    me.password_hash = hash_password("das-alte-passwort-1")
    url = "/ui-api/account/password"
    key = f"passwort:{me.id}"
    FEHLVERSUCHE.pop(key, None)
    wrong = {"current_password": "falsch-falsch-1", "new_password": LONG_PASSWORD}
    for _ in range(3):
        _err(_call(client, "PUT", url, wrong), 403, "wrong_password")
    assert delays == [1, 2, 4] and FEHLVERSUCHE[key] == 3
    ok = _call(client, "PUT", url, {"current_password": "das-alte-passwort-1", "new_password": LONG_PASSWORD})
    assert ok.status_code == 200
    assert delays == [1, 2, 4, 5] and FEHLVERSUCHE[key] == 0


def test_nobody_manages_their_own_account(client_als: Als, session: Session) -> None:
    """Deaktivieren und Umgruppieren am eigenen Konto sind gesperrt, bei anderen nicht."""
    client = client_als(ADMIN)
    me_id = _me(client)
    other = user_with_permissions(session, "kollege", [("zone.read", None)])
    ok = _call(client, "PUT", f"/ui-api/access/users/{other.id}/active", {"active": False})
    assert ok.status_code == 200
    # Aktivieren des eigenen Kontos ist kein Verwalten im Sinn der Sperre (nichts zu sperren).
    assert _call(client, "PUT", f"/ui-api/access/users/{me_id}/active", {"active": True}).status_code == 200


def test_group_permissions_of_own_group_cannot_grow_unless_sole_manager(
    client_als: Als, session: Session
) -> None:
    client = client_als(ADMIN)
    me_id = _me(client)
    group = session.scalar(select(AccessGroup).join(UserAccessGroup).where(UserAccessGroup.user_id == me_id))
    assert group is not None
    current = [{"code": c, "zone_id": None} for c, _z in ADMIN]
    wanted = [*current, {"code": "audit.read", "zone_id": None}]
    # Einzige Person mit group.manage: Erweitern bleibt möglich (Ein-Personen-Anlage).
    assert _call(client, "PUT", f"/ui-api/access/groups/{group.id}/permissions", {"permissions": wanted}).status_code == 200
    # Eine zweite Person mit group.manage: jetzt gilt die Sperre fuer das Hinzufuegen ...
    user_with_permissions(session, "zweiter-verwalter", [("group.manage", None)])
    wanted2 = [*wanted, {"code": "vacation.manage", "zone_id": None}]
    _err(_call(client, "PUT", f"/ui-api/access/groups/{group.id}/permissions", {"permissions": wanted2}), 409, "own_group")
    # ... Entziehen bleibt erlaubt.
    assert _call(client, "PUT", f"/ui-api/access/groups/{group.id}/permissions", {"permissions": current}).status_code == 200
