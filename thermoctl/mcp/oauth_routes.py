"""Öffentliche OAuth-Endpunkte und Metadaten für den MCP-HTTP-Zugang."""

import html
import json
import logging
import time
from collections.abc import Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from thermoctl.config import Settings
from thermoctl.db.engine import session_scope
from thermoctl.domain import oauth

_MAX_BODY = 16 * 1024
_RATE_LIMIT = 20
_RATE_WINDOW = 3600.0
_MAX_ADDRESSES = 10_000
log = logging.getLogger("thermoctl.oauth")


def _now() -> float:
    """Zeitquelle der Registrierungsbegrenzung; in Tests austauschbar."""
    return time.monotonic()


def _issuer(settings: Settings) -> str:
    issuer = settings.mcp_base_url()
    if issuer is None:
        raise ValueError("MCP-OAuth-Routen benötigen eine öffentliche MCP-Adresse")
    return issuer


def _metadata(settings: Settings) -> tuple[dict[str, object], dict[str, object]]:
    issuer = _issuer(settings)
    return (
        {
            "resource": issuer,
            "authorization_servers": [issuer],
            "bearer_methods_supported": ["header"],
        },
        {
            "issuer": issuer,
            "authorization_endpoint": issuer + "/authorize",
            "token_endpoint": issuer + "/token",
            "registration_endpoint": issuer + "/register",
            "revocation_endpoint": issuer + "/revoke",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
        },
    )


def _metadata_routes(settings: Settings, suffix: str = "") -> list[Route]:
    protected, server = _metadata(settings)

    async def protected_resource(_request: Request) -> Response:
        return JSONResponse(protected)

    async def authorization_server(_request: Request) -> Response:
        return JSONResponse(server)

    return [
        Route(
            "/.well-known/oauth-protected-resource" + suffix, protected_resource, methods=["GET"]
        ),
        Route(
            "/.well-known/oauth-authorization-server" + suffix,
            authorization_server,
            methods=["GET"],
        ),
    ]


def build_root_wellknown_routes(settings: Settings) -> list[Route]:
    """RFC-Pfade an der Web-Wurzel im Modus ``path``."""
    if settings.mcp_mode != "path":
        return []
    return _metadata_routes(settings, settings.mcp_path)


