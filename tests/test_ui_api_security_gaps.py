"""Rechte-, Sichtbarkeits-, Profil- und Kioskgrenzen der neuen Oberfläche (`/ui-api`, `/kiosk-api`).

Schließt die Lücken aus `docs/ui-migration/LOESCHPLAN.md` Abschnitt 8, die die abgelösten
HTML-Tests geprüft hatten und die in keinem neuen Test mehr vorkamen: Namenslecks nach
Rechteentzug, fremde Zonen mit ungültiger Eingabe (nie 422 -- das wäre ein Existenzhinweis),
ein von außen nicht umschaltbares Profil, das Kiosk-Cookie an jeder Route, Navigation nach
Recht und Profil, Token-Vorlagen mit Zonenbindung und die leeren Fälle (kein Raum, keine Zone).
"""

# ruff: noqa: E501

import re
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.helpers import (
    alle_api_routen,
    create_all_permissions,
    create_mode,
    create_settings,
    create_zone,
    source,
    user_with_permissions,
    zone_with_schedule,
)
from thermoctl.app import create_app
from thermoctl.auth.csrf import CSRF_HEADER, csrf_token
from thermoctl.auth.kiosk import KIOSK_COOKIE_NAME, KIOSK_CSRF_COOKIE_NAME
from thermoctl.auth.sessions import COOKIE_NAME
from thermoctl.config import get_settings
from thermoctl.db.models.credential import ApiToken, ApiTokenPermission
from thermoctl.db.models.identity import GroupPermission
from thermoctl.db.models.lookup import PERMISSIONS, Permission
from thermoctl.db.models.zone import SetpointMode, Zone
from thermoctl.domain.kiosk import issue_kiosk_token
from thermoctl.domain.ui_profile import WebUiProfile
from thermoctl.web.navigation import NAVIGATION_ITEMS

Als = Callable[[list[tuple[str, int | None]]], TestClient]

ALL_CODES: list[tuple[str, int | None]] = [(code, None) for code, _d, _z in PERMISSIONS]


@pytest.fixture(autouse=True)
def _base(session: Session) -> None:
    source(session, "web")
    source(session, "kiosk")
    create_all_permissions(session)


def _csrf(client: TestClient) -> dict[str, str]:
    cookie = client.cookies.get(COOKIE_NAME)
    assert cookie is not None
    return {CSRF_HEADER: csrf_token(cookie, get_settings().secret_key.get_secret_value())}


def _call(
    client: TestClient,
    method: str,
    url: str,
    body: dict[str, Any] | None = None,
    **kwargs: Any,
) -> Any:
    return client.request(method, url, json=body, headers=_csrf(client), **kwargs)


# -- (1) Namensleck im Abwesenheits-Banner -------------------------------------------------


def _two_rooms(session: Session) -> tuple[Zone, Zone]:
    first = zone_with_schedule(
        session, "wohnzimmer", [(1, 420, "tag", Decimal("21")), (1, 1320, "nacht", Decimal("17"))]
    )
    second = create_zone(session, "schlafzimmer")
    return first, second


def _withdraw(session: Session, code: str, zone: Zone) -> None:
    removed = (
        session.query(GroupPermission)
        .filter(
            GroupPermission.zone_id == zone.id,
            GroupPermission.permission_id == session.scalar(
                select(Permission.id).where(Permission.code == code)
            ),
        )
        .delete(synchronize_session=False)
    )
    assert removed == 1
    session.flush()


