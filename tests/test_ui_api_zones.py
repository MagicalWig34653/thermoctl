"""Räume anlegen/bearbeiten/löschen, Probleme melden, Glossar unter `/ui-api`.

Geprüft wird je Endpunkt: ohne Sitzung 401, ohne CSRF-Wert 403, ohne Recht 403, fremde und
unbekannte Zone dasselbe 404, die Wirkung in der Datenbank und der Audit-Eintrag. Die
Endpunkte sind dünne Adapter; die Fachprüfung (Namensdopplung, Betriebsart) liegt in der
Domäne bzw. denselben Funktionen wie in den HTML-Views.
"""

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tests.helpers import (
    create_device,
    create_mode,
    create_settings,
    create_zone,
    create_zone_state,
    operating_mode,
    source,
)
from thermoctl.auth.csrf import CSRF_HEADER, csrf_token
from thermoctl.auth.sessions import COOKIE_NAME
from thermoctl.config import get_settings
from thermoctl.db.models.dashboard import DashboardSection, DashboardTile
from thermoctl.db.models.operations import AuditEvent
from thermoctl.db.models.zone import Zone, ZoneSetpoint
from thermoctl.domain.glossary import default_glossary
from thermoctl.domain.problem_report import NOTE_LIMIT, REPORT_KINDS

Als = Callable[[list[tuple[str, int | None]]], TestClient]

MANAGE: list[tuple[str, int | None]] = [("zone.read", None), ("zone.manage", None)]


def _csrf(client: TestClient) -> dict[str, str]:
    http_session = client.cookies.get(COOKIE_NAME)
    assert http_session is not None
    return {CSRF_HEADER: csrf_token(http_session, get_settings().secret_key.get_secret_value())}


def _call(client: TestClient, method: str, url: str, body: dict[str, Any] | None = None) -> Any:
    return client.request(method, url, json=body, headers=_csrf(client))


def _fehler(response: Any, status: int, code: str, field: str | None = None) -> dict[str, Any]:
    assert response.status_code == status, response.text
    body = response.json()
    assert body["code"] == code
    assert isinstance(body["detail"], str) and body["detail"]
    if field is not None:
        assert body["field"] == field
    return body  # type: ignore[no-any-return]


def _anlage(session: Session) -> Zone:
    source(session, "web")
    create_settings(session)
    operating_mode(session, "auto")
    operating_mode(session, "off")
    return create_zone(session, "bad")


def _audit(session: Session, action: str) -> list[AuditEvent]:
    return list(session.scalars(select(AuditEvent).where(AuditEvent.action == action)))


def _count(session: Session, model: type) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


NEU = {"name": "kueche", "display_name": "Küche", "operating_mode": "auto", "sort_order": 3}


# -- Anmeldung, CSRF, Rechte, Profil -------------------------------------------------------


def test_every_endpoint_needs_a_session(client: TestClient, session: Session) -> None:
    zone = _anlage(session)
    for method, url in (
        ("GET", "/ui-api/room-form"),
        ("GET", "/ui-api/room-orders"),
        ("POST", "/ui-api/rooms"),
        ("PUT", f"/ui-api/rooms/{zone.id}"),
        ("GET", f"/ui-api/rooms/{zone.id}/delete-impact"),
        ("DELETE", f"/ui-api/rooms/{zone.id}"),
        ("GET", "/ui-api/report-options"),
        ("POST", f"/ui-api/rooms/{zone.id}/report"),
        ("GET", "/ui-api/glossary"),
    ):
        assert client.request(method, url).status_code == 401, (method, url)
    assert _count(session, Zone) == 1


@pytest.mark.parametrize(
    ("method", "url", "body"),
    [
        ("POST", "/ui-api/rooms", NEU),
        ("PUT", "/ui-api/rooms/{id}", {"display_name": "Neu"}),
        ("DELETE", "/ui-api/rooms/{id}", None),
        ("POST", "/ui-api/rooms/{id}/report", {"kind": "not_warm"}),
    ],
)
def test_writes_without_csrf_are_403_and_change_nothing(
    client_als: Als,
    session: Session,
    method: str,
    url: str,
    body: dict[str, Any] | None,
) -> None:
    zone = _anlage(session)
    all_rights = [*MANAGE, ("report.create", None)]
    response = client_als(all_rights).request(
        method, url.format(id=zone.id), json=body, headers={CSRF_HEADER: "falsch"}
    )
    assert response.status_code == 403
    assert response.json()["stale"] is True
    session.refresh(zone)
    assert zone.display_name == "Bad"
    assert _count(session, Zone) == 1
    assert _audit(session, "create") == [] and _audit(session, "report.created") == []


