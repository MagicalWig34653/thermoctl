import secrets
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

from sqlalchemy.orm import Session

from tests.helpers import user_with_permissions
from thermoctl.auth.csrf import csrf_token
from thermoctl.auth.passwords import hash_password
from thermoctl.auth.sessions import COOKIE_NAME
from thermoctl.config import get_settings
from thermoctl.db.base import utcnow
from thermoctl.db.models.oauth import OAuthAuthorizationRequest
from thermoctl.domain.oauth import register_client, start_request


def _csrf(client):
    return {
        "X-CSRF-Token": csrf_token(
            client.cookies[COOKIE_NAME], get_settings().secret_key.get_secret_value()
        )
    }


def _request(session: Session, name: str = "Client") -> str:
    client = register_client(session, name, ["https://client.example/callback?old=1"])
    return start_request(
        session,
        client,
        "https://client.example/callback?old=1",
        "x" * 43,
        "https://mcp.example",
        "opaque",
        "https://mcp.example",
    )


def test_unknown_request_has_plain_error(client_als) -> None:
    c = client_als([("token.self", None)])
    response = c.get("/oauth/consent?request=unknown")
    assert response.status_code == 404
    assert "Client" not in response.text


def test_expired_and_consumed_requests_are_rejected(client_als, session: Session) -> None:
    c = client_als([("token.self", None)])
    request_id = _request(session)
    row = (
        session.query(OAuthAuthorizationRequest)
        .order_by(OAuthAuthorizationRequest.id.desc())
        .first()
    )
    assert row is not None
    row.expires_at = utcnow() - timedelta(seconds=1)
    assert c.get(f"/oauth/consent?request={request_id}").status_code == 404
    row.expires_at = utcnow() + timedelta(minutes=5)
    row.consumed_at = utcnow()
    assert c.get(f"/oauth/consent?request={request_id}").status_code == 404


def test_client_name_is_escaped_and_preset_checked(client_als, session: Session) -> None:
    c = client_als([("token.self", None), ("zone.read", None)])
    request_id = _request(session, "<script>alert(1)</script>")
    body = c.get(f"/oauth/consent?request={request_id}").text
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;" in body
    assert 'value="zone.read" checked' in body
    assert "Selbst angemeldeter Client, nicht geprüft" in body


def test_permission_outside_owner_is_rejected(client_als, session: Session) -> None:
    c = client_als([("token.self", None), ("zone.read", None)])
    request_id = _request(session)
    response = c.post(
        "/oauth/consent",
        data={"request": request_id, "action": "allow", "permission": ["zone.manage"]},
        headers=_csrf(c),
    )
    assert response.status_code == 403


def test_deny_redirects_with_state_and_consumes_request(client_als, session: Session) -> None:
    c = client_als([("token.self", None)])
    request_id = _request(session)
    response = c.post(
        "/oauth/consent",
        data={"request": request_id, "action": "deny"},
        headers=_csrf(c),
        follow_redirects=False,
    )
    assert response.status_code == 303
    query = parse_qs(urlsplit(response.headers["location"]).query)
    assert query == {"old": ["1"], "error": ["access_denied"], "state": ["opaque"]}
    assert c.get(f"/oauth/consent?request={request_id}").status_code == 404


def test_consent_needs_token_self_and_csrf(client_als, session: Session) -> None:
    request_id = _request(session)
    without_right = client_als([("zone.read", None)])
    assert without_right.get(f"/oauth/consent?request={request_id}").status_code == 403
    c = client_als([("token.self", None), ("zone.read", None)])
    assert (
        c.post(
            "/oauth/consent",
            data={"request": request_id, "action": "allow", "permission": "zone.read"},
        ).status_code
        == 403
    )
    assert (
        c.post(
            "/oauth/consent",
            data={"request": request_id, "action": "allow", "permission": "zone.read"},
            headers={"X-CSRF-Token": "wrong"},
        ).status_code
        == 403
    )


def test_allow_appends_code_and_state_once(client_als, session: Session) -> None:
    c = client_als([("token.self", None), ("zone.read", None)])
    request_id = _request(session)
    response = c.post(
        "/oauth/consent",
        data={"request": request_id, "action": "allow", "permission": "zone.read"},
        headers=_csrf(c),
        follow_redirects=False,
    )
    assert response.status_code == 303
    query = parse_qs(urlsplit(response.headers["location"]).query)
    assert query["old"] == ["1"]
    assert query["state"] == ["opaque"]
    assert len(query["code"]) == 1
    assert c.get(f"/oauth/consent?request={request_id}").status_code == 404


def test_empty_approval_is_form_error(client_als, session: Session) -> None:
    c = client_als([("token.self", None)])
    request_id = _request(session)
    response = c.post(
        "/oauth/consent", data={"request": request_id, "action": "allow"}, headers=_csrf(c)
    )
    assert response.status_code == 200
    assert "Mindestens ein Recht" in response.text
    assert c.get(f"/oauth/consent?request={request_id}").status_code == 200


def test_tenant_with_token_self_can_consent(tenant_client, session: Session) -> None:
    c = tenant_client([("token.self", None), ("zone.read", None)])
    request_id = _request(session)
    assert c.get(f"/oauth/consent?request={request_id}").status_code == 200


def test_login_returns_to_consent(client, session: Session, monkeypatch) -> None:
    user = user_with_permissions(session, "consent-login", [("token.self", None)])
    password = secrets.token_urlsafe(18)
    user.password_hash = hash_password(password)
    request_id = _request(session)
    response = client.get(f"/oauth/consent?request={request_id}", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login?return_to=")
    login_page = client.get(response.headers["location"])
    assert 'name="return_to"' in login_page.text

    async def no_delay(_seconds):
        return None

    monkeypatch.setattr("thermoctl.web.auth_views.sleep", no_delay)
    result = client.post(
        "/login",
        data={
            "username": user.username,
            "password": password,
            "return_to": f"/oauth/consent?request={request_id}",
        },
        follow_redirects=False,
    )
    assert result.status_code == 303
    assert result.headers["location"] == f"/oauth/consent?request={request_id}"


def test_login_rejects_external_return_target(client, session: Session, monkeypatch) -> None:
    user = user_with_permissions(session, "consent-safe-login", [("token.self", None)])
    password = secrets.token_urlsafe(18)
    user.password_hash = hash_password(password)

    async def no_delay(_seconds):
        return None

    monkeypatch.setattr("thermoctl.web.auth_views.sleep", no_delay)
    response = client.post(
        "/login",
        data={
            "username": user.username,
            "password": password,
            "return_to": "https://client.example/steal",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/"