def test_a_room_taken_away_afterwards_no_longer_appears_in_the_absence_banner(
    tenant_client: Als, session: Session
) -> None:
    kept, taken = _two_rooms(session)
    client = tenant_client(
        [(code, z.id) for z in (kept, taken) for code in ("zone.read", "override.create")]
    )
    started = _call(
        client, "POST", "/ui-api/absence", {"temperature_c": "16", "return_on": "2030-01-01"}
    )
    assert started.status_code == 200, started.text
    assert {z["name"] for z in started.json()["absence"]["zones"]} == {
        kept.display_name,
        taken.display_name,
    }

    # Danach wird dem Konto zone.read auf dem Schlafzimmer entzogen (override.create bleibt).
    _withdraw(session, "zone.read", taken)

    shown = client.get("/ui-api/absence")
    assert shown.status_code == 200, shown.text
    assert [z["name"] for z in shown.json()["absence"]["zones"]] == [kept.display_name]
    assert taken.display_name not in shown.text
    ended = _call(client, "POST", "/ui-api/absence/end")
    assert ended.status_code == 200 and taken.display_name not in ended.text


def test_an_absence_without_any_readable_room_names_none(
    tenant_client: Als, session: Session
) -> None:
    kept, taken = _two_rooms(session)
    client = tenant_client([(c, z.id) for z in (kept, taken) for c in ("zone.read", "override.create")])
    _call(client, "POST", "/ui-api/absence", {"temperature_c": "16", "return_on": "2030-01-01"})
    _withdraw(session, "zone.read", kept)
    _withdraw(session, "zone.read", taken)
    body = client.get("/ui-api/absence").json()
    assert body["absence"]["zones"] == []
    assert kept.display_name not in str(body) and taken.display_name not in str(body)


# -- (2) Fremde Zone mit ungültiger Eingabe: 404 wie bei unbekannter Zone -----------------

_BAD_QUERY = {
    "from": "kaputt",
    "to": "kaputt",
    "period": "kaputt",
    "resolution": "kaputt",
    "day": "kaputt",
    "week": "kaputt",
    "version": "kaputt",
}
_BAD_BODIES: list[dict[str, Any]] = [
    {},
    {"unerwartet": 1},
    {
        "version": "x", "day": "x", "weekday": "x", "minute": "x", "minute_of_day": "x",
        "mode_id": "x", "mode_code": "x", "temperature_c": "x", "temperature": "x",
        "direction": "x", "device_id": "x", "assignment_id": "x", "role_code": "x",
        "thermostat_mode": "x", "action_code": "x", "from_day": "x", "to_days": "x",
        "to_weekday": "x", "target_zone_ids": "x", "from": "x", "to": "x", "period": "x",
        "start": "x", "end": "x", "ends_at": "x", "name": 1, "code": 1,
    },
]
_MANAGER_CODES = (
    "zone.read", "zone.manage", "schedule.manage", "setpoint.write", "override.create",
    "override.cancel", "device.read", "device.manage", "audit.read", "mode.manage",
)


def _zone_routes() -> list[APIRoute]:
    routes = [
        r
        for r in alle_api_routen(create_app())
        if r.path.startswith("/ui-api") and "{zone_id}" in r.path
    ]
    assert len(routes) >= 30, "Die Aufzählung der Zonenrouten ist unerwartet klein."
    return routes


def _sample(schema: dict[str, Any], defs: dict[str, Any]) -> Any:
    """Ein dem Typ nach gültiger, dem Inhalt nach unsinniger Wert zum JSON-Schema."""
    if "$ref" in schema:
        return _sample(defs[schema["$ref"].rsplit("/", 1)[-1]], defs)
    for key in ("anyOf", "oneOf"):
        if key in schema:
            options = [o for o in schema[key] if o.get("type") != "null"]
            return _sample(options[0], defs) if options else None
    kind = schema.get("type")
    if kind == "string":
        return "kaputt"
    if kind == "integer":
        return 99999
    if kind == "number":
        return -1.5
    if kind == "boolean":
        return False
    if kind == "array":
        return []
    if kind == "object" or "properties" in schema:
        return {
            name: _sample(prop, defs) for name, prop in schema.get("properties", {}).items()
        }
    return None


def _shaped_body(route: APIRoute) -> dict[str, Any] | None:
    field = route.body_field
    if field is None:
        return None
    schema = field.field_info.annotation.model_json_schema()  # type: ignore[union-attr]
    sample = _sample(schema, schema.get("$defs", {}))
    return sample if isinstance(sample, dict) else None


