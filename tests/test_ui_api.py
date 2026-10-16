"""Bootstrap der Svelte-Oberfläche: Sitzungspflicht, Profil, nur sichtbare Räume."""

from collections.abc import Callable

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.helpers import create_zone

Als = Callable[[list[tuple[str, int | None]]], TestClient]


def test_bootstrap_without_session_is_json_401(client: TestClient) -> None:
    response = client.get("/ui-api/bootstrap", headers={"Accept": "text/html"})
    assert response.status_code == 401
    assert response.json() == {"detail": "Nicht angemeldet"}


def test_bootstrap_lists_only_visible_zones(client_als: Als, session: Session) -> None:
    mine = create_zone(session, "Bad")
    create_zone(session, "Fremdes Zimmer")
    body = client_als([("zone.read", mine.id)]).get("/ui-api/bootstrap").json()
    assert [z["name"] for z in body["zones"]] == [mine.display_name]
    assert "Fremdes Zimmer" not in str(body)
    assert body["ui_profile"] == "admin"


def test_bootstrap_navigation_follows_grants(client_als: Als) -> None:
    body = client_als([("zone.read", None)]).get("/ui-api/bootstrap").json()
    paths = {item["path"] for item in body["navigation"]}
    assert "/zones" in paths
    assert "/users" not in paths


def test_bootstrap_reports_tenant_profile(tenant_client: Als, session: Session) -> None:
    zone = create_zone(session, "Wohnzimmer")
    body = tenant_client([("zone.read", zone.id)]).get("/ui-api/bootstrap").json()
    assert body["ui_profile"] == "tenant"
    assert {"/users", "/devices"}.isdisjoint(i["path"] for i in body["navigation"])


def test_ui_api_router_carries_csrf_protection() -> None:
    """Jede künftige Änderung unter /ui-api erbt die Prüfung vom Router."""
    from thermoctl.auth.dependencies import csrf_protection
    from thermoctl.web.ui_api import router

    assert csrf_protection in [d.dependency for d in router.dependencies]


def test_csrf_failure_under_ui_api_is_json(client_als: Als) -> None:
    client = client_als([("zone.read", None)])
    response = client.post("/ui-api/csrf-probe", headers={"X-CSRF-Token": "falsch"})
    assert response.status_code == 403
    assert response.json() == {"detail": "Ungültiges CSRF-Token", "stale": True}


def test_valid_csrf_passes_under_ui_api(client_als: Als) -> None:
    from thermoctl.web import ui_api  # noqa: F401

    client = client_als([("zone.read", None)])
    token = client.cookies.get("thermoctl_csrf")
    if token is None:
        from thermoctl.auth.csrf import csrf_token
        from thermoctl.auth.sessions import COOKIE_NAME
        from thermoctl.config import get_settings

        token = csrf_token(
            client.cookies[COOKIE_NAME], get_settings().secret_key.get_secret_value()
        )
    response = client.post("/ui-api/csrf-probe", headers={"X-CSRF-Token": token})
    assert response.json() == {"ok": True}


def test_api_responses_carry_no_store_and_nosniff(client) -> None:
    response = client.get("/ui-api/bootstrap")
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    login = client.get("/ui-api/login-options")
    assert login.headers["Cache-Control"] == "no-store"
    kiosk = client.get("/kiosk-api/bootstrap")
    assert kiosk.headers["X-Content-Type-Options"] == "nosniff"
    assert kiosk.headers["Cache-Control"] == "no-store"


def test_app_and_static_get_nosniff_but_no_framing_ban(client) -> None:
    for path in ("/app", "/static/favicon.svg"):
        response = client.get(path, follow_redirects=True)
        assert response.headers["X-Content-Type-Options"] == "nosniff", path
        assert "X-Frame-Options" not in response.headers
        assert "frame-ancestors" not in response.headers.get("Content-Security-Policy", "")
    # `/login` ist nur noch eine Umleitung (kein Svelte-Pfad): ohne Folgen der Umleitung geprüft.
    assert "X-Content-Type-Options" not in client.get("/login", follow_redirects=False).headers
