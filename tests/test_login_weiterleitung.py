"""Nicht angemeldete Browser werden zum Login geleitet und danach zurückgeführt.

Seit `ui/cutover-redirects` ist der Login die Seite der Svelte-Oberfläche
(`/app/login`), und alle GET-Seiten der alten Oberfläche sind Umleitungen (siehe
`test_legacy_redirects.py`). Der `NotLoggedIn`-Handler greift damit noch bei Schreib-POSTs
und bei jeder GET-Route, die eine Sitzung verlangt und keine Altseite ist; diese Tests
prüfen ihn an einer eigens eingehängten geschützten Route.
"""

from typing import Annotated
from urllib.parse import quote

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from thermoctl.auth.dependencies import current_principal
from thermoctl.domain.principal import Principal
from thermoctl.web.urls import safe_return_to

HTML = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
PAGE = "/geschuetzte-seite"


def _guarded(client: TestClient) -> TestClient:
    """Hängt eine GET-Seite ein, die eine Sitzung verlangt (die Altseiten sind Umleitungen)."""

    async def page(_: Annotated[Principal, Depends(current_principal)]) -> dict[str, str]:
        return {}  # pragma: no cover - ohne Sitzung nie erreicht

    client.app.add_api_route(PAGE, page, methods=["GET", "POST"])  # type: ignore[attr-defined]
    return client


def test_page_redirects_browser_to_login_with_way_back(client: TestClient) -> None:
    response = _guarded(client).get(f"{PAGE}?filter=a%20b", headers=HTML, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["cache-control"] == "no-store"
    wanted = quote(f"{PAGE}?filter=a%20b", safe="")
    assert response.headers["location"] == f"/app/login?return_to={wanted}"


def test_other_clients_keep_plain_401(client: TestClient) -> None:
    # No `Accept: text/html`: a script or a test client reads the status, not a page.
    response = _guarded(client).get(PAGE, follow_redirects=False)
    assert response.status_code == 401
    assert response.json() == {"detail": "Nicht angemeldet"}


def test_api_and_mcp_paths_never_redirect(client: TestClient) -> None:
    response = client.get("/api/v1/zones", headers=HTML, follow_redirects=False)
    assert response.status_code == 401
    assert "location" not in response.headers


def test_post_without_session_goes_to_login_without_way_back(client: TestClient) -> None:
    response = _guarded(client).post(PAGE, headers=HTML, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/app/login"


def test_prefix_is_added_once_and_stripped_from_current_url(client_with_prefix: TestClient) -> None:
    prefix = "/api/hassio_ingress/A1b2C3d4e5"
    plain = _guarded(client_with_prefix).get(PAGE, headers=HTML, follow_redirects=False)
    assert plain.headers["location"] == f"{prefix}/app/login?return_to={quote(PAGE, safe='')}"


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
        "/%2e%2e/x",
        "/%2E%2E/%2e%2e/x",
        "/./x",
        "/%2fevil.com/x",
        "/%5cevil.com",
        "/%6cogin",
        "/%6cogout",
        "/%61pi/v1/zones",
        "/zones%0d%0aSet-Cookie:a=b",
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
