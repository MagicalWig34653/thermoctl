"""Dashboard-Layout unter `/ui-api/dashboard`: Isolation, Rechte, Grenzen, Version."""

from datetime import datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from tests.helpers import create_zone, user_with_permissions
from thermoctl.auth.csrf import CSRF_HEADER, csrf_token
from thermoctl.auth.sessions import COOKIE_NAME, create_session
from thermoctl.config import get_settings
from thermoctl.db.models.dashboard import (
    DESTINATIONS,
    MAX_NOTE_LENGTH,
    MAX_SECTIONS,
    MAX_TILES_PER_SECTION,
    MAX_TITLE_LENGTH,
    METRICS,
    PERIODS_DAYS,
    TILE_KINDS,
    WIDTHS,
    DashboardLayout,
    DashboardSection,
    DashboardTile,
)
from thermoctl.db.models.identity import GroupPermission, UserAccessGroup
from thermoctl.db.models.zone import Zone
from thermoctl.domain.ui_profile import WebUiProfile

URL = "/ui-api/dashboard"
ZONE_READ: list[tuple[str, int | None]] = [("zone.read", None)]
EVERYTHING: list[tuple[str, int | None]] = [
    ("zone.read", None),
    ("device.read", None),
    ("control.arm", None),
    ("setting.manage", None),
]


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
    """Meldet einen neuen Benutzer an (der Client bleibt derselbe, das Cookie wechselt)."""
    user = user_with_permissions(session, name, permissions, ui_profile=profile)
    _http_session, secret = create_session(session, user, 3600)
    client.cookies.set(COOKIE_NAME, secret)
    return user.id


def _get(client: TestClient) -> Any:
    response = client.get(URL)
    assert response.status_code == 200, response.text
    return response.json()


def _put(client: TestClient, version: int, sections: list[dict[str, Any]]) -> Any:
    return client.put(
        URL, json={"version": version, "sections": sections}, headers=_headers(client)
    )


def _section(title: str, *tiles: dict[str, Any]) -> dict[str, Any]:
    return {"title": title, "tiles": list(tiles)}


def _tiles(body: Any) -> list[dict[str, Any]]:
    return [tile for section in body["sections"] for tile in section["tiles"]]


# -- Anmeldung, Standard ---------------------------------------------------------------


def test_every_method_needs_a_session(client: TestClient) -> None:
    assert client.get(URL).status_code == 401
    assert client.put(URL, json={"version": 0, "sections": []}).status_code == 401
    assert client.delete(URL).status_code == 401


def test_writes_need_the_csrf_header(client: TestClient, session: Session) -> None:
    _login(client, session, "a", ZONE_READ)
    assert client.put(URL, json={"version": 0, "sections": []}).status_code == 403
    assert client.delete(URL).status_code == 403
    assert _get(client)["version"] == 0


def test_default_layout_is_built_from_the_visible_rooms(
    client: TestClient, session: Session
) -> None:
    first, second = create_zone(session, "erster"), create_zone(session, "zweiter")
    _login(client, session, "a", ZONE_READ)
    body = _get(client)
    assert body["version"] == 0
    assert body["customized"] is False
    titles = [section["title"] for section in body["sections"]]
    assert titles == ["Auf einen Blick", "Dein Tag", "Deine Räume"]
    summary, today, rooms = (section["tiles"] for section in body["sections"])
    assert [tile["metric"] for tile in summary] == ["rooms", "heating", "issues", "outdoor"]
    assert [(t["kind"], t["zone_id"]) for t in today] == [("chart", first.id), ("agenda", None)]
    assert [t["zone_id"] for t in rooms] == [first.id, second.id]


def test_default_layout_shows_only_what_the_user_may_see(
    client: TestClient, session: Session
) -> None:
    create_zone(session, "geheim")
    _login(client, session, "ohne", [("device.read", None)])
    body = _get(client)
    assert body["customized"] is False
    assert [section["title"] for section in body["sections"]] == [
        "Auf einen Blick",
        "Dein Tag",
        "Deine Räume",
    ]
    assert _tiles(body) == []
    assert "Geheim" not in str(body)


def test_default_layout_caps_the_room_list(client: TestClient, session: Session) -> None:
    for number in range(MAX_TILES_PER_SECTION + 3):
        create_zone(session, f"raum{number}")
    _login(client, session, "a", ZONE_READ)
    rooms = _get(client)["sections"][2]["tiles"]
    assert len(rooms) == MAX_TILES_PER_SECTION


# -- Speichern und Lesen ---------------------------------------------------------------