@pytest.mark.parametrize(
    ("method", "url", "body"),
    [
        ("GET", "/ui-api/room-form", None),
        ("GET", "/ui-api/room-form?zone_id={id}", None),
        ("POST", "/ui-api/rooms", NEU),
        ("PUT", "/ui-api/rooms/{id}", {"display_name": "Neu"}),
        ("GET", "/ui-api/rooms/{id}/delete-impact", None),
        ("DELETE", "/ui-api/rooms/{id}", None),
    ],
)
def test_management_needs_zone_manage_and_changes_nothing_without_it(
    client_als: Als, session: Session, method: str, url: str, body: dict[str, Any] | None
) -> None:
    zone = _anlage(session)
    response = _call(client_als([("zone.read", None)]), method, url.format(id=zone.id), body)
    _fehler(response, 403, "forbidden")
    session.refresh(zone)
    assert zone.display_name == "Bad"
    assert _count(session, Zone) == 1


@pytest.mark.parametrize(
    ("method", "url", "body"),
    [
        ("GET", "/ui-api/room-form", None),
        ("GET", "/ui-api/room-form?zone_id={id}", None),
        ("POST", "/ui-api/rooms", NEU),
        ("PUT", "/ui-api/rooms/{id}", {"display_name": "Neu"}),
        ("GET", "/ui-api/rooms/{id}/delete-impact", None),
        ("DELETE", "/ui-api/rooms/{id}", None),
    ],
)
def test_management_belongs_to_the_plant_view(
    tenant_client: Als, session: Session, method: str, url: str, body: dict[str, Any] | None
) -> None:
    zone = _anlage(session)
    response = _call(tenant_client(MANAGE), method, url.format(id=zone.id), body)
    _fehler(response, 403, "wrong_profile")
    session.refresh(zone)
    assert zone.display_name == "Bad"
    assert _count(session, Zone) == 1


@pytest.mark.parametrize(
    ("method", "url", "body"),
    [
        ("GET", "/ui-api/room-form?zone_id={id}", None),
        ("PUT", "/ui-api/rooms/{id}", {"display_name": "Neu"}),
        ("GET", "/ui-api/rooms/{id}/delete-impact", None),
        ("DELETE", "/ui-api/rooms/{id}", None),
        ("POST", "/ui-api/rooms/{id}/report", {"kind": "not_warm"}),
    ],
)
def test_invisible_and_unknown_zone_are_the_identical_404(
    client_als: Als, session: Session, method: str, url: str, body: dict[str, Any] | None
) -> None:
    mine = _anlage(session)
    other = create_zone(session, "fremd")
    client = client_als(
        [("zone.read", mine.id), ("zone.manage", mine.id), ("report.create", mine.id)]
    )
    hidden = _call(client, method, url.format(id=other.id), body)
    unknown = _call(client, method, url.format(id=999999), body)
    assert hidden.status_code == unknown.status_code == 404
    assert hidden.json() == unknown.json()
    assert hidden.json()["code"] == "not_found"
    session.refresh(other)
    assert other.display_name == "Fremd"
    assert _count(session, Zone) == 2


def test_manage_on_another_zone_does_not_reach_this_zone(
    client_als: Als, session: Session
) -> None:
    mine = _anlage(session)
    other = create_zone(session, "fremd")
    client = client_als(
        [("zone.read", None), ("zone.manage", other.id)]
    )
    put = _call(client, "PUT", f"/ui-api/rooms/{mine.id}", {"display_name": "X"})
    _fehler(put, 403, "forbidden")
    _fehler(_call(client, "DELETE", f"/ui-api/rooms/{mine.id}"), 403, "forbidden")
    # Das Anlegen ist anlagenweit: Recht nur für eine Zone genügt nicht.
    _fehler(_call(client, "POST", "/ui-api/rooms", NEU), 403, "forbidden")
    own = _call(client, "PUT", f"/ui-api/rooms/{other.id}", {"display_name": "Ok"})
    assert own.status_code == 200