def _url(route: APIRoute, zone_id: int) -> str:
    path = route.path.replace("{zone_id}", str(zone_id))
    return re.sub(r"\{[a-z_]+\}", "1", path)


def _normalised(response: Any, *ids: int) -> tuple[int, str]:
    text = response.text
    for number in ids:
        text = re.sub(rf"(?<!\d){number}(?!\d)", "<id>", text)
    return response.status_code, text


def test_a_foreign_zone_with_invalid_input_is_the_same_answer_as_an_unknown_zone(
    client_als: Als, session: Session
) -> None:
    """Sichtbarkeit zuerst, dann Eingabe: nie 422, nie ein anderer Wortlaut als bei einer
    Zone, die es nicht gibt -- sonst verrät die Antwort, dass die Zone existiert."""
    create_settings(session)
    mine = create_zone(session, "meine")
    foreign = create_zone(session, "fremde")
    unknown = 99999
    client = client_als([(code, mine.id) for code in _MANAGER_CODES])
    checked = 0
    deviations: list[str] = []
    statuses: list[int] = []
    for route in _zone_routes():
        for method in sorted(m for m in route.methods if m not in {"HEAD", "OPTIONS"}):
            shaped = _shaped_body(route)
            variants: list[dict[str, Any] | None] = (
                [None] if method == "GET" else [*_BAD_BODIES, *([shaped] if shaped else [])]
            )
            for body in variants:
                params = _BAD_QUERY
                answers = []
                for zone_id in (foreign.id, unknown):
                    response = _call(
                        client, method, _url(route, zone_id), body, params=params
                    )
                    answers.append(_normalised(response, foreign.id, unknown))
                checked += 1
                statuses.append(answers[0][0])
                if answers[0] != answers[1]:
                    deviations.append(
                        f"{method} {route.path} body={sorted(body or {})[:3]}: "
                        f"fremd={answers[0][0]} unbekannt={answers[1][0]}"
                    )
    assert checked >= 100
    # Nicht leer gelaufen: fremde Zonen enden als 404 (Sichtbarkeit zuerst) oder schon an
    # der Formprüfung des Körpers (422, für jede Zone gleich) -- nie als 200 oder 403.
    assert set(statuses) <= {404, 422}, sorted(set(statuses))
    assert statuses.count(404) >= 40
    assert not deviations, "\n".join(deviations)


_VALID_RANGE = {"from": "2026-01-01T00:00:00+00:00", "to": "2026-01-02T00:00:00+00:00"}


@pytest.mark.parametrize("path", ["/ui-api/history", "/ui-api/history.csv"])
@pytest.mark.parametrize(
    "bad",
    [
        {"from": "kaputt", "to": "kaputt"},
        {"from": "kaputt"},
        {"to": "kaputt"},
        {"from": "2026-01-02T00:00:00+00:00", "to": "2026-01-01T00:00:00+00:00"},
        {"from": "1900-01-01T00:00:00+00:00", "to": "2999-01-01T00:00:00+00:00"},
        {"resolution": 0},
        {"resolution": -5},
        {"resolution": 99999999},
    ],
)
def test_history_of_a_foreign_zone_stays_404_with_invalid_input(
    client_als: Als, session: Session, path: str, bad: dict[str, Any]
) -> None:
    create_settings(session)
    mine = create_zone(session, "meine")
    foreign = create_zone(session, "fremde")
    client = client_als([("zone.read", mine.id)])
    answers = [
        _normalised(
            client.get(path, params={"zone_id": zone_id, **_VALID_RANGE, **bad}),
            foreign.id,
            99999,
        )
        for zone_id in (foreign.id, 99999)
    ]
    assert answers[0][0] == 404 and answers[0] == answers[1]


