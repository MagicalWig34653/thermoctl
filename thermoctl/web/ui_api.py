"""Same-origin-JSON-Vertrag für die Svelte-Oberfläche (`/ui-api/`).

Eigener Router und nicht `/api/v1`: Jener ist ausschließlich Bearer-Token-
authentifiziert und bleibt es. Hier gilt die Browser-Sitzung (Cookie) samt
CSRF-Prüfung bei jeder Änderung; jede Antwort wird aus dem Principal des
Aufrufs berechnet. Navigation ist Darstellung -- jeder spätere Endpunkt dieses
Routers prüft Rechte erneut.

Fremde Räume erscheinen nirgends: Der Bootstrap nennt nur Zonen, die der
Principal mit `zone.read` sehen darf (`visible_zones`).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from thermoctl.auth.csrf import CSRF_COOKIE_NAME
from thermoctl.auth.dependencies import csrf_protection, current_principal, get_session
from thermoctl.auth.sessions import COOKIE_NAME, resolve_session, revoke_session
from thermoctl.domain.authz import visible_zones
from thermoctl.domain.principal import Principal
from thermoctl.web.navigation import visible_navigation
from thermoctl.web.shared.login import ERROR_MESSAGE, attempt_login, set_session_cookies
from thermoctl.web.urls import cookie_path, safe_return_to

router = APIRouter(
    prefix="/ui-api", dependencies=[Depends(csrf_protection)], include_in_schema=False
)


class NavItemOut(BaseModel):
    path: str
    label: str
    section: str


class ZoneOut(BaseModel):
    id: int
    name: str


class BootstrapOut(BaseModel):
    user_id: int
    ui_profile: str
    navigation: list[NavItemOut]
    zones: list[ZoneOut]


@router.post("/csrf-probe")
def csrf_probe() -> dict[str, bool]:
    """Wirkungsprobe der Router-weiten CSRF-Prüfung; ändert nichts."""
    return {"ok": True}


@router.get("/bootstrap")
def bootstrap(
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> BootstrapOut:
    """Wer ist angemeldet, was sieht diese Person. Ohne Sitzung 401 (JSON, keine
    Weiterleitung), damit die Oberfläche selbst zur Anmeldung führen kann."""
    return BootstrapOut(
        user_id=principal.user_id,
        ui_profile=principal.ui_profile.value,
        navigation=[
            NavItemOut(path=i.path, label=i.label, section=i.section)
            for i in visible_navigation(principal)
        ],
        zones=[
            ZoneOut(id=z.id, name=z.display_name)
            for z in visible_zones(session, principal, "zone.read")
        ],
    )


class LoginIn(BaseModel):
    username: str
    password: str
    # Wish of the login page where to go afterwards (e.g. back to /oauth/consent).
    # Never trusted: only `safe_return_to` decides, and only its answer goes back.
    return_to: str | None = None


@router.post("/login")
async def login(
    request: Request, body: LoginIn, session: Annotated[Session, Depends(get_session)]
) -> JSONResponse:
    """JSON login for the app; same checks, throttle and audit as the HTML form."""
    outcome = await attempt_login(request, session, body.username, body.password)
    if outcome is None:
        return JSONResponse({"detail": ERROR_MESSAGE}, status_code=401)
    _user, secret, lifetime_s = outcome
    content: dict[str, bool | str] = {"ok": True}
    return_to = safe_return_to(body.return_to)
    if return_to is not None:
        content["return_to"] = return_to
    response = JSONResponse(content)
    set_session_cookies(response, request, secret, lifetime_s)
    return response


@router.post("/logout")
def logout(
    request: Request, session: Annotated[Session, Depends(get_session)]
) -> JSONResponse:
    cookie_value = request.cookies.get(COOKIE_NAME)
    if cookie_value is not None:
        http_session = resolve_session(session, cookie_value)
        if http_session is not None:
            revoke_session(session, http_session)
    response = JSONResponse({"ok": True})
    response.delete_cookie(COOKIE_NAME, path=cookie_path(request))
    response.delete_cookie(CSRF_COOKIE_NAME, path=cookie_path(request))
    return response
