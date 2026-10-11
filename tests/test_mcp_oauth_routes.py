"""HTTP-Vertrag der OAuth-Endpunkte des MCP-Servers."""

import base64
import hashlib
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy.orm import Session
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.testclient import TestClient

from tests.helpers import user_with_permissions
from thermoctl.config import Settings
from thermoctl.domain import oauth
from thermoctl.mcp import oauth_routes


class _SharedSession:
    """Reicht die Testsitzung durch session_scope und lässt sie offen."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def __call__(self):
        return self

    def __getattr__(self, name: str):
        if name == "close":
            return lambda: None
        return getattr(self._session, name)


def _settings(mode: str = "path") -> Settings:
    return Settings(
        _env_file=None,
        database_url="sqlite://",
        secret_key="x" * 32,
        mcp_mode=mode,
        mcp_path="/mcp",
        mcp_port=9000 if mode == "port" else None,
        public_url="https://web.example.invalid",
        mcp_public_url="https://mcp.example.invalid" if mode == "port" else None,
    )


def _client(session: Session, settings: Settings | None = None) -> TestClient:
    settings = settings or _settings()
    routes = oauth_routes.build_oauth_routes(_SharedSession(session), settings)
    if settings.mcp_mode == "path":
        return TestClient(
            Starlette(
                routes=[
                    *oauth_routes.build_root_wellknown_routes(settings),
                    Mount("/mcp", routes=routes),
                ]
            )
        )
    return TestClient(Starlette(routes=routes))


def _register(client: TestClient, uri: str = "https://client.example.invalid/callback") -> dict:
    response = client.post(
        "/mcp/register", json={"client_name": "Testclient", "redirect_uris": [uri]}
    )
    assert response.status_code == 201
    return response.json()


def _challenge(verifier: str) -> str:
    return (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )


def _authorize_params(client_id: str) -> dict[str, str]:
    return {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": "https://client.example.invalid/callback",
        "code_challenge": _challenge("v" * 43),
        "code_challenge_method": "S256",
        "resource": "https://web.example.invalid/mcp",
        "state": "merkmal",
    }


@pytest.mark.parametrize("mode,prefix", [("path", "/mcp"), ("port", "")])
def test_metadata_at_mount_and_rfc_root(session: Session, mode: str, prefix: str) -> None:
    settings = _settings(mode)
    client = _client(session, settings)
    issuer = settings.mcp_base_url()
    names = ("oauth-protected-resource", "oauth-authorization-server")
    for name in names:
        paths = [f"{prefix}/.well-known/{name}"]
        if mode == "path":
            paths.append(f"/.well-known/{name}/mcp")
        for path in paths:
            response = client.get(path)
            assert response.status_code == 200
            data = response.json()
            if name == "oauth-protected-resource":
                assert data == {
                    "resource": issuer,
                    "authorization_servers": [issuer],
                    "bearer_methods_supported": ["header"],
                }
            else:
                assert data == {
                    "issuer": issuer,
                    "authorization_endpoint": issuer + "/authorize",
                    "token_endpoint": issuer + "/token",
                    "registration_endpoint": issuer + "/register",
                    "revocation_endpoint": issuer + "/revoke",
                    "response_types_supported": ["code"],
                    "grant_types_supported": ["authorization_code", "refresh_token"],
                    "code_challenge_methods_supported": ["S256"],
                    "token_endpoint_auth_methods_supported": ["none"],
                }


def test_registration_success_and_invalid_metadata(session: Session) -> None:
    client = _client(session)
    data = _register(client)
    assert data["client_name"] == "Testclient"
    assert data["redirect_uris"] == ["https://client.example.invalid/callback"]
    assert data["token_endpoint_auth_method"] == "none"
    assert data["grant_types"] == ["authorization_code", "refresh_token"]
    assert data["response_types"] == ["code"]
    for body in (
        {"client_name": ""},
        {
            "client_name": "X",
            "redirect_uris": ["https://x.invalid/cb"],
            "token_endpoint_auth_method": "client_secret_basic",
        },
        {"client_name": "X", "redirect_uris": ["https://x.invalid/cb"] * 11},
    ):
        assert client.post("/mcp/register", json=body).status_code == 400
    assert (
        client.post(
            "/mcp/register", content=b"{" * 16385, headers={"content-type": "application/json"}
        ).status_code
        == 413
    )


def test_registration_rate_limit_uses_peer_and_expires(session: Session, monkeypatch) -> None:
    moment = [1000.0]
    monkeypatch.setattr(oauth_routes, "_now", lambda: moment[0])
    client = _client(session)
    payload = {"client_name": "Test", "redirect_uris": ["https://client.example.invalid/cb"]}
    for _ in range(20):
        assert (
            client.post(
                "/mcp/register", json=payload, headers={"X-Forwarded-For": "wechselnd"}
            ).status_code
            == 201
        )
    assert client.post("/mcp/register", json=payload).status_code == 429
    moment[0] += 3601
    assert client.post("/mcp/register", json=payload).status_code == 201


def test_authorize_untrusted_redirect_is_plain_escaped_error(session: Session) -> None:
    client = _client(session)
    client_id = _register(client)["client_id"]
    for change in (
        {"client_id": "unbekannt"},
        {"redirect_uri": "https://evil.example.invalid/<script>"},
    ):
        response = client.get(
            "/mcp/authorize", params=_authorize_params(client_id) | change, follow_redirects=False
        )
        assert response.status_code == 400
        assert "location" not in response.headers
        assert "<script>" not in response.text


@pytest.mark.parametrize(
    "change,error",
    [
        ({"response_type": "token"}, "unsupported_response_type"),
        ({"code_challenge": "kurz"}, "invalid_request"),
        ({"code_challenge_method": "plain"}, "invalid_request"),
        ({"resource": "https://fremd.invalid"}, "invalid_target"),
        ({"resource": ""}, "invalid_request"),
    ],
)
def test_authorize_error_redirect_keeps_query_and_state(
    session: Session, change: dict[str, str], error: str
) -> None:
    client = _client(session)
    uri = "https://client.example.invalid/callback?vorhanden=1"
    client_id = _register(client, uri)["client_id"]
    params = _authorize_params(client_id) | {"redirect_uri": uri} | change
    response = client.get("/mcp/authorize", params=params, follow_redirects=False)
    assert response.status_code == 303
    parsed = urlsplit(response.headers["location"])
    assert parsed.netloc == "client.example.invalid"
    assert parse_qs(parsed.query) == {
        "vorhanden": ["1"],
        "error": [error],
        "error_description": [parse_qs(parsed.query)["error_description"][0]],
        "state": ["merkmal"],
    }


def test_full_flow_and_code_reuse_commits_revocation(session: Session) -> None:
    client = _client(session)
    client_id = _register(client)["client_id"]
    response = client.get(
        "/mcp/authorize", params=_authorize_params(client_id), follow_redirects=False
    )
    assert response.status_code == 303
    location = urlsplit(response.headers["location"])
    assert location.path == "/oauth/consent"
    request_id = parse_qs(location.query)["request"][0]
    user = user_with_permissions(session, "oauth-route", [("zone.read", None)])
    request = oauth.load_request(session, request_id)
    assert request is not None
    code = oauth.approve(session, request, user, [("zone.read", None)])
    token_form = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "code": code,
        "code_verifier": "v" * 43,
        "redirect_uri": "https://client.example.invalid/callback",
        "resource": "https://web.example.invalid/mcp",
    }
    first = client.post("/mcp/token", data=token_form)
    assert first.status_code == 200
    assert first.headers["cache-control"] == "no-store"
    assert first.headers["pragma"] == "no-cache"
    assert first.json()["token_type"] == "Bearer"
    assert first.json()["expires_in"] > 0
    access = first.json()["access_token"]
    refresh = first.json()["refresh_token"]
    assert client.post("/mcp/token", data=token_form).json()["error"] == "invalid_grant"
    assert (
        client.post(
            "/mcp/token",
            data={"grant_type": "refresh_token", "client_id": client_id, "refresh_token": refresh},
        ).json()["error"]
        == "invalid_grant"
    )
    # Neuer Durchlauf für erfolgreichen Refresh und Widerruf.
    again = client.get(
        "/mcp/authorize", params=_authorize_params(client_id), follow_redirects=False
    )
    pending = oauth.load_request(
        session, parse_qs(urlsplit(again.headers["location"]).query)["request"][0]
    )
    assert pending is not None
    code2 = oauth.approve(session, pending, user, [("zone.read", None)])
    token_form["code"] = code2
    issued = client.post("/mcp/token", data=token_form).json()
    rotated = client.post(
        "/mcp/token",
        data={
            "grant_type": "refresh_token",
            "client_id": client_id,
            "refresh_token": issued["refresh_token"],
        },
    )
    assert rotated.status_code == 200
    assert rotated.json()["refresh_token"] != issued["refresh_token"]
    assert (
        client.post("/mcp/revoke", data={"token": rotated.json()["refresh_token"]}).status_code
        == 200
    )
    assert (
        client.post(
            "/mcp/token",
            data={
                "grant_type": "refresh_token",
                "client_id": client_id,
                "refresh_token": rotated.json()["refresh_token"],
            },
        ).json()["error"]
        == "invalid_grant"
    )
    assert client.post("/mcp/revoke", data={"token": access}).content == b""
    assert client.post("/mcp/revoke", data={"token": "unbekannt"}).status_code == 200


@pytest.mark.parametrize(
    "form,error",
    [
        ({"grant_type": "unbekannt"}, "unsupported_grant_type"),
        ({"grant_type": "authorization_code"}, "invalid_request"),
        ({"grant_type": "refresh_token"}, "invalid_request"),
    ],
)
def test_token_input_errors(session: Session, form: dict[str, str], error: str) -> None:
    response = _client(session).post("/mcp/token", data=form)
    assert response.status_code == 400
    assert response.json()["error"] == error
    assert "error_description" in response.json()


def test_token_rejects_wrong_content_type_and_large_body(session: Session) -> None:
    client = _client(session)
    assert client.post("/mcp/token", json={"grant_type": "refresh_token"}).status_code == 400
    assert (
        client.post(
            "/mcp/token",
            content=b"a" * 16385,
            headers={"content-type": "application/x-www-form-urlencoded"},
        ).status_code
        == 413
    )


@pytest.mark.parametrize(
    "field,value,error",
    [
        ("code_verifier", "f" * 43, "invalid_grant"),
        ("redirect_uri", "https://client.example.invalid/other", "invalid_grant"),
        ("resource", "https://fremd.invalid", "invalid_target"),
    ],
)
def test_token_failure_consumes_code_and_checks_audience(
    session: Session, field: str, value: str, error: str
) -> None:
    client = _client(session)
    client_id = _register(client)["client_id"]
    response = client.get(
        "/mcp/authorize", params=_authorize_params(client_id), follow_redirects=False
    )
    request_id = parse_qs(urlsplit(response.headers["location"]).query)["request"][0]
    user = user_with_permissions(session, "oauth-failure", [("zone.read", None)])
    pending = oauth.load_request(session, request_id)
    assert pending is not None
    code = oauth.approve(session, pending, user, [("zone.read", None)])
    form = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "code": code,
        "code_verifier": "v" * 43,
        "redirect_uri": "https://client.example.invalid/callback",
        "resource": "https://web.example.invalid/mcp",
    }
    form[field] = value
    wrong = client.post("/mcp/token", data=form)
    assert wrong.status_code == 400
    assert wrong.json()["error"] == error
    # Ein fehlgeschlagener Versuch verbraucht den Code dauerhaft.
    form["code_verifier"] = "v" * 43
    form["redirect_uri"] = "https://client.example.invalid/callback"
    form["resource"] = "https://web.example.invalid/mcp"
    assert client.post("/mcp/token", data=form).json()["error"] == "invalid_grant"


def test_registration_rejects_invalid_json_and_redirect(session: Session) -> None:
    client = _client(session)
    assert (
        client.post(
            "/mcp/register", content=b"{", headers={"content-type": "application/json"}
        ).json()["error"]
        == "invalid_client_metadata"
    )
    response = client.post(
        "/mcp/register",
        json={"client_name": "Test", "redirect_uris": ["https://x.invalid/cb#fragment"]},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_redirect_uri"


def test_revoke_is_indistinguishable_for_invalid_inputs(session: Session) -> None:
    client = _client(session)
    responses = [
        client.post("/mcp/revoke", json={"token": "beliebig"}),
        client.post("/mcp/revoke", data={"token": "unbekannt"}),
        client.post(
            "/mcp/revoke",
            content=b"a" * 16385,
            headers={"content-type": "application/x-www-form-urlencoded"},
        ),
    ]
    assert all(response.status_code == 200 and response.content == b"" for response in responses)