def test_history_input_that_fastapi_itself_rejects_is_the_same_for_every_zone(
    client_als: Als, session: Session
) -> None:
    """`resolution=kaputt` scheitert schon an der Typprüfung -- für fremde, unbekannte und
    eigene Zonen gleich, also ohne Existenzhinweis."""
    create_settings(session)
    mine = create_zone(session, "meine")
    foreign = create_zone(session, "fremde")
    client = client_als([("zone.read", mine.id)])
    for path in ("/ui-api/history", "/ui-api/history.csv"):
        answers = [
            _normalised(
                client.get(path, params={"zone_id": z, **_VALID_RANGE, "resolution": "kaputt"}),
                foreign.id,
                99999,
                mine.id,
            )
            for z in (foreign.id, 99999, mine.id)
        ]
        assert answers[0] == answers[1] == answers[2]
        assert answers[0][0] == 422


@pytest.mark.parametrize(
    "bad", [{"from": "kaputt"}, {"to": "kaputt"}, {"from": "x", "to": "y"}, {"weekday": "x"}]
)
def test_schedule_preview_of_a_foreign_zone_stays_404_with_invalid_input(
    client_als: Als, session: Session, bad: dict[str, str]
) -> None:
    create_settings(session)
    mine = create_zone(session, "meine")
    foreign = create_zone(session, "fremde")
    client = client_als([("zone.read", mine.id)])
    answers = [
        _normalised(
            client.get(f"/ui-api/schedule/{zone_id}/preview", params=bad), foreign.id, 99999
        )
        for zone_id in (foreign.id, 99999)
    ]
    assert answers[0][0] == 404 and answers[0] == answers[1]


# -- (3) Das Profil lässt sich von außen nicht umschalten --------------------------------


@pytest.mark.parametrize("own", ["tenant", "admin"])
def test_the_profile_cannot_be_switched_from_the_client(
    own: str, client_als: Als, tenant_client: Als, session: Session
) -> None:
    create_settings(session)
    zone = create_zone(session, "Bad")
    maker = tenant_client if own == "tenant" else client_als
    client = maker([("zone.read", zone.id), ("override.create", zone.id)])
    other = "admin" if own == "tenant" else "tenant"
    plain = client.get("/ui-api/bootstrap").json()
    assert plain["ui_profile"] == own

    client.cookies.set("ui_profile", other)
    try:
        for headers, params in (
            ({}, {"ui_profile": other}),
            ({"X-UI-Profile": other}, {}),
            ({"X-UI-Profile": other}, {"ui_profile": other}),
            ({}, {}),  # nur das Cookie
        ):
            body = client.get("/ui-api/bootstrap", headers=headers, params=params).json()
            assert body["ui_profile"] == own, (headers, params)
            assert body["navigation"] == plain["navigation"]
            # Das Profil sperrt nichts: beide Konten erreichen die Abwesenheit, und die
            # Anlagenübersicht bleibt allein am fehlenden Recht (`device.read`) zu.
            absence = client.get("/ui-api/absence", headers=headers, params=params)
            plant = client.get("/ui-api/plant", headers=headers, params=params)
            assert absence.status_code == 200
            assert plant.status_code == 403
            assert plant.json()["code"] == "forbidden"
    finally:
        client.cookies.delete("ui_profile")


# -- (4) Das Kiosk-Cookie erreicht keine /ui-api-Route ---------------------------------------

_PUBLIC_WITHOUT_SESSION = {
    ("POST", "/ui-api/login"),
    ("GET", "/ui-api/login-options"),
    ("GET", "/ui-api/setup"),
    ("POST", "/ui-api/setup"),
    ("POST", "/ui-api/logout"),
}


def _all_ui_api_routes() -> list[tuple[str, str]]:
    found = sorted(
        (method, route.path)
        for route in alle_api_routen(create_app())
        if route.path.startswith("/ui-api")
        for method in route.methods
        if method not in {"HEAD", "OPTIONS"}
    )
    assert len(found) >= 100
    return found