# -- Formular --------------------------------------------------------------------------------


def test_form_options_for_a_new_room(client_als: Als, session: Session) -> None:
    _anlage(session)
    create_device(session, "geraet-1")
    body = client_als(MANAGE).get("/ui-api/room-form").json()
    assert body["room"] is None
    assert [m["code"] for m in body["operating_modes"]] == ["auto", "off"]
    assert [d["id"] for d in body["devices"]] != []
    assert body["name_limit"] == 64 and body["display_name_limit"] == 128


def test_form_options_carry_the_current_values_of_an_existing_room(
    client_als: Als, session: Session
) -> None:
    zone = _anlage(session)
    zone.sort_order = 7
    session.flush()
    room = client_als(MANAGE).get(f"/ui-api/room-form?zone_id={zone.id}").json()["room"]
    assert room == {
        "id": zone.id,
        "name": "bad",
        "display_name": "Bad",
        "sort_order": 7,
        "operating_mode": "auto",
        "temperature_source_device_id": None,
    }


# -- Anlegen ---------------------------------------------------------------------------------


def test_create_writes_the_zone_and_the_audit_entry(client_als: Als, session: Session) -> None:
    _anlage(session)
    response = _call(client_als(MANAGE), "POST", "/ui-api/rooms", NEU)
    assert response.status_code == 201, response.text
    body = response.json()
    zone = session.scalars(select(Zone).where(Zone.name == "kueche")).one()
    assert body["id"] == zone.id and body["display_name"] == "Küche"
    assert (zone.sort_order, zone.operating_mode.code) == (3, "auto")
    entry = _audit(session, "create")[-1]
    assert (entry.object_type, entry.object_id) == ("zone", str(zone.id))
    assert entry.summary == "Zone kueche angelegt"


def test_create_trims_and_accepts_numbers_as_text_and_a_known_source_device(
    client_als: Als, session: Session
) -> None:
    _anlage(session)
    device = create_device(session, "fuehler")
    response = _call(
        client_als(MANAGE),
        "POST",
        "/ui-api/rooms",
        {
            "name": "  flur ",
            "display_name": " Flur ",
            "operating_mode": "off",
            "sort_order": "-2",
            "temperature_source_device_id": str(device.id),
        },
    )
    assert response.status_code == 201, response.text
    zone = session.scalars(select(Zone).where(Zone.name == "flur")).one()
    assert (zone.display_name, zone.sort_order) == ("Flur", -2)
    assert zone.temperature_source_device_id == device.id


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"name": "  "}, "name"),
        ({"name": "x" * 65}, "name"),
        ({"display_name": ""}, "display_name"),
        ({"display_name": "x" * 129}, "display_name"),
        ({"operating_mode": ""}, "operating_mode"),
        ({"operating_mode": "unsinn"}, "operating_mode"),
        ({"sort_order": "abc"}, "sort_order"),
        ({"sort_order": 2**31}, "sort_order"),
        ({"temperature_source_device_id": "abc"}, "temperature_source_device_id"),
        ({"temperature_source_device_id": 999999}, "temperature_source_device_id"),
    ],
)
def test_create_reports_field_errors_and_writes_nothing(
    client_als: Als, session: Session, change: dict[str, Any], field: str
) -> None:
    _anlage(session)
    _fehler(
        _call(client_als(MANAGE), "POST", "/ui-api/rooms", {**NEU, **change}),
        422,
        "validation",
        field,
    )
    assert _count(session, Zone) == 1
    assert _audit(session, "create") == []


def test_create_with_a_taken_name_is_a_409_and_writes_nothing(
    client_als: Als, session: Session
) -> None:
    _anlage(session)
    _fehler(
        _call(client_als(MANAGE), "POST", "/ui-api/rooms", {**NEU, "name": "bad"}),
        409,
        "name_taken",
        "name",
    )
    assert _count(session, Zone) == 1
    assert _audit(session, "create") == []


def test_create_with_a_non_numeric_body_is_a_generic_422(
    client_als: Als, session: Session
) -> None:
    _anlage(session)
    _fehler(
        _call(client_als(MANAGE), "POST", "/ui-api/rooms", {**NEU, "name": ["liste"]}),
        422,
        "validation",
    )
    assert _count(session, Zone) == 1


