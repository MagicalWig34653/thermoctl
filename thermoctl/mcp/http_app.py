"""HTTP-App für MCP-Protokoll und öffentliche OAuth-Endpunkte."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast
from urllib.parse import urlsplit

from sqlalchemy.orm import Session, sessionmaker
from starlette.applications import Starlette
from starlette.routing import Route

from thermoctl.config import Settings
from thermoctl.mcp import http_auth, server
from thermoctl.mcp.oauth_routes import build_oauth_routes


def create_mcp_asgi_app(settings: Settings, factory: sessionmaker[Session]) -> Starlette:
    """Baut den MCP-Endpunkt mit Tokenprüfung und eigenem SDK-Lifespan."""
    from mcp.server.transport_security import TransportSecuritySettings

    base_url = settings.mcp_base_url()
    if base_url is None:
        raise ValueError("MCP-HTTP benötigt eine öffentliche MCP-Adresse")
    parsed = urlsplit(base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    suffix = parsed.path
    metadata_url = origin + "/.well-known/oauth-protected-resource" + suffix
    default_port = (parsed.scheme == "https" and parsed.port in (None, 443)) or (
        parsed.scheme == "http" and parsed.port in (None, 80)
    )
    allowed_hosts = [parsed.netloc]
    if default_port and parsed.hostname is not None:
        allowed_hosts.append(parsed.hostname)
        allowed_hosts.append(f"{parsed.hostname}:{443 if parsed.scheme == 'https' else 80}")

    def token_source() -> str:
        plaintext = http_auth.current_token.get()
        if plaintext is None:
            # Nur erreichbar, wenn ein Programmierer die Auth-Middleware umgeht.
            raise PermissionError("Kein Token in der Anfrage")  # pragma: no cover
        return plaintext

    mcp_server = server._mcp_server_class()("thermoctl")
    server._register_tools(mcp_server, factory, token_source)
    sdk_app = cast(Any, mcp_server).streamable_http_app(
        streamable_http_path="/",
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=sorted(set(allowed_hosts)),
            allowed_origins=[origin],
        ),
    )
    authenticated = http_auth.BearerAuthMiddleware(
        sdk_app, factory, resource_metadata_url=metadata_url, allowed_origin=origin
    )

    @asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        async with sdk_app.router.lifespan_context(sdk_app):
            yield

    return Starlette(
        routes=[*build_oauth_routes(factory, settings), Route("/", authenticated)],
        lifespan=lifespan,
    )
