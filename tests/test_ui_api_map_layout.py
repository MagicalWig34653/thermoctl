"""Anordnung der Gerätekarte unter `/ui-api/devices/map/layout`: Rechte, Isolation,
Filter unsichtbarer Knoten, Vorgabe, Version."""

from datetime import datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.helpers import create_device, create_zone, role, user_with_permissions
from thermoctl.auth.csrf import CSRF_HEADER, csrf_token
from thermoctl.auth.sessions import COOKIE_NAME, create_session
from thermoctl.config import get_settings
from thermoctl.db.models.device import ZoneDevice
from thermoctl.db.models.identity import User
from thermoctl.db.models.map_layout import (
    COORD_LIMIT,
    MAX_NODES,
    ZOOM_MAX,
    ZOOM_MIN,
    MapLayout,
    MapLayoutDefault,
    MapLayoutDefaultNode,
    MapLayoutNode,
)
from thermoctl.domain.ui_profile import WebUiProfile

URL = "/ui-api/devices/map/layout"
DEFAULT_URL = URL + "/default"
READ: list[tuple[str, int | None]] = [("device.read", None)]
MANAGE: list[tuple[str, int | None]] = [("device.read", None), ("device.manage", None)]
UNKNOWN = "Dieses Gerät oder dieser Raum ist nicht verfügbar."


def _headers(client: TestClient) -> dict[str, str]:
    http_session = client.cookies.get(COOKIE_NAME)
    assert http_session is not None
    return {CSRF_HEADER: csrf_token(http_session, get_settings().secret_key.get_secret_value())}


def _login(
    client: TestClient,
    session: Session,
    name: str,
    permissions: list[tuple[str, int | None]],
    profile: WebUiProfile = WebUiProfile.ADMIN,
) -> int:
    user = user_with_permissions(session, name, permissions, ui_profile=profile)
    _http_session, secret = create_session(session, user, 3600)
    client.cookies.set(COOKIE_NAME, secret)
    return user.id


def _relogin(client: TestClient, session: Session, user_id: int) -> None:
    user = session.get(User, user_id)
    assert user is not None
    _http_session, secret = create_session(session, user, 3600)
    client.cookies.set(COOKIE_NAME, secret)


def _get(client: TestClient) -> Any:
    response = client.get(URL)
    assert response.status_code == 200, response.text
    return response.json()


def _put(client: TestClient, version: int, nodes: list[dict[str, Any]], *, url: str = URL,
         view: dict[str, float] | None = None) -> Any:
    return client.put(
        url, json={"version": version, "nodes": nodes, "view": view}, headers=_headers(client)
    )


def _node(
    key: str, x: float | None = 1.5, y: float | None = 2.5, collapsed: bool = False
) -> dict[str, Any]:
    return {"key": key, "x": x, "y": y, "collapsed": collapsed}


def _keys(body: Any) -> list[str]:
    return [n["key"] for n in body["nodes"]]


# -- Anmeldung und Rechte ---------------------------------------------------------------


def test_every_method_needs_a_session(client: TestClient) -> None:
    body = {"version": 0, "nodes": []}
    assert client.get(URL).status_code == 401
    assert client.put(URL, json=body).status_code == 401
    assert client.delete(URL).status_code == 401
    assert client.put(DEFAULT_URL, json=body).status_code == 401
    assert client.delete(DEFAULT_URL).status_code == 401


def test_writes_need_the_csrf_header(client: TestClient, session: Session) -> None:
    _login(client, session, "a", MANAGE)
    body = {"version": 0, "nodes": []}
    assert client.put(URL, json=body).status_code == 403
    assert client.delete(URL).status_code == 403
    assert client.put(DEFAULT_URL, json=body).status_code == 403
    assert client.delete(DEFAULT_URL).status_code == 403


def test_without_device_read_everything_is_forbidden(client: TestClient, session: Session) -> None:
    _login(client, session, "a", [("zone.read", None)])
    assert client.get(URL).status_code == 403
    assert _put(client, 0, []).status_code == 403
    assert client.delete(URL, headers=_headers(client)).status_code == 403


def test_a_tenant_profile_has_no_access_even_with_the_rights(
    client: TestClient, session: Session
) -> None:
    _login(client, session, "m", MANAGE, WebUiProfile.TENANT)
    assert client.get(URL).json()["code"] == "wrong_profile"
    assert _put(client, 0, []).status_code == 403
    assert client.delete(URL, headers=_headers(client)).status_code == 403
    assert _put(client, 0, [], url=DEFAULT_URL).status_code == 403
    assert client.delete(DEFAULT_URL, headers=_headers(client)).status_code == 403