def _json_error(error: str, description: str, status: int = 400) -> JSONResponse:
    return JSONResponse(
        {"error": error, "error_description": description},
        status_code=status,
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


def _html_error(error: str, description: str) -> HTMLResponse:
    return HTMLResponse(
        '<!doctype html><html lang="de"><head><meta charset="utf-8"></head>'
        f"<body><h1>{html.escape(error)}</h1><p>{html.escape(description)}</p></body></html>",
        status_code=400,
    )


def _redirect_error(uri: str, error: oauth.OAuthError, state: str | None) -> RedirectResponse:
    parts = urlsplit(uri)
    query = parse_qsl(parts.query, keep_blank_values=True)
    query.extend([("error", error.error), ("error_description", error.description)])
    if state is not None:
        query.append(("state", state))
    return RedirectResponse(urlunsplit(parts._replace(query=urlencode(query))), status_code=303)


def _run_oauth(
    factory: sessionmaker[Session], operation: Callable[[Session], object]
) -> tuple[object | None, oauth.OAuthError | None]:
    """OAuthError im Sitzungsblock auffangen, damit session_scope Änderungen committet."""
    result: object | None = None
    error: oauth.OAuthError | None = None
    with session_scope(factory) as session:
        try:
            result = operation(session)
        except oauth.OAuthError as exc:
            error = exc
    return result, error


async def _limited_body(request: Request) -> bytes | None:
    length = request.headers.get("content-length")
    if length is not None:
        try:
            if int(length) > _MAX_BODY:
                return None
        except ValueError:
            return None
    body = await request.body()
    return body if len(body) <= _MAX_BODY else None


def build_oauth_routes(factory: sessionmaker[Session], settings: Settings) -> list[Route]:
    """OAuth-Routen relativ zum MCP-Einhängepunkt."""
    issuer = _issuer(settings)
    # Zeitpunkte nur je Adresse; alte Adressen werden entfernt und der Speicher ist begrenzt.
    registrations: dict[str, list[float]] = {}

    async def register(request: Request) -> Response:
        body = await _limited_body(request)
        if body is None:
            return _json_error("invalid_request", "Anfragekörper ist zu groß", 413)
        if (
            request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            != "application/json"
        ):
            return _json_error("invalid_request", "JSON-Körper ist erforderlich")
        try:
            data = json.loads(body)
        except UnicodeDecodeError, json.JSONDecodeError:
            return _json_error("invalid_client_metadata", "Ungültiger JSON-Körper")
        if not isinstance(data, dict):
            return _json_error("invalid_client_metadata", "Client-Metadaten müssen ein Objekt sein")
        name = data.get("client_name")
        uris = data.get("redirect_uris")
        if (
            not isinstance(name, str)
            or not isinstance(uris, list)
            or not all(isinstance(uri, str) for uri in uris)
        ):
            return _json_error(
                "invalid_client_metadata", "client_name und redirect_uris sind erforderlich"
            )
        if data.get("token_endpoint_auth_method", "none") != "none":
            return _json_error("invalid_client_metadata", "Nur öffentliche Clients sind erlaubt")
        address = request.scope.get("client")
        peer = address[0] if address else ""
        now = _now()
        for key in list(registrations):
            registrations[key] = [
                stamp for stamp in registrations[key] if now - stamp < _RATE_WINDOW
            ]
            if not registrations[key]:
                del registrations[key]
        if len(registrations.get(peer, [])) >= _RATE_LIMIT:
            return _json_error("rate_limit_exceeded", "Zu viele Registrierungen", 429)
        if peer not in registrations and len(registrations) >= _MAX_ADDRESSES:
            oldest = min(registrations, key=lambda key: registrations[key][-1])
            del registrations[oldest]

        def create(session: Session) -> dict[str, object]:
            client = oauth.register_client(session, name, uris)
            return {
                "client_id": client.client_id,
                "client_name": client.name,
                "redirect_uris": oauth.client_redirect_uris(session, client),
                "token_endpoint_auth_method": "none",
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
            }

        result, error = await run_in_threadpool(_run_oauth, factory, create)
        if error is not None:
            return _json_error(error.error, error.description)
        registrations.setdefault(peer, []).append(now)
        return JSONResponse(result, status_code=201)

    async def authorize(request: Request) -> Response:
        params = request.query_params
        client_id = params.get("client_id", "")
        redirect_uri = params.get("redirect_uri", "")
        state = params.get("state")

        def locate(session: Session) -> bool:
            client = oauth.find_client(session, client_id)
            return client is not None and redirect_uri in oauth.client_redirect_uris(
                session, client
            )

        trusted = await run_in_threadpool(_run_oauth, factory, locate)
        if not trusted[0]:
            return _html_error("invalid_request", "Client oder Redirect-URI ist unbekannt")
        if params.get("response_type") != "code":
            return _redirect_error(
                redirect_uri,
                oauth.OAuthError("unsupported_response_type", "Nur code ist erlaubt"),
                state,
            )
        if params.get("code_challenge_method") != "S256":
            return _redirect_error(
                redirect_uri, oauth.OAuthError("invalid_request", "S256 ist erforderlich"), state
            )
        if not params.get("code_challenge"):
            return _redirect_error(
                redirect_uri, oauth.OAuthError("invalid_request", "code_challenge fehlt"), state
            )
        # resource ist Pflicht: Ausgestellte Token sind an genau diesen MCP-Server gebunden.
        if not params.get("resource"):
            return _redirect_error(
                redirect_uri, oauth.OAuthError("invalid_request", "resource fehlt"), state
            )

        def start(session: Session) -> str:
            client = oauth.find_client(session, client_id)
            assert client is not None  # Bereits oben mit derselben Client-ID geprüft.
            return oauth.start_request(
                session,
                client,
                redirect_uri,
                params["code_challenge"],
                params["resource"],
                state,
                issuer,
            )

        request_id, error = await run_in_threadpool(_run_oauth, factory, start)
        if error is not None:
            return _redirect_error(redirect_uri, error, state)
        assert isinstance(request_id, str)
        assert settings.public_url is not None
        return RedirectResponse(
            settings.public_url + "/oauth/consent?" + urlencode({"request": request_id}),
            status_code=303,
        )

    async def token(request: Request) -> Response:
        body = await _limited_body(request)
        if body is None:
            return _json_error("invalid_request", "Anfragekörper ist zu groß", 413)
        if (
            request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            != "application/x-www-form-urlencoded"
        ):
            return _json_error("invalid_request", "Formulardaten sind erforderlich")
        try:
            from urllib.parse import parse_qs

            values = parse_qs(body.decode("utf-8"), keep_blank_values=True, strict_parsing=True)
        except UnicodeDecodeError, ValueError:
            return _json_error("invalid_request", "Ungültige Formulardaten")
        form = {key: value[-1] for key, value in values.items()}
        grant = form.get("grant_type")
        if grant not in ("authorization_code", "refresh_token"):
            return _json_error("unsupported_grant_type", "Grant-Typ wird nicht unterstützt")
        required = (
            ("client_id", "code", "code_verifier", "redirect_uri")
            if grant == "authorization_code"
            else ("client_id", "refresh_token")
        )
        if any(not form.get(field) for field in required):
            return _json_error("invalid_request", "Erforderlicher Parameter fehlt")

        def issue(session: Session) -> oauth.TokenPair:
            if grant == "authorization_code":
                return oauth.exchange_code(
                    session,
                    form["client_id"],
                    form["code"],
                    form["code_verifier"],
                    form["redirect_uri"],
                    form.get("resource"),
                    issuer,
                )
            return oauth.refresh(
                session, form["client_id"], form["refresh_token"], form.get("resource"), issuer
            )

        pair, error = await run_in_threadpool(_run_oauth, factory, issue)
        if error is not None:
            return _json_error(error.error, error.description)
        assert isinstance(pair, oauth.TokenPair)
        return JSONResponse(
            {
                "access_token": pair.access_token,
                "token_type": "Bearer",
                "expires_in": pair.expires_in,
                "refresh_token": pair.refresh_token,
            },
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        )

    async def revoke(request: Request) -> Response:
        # RFC 7009: Auch unbekannte oder fehlerhafte Eingaben antworten identisch.
        try:
            body = await _limited_body(request)
            if (
                body is not None
                and request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                == "application/x-www-form-urlencoded"
            ):
                from urllib.parse import parse_qs

                token_plain = parse_qs(body.decode("utf-8"), keep_blank_values=True).get(
                    "token", [""]
                )[-1]
                if token_plain:
                    await run_in_threadpool(
                        _run_oauth, factory, lambda session: oauth.revoke(session, token_plain)
                    )
        except Exception:
            # RFC 7009 verlangt dieselbe Antwort auch bei fehlgeschlagenem Widerruf.
            # session_scope rollt Datenbankfehler vor dieser Antwort zurück.
            log.warning(
                "oauth.revoke",
                extra={
                    "event": "oauth.revoke",
                    "outcome": "error",
                    "reason": "Widerruf fehlgeschlagen",
                },
            )
        return Response(status_code=200, headers={"Cache-Control": "no-store"})

    return [
        *_metadata_routes(settings),
        Route("/register", register, methods=["POST"]),
        Route("/authorize", authorize, methods=["GET"]),
        Route("/token", token, methods=["POST"]),
        Route("/revoke", revoke, methods=["POST"]),
    ]
