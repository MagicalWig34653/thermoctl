from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from thermoctl.auth.csrf import CSRF_COOKIE_NAME
from thermoctl.auth.dependencies import csrf_protection, get_session
from thermoctl.auth.sessions import (
    COOKIE_NAME,
    resolve_session,
    revoke_session,
)
from thermoctl.config import get_settings
from thermoctl.setup import setup_needed
from thermoctl.web import templates
from thermoctl.web.shared.login import ERROR_MESSAGE, attempt_login, set_session_cookies
from thermoctl.web.urls import cookie_path, prefixed, safe_return_to

# `include_in_schema=False`: the OpenAPI description is the contract of the REST
# interface. These routes deliver HTML for humans, and in the interface under
# /docs there would otherwise be a form route next to every real endpoint whose
# 'Try it out' triggers a real change.
router = APIRouter(dependencies=[Depends(csrf_protection)], include_in_schema=False)


@router.get("/login")
async def login_form(
    request: Request, session: Annotated[Session, Depends(get_session)]
) -> Response:
    # See start_views.start(): without a single user, the form leads nowhere. GET
    # only -- a POST to /login without a user fails at the ordinary check anyway,
    # and that check should keep its identical error message, instead of letting the
    # redirect target reveal what the service knows.
    if setup_needed(session):
        return RedirectResponse(prefixed(request, "/setup"), status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "passkeys_available": get_settings().passkeys_available(),
            "return_to": safe_return_to(request.query_params.get("return_to")),
        },
    )


@router.post("/login")
async def login(
    request: Request,
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
    session: Annotated[Session, Depends(get_session)],
    return_to: Annotated[str | None, Form()] = None,
) -> Response:
    settings = get_settings()
    outcome = await attempt_login(request, session, username, password)
    if outcome is None:
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "errors": ERROR_MESSAGE,
                "passkeys_available": settings.passkeys_available(),
                "return_to": safe_return_to(return_to),
            },
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    _user, secret, lifetime_s = outcome

    response = RedirectResponse(
        prefixed(request, safe_return_to(return_to) or "/"),
        status_code=status.HTTP_303_SEE_OTHER,
    )
    set_session_cookies(response, request, secret, lifetime_s)
    return response


@router.post("/logout")
async def logout(request: Request, session: Annotated[Session, Depends(get_session)]) -> Response:
    # The CSRF proof is attached to the router (`csrf_schutz`) and has already been
    # provided by this point.
    cookie_value = request.cookies.get(COOKIE_NAME)
    if cookie_value is not None:
        http_session = resolve_session(session, cookie_value)
        if http_session is not None:
            revoke_session(session, http_session)

    # A boosted form follows ordinary redirects in the background and only swaps
    # the response body.  The login form would then appear while the address still
    # named the authenticated page.  Use htmx's navigation response for that path;
    # a non-htmx client receives the ordinary direct redirect to the same target.
    if request.headers.get("hx-request") is not None:
        response: Response = Response(status_code=status.HTTP_204_NO_CONTENT)
        response.headers["HX-Redirect"] = prefixed(request, "/login")
    else:
        response = RedirectResponse(
            prefixed(request, "/login"), status_code=status.HTTP_303_SEE_OTHER
        )
    response.delete_cookie(COOKIE_NAME, path=cookie_path(request))
    response.delete_cookie(CSRF_COOKIE_NAME, path=cookie_path(request))
    return response
