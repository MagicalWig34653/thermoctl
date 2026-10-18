"""Übersicht pflegen: Vorlagen, Anwenden beim Anlegen, Übersicht anderer Benutzer."""

from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.helpers import _group_with_permissions, create_zone, user_with_permissions
from tests.test_ui_api_dashboard import (
    EVERYTHING,
    ZONE_READ,
    _headers,
    _login,
    _section,
    _tiles,
)
from thermoctl.db.models.dashboard import DashboardTemplate
from thermoctl.db.models.identity import User
from thermoctl.db.models.operations import AuditEvent
from thermoctl.domain.ui_profile import WebUiProfile

ADMIN: list[tuple[str, int | None]] = [*EVERYTHING, ("user.manage", None)]
PASSWORD = "ein-langes-passwort-1"
UNKNOWN_ZONE = "Dieser Raum ist nicht verfügbar."


def _admin(client: TestClient, session: Session) -> int:
    return _login(client, session, "verwalter", ADMIN)


def _room(zone_id: int, kind: str = "room") -> dict[str, Any]:
    return {"kind": kind, "zone_id": zone_id, "width": 4}


def _template(client: TestClient, name: str, *sections: dict[str, Any]) -> Any:
    return client.post(
        "/ui-api/dashboard/templates",
        json={"name": name, "sections": list(sections)},
        headers=_headers(client),
    )


def _actions(session: Session) -> list[str]:
    return [row.action for row in session.scalars(select(AuditEvent).order_by(AuditEvent.id))]


def _create_user(
    client: TestClient, name: str, group_id: int | None, template_id: int | None
) -> Any:
    return client.post(
        "/ui-api/access/users",
        json={
            "username": name,
            "display_name": name,
            "password": PASSWORD,
            "group_id": group_id,
            "template_id": template_id,
        },
        headers=_headers(client),
    )


def _login_as(client: TestClient, session: Session, username: str) -> None:
    from thermoctl.auth.sessions import COOKIE_NAME, create_session

    user = session.scalar(select(User).where(User.username == username))
    assert user is not None
    _http_session, secret = create_session(session, user, 3600)
    client.cookies.set(COOKIE_NAME, secret)


# -- Zugang ------------------------------------------------------------------------------


def test_every_endpoint_needs_login_and_the_right(
    client: TestClient, session: Session
) -> None:
    target = user_with_permissions(session, "ziel", ZONE_READ).id
    calls: list[tuple[str, str, dict[str, Any] | None]] = [
        ("GET", "/ui-api/dashboard/templates", None),
        ("GET", "/ui-api/dashboard/templates/1", None),
        ("POST", "/ui-api/dashboard/templates", {"name": "x", "sections": []}),
        ("POST", f"/ui-api/dashboard/templates/from-user/{target}", {"name": "x"}),
        ("PUT", "/ui-api/dashboard/templates/1", {"name": "x", "sections": []}),
        ("DELETE", "/ui-api/dashboard/templates/1", None),
        ("GET", f"/ui-api/dashboard/users/{target}", None),
        ("PUT", f"/ui-api/dashboard/users/{target}", {"version": 0, "sections": []}),
        ("DELETE", f"/ui-api/dashboard/users/{target}", None),
    ]
    for method, url, body in calls:
        assert client.request(method, url, json=body).status_code == 401, (method, url)
    _login(client, session, "ohne-recht", EVERYTHING)
    for method, url, body in calls:
        response = client.request(method, url, json=body, headers=_headers(client))
        assert response.status_code == 403, (method, url)
    # Das Profil sperrt nichts: nur ein Mieterprofil, aber mit Recht, kommt hinein.
    _login(client, session, "mieter", [*ADMIN, ("zone.read", None)], WebUiProfile.TENANT)
    for method, url, body in calls:
        response = client.request(method, url, json=body, headers=_headers(client))
        assert response.status_code != 403, (method, url)


def test_writes_need_the_csrf_header(client: TestClient, session: Session) -> None:
    _admin(client, session)
    response = client.post("/ui-api/dashboard/templates", json={"name": "x", "sections": []})
    assert response.status_code == 403


