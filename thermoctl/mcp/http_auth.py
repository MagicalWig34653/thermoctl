"""Tokenprüfung am HTTP-Eingang des MCP-Servers.

Eine reine ASGI-Schicht (kein `BaseHTTPMiddleware`: Das puffert Antworten und bricht
Streaming des MCP-Transports). Sie prüft `Authorization: Bearer` und den `Origin`, legt das
Klartext-Token und dessen Präfix in `ContextVar`s ab und ruft dann die App. Die Werkzeuge in
`server.py` lesen das Token von dort, statt eines beim Start festgelegten.

Das Token wird hier nur auf Gültigkeit geprüft (vorhanden, nicht widerrufen/abgelaufen, kein
Kiosk-Token). **Welche Rechte es trägt, entscheidet jedes Werkzeug selbst** über
`principal_for_token` und `require` -- ein Token ohne Rechte kommt bis hierher und scheitert
dort mit einer Begründung, die sich lesen lässt.

Ausgestellt-für-wen gilt hier ebenfalls: Bearer-Token (`source="bearer"`) und OAuth-Token
(`source="oauth"`) sind beide zulässig. Nur an dieser Stelle dürfen OAuth-Token gelten; REST
weist sie ab (`thermoctl/api/routes.py`).
"""

import logging
from contextvars import ContextVar
from typing import Any

from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from thermoctl.auth.tokens import resolve_token
from thermoctl.db.engine import session_scope

log = logging.getLogger("thermoctl.mcp.http")

current_token: ContextVar[str | None] = ContextVar("mcp_current_token", default=None)
current_key_prefix: ContextVar[str | None] = ContextVar("mcp_current_key_prefix", default=None)


def _log(outcome: str, **extra: object) -> None:
    # Schlüssel `key_prefix`, nicht `token_prefix`: Die Maskierung in `thermoctl/logging.py`
    # blendet jeden Schlüssel mit dem Segment "token" aus.
    log.info("mcp.http.auth", extra={"event": "mcp.http.auth", "outcome": outcome, **extra})


class BearerAuthMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        factory: sessionmaker[Session] | Any,
        *,
        resource_metadata_url: str,
        allowed_origin: str,
    ) -> None:
        self.app = app
        self.factory = factory
        self.resource_metadata_url = resource_metadata_url
        self.allowed_origin = allowed_origin

    def _authenticate(self, plaintext: str) -> str | None:
        """Liefert das Präfix des Tokens, wenn es hier gelten darf, sonst `None`."""
        with session_scope(self.factory) as session:
            token = resolve_token(session, plaintext)
            if token is None or token.is_kiosk:
                return None
            return token.prefix

    def _challenge(self, *, invalid: bool) -> str:
        error = 'error="invalid_token", ' if invalid else ""
        return f'Bearer {error}resource_metadata="{self.resource_metadata_url}"'

    async def _reject(self, send: Send, status: int, error: str, challenge: str | None) -> None:
        headers = [(b"content-type", b"application/json"), (b"cache-control", b"no-store")]
        if challenge is not None:
            headers.append((b"www-authenticate", challenge.encode("latin-1")))
        body = b'{"error":"' + error.encode("ascii") + b'"}'
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "websocket":
            message: Message = {"type": "websocket.close", "code": 1008}
            await send(message)
            return
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        origin = headers.get("origin")
        if origin is not None and origin != self.allowed_origin:
            _log("denied", reason="Origin nicht erlaubt", origin=origin[:200])
            await self._reject(send, 403, "forbidden_origin", None)
            return

        scheme, _, credentials = headers.get("authorization", "").partition(" ")
        credentials = credentials.strip()
        if scheme.lower() != "bearer" or not credentials:
            _log("denied", reason="Kein Bearer-Token im Authorization-Header")
            await self._reject(send, 401, "invalid_request", self._challenge(invalid=False))
            return

        prefix = await run_in_threadpool(self._authenticate, credentials)
        if prefix is None:
            _log("denied", reason="Token ungültig, widerrufen, abgelaufen oder nur für den Kiosk")
            await self._reject(send, 401, "invalid_token", self._challenge(invalid=True))
            return

        token_reset = current_token.set(credentials)
        prefix_reset = current_key_prefix.set(prefix)
        try:
            _log("ok", key_prefix=prefix)
            await self.app(scope, receive, send)
        finally:
            current_key_prefix.reset(prefix_reset)
            current_token.reset(token_reset)
