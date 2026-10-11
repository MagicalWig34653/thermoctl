"""Zustimmung zu einem selbst angemeldeten OAuth-Client."""

from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Annotated, Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.auth.dependencies import csrf_protection, current_principal, get_session
from thermoctl.db.models.identity import User
from thermoctl.db.models.lookup import Permission
from thermoctl.db.models.oauth import OAuthClient
from thermoctl.domain.authz import PERMISSION_AREAS, Forbidden, has_permission, require
from thermoctl.domain.oauth import OAuthError, approve, deny, load_request
from thermoctl.domain.principal import Principal
from thermoctl.domain.token_presets import PRESETS
from thermoctl.domain.ui_profile import WebUiProfile
from thermoctl.web import templates
from thermoctl.web.urls import prefixed


def _unframed(handler: Callable[..., Awaitable[Response]]) -> Callable[..., Awaitable[Response]]:
    """Die Zustimmungsseite darf in keinem fremden Rahmen erscheinen.

    Sonst könnte eine Seite sie unsichtbar einbetten und den "Zulassen"-Knopf
    unterschieben (Clickjacking) -- und damit einem Client ohne Wissen des Nutzers
    Zugriff auf die Heizung geben. Gilt für alle Antworten dieser Routen, auch Fehler und
    Weiterleitungen. Ein Dekorator statt einer Abhängigkeit: FastAPI übernimmt Header
    einer Abhängigkeit nicht, wenn die Route eine `Response` selbst zurückgibt.
    """

    @wraps(handler)
    async def wrapped(*args: Any, **kwargs: Any) -> Response:
        response = await handler(*args, **kwargs)
        response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Cache-Control"] = "no-store"
        return response

    return wrapped


router = APIRouter(dependencies=[Depends(csrf_protection)], include_in_schema=False)


def _principal(request: Request, session: Session) -> Principal | None:
    try:
        return current_principal(request, session)
    except HTTPException as exc:
        if exc.status_code != status.HTTP_401_UNAUTHORIZED:
            raise
        return None


def _error() -> Response:
    return HTMLResponse("<h1>Diese Anfrage ist nicht verfügbar.</h1>", status_code=404)


def _redirect(uri: str, **params: str | None) -> str:
    parts = urlsplit(uri)
    query = parse_qsl(parts.query, keep_blank_values=True)
    query.extend((key, value) for key, value in params.items() if value is not None)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _choices(
    session: Session,
    principal: Principal,
) -> list[tuple[str, str, list[tuple[str, str, str]]]]:
    descriptions = {row.code: row.description for row in session.scalars(select(Permission))}
    areas = []
    for title, detail, codes in PERMISSION_AREAS:
        options = []
        for code in codes:
            for granted_code, zone_id in sorted(
                principal.grants, key=lambda item: (item[0], item[1] or 0)
            ):
                if granted_code == code:
                    value = f"{code}:{zone_id}" if zone_id is not None else code
                    options.append((value, code, descriptions.get(code, code)))
        if options:
            areas.append((title, detail, options))
    return areas


def _page(
    request: Request,
    session: Session,
    principal: Principal,
    request_id: str,
    preset: str,
    selected: set[str] | None = None,
    error: str | None = None,
) -> Response:
    pending = load_request(session, request_id)
    if pending is None:
        return _error()
    client = session.get(OAuthClient, pending.client_id)
    if client is None:
        return _error()
    areas = _choices(session, principal)
    available = {value for _, _, entries in areas for value, _, _ in entries}
    requested = PRESETS.get(preset, PRESETS["read_only"])
    checked = selected if selected is not None else available & requested
    omitted = any(code not in available for code in requested)
    return templates.TemplateResponse(
        request,
        "oauth_consent.html",
        {
            "request_id": request_id,
            "client_name": client.name,
            "redirect_host": urlsplit(pending.redirect_uri).hostname or pending.redirect_uri,
            "areas": areas,
            "selected": checked,
            "preset": preset,
            "omitted": omitted,
            "error": error,
            "is_tenant": principal.ui_profile is WebUiProfile.TENANT,
        },
    )


@router.get("/oauth/consent")
@_unframed
async def consent_form(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    request_id: str = "",
    preset: str = "read_only",
) -> Response:
    # FastAPI liest den externen Namen `request`; die Signatur bleibt dadurch eindeutig.
    request_id = request.query_params.get("request", request_id)
    principal = _principal(request, session)
    if principal is None:
        target = f"/oauth/consent?{urlencode({'request': request_id})}"
        return RedirectResponse(
            prefixed(request, f"/login?{urlencode({'return_to': target})}"), status_code=303
        )
    require(principal, "token.self")
    return _page(request, session, principal, request_id, preset)


@router.post("/oauth/consent")
@_unframed
async def consent_submit(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    request_id: Annotated[str, Form(alias="request")],
    action: Annotated[str, Form()],
    permission: Annotated[list[str] | None, Form()] = None,
) -> Response:
    principal = _principal(request, session)
    if principal is None:
        return RedirectResponse(prefixed(request, "/login"), status_code=303)
    require(principal, "token.self")
    pending = load_request(session, request_id)
    if pending is None:
        return _error()
    if action == "deny":
        deny(session, pending)
        return RedirectResponse(
            _redirect(pending.redirect_uri, error="access_denied", state=pending.state),
            status_code=303,
        )
    if action != "allow":
        return _page(
            request, session, principal, request_id, "read_only", error="Ungültige Aktion."
        )
    selected = set(permission or [])
    if not selected:
        return _page(
            request,
            session,
            principal,
            request_id,
            "read_only",
            selected=selected,
            error="Mindestens ein Recht auswählen.",
        )
    available = {value for _, _, entries in _choices(session, principal) for value, _, _ in entries}
    if not selected <= available:
        return HTMLResponse("<h1>Recht nicht erlaubt.</h1>", status_code=403)
    grants = []
    for value in sorted(selected):
        code, separator, zone = value.partition(":")
        zone_id = int(zone) if separator else None
        if not has_permission(principal, code, zone_id):
            return HTMLResponse("<h1>Recht nicht erlaubt.</h1>", status_code=403)
        grants.append((code, zone_id))
    user = session.get(User, principal.user_id)
    if user is None:
        return _error()
    failure: OAuthError | Forbidden | None = None
    code_plain = None
    try:
        code_plain = approve(session, pending, user, grants)
    except (OAuthError, Forbidden) as exc:
        # get_session committet nur, wenn keine Exception den Dependency-Block verlässt.
        failure = exc
    if failure is not None or code_plain is None:
        return HTMLResponse(
            "<h1>Diese Anfrage konnte nicht bestätigt werden.</h1>", status_code=400
        )
    return RedirectResponse(
        _redirect(pending.redirect_uri, code=code_plain, state=pending.state), status_code=303
    )