def test_a_kiosk_cookie_reaches_no_ui_api_route(client: TestClient, session: Session) -> None:
    create_settings(session)
    zone = create_zone(session, "Wohnen")
    owner = user_with_permissions(session, "besitzer", ALL_CODES)
    _token, plaintext = issue_kiosk_token(
        session, owner, "tablet", [zone.id], control_allowed=True, expires_at=None
    )
    secret = get_settings().secret_key.get_secret_value()
    routes = _all_ui_api_routes()
    unauthenticated: set[tuple[str, str]] = set()
    client.cookies.clear()
    for method, path in routes:
        url = re.sub(r"\{[a-z_]+\}", "1", path)
        if client.request(method, url, json={}).status_code != 401:
            unauthenticated.add((method, path))
    # Ohne jedes Cookie bleiben nur die ausdrücklich öffentlichen Routen (und die
    # CSRF-Sonde, die vor der Anmeldung nichts preisgibt) übrig.
    assert unauthenticated <= _PUBLIC_WITHOUT_SESSION | {("POST", "/ui-api/csrf-probe")}, sorted(
        unauthenticated
    )

    client.cookies.set(KIOSK_COOKIE_NAME, plaintext)
    client.cookies.set(KIOSK_CSRF_COOKIE_NAME, csrf_token(plaintext, secret))
    for method, path in routes:
        if (method, path) in unauthenticated:
            continue
        url = re.sub(r"\{[a-z_]+\}", "1", path)
        for headers in (
            {},
            {"Authorization": f"Bearer {plaintext}"},
            {CSRF_HEADER: csrf_token(plaintext, secret)},
        ):
            response = client.request(method, url, json={}, headers=headers)
            assert response.status_code == 401, (method, path, response.status_code)


def test_the_kiosk_token_of_an_issuer_with_a_tenant_profile_still_serves_the_state(
    client: TestClient, session: Session
) -> None:
    create_settings(session)
    zone = create_zone(session, "Flur")
    issuer = user_with_permissions(
        session, "mieter-aussteller", [("zone.read", None), ("token.manage", None)],
        ui_profile=WebUiProfile.TENANT,
    )
    zone_with_schedule  # noqa: B018 - Zone ohne Plan genügt für die Zustandsliste
    _token, plaintext = issue_kiosk_token(
        session, issuer, "tablet", [zone.id], control_allowed=False, expires_at=None
    )
    client.cookies.clear()
    client.cookies.set(KIOSK_COOKIE_NAME, plaintext)
    response = client.get("/kiosk-api/state")
    assert response.status_code == 200, response.text
    assert [room["id"] for room in response.json()["rooms"]] == [zone.id]


# -- (5) Bediengeräte, Schnittstellen, Sollwertschritt -----------------------------------


def test_controllers_need_device_read_not_just_zone_read(client_als: Als, session: Session) -> None:
    create_settings(session)
    create_zone(session, "bad")
    answer = client_als([("zone.read", None)]).get("/ui-api/controllers")
    assert answer.status_code == 403 and answer.json()["code"] == "forbidden"
    # Mit device.read (hier zonengebunden) geht es auf.
    zone = create_zone(session, "flur")
    assert client_als([("device.read", zone.id), ("zone.read", zone.id)]).get(
        "/ui-api/controllers"
    ).status_code == 200


def test_homebridge_zones_are_empty_without_zone_read(client_als: Als, session: Session) -> None:
    create_settings(session)
    create_zone(session, "geheimzimmer")
    response = client_als([("setting.manage", None)]).get("/ui-api/interfaces")
    assert response.status_code == 200, response.text
    assert response.json()["homebridge_zones"] == []
    assert "Geheimzimmer" not in response.text and "geheimzimmer" not in response.text


def test_plant_wide_zone_read_shows_every_zone_in_the_homebridge_part(
    client_als: Als, session: Session
) -> None:
    create_settings(session)
    zones = [create_zone(session, "bad"), create_zone(session, "flur")]
    body = client_als([("setting.manage", None), ("zone.read", None)]).get("/ui-api/interfaces").json()
    assert sorted(z["zone_id"] for z in body["homebridge_zones"]) == sorted(z.id for z in zones)