# -- Speichern -------------------------------------------------------------------------------


def test_save_changes_the_sent_fields_only_and_audits(
    client_als: Als, session: Session
) -> None:
    zone = _anlage(session)
    device = create_device(session, "fuehler")
    zone.temperature_source_device_id = device.id
    session.flush()
    response = _call(
        client_als(MANAGE),
        "PUT",
        f"/ui-api/rooms/{zone.id}",
        {"display_name": " Badezimmer ", "sort_order": 5},
    )
    assert response.status_code == 200, response.text
    session.refresh(zone)
    assert (zone.name, zone.display_name, zone.sort_order) == ("bad", "Badezimmer", 5)
    assert zone.operating_mode.code == "auto"
    assert zone.temperature_source_device_id == device.id
    assert response.json()["display_name"] == "Badezimmer"
    entry = _audit(session, "update")[-1]
    assert (entry.object_type, entry.object_id) == ("zone", str(zone.id))


def test_save_changes_every_field_and_can_clear_the_source_device(
    client_als: Als, session: Session
) -> None:
    zone = _anlage(session)
    device = create_device(session, "fuehler")
    zone.temperature_source_device_id = device.id
    session.flush()
    response = _call(
        client_als(MANAGE),
        "PUT",
        f"/ui-api/rooms/{zone.id}",
        {
            "name": "wc",
            "display_name": "WC",
            "operating_mode": "off",
            "sort_order": "9",
            "temperature_source_device_id": None,
        },
    )
    assert response.status_code == 200, response.text
    session.refresh(zone)
    assert (zone.name, zone.display_name, zone.sort_order) == ("wc", "WC", 9)
    assert zone.operating_mode.code == "off"
    assert zone.temperature_source_device_id is None


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"name": None}, "name"),
        ({"name": "x" * 65}, "name"),
        ({"display_name": ""}, "display_name"),
        ({"operating_mode": "unsinn"}, "operating_mode"),
        ({"sort_order": "abc"}, "sort_order"),
        ({"temperature_source_device_id": 999999}, "temperature_source_device_id"),
    ],
)
def test_save_reports_field_errors_and_changes_nothing(
    client_als: Als, session: Session, change: dict[str, Any], field: str
) -> None:
    zone = _anlage(session)
    _fehler(
        _call(client_als(MANAGE), "PUT", f"/ui-api/rooms/{zone.id}", change),
        422,
        "validation",
        field,
    )
    session.refresh(zone)
    assert (zone.name, zone.display_name, zone.sort_order) == ("bad", "Bad", 0)
    assert _audit(session, "update") == []


def test_save_with_a_name_of_another_room_is_a_409_and_changes_nothing(
    client_als: Als, session: Session
) -> None:
    zone = _anlage(session)
    create_zone(session, "kueche")
    _fehler(
        _call(client_als(MANAGE), "PUT", f"/ui-api/rooms/{zone.id}", {"name": "kueche"}),
        409,
        "name_taken",
        "name",
    )
    session.refresh(zone)
    assert zone.name == "bad"
    assert _audit(session, "update") == []


def test_save_keeping_its_own_name_is_not_a_conflict(client_als: Als, session: Session) -> None:
    zone = _anlage(session)
    response = _call(
        client_als(MANAGE), "PUT", f"/ui-api/rooms/{zone.id}", {"name": "bad", "sort_order": 1}
    )
    assert response.status_code == 200


# -- Löschen ---------------------------------------------------------------------------------


def test_delete_impact_counts_what_goes_with_the_room(
    client_als: Als, session: Session
) -> None:
    zone = _anlage(session)
    mode = create_mode(session, "tag", "Komfort")
    session.add(ZoneSetpoint(zone_id=zone.id, setpoint_mode_id=mode.id, temperature_c=21))
    session.flush()
    body = client_als(MANAGE).get(f"/ui-api/rooms/{zone.id}/delete-impact").json()
    assert body == {
        "id": zone.id,
        "name": "bad",
        "display_name": "Bad",
        "schedule_points": 0,
        "devices": 0,
        "setpoints": 1,
        "overrides": 0,
        "shadow_decisions": 0,
    }