def test_the_default_needs_plant_wide_device_manage(client: TestClient, session: Session) -> None:
    zone = create_zone(session, "wohnen")
    _login(client, session, "leser", READ)
    assert _get(client)["may_set_default"] is False
    assert _put(client, 0, [], url=DEFAULT_URL).status_code == 403
    assert client.delete(DEFAULT_URL, headers=_headers(client)).status_code == 403
    _login(client, session, "zonenverwalter", [("device.read", None), ("device.manage", zone.id)])
    assert _get(client)["may_set_default"] is False
    assert _put(client, 0, [], url=DEFAULT_URL).status_code == 403
    _login(client, session, "admin", MANAGE)
    assert _get(client)["may_set_default"] is True
    assert _put(client, 0, [], url=DEFAULT_URL).status_code == 200


def test_the_unknown_routes_stay_unknown(client: TestClient, session: Session) -> None:
    _login(client, session, "a", MANAGE)
    assert client.get(URL + "/gibtsnicht").status_code == 404


# -- Lesen, Speichern, Isolation --------------------------------------------------------


def test_without_anything_stored_the_layout_is_empty(client: TestClient, session: Session) -> None:
    _login(client, session, "a", READ)
    assert _get(client) == {
        "source": "none", "version": 0, "default_version": 0, "has_default": False,
        "may_set_default": False,
        "nodes": [], "view": None,
    }


def test_a_saved_layout_is_read_back_with_view_and_fold_state(
    client: TestClient, session: Session
) -> None:
    zone = create_zone(session, "wohnen")
    device = create_device(session, "d1")
    _login(client, session, "a", READ)
    nodes = [_node(f"zone-{zone.id}", 10, -20), _node(f"dev-{device.id}", None, None, True)]
    response = _put(client, 0, nodes, view={"x": 5, "y": -6.5, "k": 0.75})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source"] == "personal" and body["version"] == 1
    assert body["nodes"] == [
        {"key": f"zone-{zone.id}", "x": 10, "y": -20, "collapsed": False},
        {"key": f"dev-{device.id}", "x": None, "y": None, "collapsed": True},
    ]
    assert body["view"] == {"x": 5, "y": -6.5, "k": 0.75}
    assert _get(client) == body
    # Neu speichern ersetzt alles, auch die Ansicht.
    second = _put(client, 1, [_node(f"zone-{zone.id}")]).json()
    assert second["version"] == 2 and second["view"] is None
    assert _keys(second) == [f"zone-{zone.id}"]


def test_each_user_has_their_own_layout(client: TestClient, session: Session) -> None:
    zone = create_zone(session, "wohnen")
    first = _login(client, session, "a", READ)
    assert _put(client, 0, [_node(f"zone-{zone.id}", 1, 1)]).status_code == 200
    _login(client, session, "b", READ)
    assert _get(client)["source"] == "none"
    assert _put(client, 0, [_node(f"zone-{zone.id}", 9, 9)]).status_code == 200
    assert client.delete(URL, headers=_headers(client)).status_code == 200
    _relogin(client, session, first)
    assert _get(client)["nodes"][0]["x"] == 1


def test_the_shared_default_applies_until_a_personal_layout_exists(
    client: TestClient, session: Session
) -> None:
    zone = create_zone(session, "wohnen")
    key = f"zone-{zone.id}"
    admin = _login(client, session, "admin", MANAGE)
    view = {"x": 1, "y": 2, "k": 1}
    saved = _put(client, 0, [_node(key, 7, 8, True)], url=DEFAULT_URL, view=view)
    assert saved.status_code == 200, saved.text
    assert saved.json()["source"] == "default" and saved.json()["default_version"] == 1
    assert saved.json()["has_default"] is True
    other = _login(client, session, "anderer", READ)
    shown = _get(client)
    assert shown["source"] == "default" and shown["version"] == 0 and shown["default_version"] == 1
    assert shown["nodes"] == [{"key": key, "x": 7, "y": 8, "collapsed": True}]
    assert shown["view"] == {"x": 1, "y": 2, "k": 1}
    # Eigene Anordnung hat Vorrang; Zurücksetzen führt zur Vorgabe zurück.
    own = _put(client, 0, [_node(key, 3, 3)]).json()
    assert own["source"] == "personal" and own["nodes"][0]["x"] == 3 and own["view"] is None
    back = client.delete(URL, headers=_headers(client)).json()
    assert back["source"] == "default" and back["nodes"][0]["x"] == 7 and back["version"] == 2
    # Die Vorgabe zu entfernen lässt die automatische Anordnung übrig.
    _relogin(client, session, admin)
    gone = client.delete(DEFAULT_URL, headers=_headers(client))
    assert gone.status_code == 200 and gone.json()["source"] == "none"
    assert gone.json()["default_version"] == 2 and gone.json()["has_default"] is False
    assert session.scalars(select(MapLayoutDefaultNode)).all() == []
    _relogin(client, session, other)
    assert _get(client)["source"] == "none"