def test_a_global_and_a_room_grant_together_show_each_room_once(
    client_als: Als, session: Session
) -> None:
    create_settings(session)
    zones = [create_zone(session, "bad"), create_zone(session, "flur")]
    client = client_als(
        [("setting.manage", None), ("zone.read", None), ("zone.read", zones[0].id)]
    )
    ids = [z["zone_id"] for z in client.get("/ui-api/interfaces").json()["homebridge_zones"]]
    assert sorted(ids) == sorted(z.id for z in zones)
    assert [z["id"] for z in client.get("/ui-api/bootstrap").json()["zones"]].count(zones[0].id) == 1
    rooms = [r["id"] for r in client.get("/ui-api/rooms").json()["rooms"]]
    assert sorted(rooms) == sorted(z.id for z in zones)


def test_the_setpoint_step_action_follows_setpoint_write_of_that_room_alone(
    client_als: Als, session: Session
) -> None:
    create_settings(session)
    writable, other = create_zone(session, "schreibbar"), create_zone(session, "nur-lesbar")
    client = client_als(
        [
            ("zone.read", None),
            ("setpoint.write", writable.id),
            # Andere Rechte derselben Zone bringen den Sollwertschritt nicht mit.
            ("override.create", other.id),
            ("schedule.manage", other.id),
            ("zone.manage", other.id),
        ]
    )
    rooms = {r["id"]: r["actions"] for r in client.get("/ui-api/rooms").json()["rooms"]}
    assert rooms[writable.id]["edit_setpoint_step"] is True
    assert rooms[writable.id]["edit_mode_setpoints"] is True
    assert rooms[writable.id]["override"] is False
    assert rooms[other.id]["edit_setpoint_step"] is False
    assert rooms[other.id]["edit_mode_setpoints"] is False
    assert rooms[other.id]["override"] is True


# -- (6) Navigation nach Recht, Zonenbindung und Profil ---------------------------------


def _paths(client: TestClient) -> set[str]:
    return {item["path"] for item in client.get("/ui-api/bootstrap").json()["navigation"]}


def test_every_account_with_every_right_gets_every_item_whatever_its_profile(
    client_als: Als, tenant_client: Als
) -> None:
    """Allein die Rechte entscheiden: Anlagen- und Mieterprofil sehen dasselbe."""
    everything = {i.path for i in NAVIGATION_ITEMS}
    assert len(everything) >= 15
    assert _paths(client_als(ALL_CODES)) == everything
    assert _paths(tenant_client(ALL_CODES)) == everything
    assert "/temperature-history" in everything


def test_a_tenant_profile_sees_the_plant_items_its_rights_allow(tenant_client: Als) -> None:
    assert _paths(tenant_client([("zone.read", None)])).isdisjoint({"/users", "/audit", "/devices"})
    assert "/users" in _paths(tenant_client([("zone.read", None), ("user.manage", None)]))


def test_zone_scoped_zone_read_shows_the_zone_list_but_not_the_plant_wide_pages(
    client_als: Als, session: Session
) -> None:
    zone = create_zone(session, "bad")
    paths = _paths(client_als([("zone.read", zone.id)]))
    assert "/zones" in paths
    assert {"/control", "/settings", "/vacation", "/statistics"}.isdisjoint(paths)


def test_zone_scoped_device_read_shows_the_controllers_but_not_the_device_overview(
    client_als: Als, session: Session
) -> None:
    zone = create_zone(session, "bad")
    paths = _paths(client_als([("device.read", zone.id)]))
    assert {"/controllers", "/devices/map"} <= paths
    assert "/devices" not in paths


# -- (7) Token-Vorlagen mit Zonenbindung ------------------------------------------------


