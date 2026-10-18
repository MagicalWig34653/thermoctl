"""Vorschau für Administratoren: wie die Oberfläche für ein anderes Konto aussieht (nur Lesen)."""

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.helpers import create_zone, user_with_permissions
from tests.test_ui_api_dashboard import EVERYTHING, ZONE_READ, _headers, _login
from tests.test_ui_api_dashboard_admin import ADMIN, _actions, _admin, _login_as
from thermoctl.db.models.identity import User
from thermoctl.domain.ui_profile import WebUiProfile


def test_preview_needs_login_and_the_right(client: TestClient, session: Session) -> None:
    target = user_with_permissions(session, "ziel", ZONE_READ).id
    url = f"/ui-api/access/users/{target}/preview"
    assert client.get(url).status_code == 401
    _login(client, session, "ohne-recht", EVERYTHING)
    response = client.get(url)
    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"
    # Das Mieterprofil allein sperrt nicht, das Recht öffnet.
    _login(client, session, "mieter-mit-recht", ADMIN, WebUiProfile.TENANT)
    assert client.get(url).status_code == 200


def test_preview_of_an_unknown_user_is_404(client: TestClient, session: Session) -> None:
    _admin(client, session)
    response = client.get("/ui-api/access/users/99999/preview")
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_preview_equals_the_real_session_of_the_target(
    client: TestClient, session: Session
) -> None:
    zone = create_zone(session, "Bad")
    create_zone(session, "Fremd")
    user_with_permissions(
        session,
        "mieterin",
        [("zone.read", zone.id), ("override.create", zone.id)],
        ui_profile=WebUiProfile.TENANT,
    )
    target_id = session.scalar(select(User.id).where(User.username == "mieterin"))
    _admin(client, session)
    preview = client.get(f"/ui-api/access/users/{target_id}/preview")
    assert preview.status_code == 200, preview.text
    body = preview.json()

    _login_as(client, session, "mieterin")
    real_bootstrap = client.get("/ui-api/bootstrap").json()
    real_dashboard = client.get("/ui-api/dashboard").json()
    assert body["user_id"] == target_id
    assert body["username"] == "mieterin"
    assert body["ui_profile"] == "tenant"
    assert body["navigation"] == real_bootstrap["navigation"]
    assert body["dashboard"] == real_dashboard
    # Rechte als lesbare Liste mit Beschreibung und Raumname.
    assert [(p["code"], p["zone_id"], p["zone_name"]) for p in body["permissions"]] == [
        ("override.create", zone.id, zone.display_name),
        ("zone.read", zone.id, zone.display_name),
    ]
    assert all(p["description"] for p in body["permissions"])
    assert "Fremd" not in str(body["dashboard"])


def test_preview_is_read_only_and_leaves_no_trace(client: TestClient, session: Session) -> None:
    target = user_with_permissions(session, "ziel2", ZONE_READ).id
    _admin(client, session)
    audit_before = len(_actions(session))
    sessions_before = client.get("/ui-api/account/sessions").json()
    assert client.get(f"/ui-api/access/users/{target}/preview").status_code == 200
    assert len(_actions(session)) == audit_before
    assert client.get("/ui-api/account/sessions").json() == sessions_before
    # Weiterhin der eigene Benutzer, keine Identitätsübernahme.
    assert client.get("/ui-api/account").json()["username"] == "verwalter"
    # Schreiben gibt es nicht.
    write = client.put(f"/ui-api/access/users/{target}/preview", json={}, headers=_headers(client))
    assert write.status_code == 405


def test_preview_of_an_account_without_rights_shows_nothing_but_the_start(
    client: TestClient, session: Session
) -> None:
    target = user_with_permissions(session, "leer", []).id
    _admin(client, session)
    body = client.get(f"/ui-api/access/users/{target}/preview").json()
    assert body["permissions"] == []
    assert {item["path"] for item in body["navigation"]} == {"/account"}
    assert all(section["tiles"] == [] for section in body["dashboard"]["sections"])