def test_resetting_without_anything_stored_is_fine(client: TestClient, session: Session) -> None:
    _login(client, session, "a", MANAGE)
    body = client.delete(URL, headers=_headers(client)).json()
    assert body["source"] == "none" and body["version"] == 1
    assert client.delete(DEFAULT_URL, headers=_headers(client)).json()["default_version"] == 1
    assert _put(client, 1, []).status_code == 200


# -- Sichtbarkeit -----------------------------------------------------------------------


def test_nodes_the_principal_may_not_see_are_filtered_and_rejected_like_unknown(
    client: TestClient, session: Session
) -> None:
    mine, theirs = create_zone(session, "meins"), create_zone(session, "fremd")
    device_mine, device_theirs, device_free = (
        create_device(session, "m"), create_device(session, "t"), create_device(session, "frei")
    )
    for zone, device in ((mine, device_mine), (theirs, device_theirs)):
        session.add(ZoneDevice(zone_id=zone.id, device_id=device.id,
                               device_role_id=role(session, "sensor").id))
    session.flush()
    admin = _login(client, session, "admin", MANAGE)
    everything = [
        f"zone-{mine.id}", f"zone-{theirs.id}", f"dev-{device_mine.id}",
        f"dev-{device_theirs.id}", f"dev-{device_free.id}",
    ]
    assert _put(client, 0, [_node(k) for k in everything], url=DEFAULT_URL).status_code == 200
    assert _put(client, 0, [_node(k) for k in everything]).status_code == 200
    assert _keys(_get(client)) == everything
    assert admin

    _login(client, session, "eingeschraenkt", [("device.read", mine.id)])
    expected = [f"zone-{mine.id}", f"dev-{device_mine.id}", f"dev-{device_free.id}"]
    assert _keys(_get(client)) == expected  # aus der Vorgabe
    for key in (f"zone-{theirs.id}", f"dev-{device_theirs.id}", "zone-999999", "dev-999999"):
        rejected = _put(client, 0, [_node(key)])
        assert rejected.status_code == 422
        assert rejected.json() == {"detail": UNKNOWN, "code": "validation", "field": "nodes"}
    own = _put(client, 0, [_node(k) for k in expected])
    assert own.status_code == 200
    # Gespeichert ist nichts Fremdes, und beim Lesen fällt Fremdes ohnehin weg.
    stored = session.scalars(select(MapLayoutNode.node_key)).all()
    assert sorted(stored[-3:]) == sorted(expected)


def test_a_stored_node_of_a_deleted_device_or_zone_is_harmless(
    client: TestClient, session: Session
) -> None:
    zone = create_zone(session, "wohnen")
    device = create_device(session, "d")
    user_id = _login(client, session, "a", READ)
    keys = [f"zone-{zone.id}", f"dev-{device.id}"]
    assert _put(client, 0, [_node(k) for k in keys]).status_code == 200
    session.delete(device)
    session.delete(zone)
    session.flush()
    assert _keys(_get(client)) == []
    # Die verwaiste Zeile blockiert nichts und fällt beim nächsten Speichern weg.
    assert _put(client, 1, []).status_code == 200
    left = session.scalars(select(MapLayoutNode).where(MapLayoutNode.user_id == user_id))
    assert left.all() == []


# -- Eingabe ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "node",
    [
        {"key": "dev-1; DROP", "x": 0, "y": 0, "collapsed": False},
        {"key": "x" * 40, "x": 0, "y": 0, "collapsed": False},
        {"key": "dev-1", "x": 0, "y": None, "collapsed": False},
        {"key": "dev-1", "x": COORD_LIMIT + 1, "y": 0, "collapsed": False},
        {"key": "dev-1", "x": 0, "y": -COORD_LIMIT - 1, "collapsed": False},
        {"key": "dev-1", "x": 0, "y": 0, "collapsed": False, "extra": 1},
    ],
)
def test_bad_nodes_are_rejected_by_the_schema(
    client: TestClient, session: Session, node: dict[str, Any]
) -> None:
    _login(client, session, "a", READ)
    response = _put(client, 0, [node])
    assert response.status_code == 422 and response.json()["code"] == "validation"