def test_delete_removes_the_room_and_its_tiles_but_not_other_rooms(
    client_als: Als, session: Session
) -> None:
    zone = _anlage(session)
    other = create_zone(session, "bleibt")
    client = client_als(MANAGE)
    from tests.helpers import user_with_permissions

    owner = user_with_permissions(session, "kachel", [("zone.read", None)])
    section = DashboardSection(user_id=owner.id, position=0, title="Test")
    session.add(section)
    session.flush()
    for position, target in enumerate((zone, other)):
        session.add(
            DashboardTile(
                section_id=section.id,
                position=position,
                kind="room",
                zone_id=target.id,
                title="Raum",
                period_days=7,
                width=4,
                note="",
            )
        )
    session.flush()
    session.expire_all()
    zone_id = zone.id
    response = _call(client, "DELETE", f"/ui-api/rooms/{zone_id}")
    assert response.status_code == 200, response.text
    assert response.json() == {"id": zone_id, "deleted": True}
    session.expire_all()
    assert session.get(Zone, zone_id) is None
    assert session.get(Zone, other.id) is not None
    tiles = list(session.scalars(select(DashboardTile)))
    assert [tile.zone_id for tile in tiles] == [other.id]
    entry = _audit(session, "delete")[-1]
    assert (entry.object_type, entry.object_id) == ("zone", str(zone_id))
    assert entry.summary == "Zone bad gelöscht"
    _fehler(_call(client, "DELETE", f"/ui-api/rooms/{zone_id}"), 404, "not_found")


# -- Probleme melden ---------------------------------------------------------------------------


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> Any:
    captured: list[Any] = []

    async def _deliver(_factory: Any, _settings: Any, notice: Any) -> None:
        captured.append(notice)

    monkeypatch.setattr("thermoctl.integrations.notification.deliver", _deliver)
    monkeypatch.setenv("THERMOCTL_NOTIFY_WEBHOOK", "https://example.invalid/hook")
    get_settings.cache_clear()
    yield captured
    get_settings.cache_clear()


def _meldbar(session: Session) -> Zone:
    zone = _anlage(session)
    create_zone_state(session, zone)
    return zone


def test_report_options_list_kinds_and_only_reportable_rooms(
    client_als: Als, session: Session
) -> None:
    zone = _meldbar(session)
    create_zone(session, "fremd")
    body = client_als([("zone.read", None), ("report.create", zone.id)]).get(
        "/ui-api/report-options"
    ).json()
    assert [(k["code"], k["label"]) for k in body["kinds"]] == list(REPORT_KINDS)
    assert body["note_limit"] == NOTE_LIMIT
    assert body["rooms"] == [{"id": zone.id, "name": "Bad"}]


def test_report_options_without_the_right_are_empty(client_als: Als, session: Session) -> None:
    _meldbar(session)
    body = client_als([("zone.read", None)]).get("/ui-api/report-options").json()
    assert body["rooms"] == []


def test_report_goes_out_and_into_the_audit(
    tenant_client: Als, session: Session, sent: list[Any]
) -> None:
    zone = _meldbar(session)
    client = tenant_client([("zone.read", zone.id), ("report.create", zone.id)])
    response = _call(
        client,
        "POST",
        f"/ui-api/rooms/{zone.id}/report",
        {"kind": "not_warm", "note": "seit gestern"},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"message": "Die Meldung wurde weitergegeben."}
    assert len(sent) == 1 and "Bad" in sent[0].title
    entry = _audit(session, "report.created")[0]
    assert entry.object_id == str(zone.id)
    assert "seit gestern" in (entry.detail or "")


