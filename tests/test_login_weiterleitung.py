"""Nicht angemeldete Browser werden zum Login geleitet und danach zurückgeführt."""

import secrets
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.helpers import user_with_permissions
from thermoctl.auth.passwords import hash_password
from thermoctl.web.urls import safe_return_to

HTML = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}


def test_page_redirects_browser_to_login_with_way_back(client: TestClient) -> None:
    response = client.get("/zones?filter=a%20b", headers=HTML, follow_redirects=False)
    assert response.status_code == 303
    wanted = quote("/zones?filter=a%20b", safe="")
    assert response.headers["location"] == f"/login?return_to={wanted}"


def test_other_clients_keep_plain_401(client: TestClient) -> None:
    # No `Accept: text/html`: a script or a test client reads the status, not a page.
    response = client.get("/zones", follow_redirects=False)
    assert response.status_code == 401
    assert response.json() == {"detail": "Nicht angemeldet"}


def test_api_and_mcp_paths_never_redirect(client: TestClient) -> None:
    response = client.get("/api/v1/zones", headers=HTML, follow_redirects=False)
    assert response.status_code == 401
    assert "location" not in response.headers


def test_post_without_session_goes_to_login_without_way_back(client: TestClient) -> None:
    response = client.post("/zones/1/devices/detach", headers=HTML, follow_redirects=False)
    assert response.status_code in (303, 404, 405)
    if response.status_code == 303:
        assert response.headers["location"] == "/login"


def test_htmx_gets_header_and_the_page_it_is_on(client: TestClient) -> None:
    response = client.get(
        "/zones/history",
        headers={
            "HX-Request": "true",
            "HX-Current-URL": "http://testserver/zones?x=1",
        },
        follow_redirects=False,
    )
    assert response.status_code == 401
    assert response.headers["HX-Redirect"] == f"/login?return_to={quote('/zones?x=1', safe='')}"


def test_htmx_boosted_uses_requested_page(client: TestClient) -> None:
    response = client.get(
        "/zones",
        headers={
            "HX-Request": "true",
            "HX-Boosted": "true",
            "HX-Current-URL": "http://testserver/other",
        },
        follow_redirects=False,
    )
    assert response.headers["HX-Redirect"] == f"/login?return_to={quote('/zones', safe='')}"


def test_prefix_is_added_once_and_stripped_from_current_url(client_with_prefix: TestClient) -> None:
    prefix = "/api/hassio_ingress/A1b2C3d4e5"
    plain = client_with_prefix.get("/zones", headers=HTML, follow_redirects=False)
    assert plain.headers["location"] == f"{prefix}/login?return_to={quote('/zones', safe='')}"
    htmx = client_with_prefix.get(
        "/zones/history",
        headers={"HX-Request": "true", "HX-Current-URL": f"http://h{prefix}/zones"},
        follow_redirects=False,
    )
    assert htmx.headers["HX-Redirect"] == (f"{prefix}/login?return_to={quote('/zones', safe='')}")


@pytest.mark.parametrize(
    "value",
    [
        "https://evil.example/x",
        "//evil.example/x",
        "/\\evil.example",
        "evil",
        "/zones#frag",
        "/zones\r\nSet-Cookie: a=b",
        "/../etc",
        "/login",
        "/logout",
        "/setup",
        "/api/v1/zones",
        "/mcp",
        "/static/x.js",
        "/" + "a" * 3000,
        "",
        None,
    ],
)
def test_unsafe_return_targets_are_dropped(value: str | None) -> None:
    assert safe_return_to(value) is None


@pytest.mark.parametrize(
    "value", ["/", "/zones", "/zones?filter=a%20b", "/devices/map", "/oauth/consent?request=abc"]
)
def test_safe_return_targets_pass(value: str | None) -> None:
    assert safe_return_to(value) == value


def test_after_login_the_person_lands_on_the_wanted_page(
    client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = user_with_permissions(session, "zurueck", [("zone.read", None)])
    password = secrets.token_urlsafe(18)
    user.password_hash = hash_password(password)

    async def no_delay(_seconds: float) -> None:
        return None

    monkeypatch.setattr("thermoctl.web.auth_views.sleep", no_delay)
    first = client.get("/zones", headers=HTML, follow_redirects=False)
    page = client.get(first.headers["location"])
    assert 'name="return_to" value="/zones"' in page.text
    result = client.post(
        "/login",
        data={"username": user.username, "password": password, "return_to": "/zones"},
        follow_redirects=False,
    )
    assert result.status_code == 303
    assert result.headers["location"] == "/zones"


def test_login_ignores_loop_and_foreign_targets(
    client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = user_with_permissions(session, "schleife", [("zone.read", None)])
    password = secrets.token_urlsafe(18)
    user.password_hash = hash_password(password)

    async def no_delay(_seconds: float) -> None:
        return None

    monkeypatch.setattr("thermoctl.web.auth_views.sleep", no_delay)
    for target in ("/login", "https://evil.example", "//evil.example"):
        client.cookies.clear()
        result = client.post(
            "/login",
            data={"username": user.username, "password": password, "return_to": target},
            follow_redirects=False,
        )
        assert result.headers["location"] == "/"