@pytest.mark.parametrize(
    "view",
    [
        {"x": 0, "y": 0, "k": ZOOM_MIN / 2},
        {"x": 0, "y": 0, "k": ZOOM_MAX * 2},
        {"x": COORD_LIMIT * 2, "y": 0, "k": 1},
        {"x": 0, "y": 0},
    ],
)
def test_bad_views_are_rejected(
    client: TestClient, session: Session, view: dict[str, float]
) -> None:
    _login(client, session, "a", READ)
    assert _put(client, 0, [], view=view).status_code == 422


def test_duplicates_and_too_many_nodes_are_rejected(client: TestClient, session: Session) -> None:
    zone = create_zone(session, "wohnen")
    _login(client, session, "a", READ)
    key = f"zone-{zone.id}"
    duplicate = _put(client, 0, [_node(key), _node(key)])
    assert duplicate.status_code == 422 and duplicate.json()["field"] == "nodes"
    many = [_node(f"dev-{i}") for i in range(MAX_NODES + 1)]
    assert _put(client, 0, many).status_code == 422
    assert _put(client, -1, []).status_code == 422
    assert client.get(URL).json()["source"] == "none"


# -- Version ----------------------------------------------------------------------------


def test_a_stale_version_is_a_conflict_and_changes_nothing(
    client: TestClient, session: Session
) -> None:
    zone = create_zone(session, "wohnen")
    key = f"zone-{zone.id}"
    _login(client, session, "a", READ)
    assert _put(client, 5, [_node(key)]).status_code == 409  # ohne Zeile nur Version 0
    assert _put(client, 0, [_node(key, 1, 1)]).json()["version"] == 1
    stale = _put(client, 0, [_node(key, 9, 9)])
    assert stale.status_code == 409
    assert stale.json() == {"detail": "Die Anordnung wurde inzwischen geändert. Bitte neu laden.",
                            "code": "conflict", "field": "version"}
    assert _get(client)["nodes"][0]["x"] == 1
    # Auch das Zurücksetzen zählt: der Stand davor kann nicht mehr schreiben.
    client.delete(URL, headers=_headers(client))
    assert _put(client, 1, []).status_code == 409
    assert _put(client, 2, []).status_code == 200


def test_the_default_has_its_own_version(client: TestClient, session: Session) -> None:
    _login(client, session, "admin", MANAGE)
    assert _put(client, 0, [], url=DEFAULT_URL).json()["default_version"] == 1
    assert _put(client, 0, [], url=DEFAULT_URL).status_code == 409
    # Die persönliche Version läuft getrennt davon.
    assert _put(client, 0, []).json()["version"] == 1


def test_losing_the_race_for_the_first_save_is_a_conflict(
    client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_id = _login(client, session, "a", READ)
    session.add(
        MapLayout(user_id=user_id, version=1, customized=True, updated_at=datetime(2026, 1, 1))
    )
    session.flush()
    real_get = Session.get

    def blind(self: Session, entity: Any, ident: Any, *args: Any, **kwargs: Any) -> Any:
        return None if entity is MapLayout else real_get(self, entity, ident, *args, **kwargs)

    monkeypatch.setattr(Session, "get", blind)
    response = _put(client, 0, [])
    assert response.status_code == 409 and response.json()["code"] == "conflict"


def test_losing_the_race_for_a_reset_is_not_an_error(
    client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_id = _login(client, session, "a", READ)
    session.add(
        MapLayout(user_id=user_id, version=1, customized=False, updated_at=datetime(2026, 1, 1))
    )
    session.flush()
    real_get = Session.get

    def blind(self: Session, entity: Any, ident: Any, *args: Any, **kwargs: Any) -> Any:
        return None if entity is MapLayout else real_get(self, entity, ident, *args, **kwargs)

    monkeypatch.setattr(Session, "get", blind)
    assert client.delete(URL, headers=_headers(client)).status_code == 200
    monkeypatch.undo()
    assert session.get(MapLayout, user_id) is not None


def test_the_default_head_is_one_row(client: TestClient, session: Session) -> None:
    _login(client, session, "admin", MANAGE)
    _put(client, 0, [], url=DEFAULT_URL)
    _put(client, 1, [], url=DEFAULT_URL)
    assert [r.id for r in session.scalars(select(MapLayoutDefault))] == [1]