# -- Vorlagen ----------------------------------------------------------------------------


def test_template_lifecycle(client: TestClient, session: Session) -> None:
    zone = create_zone(session, "wohnen")
    _admin(client, session)
    created = _template(
        client, "Mieter", _section("Räume", _room(zone.id), _room(zone.id, "control"))
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["name"] == "Mieter"
    assert [t["kind"] for t in _tiles(body)] == ["room", "control"]

    listing = client.get("/ui-api/dashboard/templates").json()["templates"]
    assert listing == [{"id": body["id"], "name": "Mieter", "tile_count": 2}]
    assert client.get(f"/ui-api/dashboard/templates/{body['id']}").json() == body

    saved = client.put(
        f"/ui-api/dashboard/templates/{body['id']}",
        json={"name": "Mieter neu", "sections": [_section("Eins", _room(zone.id))]},
        headers=_headers(client),
    )
    assert saved.status_code == 200
    assert saved.json()["name"] == "Mieter neu"
    assert len(_tiles(saved.json())) == 1

    deleted = client.delete(f"/ui-api/dashboard/templates/{body['id']}", headers=_headers(client))
    assert deleted.status_code == 204
    assert client.get(f"/ui-api/dashboard/templates/{body['id']}").status_code == 404
    assert client.get("/ui-api/dashboard/templates").json()["templates"] == []
    assert _actions(session) == [
        "dashboard.template_created",
        "dashboard.template_saved",
        "dashboard.template_deleted",
    ]


def test_template_name_must_be_unique_and_unknown_ids_are_404(
    client: TestClient, session: Session
) -> None:
    _admin(client, session)
    first = _template(client, "Doppelt").json()
    clash = _template(client, "Doppelt")
    assert clash.status_code == 409
    assert clash.json()["field"] == "name"
    other = _template(client, "Anders").json()
    rename = client.put(
        f"/ui-api/dashboard/templates/{other['id']}",
        json={"name": "Doppelt", "sections": []},
        headers=_headers(client),
    )
    assert rename.status_code == 409
    # Den eigenen Namen beim Speichern zu behalten ist kein Konflikt.
    same = client.put(
        f"/ui-api/dashboard/templates/{first['id']}",
        json={"name": "Doppelt", "sections": []},
        headers=_headers(client),
    )
    assert same.status_code == 200
    for method in ("GET", "DELETE"):
        response = client.request(
            method, "/ui-api/dashboard/templates/9999", headers=_headers(client)
        )
        assert response.status_code == 404
    missing = client.put(
        "/ui-api/dashboard/templates/9999",
        json={"name": "x", "sections": []},
        headers=_headers(client),
    )
    assert missing.status_code == 404


def test_template_rejects_a_zone_the_admin_cannot_see(
    client: TestClient, session: Session
) -> None:
    hidden = create_zone(session, "geheim")
    _login(client, session, "verwalter", [("user.manage", None), ("device.read", None)])
    unknown = _template(client, "T", _section("S", _room(hidden.id)))
    assert unknown.status_code == 422
    assert unknown.json()["detail"] == UNKNOWN_ZONE
    ghost = _template(client, "T", _section("S", _room(99999)))
    assert ghost.status_code == 422
    assert ghost.json()["detail"] == UNKNOWN_ZONE
    assert session.scalars(select(DashboardTemplate)).all() == []


def test_control_tile_needs_a_visible_room(client: TestClient, session: Session) -> None:
    create_zone(session, "wohnen")
    _admin(client, session)
    response = client.put(
        "/ui-api/dashboard",
        json={"version": 0, "sections": [_section("S", {"kind": "control", "width": 4})]},
        headers=_headers(client),
    )
    assert response.status_code == 422
    assert response.json()["field"] == "zone_id"


# -- Vorlage beim Anlegen eines Benutzers -----------------------------------------------


def test_new_user_gets_the_template_filtered_by_their_rights(
    client: TestClient, session: Session
) -> None:
    visible, other = create_zone(session, "sichtbar"), create_zone(session, "andere")
    group = _group_with_permissions(session, "mieter-gruppe", [("zone.read", visible.id)])
    _admin(client, session)
    template = _template(
        client,
        "Mieter",
        _section(
            "Mein Zuhause", _room(visible.id), _room(other.id), _room(visible.id, "control")
        ),
    ).json()
    created = _create_user(client, "neu", group.id, template["id"])
    assert created.status_code == 201, created.text

    _login_as(client, session, "neu")
    body = client.get("/ui-api/dashboard").json()
    assert body["customized"] is True
    assert [(t["kind"], t["zone_id"]) for t in _tiles(body)] == [
        ("room", visible.id),
        ("control", visible.id),
    ]
    assert "dashboard.template_applied" in _actions(session)


def test_template_is_a_snapshot(client: TestClient, session: Session) -> None:
    zone = create_zone(session, "wohnen")
    group = _group_with_permissions(session, "g", ZONE_READ)
    _admin(client, session)
    template = _template(client, "T", _section("A", _room(zone.id))).json()
    assert _create_user(client, "u1", group.id, template["id"]).status_code == 201
    client.put(
        f"/ui-api/dashboard/templates/{template['id']}",
        json={"name": "T", "sections": [_section("B")]},
        headers=_headers(client),
    )
    client.delete(f"/ui-api/dashboard/templates/{template['id']}", headers=_headers(client))
    _login_as(client, session, "u1")
    body = client.get("/ui-api/dashboard").json()
    assert [s["title"] for s in body["sections"]] == ["A"]


def test_unknown_template_creates_no_user(client: TestClient, session: Session) -> None:
    _admin(client, session)
    response = _create_user(client, "nie", None, 4242)
    assert response.status_code == 422
    assert response.json()["field"] == "template_id"
    assert session.scalar(select(User).where(User.username == "nie")) is None


def test_user_without_template_keeps_the_default(client: TestClient, session: Session) -> None:
    create_zone(session, "wohnen")
    group = _group_with_permissions(session, "g", ZONE_READ)
    _admin(client, session)
    assert _create_user(client, "std", group.id, None).status_code == 201
    _login_as(client, session, "std")
    body = client.get("/ui-api/dashboard").json()
    assert body["customized"] is False
    assert body["version"] == 0


# -- Übersicht eines anderen Benutzers ---------------------------------------------------


def test_admin_edits_the_overview_of_another_user(client: TestClient, session: Session) -> None:
    visible, hidden = create_zone(session, "sichtbar"), create_zone(session, "versteckt")
    target = user_with_permissions(session, "ziel", [("zone.read", visible.id)]).id
    admin_id = _admin(client, session)
    url = f"/ui-api/dashboard/users/{target}"

    before = client.get(url).json()
    assert before["customized"] is False
    saved = client.put(
        url,
        json={"version": before["version"], "sections": [_section("Mein", _room(visible.id))]},
        headers=_headers(client),
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["customized"] is True
    # Es gehört dem Ziel, nicht dem Verwalter.
    assert client.get("/ui-api/dashboard").json()["customized"] is False
    _login_as(client, session, "ziel")
    assert [s["title"] for s in client.get("/ui-api/dashboard").json()["sections"]] == ["Mein"]

    _login_as(client, session, "verwalter")
    # Was das Ziel nicht sehen darf, kann ihm der Verwalter auch nicht geben.
    refused = client.put(
        url,
        json={"version": saved.json()["version"], "sections": [_section("X", _room(hidden.id))]},
        headers=_headers(client),
    )
    assert refused.status_code == 422
    assert refused.json()["detail"] == UNKNOWN_ZONE
    stale = client.put(
        url, json={"version": 0, "sections": []}, headers=_headers(client)
    )
    assert stale.status_code == 409

    reset = client.delete(url, headers=_headers(client))
    assert reset.status_code == 200
    assert reset.json()["customized"] is False
    actions = _actions(session)
    assert actions == ["dashboard.user_saved", "dashboard.user_reset"]
    events = session.scalars(select(AuditEvent)).all()
    assert {e.actor_user_id for e in events} == {admin_id}
    assert {e.object_id for e in events} == {str(target)}


def test_unknown_user_is_404(client: TestClient, session: Session) -> None:
    _admin(client, session)
    for method, body in (
        ("GET", None),
        ("PUT", {"version": 0, "sections": []}),
        ("DELETE", None),
    ):
        response = client.request(
            method, "/ui-api/dashboard/users/9999", json=body, headers=_headers(client)
        )
        assert response.status_code == 404, method
    response = client.post(
        "/ui-api/dashboard/templates/from-user/9999",
        json={"name": "x"},
        headers=_headers(client),
    )
    assert response.status_code == 404


def test_template_from_the_overview_of_a_user(client: TestClient, session: Session) -> None:
    zone = create_zone(session, "wohnen")
    target = user_with_permissions(session, "ziel", ZONE_READ).id
    _admin(client, session)
    client.put(
        f"/ui-api/dashboard/users/{target}",
        json={
            "version": 0,
            "sections": [_section("Mein", _room(zone.id), _room(zone.id, "control"))],
        },
        headers=_headers(client),
    )
    response = client.post(
        f"/ui-api/dashboard/templates/from-user/{target}",
        json={"name": "Wie Ziel"},
        headers=_headers(client),
    )
    assert response.status_code == 201, response.text
    assert [t["kind"] for t in _tiles(response.json())] == ["room", "control"]
    clash = client.post(
        f"/ui-api/dashboard/templates/from-user/{target}",
        json={"name": "Wie Ziel"},
        headers=_headers(client),
    )
    assert clash.status_code == 409
    # Auch wer nur den Standard hat, ergibt eine Vorlage.
    plain = user_with_permissions(session, "standard", ZONE_READ).id
    default = client.post(
        f"/ui-api/dashboard/templates/from-user/{plain}",
        json={"name": "Standard"},
        headers=_headers(client),
    )
    assert default.status_code == 201
    assert [s["title"] for s in default.json()["sections"]] == [
        "Auf einen Blick",
        "Dein Tag",
        "Deine Räume",
    ]


# -- Fachfunktion direkt -----------------------------------------------------------------


def test_apply_template_replaces_a_stored_layout_and_counts_the_version_up(
    session: Session,
) -> None:
    from thermoctl.db.models.dashboard import DashboardLayout
    from thermoctl.domain import dashboard_templates as templates

    zone = create_zone(session, "wohnen")
    user_id = user_with_permissions(session, "ziel", ZONE_READ).id
    first = templates.create_template(
        session, "Eins", [("A", [{**_tile(), "zone_id": zone.id}])]
    )
    second = templates.create_template(session, "Zwei", [("B", [_tile(kind="note")])])
    templates.apply_template(session, user_id, first.id)
    layout = session.get(DashboardLayout, user_id)
    assert layout is not None
    assert (layout.version, layout.customized) == (1, True)
    templates.apply_template(session, user_id, second.id)
    session.refresh(layout)
    assert layout.version == 2
    sections = [
        (s.title, len(s.tiles)) for s in _stored(session, user_id)
    ]
    assert sections == [("B", 1)]


def test_apply_unknown_template_is_a_domain_error(session: Session) -> None:
    import pytest

    from thermoctl.domain import dashboard_templates as templates

    user_id = user_with_permissions(session, "ziel", ZONE_READ).id
    with pytest.raises(templates.TemplateError):
        templates.apply_template(session, user_id, 4711)


def _tile(kind: str = "room") -> dict[str, Any]:
    return {
        "kind": kind,
        "zone_id": None,
        "title": "",
        "period_days": 1,
        "width": 4,
        "metric": None,
        "destination": None,
        "note": "",
    }


def _stored(session: Session, user_id: int) -> list[Any]:
    from thermoctl.web.ui_api_dashboard import _stored_sections

    return _stored_sections(session, user_id)
