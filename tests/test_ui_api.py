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