def test_save_roundtrip_keeps_order_and_counts_the_version(
    client: TestClient, session: Session
) -> None:
    zone = create_zone(session, "wohnen")
    _login(client, session, "a", EVERYTHING)
    sections = [
        _section(
            "Oben",
            {"kind": "note", "note": "Müll raus", "title": "Merker", "width": 6},
            {"kind": "room", "zone_id": zone.id, "width": 12},
        ),
        _section(
            "Unten",
            {"kind": "chart", "zone_id": zone.id, "period_days": 30, "width": 8},
            {"kind": "metric", "metric": "outdoor", "width": 3},
            {"kind": "shortcut", "destination": "mapping"},
            {"kind": "devices"},
            {"kind": "system"},
        ),
        _section("Leer"),
    ]
    saved = _put(client, 0, sections)
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["version"] == 1
    assert body["customized"] is True
    assert [s["title"] for s in body["sections"]] == ["Oben", "Unten", "Leer"]
    oben, unten, leer = body["sections"]
    assert [t["kind"] for t in oben["tiles"]] == ["note", "room"]
    assert oben["tiles"][0] == {
        "kind": "note",
        "zone_id": None,
        "title": "Merker",
        "period_days": 1,
        "width": 6,
        "metric": None,
        "destination": None,
        "note": "Müll raus",
    }
    assert [t["kind"] for t in unten["tiles"]] == [
        "chart", "metric", "shortcut", "devices", "system",
    ]
    assert unten["tiles"][0]["period_days"] == 30
    assert leer["tiles"] == []
    assert _get(client) == body
    again = _put(client, 1, [_section("Nur eins")])
    assert again.json()["version"] == 2
    assert [s["title"] for s in _get(client)["sections"]] == ["Nur eins"]
    assert session.scalar(select(func.count()).select_from(DashboardSection)) == 1
    assert session.scalar(select(func.count()).select_from(DashboardTile)) == 0


def test_fields_that_do_not_belong_to_the_kind_are_dropped(
    client: TestClient, session: Session
) -> None:
    zone = create_zone(session, "wohnen")
    _login(client, session, "a", EVERYTHING)
    noisy = {
        "kind": "agenda",
        "zone_id": zone.id,
        "metric": "rooms",
        "destination": "schedule",
        "note": "x",
        "period_days": 90,
    }
    (tile,) = _tiles(_put(client, 0, [_section("A", noisy)]).json())
    assert tile["zone_id"] is None
    assert tile["metric"] is None
    assert tile["destination"] is None
    assert tile["note"] == ""
    assert tile["period_days"] == 1


def test_an_empty_layout_is_a_real_layout(client: TestClient, session: Session) -> None:
    _login(client, session, "a", ZONE_READ)
    body = _put(client, 0, []).json()
    assert body == {"version": 1, "customized": True, "sections": []}


# -- Isolation -------------------------------------------------------------------------


def test_layouts_are_separate_per_user(client: TestClient, session: Session) -> None:
    zone = create_zone(session, "wohnen")
    _login(client, session, "anna", ZONE_READ)
    note = {"kind": "note", "note": "nur für Anna"}
    assert _put(client, 0, [_section("Annas", note)]).status_code == 200

    _login(client, session, "ben", ZONE_READ)
    ben = _get(client)
    assert ben["customized"] is False and ben["version"] == 0
    assert "nur für Anna" not in str(ben)
    saved = _put(client, 0, [_section("Bens", {"kind": "room", "zone_id": zone.id})])
    assert saved.status_code == 200
    assert saved.json()["version"] == 1

    assert session.scalar(select(func.count()).select_from(DashboardLayout)) == 2
    titles = session.scalars(select(DashboardSection.title).order_by(DashboardSection.title)).all()
    assert titles == ["Annas", "Bens"]


def test_resetting_does_not_touch_other_users(client: TestClient, session: Session) -> None:
    _login(client, session, "anna", ZONE_READ)
    _put(client, 0, [_section("Annas", {"kind": "note", "note": "bleibt"})])
    _login(client, session, "ben", ZONE_READ)
    _put(client, 0, [_section("Bens")])
    assert client.delete(URL, headers=_headers(client)).status_code == 200
    titles = session.scalars(select(DashboardSection.title)).all()
    assert titles == ["Annas"]


# -- Rechte beim Lesen -----------------------------------------------------------------


