"""CSRF protection must automatically apply to every future route.

The reason is in the closing review of subproject 1: the check was hand-added
to the one state-changing route that existed at the time. Every further route
would have needed the same repeated by hand, and eventually someone would
forget. Since then `csrf_schutz` hangs off the router itself -- and this test
turns red as soon as an unprotected, state-changing route is added.
"""

import pytest
from fastapi import status
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from tests.helpers import alle_api_routen
from thermoctl.app import create_app
from thermoctl.auth.csrf import CSRF_COOKIE_NAME, csrf_token
from thermoctl.auth.dependencies import csrf_schutz
from thermoctl.auth.sessions import COOKIE_NAME, create_session

WRITING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# Justified exceptions. The REST API only ever reads the `Authorization`
# header and never a cookie -- without cookie authentication there is no
# CSRF path. `test_the_api_does_not_accept_a_session_cookie` verifies exactly
# that, so the exception does not silently become invalid.
EXEMPT_PREFIXES = ("/api/",)


def _writing_routes() -> list[APIRoute]:
    return [
        route
        for route in alle_api_routen(create_app())
        if route.methods & WRITING_METHODS
    ]


def test_every_state_changing_route_depends_on_csrf_protection() -> None:
    unprotected = [
        f"{sorted(route.methods & WRITING_METHODS)} {route.path}"
        for route in _writing_routes()
        if not route.path.startswith(EXEMPT_PREFIXES)
        and not any(d.dependency is csrf_schutz for d in route.dependencies)
    ]
    assert not unprotected, (
        "These state-changing routes have no CSRF protection: " + ", ".join(unprotected)
    )


def test_the_api_does_not_accept_a_session_cookie(client: TestClient, session, user) -> None:
    """Underpins the exception above: the API cannot be authenticated via cookie."""
    _entry, secret = create_session(session, user, 3600)
    client.cookies.set(COOKIE_NAME, secret)
    response = client.get("/api/v1/me")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.parametrize("path", ["/logout"])
def test_a_state_changing_route_without_a_token_is_rejected(
    client: TestClient, angemeldeter_client: TestClient, path: str
) -> None:
    response = angemeldeter_client.post(path)
    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_a_state_changing_route_with_a_valid_token_goes_through(
    angemeldeter_client: TestClient,
) -> None:
    secret = angemeldeter_client.cookies[COOKIE_NAME]
    from thermoctl.config import get_settings

    headers = {"X-CSRF-Token": csrf_token(secret, get_settings().secret_key.get_secret_value())}
    response = angemeldeter_client.post("/logout", headers=headers, follow_redirects=False)
    assert response.status_code == status.HTTP_303_SEE_OTHER


def test_without_a_session_cookie_the_protection_does_not_apply(client: TestClient) -> None:
    """Login itself carries no cookie and must not fail due to CSRF protection."""
    response = client.post("/login", data={"username": "gibtesnicht", "password": "x"})
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_csrf_cookie_is_set_at_login(client: TestClient, user) -> None:
    response = client.post(
        "/login",
        data={"username": "lino", "password": "passwort-lang-genug"},
        follow_redirects=False,
    )
    assert response.status_code == status.HTTP_303_SEE_OTHER
    assert CSRF_COOKIE_NAME in client.cookies


def test_base_template_carries_the_csrf_cookie_via_htmx() -> None:
    """Without the browser bridge, real form submissions end in 403 despite the cookie."""
    from pathlib import Path

    base_template = (
        Path(__file__).parent.parent / "thermoctl" / "web" / "templates" / "base.html"
    ).read_text(encoding="utf-8")
    assert 'hx-boost="true"' in base_template
    assert 'headers["X-CSRF-Token"]' in base_template
    assert "thermoctl_csrf=" in base_template
