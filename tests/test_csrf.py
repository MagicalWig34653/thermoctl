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
from thermoctl.auth.dependencies import csrf_protection
from thermoctl.auth.kiosk import kiosk_csrf_protection
from thermoctl.auth.sessions import COOKIE_NAME, create_session

WRITING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# Justified exceptions. The REST API only ever reads the `Authorization`
# header and never a cookie -- without cookie authentication there is no
# CSRF path. `test_the_api_does_not_accept_a_session_cookie` verifies exactly
# that, so the exception does not silently become invalid.
EXEMPT_PREFIXES = ("/api/",)

# Two flavors of the same HMAC scheme (thermoctl/auth/csrf.py): `csrf_schutz` binds
# to the session cookie, `kiosk_csrf_protection` to the kiosk cookie. A kiosk token has
# no session to bind to, so it carries its own dependency instead of reusing one
# bound to a cookie it never has -- either counts as protection for this guard.
CSRF_PROTECTIONS = (csrf_protection, kiosk_csrf_protection)


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
        and not any(d.dependency in CSRF_PROTECTIONS for d in route.dependencies)
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


@pytest.mark.parametrize("path", ["/zones"])
def test_a_state_changing_route_without_a_token_is_rejected(
    client: TestClient, angemeldeter_client: TestClient, path: str
) -> None:
    """Deliberately not `/logout` any more -- see the recovery tests below.

    This test used to point at `/logout`, and that was exactly the behaviour that
    stranded a user: with a stale page, every form was refused *and so was the way
    out of it*. The rejection itself still has to hold, so it now uses an ordinary
    state-changing route.
    """
    response = angemeldeter_client.post(path)
    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.parametrize("path", ["/logout", "/login"])
def test_a_stale_page_can_still_log_out_and_back_in(
    angemeldeter_client: TestClient, path: str
) -> None:
    """The two ways out of a stale page must not be blocked by a stale token.

    Reported from use: a tab left open could no longer submit anything -- and could
    neither log out nor log in again, because the old session cookie was still
    around and turned both into a CSRF failure. The only escape was deleting exactly
    the right cookie by hand, which is not something to ask of anyone.

    The request stays unexecuted; what changes is that the session and CSRF cookies
    are cleared and the browser lands on the login form.
    """
    response = angemeldeter_client.post(path, follow_redirects=False)

    assert response.status_code == status.HTTP_303_SEE_OTHER
    assert response.headers["location"] == "/login?stale=1"
    gesetzt = response.headers.get_list("set-cookie")
    assert any(COOKIE_NAME in eintrag for eintrag in gesetzt)
    assert any(CSRF_COOKIE_NAME in eintrag for eintrag in gesetzt)


def test_a_browser_gets_a_readable_page_instead_of_json(
    angemeldeter_client: TestClient,
) -> None:
    """`{"detail": "Ungueltiges CSRF-Token"}` tells nobody what to do next."""
    response = angemeldeter_client.post("/zones", headers={"Accept": "text/html"})

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert "text/html" in response.headers["content-type"]
    assert "veraltet" in response.text
    assert "neu laden" in response.text


def test_an_htmx_control_is_marked_so_the_page_can_say_something(
    angemeldeter_client: TestClient,
) -> None:
    """Measured in the browser: without this, the control simply did nothing.

    htmx ignores the body of an error answer, so a message put there is seen by
    nobody. The marker header is what the small handler in `base.html` turns into a
    visible notice. Deliberately not `HX-Refresh`: that was tried, and a control
    that fires again on restore turns it into a loop of reload and refusal.
    """
    response = angemeldeter_client.post(
        "/zones", headers={"Accept": "text/html", "HX-Request": "true"}
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.headers["HX-Stale-Page"] == "1"
    assert "HX-Refresh" not in response.headers
    assert response.json() == {"detail": "Ungueltiges CSRF-Token"}


def test_the_base_template_turns_that_marker_into_something_visible() -> None:
    """The header alone changes nothing -- somebody has to render it."""
    from pathlib import Path as _Path

    base = (
        _Path(__file__).parent.parent
        / "thermoctl" / "web" / "templates" / "base.html"
    ).read_text(encoding="utf-8")

    assert "htmx:responseError" in base
    assert "HX-Stale-Page" in base
    assert "location.reload" in base


@pytest.mark.parametrize("path", ["/logout", "/login"])
def test_htmx_is_sent_out_of_a_stale_page_by_its_own_header(
    angemeldeter_client: TestClient, path: str
) -> None:
    """A redirect alone would be followed invisibly and swapped into the old page."""
    response = angemeldeter_client.post(
        path, headers={"HX-Request": "true"}, follow_redirects=False
    )

    assert response.headers["HX-Redirect"] == "/login?stale=1"
    gesetzt = response.headers.get_list("set-cookie")
    assert any(COOKIE_NAME in eintrag for eintrag in gesetzt)


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