def test_revoking_a_zone_right_removes_the_tile_at_once(
    client: TestClient, session: Session
) -> None:
    kept, lost = create_zone(session, "behalten"), create_zone(session, "verloren")
    user_id = _login(client, session, "a", [("zone.read", kept.id), ("zone.read", lost.id)])
    layout = [
        _section(
            "R",
            {"kind": "room", "zone_id": kept.id},
            {"kind": "room", "zone_id": lost.id, "title": "Verloren"},
            {"kind": "chart", "zone_id": lost.id},
        )
    ]
    assert _put(client, 0, layout).status_code == 200
    assert len(_tiles(_get(client))) == 3

    group_id = session.scalar(
        select(UserAccessGroup.access_group_id).where(UserAccessGroup.user_id == user_id)
    )
    session.execute(
        delete(GroupPermission).where(
            GroupPermission.access_group_id == group_id, GroupPermission.zone_id == lost.id
        )
    )
    session.flush()
    body = _get(client)
    assert [t["zone_id"] for t in _tiles(body)] == [kept.id]
    assert "Verloren" not in str(body)
    assert "Verloren" not in client.get(URL).text


def test_revoking_a_function_right_removes_its_tiles(
    client: TestClient, session: Session
) -> None:
    create_zone(session, "wohnen")
    user_id = _login(client, session, "a", EVERYTHING)
    layout = [
        _section(
            "F",
            {"kind": "devices"},
            {"kind": "system"},
            {"kind": "shortcut", "destination": "solar"},
            {"kind": "shortcut", "destination": "operation"},
            {"kind": "shortcut", "destination": "account"},
            {"kind": "metric", "metric": "rooms"},
            {"kind": "note", "note": "bleibt"},
        )
    ]
    saved = _put(client, 0, layout)
    assert saved.status_code == 200, saved.text
    assert len(_tiles(_get(client))) == 7
    group_id = session.scalar(
        select(UserAccessGroup.access_group_id).where(UserAccessGroup.user_id == user_id)
    )
    session.execute(
        delete(GroupPermission).where(GroupPermission.access_group_id == group_id)
    )
    session.flush()
    assert [(t["kind"], t["destination"]) for t in _tiles(_get(client))] == [
        ("shortcut", "account"),
        ("note", None),
    ]


def test_a_tenant_never_gets_plant_tiles(client: TestClient, session: Session) -> None:
    create_zone(session, "wohnen")
    _login(client, session, "mieter", EVERYTHING, WebUiProfile.TENANT)
    for tile in (
        {"kind": "devices"},
        {"kind": "system"},
        {"kind": "shortcut", "destination": "devices"},
    ):
        response = _put(client, 0, [_section("X", tile)])
        assert response.status_code == 422, tile
        assert response.json()["code"] == "validation"
    allowed = _put(client, 0, [_section("X", {"kind": "shortcut", "destination": "schedule"})])
    assert allowed.status_code == 200, allowed.text


# -- Manipulierte Eingabe --------------------------------------------------------------


def test_foreign_and_unknown_zones_are_refused_identically(
    client: TestClient, session: Session
) -> None:
    own, foreign = create_zone(session, "eigen"), create_zone(session, "fremd")
    _login(client, session, "a", [("zone.read", own.id)])
    missing = foreign.id + 1000
    answers = []
    for zone_id in (foreign.id, missing, None):
        response = _put(client, 0, [_section("X", {"kind": "room", "zone_id": zone_id})])
        assert response.status_code == 422
        answers.append(response.json())
    assert answers[0] == answers[1] == answers[2]
    assert answers[0]["code"] == "validation" and answers[0]["field"] == "zone_id"
    assert "Fremd" not in str(answers) and "fremd" not in str(answers)
    # Nichts wurde gespeichert, auch nicht die Version.
    assert _get(client)["version"] == 0
    assert session.scalar(select(func.count()).select_from(DashboardTile)) == 0
    assert _put(client, 0, [_section("X", {"kind": "room", "zone_id": own.id})]).status_code == 200


def test_incomplete_tiles_are_refused(client: TestClient, session: Session) -> None:
    _login(client, session, "a", EVERYTHING)
    for tile, field in (
        ({"kind": "metric"}, "metric"),
        ({"kind": "shortcut"}, "destination"),
    ):
        response = _put(client, 0, [_section("X", tile)])
        assert response.status_code == 422
        assert response.json()["field"] == field


def test_a_function_without_the_right_is_refused(client: TestClient, session: Session) -> None:
    _login(client, session, "a", ZONE_READ)
    for tile in (
        {"kind": "devices"},
        {"kind": "system"},
        {"kind": "shortcut", "destination": "devices"},
        {"kind": "shortcut", "destination": "solar"},
        {"kind": "shortcut", "destination": "operation"},
    ):
        response = _put(client, 0, [_section("X", tile)])
        assert response.status_code == 422, tile
        assert response.json() == {
            "detail": "Diese Kachel ist nicht verfügbar.",
            "code": "validation",
            "field": "kind",
        }