def test_report_without_a_webhook_is_taken_but_honestly_not_forwarded(
    client_als: Als, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    zone = _meldbar(session)
    monkeypatch.delenv("THERMOCTL_NOTIFY_WEBHOOK", raising=False)
    get_settings.cache_clear()
    response = _call(
        client_als([("zone.read", zone.id), ("report.create", zone.id)]),
        "POST",
        f"/ui-api/rooms/{zone.id}/report",
        {"kind": "too_warm"},
    )
    assert response.status_code == 200, response.text
    assert "nicht eingerichtet" in response.json()["message"]
    assert len(_audit(session, "report.created")) == 1


def test_report_with_an_unknown_kind_is_a_field_error_and_sends_nothing(
    client_als: Als, session: Session, sent: list[Any]
) -> None:
    zone = _meldbar(session)
    _fehler(
        _call(
            client_als([("zone.read", zone.id), ("report.create", zone.id)]),
            "POST",
            f"/ui-api/rooms/{zone.id}/report",
            {"kind": "unsinn"},
        ),
        422,
        "validation",
        "kind",
    )
    assert sent == [] and _audit(session, "report.created") == []


def test_report_needs_report_create_not_just_reading(
    client_als: Als, session: Session, sent: list[Any]
) -> None:
    zone = _meldbar(session)
    _fehler(
        _call(
            client_als([("zone.read", zone.id)]),
            "POST",
            f"/ui-api/rooms/{zone.id}/report",
            {"kind": "not_warm"},
        ),
        403,
        "forbidden",
    )
    assert sent == [] and _audit(session, "report.created") == []


def test_report_is_open_to_both_profiles_but_carries_nothing_of_other_rooms(
    client_als: Als, session: Session, sent: list[Any]
) -> None:
    zone = _meldbar(session)
    other = create_zone(session, "geheim")
    other.display_name = "Nachbars Zimmer"
    session.flush()
    response = _call(
        client_als([("zone.read", None), ("report.create", zone.id)]),
        "POST",
        f"/ui-api/rooms/{zone.id}/report",
        {"kind": "other", "note": "x" * (NOTE_LIMIT + 50)},
    )
    assert response.status_code == 200
    assert "Nachbars Zimmer" not in sent[0].text
    _fehler(
        _call(
            client_als([("zone.read", None), ("report.create", zone.id)]),
            "POST",
            f"/ui-api/rooms/{other.id}/report",
            {"kind": "other"},
        ),
        403,
        "forbidden",
    )


# -- Glossar -----------------------------------------------------------------------------------


def test_glossary_is_the_domain_glossary_for_every_login(
    client_als: Als, tenant_client: Als, session: Session
) -> None:
    source = default_glossary()
    for make in (client_als, tenant_client):
        body = make([]).get("/ui-api/glossary").json()
        assert len(body["entries"]) == len(source.entries) > 0
        assert body["letters"] == list(source.letters())
        first = source.entries[0]
        assert body["entries"][0] == {
            "id": first.id,
            "term": first.term,
            "short": first.short,
            "explanation": first.explanation,
            "area": first.area,
            "letter": first.letter,
            "synonyms": list(first.synonyms),
            "see_also": list(first.see_also),
        }
        assert {e["area"] for e in body["entries"]} <= set(body["areas"])


# -- Sortierwerte der Räume-Liste ---------------------------------------------------------------


def test_room_orders_list_only_visible_rooms_in_list_order(
    client_als: Als, session: Session
) -> None:
    source(session, "web")
    zweiter = create_zone(session, "zweiter")
    erster = create_zone(session, "erster")
    verdeckt = create_zone(session, "verdeckt")
    zweiter.sort_order = 7
    erster.sort_order = -2
    verdeckt.sort_order = 1
    session.flush()
    body = client_als([("zone.read", erster.id), ("zone.read", zweiter.id)]).get(
        "/ui-api/room-orders"
    )
    assert body.status_code == 200
    assert body.json() == {
        "rooms": [
            {"id": erster.id, "sort_order": -2},
            {"id": zweiter.id, "sort_order": 7},
        ]
    }
    assert client_als([]).get("/ui-api/room-orders").json() == {"rooms": []}


def test_room_orders_belong_to_the_plant_view(tenant_client: Als, session: Session) -> None:
    zone = _anlage(session)
    _fehler(
        tenant_client([("zone.read", zone.id)]).get("/ui-api/room-orders"), 403, "wrong_profile"
    )


# -- Verdrahtung -------------------------------------------------------------------------------


def test_zones_router_is_csrf_protected_and_hidden_from_the_schema() -> None:
    from thermoctl.auth.dependencies import csrf_protection
    from thermoctl.web.ui_api_zones import router

    assert csrf_protection in [d.dependency for d in router.dependencies]
    assert not router.include_in_schema
    assert router.prefix == "/ui-api"
