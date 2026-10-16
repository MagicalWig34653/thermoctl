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

    monkeypatch.setattr("thermoctl.web.shared.login.sleep", no_delay)
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

    monkeypatch.setattr("thermoctl.web.shared.login.sleep", no_delay)
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


def test_consent_pages_cannot_be_framed(client_als, session: Session) -> None:
    """Sonst könnte eine fremde Seite den Zustimmen-Knopf unsichtbar einbetten."""
    c = client_als([("token.self", None)])
    request_id = _request(session, "Rahmen")
    for response in (
        c.get(f"/oauth/consent?request={request_id}"),  # Formular
        c.get("/oauth/consent?request=unbekannt"),  # Fehlerseite
        c.post(
            "/oauth/consent",
            data={"request": request_id, "action": "deny"},
            headers=_csrf(c),
            follow_redirects=False,
        ),  # Weiterleitung
    ):
        assert response.headers["content-security-policy"] == "frame-ancestors 'none'"
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["cache-control"] == "no-store"


def _request_for(session: Session, uri: str, name: str = "Client") -> str:
    client = register_client(session, name, [uri])
    return start_request(
        session, client, uri, "x" * 43, "https://mcp.example", "opaque", "https://mcp.example"
    )


def test_consent_shows_scheme_host_and_port_of_a_network_redirect(
    client_als, session: Session
) -> None:
    c = client_als([("token.self", None)])
    body = c.get(
        f"/oauth/consent?request={_request_for(session, 'https://client.example:8443/cb')}"
    ).text
    assert "<code>https://client.example:8443</code>" in body
    assert "Nur zustimmen, wenn Sie diesen Dienst selbst eingerichtet haben" in body
    assert "läuft auf diesem Rechner" not in body


def test_consent_marks_loopback_redirect_as_local_without_warning(
    client_als, session: Session
) -> None:
    c = client_als([("token.self", None)])
    body = c.get(f"/oauth/consent?request={_request_for(session, 'http://localhost:9999/cb')}").text
    assert "<code>http://localhost:9999</code>" in body
    assert "läuft auf diesem Rechner" in body
    assert "Nur zustimmen, wenn Sie diesen Dienst selbst eingerichtet haben" not in body


def test_consent_app_scheme_has_neither_warning_nor_local_hint(
    client_als, session: Session
) -> None:
    c = client_als([("token.self", None)])
    body = c.get(f"/oauth/consent?request={_request_for(session, 'meineapp:/cb')}").text
    assert "<code>meineapp:</code>" in body
    assert "Nur zustimmen, wenn Sie diesen Dienst selbst eingerichtet haben" not in body
    assert "läuft auf diesem Rechner" not in body


def test_consent_escapes_client_name_next_to_the_warning(client_als, session: Session) -> None:
    c = client_als([("token.self", None)])
    request_id = _request_for(session, "https://client.example/cb", "<script>x</script>")
    body = c.get(f"/oauth/consent?request={request_id}").text
    assert "<script>x</script>" not in body
    assert "&lt;script&gt;x&lt;/script&gt;" in body
    assert "Nur zustimmen, wenn Sie diesen Dienst selbst eingerichtet haben" in body


def test_unexpected_http_errors_of_the_principal_lookup_are_not_swallowed(
    client_als, session: Session, monkeypatch
) -> None:
    from fastapi import HTTPException

    from thermoctl.web import oauth_views

    def broken(_request, _session):
        raise HTTPException(500, "kaputt")

    monkeypatch.setattr(oauth_views, "current_principal", broken)
    request_id = _request(session)
    # raise_server_exceptions=False greift für HTTPException nicht; sie wird zur Antwort.
    assert (
        client_als([("token.self", None)]).get(f"/oauth/consent?request={request_id}").status_code
        == 500
    )