def test_nothing_without_any_zone_right_is_accepted_for_zone_tiles(
    client: TestClient, session: Session
) -> None:
    zone = create_zone(session, "z")
    _login(client, session, "a", [("device.read", None)])
    assert _put(client, 0, [_section("X", {"kind": "agenda"})]).status_code == 422
    assert _put(client, 0, [_section("X", {"kind": "room", "zone_id": zone.id})]).status_code == 422


# -- Grenzen ---------------------------------------------------------------------------


def test_limits_are_exact(client: TestClient, session: Session) -> None:
    _login(client, session, "a", ZONE_READ)
    note = {"kind": "note", "note": "x" * MAX_NOTE_LENGTH, "title": "t" * MAX_TITLE_LENGTH}
    full = [_section("s" * MAX_TITLE_LENGTH, *([note] * MAX_TILES_PER_SECTION))]
    full += [_section(f"s{n}") for n in range(MAX_SECTIONS - 1)]
    saved = _put(client, 0, full)
    assert saved.status_code == 200, saved.text
    assert len(saved.json()["sections"]) == MAX_SECTIONS


@pytest.mark.parametrize(
    "sections",
    [
        [_section(f"s{n}") for n in range(MAX_SECTIONS + 1)],
        [_section("x", *([{"kind": "note"}] * (MAX_TILES_PER_SECTION + 1)))],
        [_section("t" * (MAX_TITLE_LENGTH + 1))],
        [_section("   ")],
        [_section("")],
        [_section("x", {"kind": "note", "title": "t" * (MAX_TITLE_LENGTH + 1)})],
        [_section("x", {"kind": "note", "note": "n" * (MAX_NOTE_LENGTH + 1)})],
        [_section("x", {"kind": "gibtsnicht"})],
        [_section("x", {"kind": "note", "width": 5})],
        [_section("x", {"kind": "note", "period_days": 2})],
        [_section("x", {"kind": "metric", "metric": "kaffee"})],
        [_section("x", {"kind": "shortcut", "destination": "/admin"})],
        [_section("x", {"kind": "note", "extra": 1})],
        [{"title": "x"}],
        [{"title": "x", "tiles": [], "id": 4}],
    ],
)
def test_out_of_bounds_layouts_are_refused(
    client: TestClient, session: Session, sections: list[dict[str, Any]]
) -> None:
    _login(client, session, "a", ZONE_READ)
    response = _put(client, 0, sections)
    assert response.status_code == 422
    assert response.json() == {"detail": "Die Eingabe ist ungültig.", "code": "validation"}
    assert _get(client)["version"] == 0


def test_negative_or_missing_version_is_refused(client: TestClient, session: Session) -> None:
    _login(client, session, "a", ZONE_READ)
    assert _put(client, -1, []).status_code == 422
    response = client.put(URL, json={"sections": []}, headers=_headers(client))
    assert response.status_code == 422


def test_the_enumerations_cover_what_the_schema_allows() -> None:
    assert len(TILE_KINDS) == 10
    assert set(METRICS) == {"rooms", "heating", "issues", "outdoor"}
    assert set(DESTINATIONS) == {
        "schedule", "absence", "history", "devices", "mapping", "solar", "operation", "account",
    }
    assert WIDTHS == (3, 4, 6, 8, 12)
    assert PERIODS_DAYS == (1, 7, 30, 90)


# -- Notizen sind Text -----------------------------------------------------------------


def test_notes_are_text_and_never_interpreted(client: TestClient, session: Session) -> None:
    _login(client, session, "a", ZONE_READ)
    html = '<script>alert("x")</script><img src=x onerror=alert(1)> &amp; {{7*7}}'
    saved = _put(client, 0, [_section("<b>Titel</b>", {"kind": "note", "note": html})])
    assert saved.status_code == 200
    assert saved.headers["content-type"].startswith("application/json")
    (tile,) = _tiles(saved.json())
    assert tile["note"] == html
    assert saved.json()["sections"][0]["title"] == "<b>Titel</b>"
    response = client.get(URL)
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["sections"][0]["tiles"][0]["note"] == html


# -- Version / Konflikt ----------------------------------------------------------------


def test_a_stale_version_is_a_conflict_and_changes_nothing(
    client: TestClient, session: Session
) -> None:
    _login(client, session, "a", ZONE_READ)
    assert _put(client, 0, [_section("Erst")]).status_code == 200
    assert _put(client, 1, [_section("Zweit")]).status_code == 200
    stale = _put(client, 1, [_section("Veraltet")])
    assert stale.status_code == 409
    assert stale.json()["code"] == "conflict"
    assert stale.json()["field"] == "version"
    future = _put(client, 7, [_section("Zukunft")])
    assert future.status_code == 409
    assert [s["title"] for s in _get(client)["sections"]] == ["Zweit"]
    assert _get(client)["version"] == 2


