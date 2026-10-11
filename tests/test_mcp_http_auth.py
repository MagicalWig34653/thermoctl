"""Tokenprüfung am HTTP-Eingang des MCP-Servers (Grundsatz 7)."""

import asyncio
import logging

import pytest
from sqlalchemy.orm import Session
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from tests.helpers import user_with_permissions
from thermoctl.auth.tokens import issue_token, revoke_token
from thermoctl.mcp import http_auth

METADATA = "https://mcp.example.invalid/.well-known/oauth-protected-resource/mcp"
ORIGIN = "https://mcp.example.invalid"


class _SharedSession:
    """Reicht die Testsitzung durch `session_scope`, ohne sie zu schließen."""

    def __init__(self, session: Session) -> None:
        self._s = session

    def __call__(self) -> _SharedSession:
        return self

    def __getattr__(self, name: str):
        if name == "close":
            return lambda: None
        return getattr(self._s, name)


async def _echo(request):
    return JSONResponse(
        {
            "token": http_auth.current_token.get(),
            "prefix": http_auth.current_key_prefix.get(),
        }
    )


def _client(session: Session) -> TestClient:
    inner = Starlette(routes=[Route("/mcp", _echo, methods=["GET", "POST"])])
    app = http_auth.BearerAuthMiddleware(
        inner, _SharedSession(session), resource_metadata_url=METADATA, allowed_origin=ORIGIN
    )
    return TestClient(app)


def _token(session: Session, name: str = "mcp-http", source: str = "bearer"):
    owner = user_with_permissions(session, name, [("zone.read", None)])
    token, plaintext = issue_token(session, owner, name, [("zone.read", None)], None)
    token.source = source
    session.flush()
    return token, plaintext


def _auth(plaintext: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {plaintext}"}


def test_valid_bearer_token_reaches_the_app_with_context(session: Session) -> None:
    token, plaintext = _token(session)
    response = _client(session).post("/mcp", headers=_auth(plaintext))
    assert response.status_code == 200
    assert response.json() == {"token": plaintext, "prefix": token.prefix}
    assert http_auth.current_token.get() is None  # nach der Anfrage zurückgesetzt


def test_valid_oauth_token_is_accepted_here(session: Session) -> None:
    _token_row, plaintext = _token(session, "oauth-ok", "oauth")
    assert _client(session).post("/mcp", headers=_auth(plaintext)).status_code == 200


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Basic abc"},
        {"Authorization": "Bearer"},
        {"Authorization": "Bearer tctl_x_y"},
        {"Authorization": "Bearer kein-token"},
    ],
)
def test_missing_or_invalid_credentials_get_401_with_metadata(
    session: Session, headers: dict[str, str]
) -> None:
    response = _client(session).post("/mcp", headers=headers)
    assert response.status_code == 401
    challenge = response.headers["www-authenticate"]
    assert challenge.startswith("Bearer ") and f'resource_metadata="{METADATA}"' in challenge


def test_challenge_never_contains_the_submitted_token(session: Session) -> None:
    response = _client(session).post("/mcp", headers=_auth("tctl_geheim_abcdef"))
    assert "geheim" not in response.headers["www-authenticate"]
    assert "geheim" not in response.text


def test_revoked_and_expired_tokens_are_refused(session: Session) -> None:
    token, plaintext = _token(session, "widerrufen")
    revoke_token(session, token)
    assert _client(session).post("/mcp", headers=_auth(plaintext)).status_code == 401


def test_kiosk_tokens_are_refused(session: Session) -> None:
    token, plaintext = _token(session, "kiosk")
    token.is_kiosk = True
    session.flush()
    assert _client(session).post("/mcp", headers=_auth(plaintext)).status_code == 401


def test_foreign_origin_is_refused_even_with_a_valid_token(session: Session) -> None:
    _row, plaintext = _token(session, "origin")
    headers = _auth(plaintext) | {"Origin": "https://boese.example.invalid"}
    assert _client(session).post("/mcp", headers=headers).status_code == 403


def test_own_origin_and_missing_origin_pass(session: Session) -> None:
    _row, plaintext = _token(session, "origin-ok")
    client = _client(session)
    assert client.post("/mcp", headers=_auth(plaintext) | {"Origin": ORIGIN}).status_code == 200
    assert client.post("/mcp", headers=_auth(plaintext)).status_code == 200


def test_concurrent_requests_do_not_see_each_others_token(session: Session) -> None:
    # Die Datenbankprüfung ist hier ersetzt: Die Testsitzung ist eine einzige und darf nicht
    # von mehreren Threads gleichzeitig benutzt werden. Geprüft wird die Isolation der
    # ContextVars, nicht die Tokenprüfung (die haben die Tests oben).
    plain_a, plain_b = "tctl_aaaaaaaa_eins", "tctl_bbbbbbbb_zwei"
    seen: dict[str, str | None] = {}

    async def slow(scope, receive, send):
        await asyncio.sleep(0.05)
        seen[scope["headers"][0][1].decode()] = http_auth.current_token.get()
        own = (http_auth.current_token.get() or "").split("_")[1]
        assert http_auth.current_key_prefix.get() == own
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    app = http_auth.BearerAuthMiddleware(
        slow, _SharedSession(session), resource_metadata_url=METADATA, allowed_origin=ORIGIN
    )
    app._authenticate = lambda plaintext: plaintext.split("_")[1]  # type: ignore[method-assign]

    async def call(plain: str) -> None:
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/mcp",
            "headers": [(b"authorization", f"Bearer {plain}".encode())],
        }

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(_message):
            return None

        await app(scope, receive, send)

    async def both() -> None:
        await asyncio.gather(call(plain_a), call(plain_b))

    asyncio.run(both())
    assert seen == {f"Bearer {plain_a}": plain_a, f"Bearer {plain_b}": plain_b}


def test_non_http_scopes_are_passed_through_and_websockets_refused(session: Session) -> None:
    seen: list[str] = []

    async def inner(scope, receive, send):
        seen.append(scope["type"])

    app = http_auth.BearerAuthMiddleware(
        inner, _SharedSession(session), resource_metadata_url=METADATA, allowed_origin=ORIGIN
    )
    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    async def receive():
        return {}

    asyncio.run(app({"type": "lifespan"}, receive, send))
    assert seen == ["lifespan"]
    asyncio.run(app({"type": "websocket", "headers": []}, receive, send))
    assert seen == ["lifespan"] and sent[-1]["type"] == "websocket.close"


def test_denials_are_logged_without_the_token(session: Session, caplog) -> None:
    caplog.set_level(logging.INFO)
    _client(session).post("/mcp", headers=_auth("tctl_abcd1234_geheimgeheim"))
    records = [r for r in caplog.records if getattr(r, "event", "") == "mcp.http.auth"]
    assert records and records[-1].outcome == "denied"
    assert "geheimgeheim" not in " ".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