def test_zone_scoped_permission_can_be_selected_without_widening(
    client_als: Als, session: Session
) -> None:
    zone = create_zone(session, "bad")
    create_zone(session, "flur")
    client = client_als([("token.self", None), ("zone.read", zone.id), ("setpoint.write", None)])
    body = client.get("/ui-api/access/tokens").json()
    offered = {(g["code"], g["zone_id"]) for g in body["available"]}
    assert ("zone.read", zone.id) in offered
    assert ("zone.read", None) not in offered
    assert ("setpoint.write", None) in offered
    # Die Vorlagen bieten nur anlagenweit gehaltene Rechte an -- nie das zonengebundene.
    assert all("zone.read" not in codes for codes in body["presets"].values())

    issued = _call(
        client,
        "POST",
        "/ui-api/access/tokens",
        {"name": "bad-nur", "permissions": [{"code": "zone.read", "zone_id": zone.id}]},
    )
    assert issued.status_code == 201, issued.text
    stored = session.execute(
        select(Permission.code, ApiTokenPermission.zone_id)
        .join(ApiTokenPermission, ApiTokenPermission.permission_id == Permission.id)
        .join(ApiToken, ApiToken.id == ApiTokenPermission.api_token_id)
        .where(ApiToken.name == "bad-nur")
    ).all()
    assert [tuple(row) for row in stored] == [("zone.read", zone.id)]
    assert issued.json()["token"]["permissions"] == [{"code": "zone.read", "zone_id": zone.id}]


# -- (8) Leere Fälle ------------------------------------------------------------------------


def test_relay_wear_with_audit_read_but_no_visible_zone_is_an_empty_list(
    client_als: Als, session: Session
) -> None:
    create_settings(session)
    create_zone(session, "unsichtbar")
    response = client_als([("audit.read", None)]).get("/ui-api/relay-wear")
    assert response.status_code == 200, response.text
    assert response.json()["devices"] == []
    assert "Unsichtbar" not in response.text


def test_the_forecast_is_visible_with_only_zone_read(client_als: Als, session: Session) -> None:
    zone = zone_with_schedule(
        session, "wohnzimmer", [(1, 420, "tag", Decimal("21")), (1, 1320, "nacht", Decimal("17"))]
    )
    response = client_als([("zone.read", zone.id)]).get(f"/ui-api/schedule/{zone.id}/preview")
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("profile", ["tenant", "admin"])
def test_an_account_without_any_room_gets_empty_lists_not_an_error(
    profile: str, client_als: Als, tenant_client: Als, session: Session
) -> None:
    create_settings(session)
    create_zone(session, "fremd")
    client = (tenant_client if profile == "tenant" else client_als)([])
    boot = client.get("/ui-api/bootstrap")
    assert boot.status_code == 200 and boot.json()["zones"] == []
    rooms = client.get("/ui-api/rooms")
    assert rooms.status_code == 200 and rooms.json()["rooms"] == []
    assert "Fremd" not in boot.text + rooms.text


def test_the_schedule_view_can_tell_which_room_is_editable(
    client_als: Als, session: Session
) -> None:
    create_settings(session)
    read_only, editable = create_zone(session, "a-lesbar"), create_zone(session, "b-bearbeitbar")
    client = client_als(
        [("zone.read", read_only.id), ("zone.read", editable.id), ("schedule.manage", editable.id)]
    )
    flags = {z["id"]: z["can_edit_schedule"] for z in client.get("/ui-api/bootstrap").json()["zones"]}
    assert flags == {read_only.id: False, editable.id: True}


# -- (9) Eingebaute Modi sind nicht löschbar -----------------------------------------------


def test_a_builtin_mode_cannot_be_deleted_and_says_why(client_als: Als, session: Session) -> None:
    create_settings(session)
    mode = create_mode(session, "tag", "Tag", eingebaut=True)
    client = client_als([("mode.manage", None)])
    listed = {m["code"]: m for m in client.get("/ui-api/modes").json()["modes"]}
    assert listed["tag"]["is_builtin"] is True
    assert "weil die Anwendung sie benötigt" in listed["tag"]["delete_lock"]
    refused = _call(client, "DELETE", f"/ui-api/modes/{mode.id}")
    assert refused.status_code == 409, refused.text
    assert refused.json()["code"] == "mode_locked"
    assert "weil die Anwendung sie benötigt" in refused.json()["detail"]
    assert session.get(SetpointMode, mode.id) is not None