def test_the_first_save_needs_version_zero(client: TestClient, session: Session) -> None:
    _login(client, session, "a", ZONE_READ)
    assert _put(client, 3, [_section("X")]).status_code == 409
    assert _get(client)["version"] == 0


def test_losing_the_race_for_the_first_save_is_a_conflict(
    client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Zwei Erstspeicherungen zugleich: Die zweite sieht noch keine Zeile, scheitert aber
    am Schlüssel -- 409 statt 500."""
    user_id = _login(client, session, "a", ZONE_READ)
    session.add(DashboardLayout(user_id=user_id, version=1, customized=True,
                                updated_at=datetime(2026, 1, 1)))
    session.flush()
    real_get = Session.get

    def blind(self: Session, entity: Any, ident: Any, *args: Any, **kwargs: Any) -> Any:
        if entity is DashboardLayout:
            return None
        return real_get(self, entity, ident, *args, **kwargs)

    monkeypatch.setattr(Session, "get", blind)
    response = _put(client, 0, [_section("X")])
    assert response.status_code == 409
    assert response.json()["code"] == "conflict"


def test_reset_restores_the_default_and_keeps_counting(
    client: TestClient, session: Session
) -> None:
    create_zone(session, "wohnen")
    _login(client, session, "a", ZONE_READ)
    assert _put(client, 0, [_section("Meins", {"kind": "note", "note": "x"})]).status_code == 200
    reset = client.delete(URL, headers=_headers(client))
    assert reset.status_code == 200
    body = reset.json()
    assert body["customized"] is False
    assert body["version"] == 2
    assert [s["title"] for s in body["sections"]][0] == "Auf einen Blick"
    assert session.scalar(select(func.count()).select_from(DashboardSection)) == 0
    # Der Stand von vor dem Zurücksetzen kann nicht mehr schreiben.
    assert _put(client, 1, [_section("Alt")]).status_code == 409
    assert _put(client, 2, [_section("Neu")]).json()["version"] == 3


def test_reset_without_a_stored_layout(client: TestClient, session: Session) -> None:
    _login(client, session, "a", ZONE_READ)
    body = client.delete(URL, headers=_headers(client)).json()
    assert body["customized"] is False and body["version"] == 1
    assert _put(client, 1, [_section("X")]).status_code == 200


# -- Zonen und Kacheln -----------------------------------------------------------------


def test_deleting_a_zone_deletes_its_tiles_only(client: TestClient, session: Session) -> None:
    gone, stays = create_zone(session, "weg"), create_zone(session, "bleibt")
    _login(client, session, "a", ZONE_READ)
    layout = [
        _section(
            "R",
            {"kind": "room", "zone_id": gone.id},
            {"kind": "room", "zone_id": stays.id},
            {"kind": "note", "note": "n"},
        )
    ]
    assert _put(client, 0, layout).status_code == 200
    session.execute(delete(Zone).where(Zone.id == gone.id))
    session.flush()
    kinds = [(t["kind"], t["zone_id"]) for t in _tiles(_get(client))]
    assert kinds == [("room", stays.id), ("note", None)]
    assert session.scalar(select(func.count()).select_from(DashboardTile)) == 2


def test_removing_a_tile_never_deletes_the_zone(client: TestClient, session: Session) -> None:
    zone = create_zone(session, "wohnen")
    _login(client, session, "a", ZONE_READ)
    assert _put(client, 0, [_section("R", {"kind": "room", "zone_id": zone.id})]).status_code == 200
    assert _put(client, 1, [_section("R")]).status_code == 200
    assert client.delete(URL, headers=_headers(client)).status_code == 200
    assert session.get(Zone, zone.id) is not None


def test_a_stored_shortcut_without_target_is_not_shown(
    client: TestClient, session: Session
) -> None:
    """Die Schnittstelle speichert so etwas nie; eine von Hand geänderte Zeile bleibt
    trotzdem unsichtbar, statt einen Fehler zu werfen."""
    user_id = _login(client, session, "a", ZONE_READ)
    assert _put(client, 0, [_section("X", {"kind": "note"})]).status_code == 200
    tile = session.scalars(select(DashboardTile)).one()
    tile.kind = "shortcut"
    session.flush()
    assert _tiles(_get(client)) == []
    assert session.get(DashboardLayout, user_id) is not None