def test_post_without_login_goes_to_login(client, session: Session) -> None:
    request_id = _request(session)
    client.cookies.clear()
    response = client.post(
        "/oauth/consent",
        data={"request": request_id, "action": "allow", "permission": "zone.read"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_post_for_unknown_request_is_plain_404(client_als) -> None:
    c = client_als([("token.self", None)])
    response = c.post(
        "/oauth/consent",
        data={"request": "unbekannt", "action": "allow", "permission": "zone.read"},
        headers=_csrf(c),
    )
    assert response.status_code == 404


def test_unknown_action_shows_form_error(client_als, session: Session) -> None:
    c = client_als([("token.self", None)])
    request_id = _request(session)
    response = c.post(
        "/oauth/consent", data={"request": request_id, "action": "vielleicht"}, headers=_csrf(c)
    )
    assert response.status_code == 200
    assert "Ungültige Aktion." in response.text


def test_approval_refused_by_the_domain_is_a_plain_400(
    client_als, session: Session, monkeypatch
) -> None:
    from thermoctl.domain.oauth import OAuthError
    from thermoctl.web import oauth_views

    def refuse(*_args, **_kwargs):
        raise OAuthError("invalid_request", "abgelaufen")

    monkeypatch.setattr(oauth_views, "approve", refuse)
    c = client_als([("token.self", None), ("zone.read", None)])
    request_id = _request(session)
    response = c.post(
        "/oauth/consent",
        data={"request": request_id, "action": "allow", "permission": "zone.read"},
        headers=_csrf(c),
    )
    assert response.status_code == 400
    assert "nicht bestätigt" in response.text


def _form_field_token(c) -> dict[str, str]:
    return {"csrf_token": _csrf(c)["X-CSRF-Token"]}


def test_consent_form_is_not_boosted_and_carries_csrf_field(client_als, session: Session) -> None:
    """Die Zustimmung führt zu einer fremden Adresse und muss ein echter Seitenwechsel sein."""
    c = client_als([("token.self", None), ("zone.read", None)])
    request_id = _request(session)
    body = c.get(f"/oauth/consent?request={request_id}").text
    post_form = body[body.index('<form method="post"') :]
    post_form = post_form[: post_form.index("</form>")]
    assert 'hx-boost="false"' in post_form.split(">", 1)[0]
    assert 'name="csrf_token"' in post_form


def test_htmx_submit_gets_hx_redirect_not_cross_origin_303(client_als, session: Session) -> None:
    c = client_als([("token.self", None), ("zone.read", None)])
    request_id = _request(session)
    response = c.post(
        "/oauth/consent",
        data={"request": request_id, "action": "allow", "permission": "zone.read"},
        headers={**_csrf(c), "HX-Request": "true", "HX-Boosted": "true"},
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert "location" not in response.headers
    target = urlsplit(response.headers["HX-Redirect"])
    assert (target.scheme, target.netloc, target.path) == ("https", "client.example", "/callback")
    query = parse_qs(target.query)
    assert query["state"] == ["opaque"]
    assert len(query["code"]) == 1


def test_htmx_deny_gets_hx_redirect(client_als, session: Session) -> None:
    c = client_als([("token.self", None)])
    request_id = _request(session)
    response = c.post(
        "/oauth/consent",
        data={"request": request_id, "action": "deny"},
        headers={**_csrf(c), "HX-Request": "true", "HX-Boosted": "true"},
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert "location" not in response.headers
    query = parse_qs(urlsplit(response.headers["HX-Redirect"]).query)
    assert query["error"] == ["access_denied"]
    assert query["state"] == ["opaque"]


def test_plain_form_post_with_field_token_redirects_with_303(client_als, session: Session) -> None:
    """Ohne htmx kommt das Token nur als Formularfeld; die Antwort ist eine gewöhnliche 303."""
    c = client_als([("token.self", None), ("zone.read", None)])
    request_id = _request(session)
    response = c.post(
        "/oauth/consent",
        data={
            "request": request_id,
            "action": "allow",
            "permission": "zone.read",
            **_form_field_token(c),
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "HX-Redirect" not in response.headers
    query = parse_qs(urlsplit(response.headers["location"]).query)
    assert query["state"] == ["opaque"] and len(query["code"]) == 1


def test_plain_form_deny_with_field_token_and_without_token(client_als, session: Session) -> None:
    c = client_als([("token.self", None)])
    request_id = _request(session)
    rejected = c.post(
        "/oauth/consent",
        data={"request": request_id, "action": "deny"},
        follow_redirects=False,
    )
    assert rejected.status_code == 403
    response = c.post(
        "/oauth/consent",
        data={"request": request_id, "action": "deny", **_form_field_token(c)},
        follow_redirects=False,
    )
    assert response.status_code == 303
    query = parse_qs(urlsplit(response.headers["location"]).query)
    assert query["error"] == ["access_denied"]
