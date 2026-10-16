"""Zeitplan, Vorschau, Urlaub, Abwesenheit und Modi unter `/ui-api`: Rechte, Profile,
Fehlerform, Konflikte, DB-Wirkung, Audit und Sommerzeit."""

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.helpers import create_zone, source, zone_with_schedule
from thermoctl.auth.csrf import CSRF_HEADER, csrf_token
from thermoctl.auth.sessions import COOKIE_NAME
from thermoctl.config import get_settings
from thermoctl.db.models.absence import Absence
from thermoctl.db.models.change_log import ChangeLog
from thermoctl.db.models.operations import AuditEvent
from thermoctl.db.models.override import ZoneOverride
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.vacation import Vacation
from thermoctl.db.models.zone import SetpointMode, Zone

Als = Callable[[list[tuple[str, int | None]]], TestClient]

PLAN: list[tuple[str, int | None]] = [
    ("zone.read", None),
    ("schedule.manage", None),
    ("vacation.manage", None),
    ("mode.manage", None),
    ("override.create", None),
]


def _csrf(client: TestClient) -> dict[str, str]:
    http_session = client.cookies.get(COOKIE_NAME)
    assert http_session is not None
    return {CSRF_HEADER: csrf_token(http_session, get_settings().secret_key.get_secret_value())}


def _call(client: TestClient, method: str, url: str, body: dict[str, Any] | None = None) -> Any:
    if method == "GET":
        return client.get(url)
    return client.request(method, url, json=body, headers=_csrf(client))


def _mode(session: Session, code: str) -> SetpointMode:
    return session.query(SetpointMode).filter_by(code=code).one()


def _raum(session: Session) -> Zone:
    """Mo-So 07:00 Tag (21 Grad) und 22:00 Nacht (17 Grad), Zeitzone Europe/Berlin."""
    source(session, "web")
    zone = zone_with_schedule(
        session,
        "wohnzimmer",
        [(1, 420, "tag", Decimal("21.0")), (1, 1320, "nacht", Decimal("17.0"))],
    )
    for day in range(2, 8):
        for minute, code in ((420, "tag"), (1320, "nacht")):
            session.add(
                SchedulePoint(
                    zone_id=zone.id,
                    weekday=day,
                    minute_of_day=minute,
                    setpoint_mode_id=_mode(session, code).id,
                )
            )
    session.flush()
    return zone


def _fehler(response: Any, status: int, code: str) -> dict[str, Any]:
    assert response.status_code == status, response.text
    body = response.json()
    assert body["code"] == code
    assert isinstance(body["detail"], str) and body["detail"]
    return body  # type: ignore[no-any-return]


def _points(session: Session, zone: Zone) -> list[tuple[int, int, int]]:
    session.flush()
    return sorted(
        (p.weekday, p.minute_of_day, p.setpoint_mode_id)
        for p in session.query(SchedulePoint).filter_by(zone_id=zone.id)
    )


def _audit(session: Session, object_type: str) -> int:
    return session.query(AuditEvent).filter_by(object_type=object_type).count()


def _version(client: TestClient, zone: Zone) -> str:
    return str(client.get(f"/ui-api/schedule/{zone.id}").json()["version"])


def _jetzt(monkeypatch: pytest.MonkeyPatch, moment: datetime) -> None:
    monkeypatch.setattr("thermoctl.web.ui_api_schedule.utcnow", lambda: moment)


# Alle Endpunkte: (Methode, Pfad-Vorlage, Körper, Profil, nötiges Recht, schreibend).
# `{z}` Raum, `{p}` Schaltpunkt, `{m}` Modus.
Tabelle = list[tuple[str, str, dict[str, Any] | None, str, str, bool]]
ENDPUNKTE: Tabelle = [
    ("GET", "/ui-api/schedule/{z}", None, "beide", "zone.read", False),
    ("GET", "/ui-api/schedule/{z}/preview", None, "beide", "zone.read", False),
    (
        "POST",
        "/ui-api/schedule/{z}/paint",
        {"version": "0", "weekday": 1, "start_minute": 60, "end_minute": 120, "mode_id": 0},
        "admin",
        "schedule.manage",
        True,
    ),
    (
        "POST",
        "/ui-api/schedule/{z}/points",
        {"version": "0", "weekday": 1, "minute_of_day": 60, "mode_id": 0},
        "admin",
        "schedule.manage",
        True,
    ),
    (
        "PUT",
        "/ui-api/schedule/{z}/points/{p}",
        {"version": "0", "weekday": 1, "minute_of_day": 61},
        "admin",
        "schedule.manage",
        True,
    ),
    (
        "PUT",
        "/ui-api/schedule/{z}/points/{p}/mode",
        {"version": "0", "mode_id": 0},
        "admin",
        "schedule.manage",
        True,
    ),
    ("DELETE", "/ui-api/schedule/{z}/points/{p}?version=0", None, "admin", "schedule.manage", True),
    (
        "POST",
        "/ui-api/schedule/{z}/copy-day",
        {"version": "0", "weekday": 1, "scope": "all"},
        "beide",
        "schedule.manage",
        True,
    ),
    (
        "POST",
        "/ui-api/schedule/{z}/copy-week",
        {"version": "0", "targets": []},
        "beide",
        "zone.read",
        True,
    ),
    ("POST", "/ui-api/schedule/{z}/undo", {"undo_token": "x"}, "beide", "schedule.manage", True),
    ("GET", "/ui-api/vacation", None, "admin", "zone.read", False),
    (
        "POST",
        "/ui-api/vacation",
        {"start_date": "2030-03-30", "end_date": "2030-04-01", "setback_temperature_c": "16.0"},
        "admin",
        "vacation.manage",
        True,
    ),
    ("DELETE", "/ui-api/vacation", None, "admin", "vacation.manage", True),
    ("GET", "/ui-api/absence", None, "mieter", "", False),
    (
        "POST",
        "/ui-api/absence",
        {"temperature_c": "16.0", "return_on": "2030-01-01"},
        "mieter",
        "override.create",
        True,
    ),
    ("POST", "/ui-api/absence/end", None, "mieter", "", True),
    ("GET", "/ui-api/modes", None, "admin", "mode.manage", False),
    ("POST", "/ui-api/modes", {"code": "neu", "name": "Neu"}, "admin", "mode.manage", True),
    ("PUT", "/ui-api/modes/{m}", {"code": "neu", "name": "Neu"}, "admin", "mode.manage", True),
    ("DELETE", "/ui-api/modes/{m}", None, "admin", "mode.manage", True),
]


def _url(session: Session, zone: Zone, template: str) -> str:
    point = session.query(SchedulePoint).filter_by(zone_id=zone.id).first()
    return template.format(
        z=zone.id, p=point.id if point is not None else 0, m=_mode(session, "tag").id
    )


@pytest.mark.parametrize("index", range(len(ENDPUNKTE)))
def test_every_endpoint_without_session_is_json_401(
    client: TestClient, session: Session, index: int
) -> None:
    zone = _raum(session)
    method, template, body, *_ = ENDPUNKTE[index]
    response = client.request(method, _url(session, zone, template), json=body)
    assert response.status_code == 401
    assert response.json() == {"detail": "Nicht angemeldet"}


@pytest.mark.parametrize("index", [i for i, e in enumerate(ENDPUNKTE) if e[5]])
def test_writes_without_csrf_are_403_and_change_nothing(
    client_als: Als, session: Session, index: int
) -> None:
    zone = _raum(session)
    method, template, body, *_ = ENDPUNKTE[index]
    before = (_points(session, zone), session.query(AuditEvent).count())
    client = client_als(PLAN)
    response = client.request(
        method, _url(session, zone, template), json=body, headers={CSRF_HEADER: "falsch"}
    )
    assert response.status_code == 403
    assert response.json()["stale"] is True
    assert (_points(session, zone), session.query(AuditEvent).count()) == before


# Ohne `zone.read` ist ein Raum nicht sichtbar (404, eigener Test); ein 403 gibt es dort nur
# für die raumlosen Anlagenseiten.
@pytest.mark.parametrize(
    "index",
    [
        i
        for i, e in enumerate(ENDPUNKTE)
        if e[3] != "mieter" and e[4] and not (e[4] == "zone.read" and "{z}" in e[1])
    ],
)
def test_missing_permission_is_403_and_changes_nothing(
    client_als: Als, session: Session, index: int
) -> None:
    zone = _raum(session)
    method, template, body, _profil, perm, _w = ENDPUNKTE[index]
    granted = [p for p in PLAN if p[0] != perm]
    if perm != "zone.read":
        granted.append(("zone.read", None))
    before = (_points(session, zone), session.query(AuditEvent).count())
    _fehler(
        _call(client_als(granted), method, _url(session, zone, template), body), 403, "forbidden"
    )
    assert (_points(session, zone), session.query(AuditEvent).count()) == before


@pytest.mark.parametrize("index", [i for i, e in enumerate(ENDPUNKTE) if "{z}" in e[1]])
def test_hidden_and_unknown_zone_are_identical_404(
    client_als: Als, session: Session, index: int
) -> None:
    zone = _raum(session)
    other = create_zone(session, "fremd")
    method, template, body, *_ = ENDPUNKTE[index]
    client = client_als([(code, other.id) for code, _ in PLAN])
    hidden = _call(client, method, _url(session, zone, template), body)
    unknown = _call(client, method, template.format(z=999999, p=1, m=1), body)
    assert hidden.status_code == unknown.status_code == 404
    assert hidden.json() == unknown.json()
    assert hidden.json()["code"] == "not_found"


@pytest.mark.parametrize(
    "index", [i for i, e in enumerate(ENDPUNKTE) if e[3] in {"admin", "mieter"}]
)
def test_wrong_profile_is_403(
    client_als: Als, tenant_client: Als, session: Session, index: int
) -> None:
    zone = _raum(session)
    method, template, body, profil, *_ = ENDPUNKTE[index]
    client = tenant_client(PLAN) if profil == "admin" else client_als(PLAN)
    before = (_points(session, zone), session.query(AuditEvent).count())
    _fehler(_call(client, method, _url(session, zone, template), body), 403, "wrong_profile")
    assert (_points(session, zone), session.query(AuditEvent).count()) == before


def test_incomplete_setup_is_503(client_als: Als, session: Session) -> None:
    zone = create_zone(session, "leer")
    client = client_als(PLAN)
    _fehler(client.get(f"/ui-api/schedule/{zone.id}"), 503, "setup_incomplete")
    _fehler(client.get(f"/ui-api/schedule/{zone.id}/preview"), 503, "setup_incomplete")
    _fehler(client.get("/ui-api/vacation"), 503, "setup_incomplete")


def test_malformed_body_is_422_with_code(client_als: Als, session: Session) -> None:
    zone = _raum(session)
    client = client_als(PLAN)
    _fehler(
        _call(client, "POST", f"/ui-api/schedule/{zone.id}/paint", {"version": "0"}),
        422,
        "validation",
    )


# -- Lesen -------------------------------------------------------------------------------


def test_show_schedule_delivers_points_segments_modes_version(
    client_als: Als, session: Session
) -> None:
    zone = _raum(session)
    other = create_zone(session, "bad")
    body = client_als(PLAN).get(f"/ui-api/schedule/{zone.id}").json()
    assert body["zone_id"] == zone.id
    assert body["timezone"] == "Europe/Berlin"
    assert body["version"] == "0"
    assert len(body["points"]) == 14
    assert {"id", "weekday", "minute_of_day", "mode_id"} <= set(body["points"][0])
    monday = [s for s in body["segments"] if s["weekday"] == 1]
    assert [(s["start_minute"], s["end_minute"]) for s in monday] == [
        (0, 420),
        (420, 1320),
        (1320, 1440),
    ]
    assert monday[0]["mode_name"] == "Nacht"  # Ring: Sonntag 22:00 gilt bis Montag 07:00
    assert monday[0]["point_id"] is None
    assert monday[1]["point_id"] is not None
    assert monday[1]["temperature_c"] == "21.0"
    assert monday[1]["warmth"] > monday[0]["warmth"]
    assert {m["code"] for m in body["modes"]} >= {"tag", "nacht"}
    assert body["can_edit"] is True and body["full_editor"] is True
    assert body["copy_targets"] == [
        {"id": other.id, "name": other.display_name, "version": "0", "has_plan": False}
    ]


def test_read_only_and_tenant_views_flags(
    client_als: Als, tenant_client: Als, session: Session
) -> None:
    zone = _raum(session)
    read_only = client_als([("zone.read", None)]).get(f"/ui-api/schedule/{zone.id}").json()
    assert read_only["can_edit"] is False and read_only["full_editor"] is False
    assert read_only["copy_targets"] == []
    tenant = tenant_client(PLAN).get(f"/ui-api/schedule/{zone.id}").json()
    assert tenant["can_edit"] is True and tenant["full_editor"] is False


def test_empty_plan_has_no_segments(client_als: Als, session: Session) -> None:
    zone = _raum(session)
    session.query(SchedulePoint).filter_by(zone_id=zone.id).delete()
    body = client_als(PLAN).get(f"/ui-api/schedule/{zone.id}").json()
    assert body["points"] == [] and body["segments"] == []


def _segmente(client: TestClient, zone: Zone) -> list[dict[str, Any]]:
    response = client.get(f"/ui-api/schedule/{zone.id}/preview")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["horizon_hours"] == 24
    segments: list[dict[str, Any]] = body["segments"]
    for left, right in zip(segments, segments[1:], strict=False):
        assert left["ends_at"] == right["starts_at"]
    assert sum(s["duration_minutes"] for s in segments) == 24 * 60  # echte Stunden
    return segments


def test_preview_over_midnight_and_week_ring(
    client_als: Als, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    zone = _raum(session)
    _jetzt(monkeypatch, datetime(2026, 8, 30, 21, 30))  # So 23:30 Ortszeit (MESZ)
    segments = _segmente(client_als(PLAN), zone)
    assert [(s["mode_name"], s["starts_at"]) for s in segments] == [
        ("Nacht", "2026-08-30T21:30:00+00:00"),
        ("Tag", "2026-08-31T05:00:00+00:00"),  # Mo 07:00 Ortszeit, Sonntag->Montag-Ring
        ("Nacht", "2026-08-31T20:00:00+00:00"),  # Mo 22:00 Ortszeit
    ]
    assert segments[1]["local_starts_at"] == "2026-08-31T07:00:00+02:00"
    assert segments[0]["warmth"] < segments[1]["warmth"]
    assert segments[1]["temperature_c"] == "21.0"


def test_preview_across_spring_forward(
    client_als: Als, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sa 28.03.2026 13:00 MEZ bis So 29.03. 13:00 MESZ: der Tag hat 23 Stunden, das
    Fenster aber 24 echte."""
    zone = _raum(session)
    _jetzt(monkeypatch, datetime(2026, 3, 28, 12, 0))
    segments = _segmente(client_als(PLAN), zone)
    assert [s["starts_at"] for s in segments] == [
        "2026-03-28T12:00:00+00:00",
        "2026-03-28T21:00:00+00:00",  # Sa 22:00 MEZ
        "2026-03-29T05:00:00+00:00",  # So 07:00 MESZ (nicht 06:00 UTC)
    ]
    assert [s["local_starts_at"] for s in segments][1:] == [
        "2026-03-28T22:00:00+01:00",
        "2026-03-29T07:00:00+02:00",
    ]


def test_preview_across_fall_back(
    client_als: Als, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    zone = _raum(session)
    _jetzt(monkeypatch, datetime(2026, 10, 24, 12, 0))
    segments = _segmente(client_als(PLAN), zone)
    assert [s["starts_at"] for s in segments] == [
        "2026-10-24T12:00:00+00:00",
        "2026-10-24T20:00:00+00:00",  # Sa 22:00 MESZ
        "2026-10-25T06:00:00+00:00",  # So 07:00 MEZ
    ]
    # Die Nacht ist wegen der doppelten Stunde 10 echte Stunden lang, nicht 9.
    assert segments[1]["duration_minutes"] == 600


def test_point_in_the_spring_gap_is_stored_as_given_and_previewed_consistently(
    client_als: Als, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """So 02:30 gibt es am 29.03.2026 nicht. Der Punkt bleibt, wie eingegeben, im Plan
    (Ortszeit, keine UTC-Umrechnung beim Speichern); die Vorschau schaltet zum ersten
    Zeitpunkt danach (03:30 MESZ = 01:30 UTC), nie rückwärts."""
    zone = _raum(session)
    client = client_als(PLAN)
    response = _call(
        client,
        "POST",
        f"/ui-api/schedule/{zone.id}/points",
        {"version": "0", "weekday": 7, "minute_of_day": 150, "mode_id": _mode(session, "tag").id},
    )
    assert response.status_code == 200, response.text
    assert (7, 150, _mode(session, "tag").id) in _points(session, zone)
    _jetzt(monkeypatch, datetime(2026, 3, 28, 23, 0))
    segments = _segmente(client, zone)
    assert [(s["mode_name"], s["starts_at"]) for s in segments] == [
        ("Nacht", "2026-03-28T23:00:00+00:00"),
        ("Tag", "2026-03-29T01:30:00+00:00"),
        ("Nacht", "2026-03-29T20:00:00+00:00"),
    ]


def test_point_in_the_repeated_autumn_hour_switches_at_its_first_occurrence(
    client_als: Als, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    zone = _raum(session)
    client = client_als(PLAN)
    _call(
        client,
        "POST",
        f"/ui-api/schedule/{zone.id}/points",
        {"version": "0", "weekday": 7, "minute_of_day": 150, "mode_id": _mode(session, "tag").id},
    )
    _jetzt(monkeypatch, datetime(2026, 10, 24, 23, 0))
    segments = _segmente(client, zone)
    assert [(s["mode_name"], s["starts_at"]) for s in segments] == [
        ("Nacht", "2026-10-24T23:00:00+00:00"),
        ("Tag", "2026-10-25T00:30:00+00:00"),  # 02:30 MESZ, erstes Vorkommen
        ("Nacht", "2026-10-25T21:00:00+00:00"),  # 22:00 MEZ
    ]


def test_preview_shows_override_without_mode(
    client_als: Als, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    zone = _raum(session)
    session.add(
        ZoneOverride(
            zone_id=zone.id,
            temperature_c=Decimal("23.0"),
            starts_at=datetime(2026, 9, 2, 6, 0),
            ends_at=datetime(2026, 9, 2, 10, 0),
            source_id=source(session).id,
        )
    )
    session.flush()
    _jetzt(monkeypatch, datetime(2026, 9, 2, 8, 0))
    segments = _segmente(client_als(PLAN), zone)
    assert segments[0]["temperature_c"] == "23.0"
    assert segments[0]["mode_id"] is None and segments[0]["mode_name"] is None
    assert segments[0]["ends_at"] == "2026-09-02T10:00:00+00:00"


# -- Bereich setzen, Undo, Konflikt ------------------------------------------------------


def test_paint_changes_plan_audits_and_undo_restores(client_als: Als, session: Session) -> None:
    zone = _raum(session)
    client = client_als(PLAN)
    nacht = _mode(session, "nacht").id
    before = _points(session, zone)
    response = _call(
        client,
        "POST",
        f"/ui-api/schedule/{zone.id}/paint",
        {"version": "0", "weekday": 2, "start_minute": 600, "end_minute": 720, "mode_id": nacht},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["changed"] is True and body["undo_token"]
    assert body["version"] != "0"
    after = _points(session, zone)
    assert after != before
    assert (2, 600, nacht) in after
    assert _audit(session, "schedule") == 1
    tuesday = [s for s in body["segments"] if s["weekday"] == 2]
    assert any(s["start_minute"] == 600 and s["end_minute"] == 720 for s in tuesday)
    undone = _call(
        client, "POST", f"/ui-api/schedule/{zone.id}/undo", {"undo_token": body["undo_token"]}
    )
    assert undone.status_code == 200, undone.text
    assert undone.json()["changed"] is True and undone.json()["undo_token"] is None
    assert _points(session, zone) == before
    assert _audit(session, "schedule") == 2


def test_paint_same_mode_is_a_noop_without_undo(client_als: Als, session: Session) -> None:
    zone = _raum(session)
    client = client_als(PLAN)
    body = _call(
        client,
        "POST",
        f"/ui-api/schedule/{zone.id}/paint",
        {
            "version": "0",
            "weekday": 2,
            "start_minute": 480,
            "end_minute": 600,
            "mode_id": _mode(session, "tag").id,
        },
    ).json()
    assert body["changed"] is False and body["undo_token"] is None and body["version"] == "0"
    assert _audit(session, "schedule") == 0


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"weekday": 9}, "weekday"),
        ({"start_minute": 700, "end_minute": 600}, "time_range"),  # über Mitternacht
        ({"start_minute": 1500}, "time_range"),
        ({"mode_id": 999999}, "mode_id"),
    ],
)
def test_paint_rejects_invalid_intent_without_change(
    client_als: Als, session: Session, changes: dict[str, int], field: str
) -> None:
    zone = _raum(session)
    before = _points(session, zone)
    body = {
        "version": "0",
        "weekday": 2,
        "start_minute": 600,
        "end_minute": 720,
        "mode_id": _mode(session, "nacht").id,
    } | changes
    error = _fehler(
        _call(client_als(PLAN), "POST", f"/ui-api/schedule/{zone.id}/paint", body),
        422,
        "validation",
    )
    assert error["field"] == field
    assert _points(session, zone) == before


def test_stale_version_is_409_for_every_schedule_write(client_als: Als, session: Session) -> None:
    zone = _raum(session)
    client = client_als(PLAN)
    nacht = _mode(session, "nacht").id
    first = _call(
        client,
        "POST",
        f"/ui-api/schedule/{zone.id}/paint",
        {"version": "0", "weekday": 3, "start_minute": 600, "end_minute": 720, "mode_id": nacht},
    ).json()
    assert first["version"] != "0"
    point = session.query(SchedulePoint).filter_by(zone_id=zone.id).first()
    assert point is not None
    stale = "0"
    base = f"/ui-api/schedule/{zone.id}"
    attempts = [
        (
            "POST",
            f"{base}/paint",
            {
                "version": stale,
                "weekday": 4,
                "start_minute": 60,
                "end_minute": 120,
                "mode_id": nacht,
            },
        ),
        (
            "POST",
            f"{base}/points",
            {"version": stale, "weekday": 4, "minute_of_day": 61, "mode_id": nacht},
        ),
        ("PUT", f"{base}/points/{point.id}", {"version": stale, "weekday": 4, "minute_of_day": 62}),
        ("PUT", f"{base}/points/{point.id}/mode", {"version": stale, "mode_id": nacht}),
        ("DELETE", f"{base}/points/{point.id}?version={stale}", None),
        ("POST", f"{base}/copy-day", {"version": stale, "weekday": 1, "scope": "all"}),
        ("POST", f"{base}/copy-week", {"version": stale, "targets": []}),
    ]
    snapshot = (_points(session, zone), session.query(AuditEvent).count())
    for method, url, body in attempts:
        error = _fehler(_call(client, method, url, body), 409, "version_conflict")
        assert error["field"] == "version"
    assert (_points(session, zone), session.query(AuditEvent).count()) == snapshot


def test_undo_is_refused_after_a_later_change(client_als: Als, session: Session) -> None:
    zone = _raum(session)
    client = client_als(PLAN)
    nacht = _mode(session, "nacht").id
    token = _call(
        client,
        "POST",
        f"/ui-api/schedule/{zone.id}/paint",
        {"version": "0", "weekday": 2, "start_minute": 600, "end_minute": 720, "mode_id": nacht},
    ).json()["undo_token"]
    _call(
        client,
        "POST",
        f"/ui-api/schedule/{zone.id}/paint",
        {
            "version": _version(client, zone),
            "weekday": 5,
            "start_minute": 600,
            "end_minute": 720,
            "mode_id": nacht,
        },
    )
    changed = _points(session, zone)
    _fehler(
        _call(client, "POST", f"/ui-api/schedule/{zone.id}/undo", {"undo_token": token}),
        409,
        "undo_stale",
    )
    assert _points(session, zone) == changed


def test_undo_rejects_forged_and_foreign_tokens(client_als: Als, session: Session) -> None:
    zone = _raum(session)
    other = create_zone(session, "bad")
    session.add(
        SchedulePoint(
            zone_id=other.id, weekday=1, minute_of_day=0, setpoint_mode_id=_mode(session, "tag").id
        )
    )
    session.flush()
    client = client_als(PLAN)
    token = _call(
        client,
        "POST",
        f"/ui-api/schedule/{other.id}/paint",
        {
            "version": "0",
            "weekday": 2,
            "start_minute": 600,
            "end_minute": 720,
            "mode_id": _mode(session, "nacht").id,
        },
    ).json()["undo_token"]
    assert token
    for bad in ("müll", token):  # Fälschung; Marke einer anderen Zone
        error = _fehler(
            _call(client, "POST", f"/ui-api/schedule/{zone.id}/undo", {"undo_token": bad}),
            422,
            "validation",
        )
        assert error["field"] == "undo_token"


# -- Schaltpunkte ------------------------------------------------------------------------


def test_create_point_audits_and_overlap_is_409(client_als: Als, session: Session) -> None:
    zone = _raum(session)
    client = client_als(PLAN)
    nacht = _mode(session, "nacht").id
    url = f"/ui-api/schedule/{zone.id}/points"
    created = _call(
        client,
        "POST",
        url,
        {"version": "0", "weekday": 1, "minute_of_day": 600, "mode_id": nacht},
    )
    assert created.status_code == 200, created.text
    assert (1, 600, nacht) in _points(session, zone)
    assert _audit(session, "schedule_point") == 1
    assert created.json()["changed"] is True and created.json()["undo_token"] is None
    version = created.json()["version"]
    count = len(_points(session, zone))
    error = _fehler(
        _call(
            client,
            "POST",
            url,
            {"version": version, "weekday": 1, "minute_of_day": 600, "mode_id": nacht},
        ),
        409,
        "overlap",
    )
    assert error["field"] == "time_of_day"
    assert len(_points(session, zone)) == count


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"minute_of_day": 1500}, "time_of_day"),
        ({"weekday": 8}, "weekday"),
        ({"mode_id": 999999}, "mode_id"),
    ],
)
def test_create_point_validation_comes_from_the_domain(
    client_als: Als, session: Session, changes: dict[str, int], field: str
) -> None:
    zone = _raum(session)
    body = {
        "version": "0",
        "weekday": 1,
        "minute_of_day": 600,
        "mode_id": _mode(session, "nacht").id,
    } | changes
    error = _fehler(
        _call(client_als(PLAN), "POST", f"/ui-api/schedule/{zone.id}/points", body),
        422,
        "validation",
    )
    assert error["field"] == field
    assert len(_points(session, zone)) == 14


def test_move_point_logs_change_and_rejects_overlap(client_als: Als, session: Session) -> None:
    zone = _raum(session)
    client = client_als(PLAN)
    point = (
        session.query(SchedulePoint).filter_by(zone_id=zone.id, weekday=1, minute_of_day=420).one()
    )
    url = f"/ui-api/schedule/{zone.id}/points/{point.id}"
    moved = _call(client, "PUT", url, {"version": "0", "weekday": 1, "minute_of_day": 450})
    assert moved.status_code == 200, moved.text
    assert moved.json()["changed"] is True
    session.refresh(point)
    assert (point.weekday, point.minute_of_day) == (1, 450)
    assert _audit(session, "schedule_point") == 1
    assert session.query(ChangeLog).filter_by(change_type="schedule_point").count() == 1
    version = moved.json()["version"]
    same = _call(client, "PUT", url, {"version": version, "weekday": 1, "minute_of_day": 450})
    assert same.json()["changed"] is False
    assert session.query(ChangeLog).filter_by(change_type="schedule_point").count() == 1
    _fehler(
        _call(client, "PUT", url, {"version": version, "weekday": 1, "minute_of_day": 1320}),
        409,
        "overlap",
    )
    error = _fehler(
        _call(client, "PUT", url, {"version": version, "weekday": 9, "minute_of_day": 10}),
        422,
        "validation",
    )
    assert error["field"] == "weekday"
    session.refresh(point)
    assert (point.weekday, point.minute_of_day) == (1, 450)


def test_move_point_across_midnight_to_next_day(client_als: Als, session: Session) -> None:
    zone = _raum(session)
    point = (
        session.query(SchedulePoint).filter_by(zone_id=zone.id, weekday=1, minute_of_day=1320).one()
    )
    moved = _call(
        client_als(PLAN),
        "PUT",
        f"/ui-api/schedule/{zone.id}/points/{point.id}",
        {"version": "0", "weekday": 2, "minute_of_day": 0},
    )
    assert moved.status_code == 200, moved.text
    session.refresh(point)
    assert (point.weekday, point.minute_of_day) == (2, 0)


def test_change_point_mode(client_als: Als, session: Session) -> None:
    zone = _raum(session)
    client = client_als(PLAN)
    point = (
        session.query(SchedulePoint).filter_by(zone_id=zone.id, weekday=1, minute_of_day=420).one()
    )
    nacht = _mode(session, "nacht").id
    url = f"/ui-api/schedule/{zone.id}/points/{point.id}/mode"
    changed = _call(client, "PUT", url, {"version": "0", "mode_id": nacht})
    assert changed.status_code == 200, changed.text
    assert changed.json()["changed"] is True
    session.refresh(point)
    assert point.setpoint_mode_id == nacht
    assert _audit(session, "schedule_point") == 1
    version = changed.json()["version"]
    assert (
        _call(client, "PUT", url, {"version": version, "mode_id": nacht}).json()["changed"] is False
    )
    error = _fehler(
        _call(client, "PUT", url, {"version": version, "mode_id": 999999}), 422, "validation"
    )
    assert error["field"] == "mode_id"


def test_delete_point(client_als: Als, session: Session) -> None:
    zone = _raum(session)
    client = client_als(PLAN)
    point = (
        session.query(SchedulePoint).filter_by(zone_id=zone.id, weekday=1, minute_of_day=420).one()
    )
    point_id = point.id
    deleted = _call(client, "DELETE", f"/ui-api/schedule/{zone.id}/points/{point_id}?version=0")
    assert deleted.status_code == 200, deleted.text
    assert point_id not in {p["id"] for p in deleted.json()["points"]}
    assert len(_points(session, zone)) == 13
    assert _audit(session, "schedule_point") == 1
    _fehler(
        _call(
            client,
            "DELETE",
            f"/ui-api/schedule/{zone.id}/points/{point_id}?version={deleted.json()['version']}",
        ),
        404,
        "point_not_found",
    )


def test_point_of_another_zone_is_not_addressable(client_als: Als, session: Session) -> None:
    zone = _raum(session)
    other = create_zone(session, "bad")
    foreign = SchedulePoint(
        zone_id=other.id, weekday=1, minute_of_day=5, setpoint_mode_id=_mode(session, "tag").id
    )
    session.add(foreign)
    session.flush()
    client = client_als(PLAN)
    _fehler(
        _call(
            client,
            "PUT",
            f"/ui-api/schedule/{zone.id}/points/{foreign.id}",
            {"version": "0", "weekday": 1, "minute_of_day": 6},
        ),
        404,
        "point_not_found",
    )
    _fehler(
        _call(
            client,
            "PUT",
            f"/ui-api/schedule/{zone.id}/points/999999/mode",
            {"version": "0", "mode_id": 1},
        ),
        404,
        "point_not_found",
    )
    assert foreign.minute_of_day == 5


# -- Tag kopieren ------------------------------------------------------------------------


def test_copy_day_scopes_noop_and_tenant(
    client_als: Als, tenant_client: Als, session: Session
) -> None:
    zone = _raum(session)
    session.add(
        SchedulePoint(
            zone_id=zone.id,
            weekday=1,
            minute_of_day=720,
            setpoint_mode_id=_mode(session, "nacht").id,
        )
    )
    session.flush()
    client = tenant_client(PLAN)  # Mieter dürfen Tage kopieren, wie in ihrer HTML-Ansicht
    url = f"/ui-api/schedule/{zone.id}/copy-day"
    result = _call(client, "POST", url, {"version": "0", "weekday": 1, "scope": "workdays"})
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["changed"] is True and body["undo_token"]
    for day in range(2, 6):
        assert (day, 720, _mode(session, "nacht").id) in _points(session, zone)
    assert (6, 720, _mode(session, "nacht").id) not in _points(session, zone)
    assert _audit(session, "schedule") == 1
    again = _call(
        client, "POST", url, {"version": body["version"], "weekday": 1, "scope": "workdays"}
    )
    assert again.json()["changed"] is False and again.json()["undo_token"] is None
    all_days = _call(
        client, "POST", url, {"version": body["version"], "weekday": 1, "scope": "all"}
    )
    assert all_days.json()["changed"] is True
    assert (7, 720, _mode(session, "nacht").id) in _points(session, zone)
    undone = _call(
        client,
        "POST",
        f"/ui-api/schedule/{zone.id}/undo",
        {"undo_token": all_days.json()["undo_token"]},
    )
    assert undone.status_code == 200
    assert (7, 720, _mode(session, "nacht").id) not in _points(session, zone)


def test_copy_day_rejects_invalid_weekday_and_scope(client_als: Als, session: Session) -> None:
    zone = _raum(session)
    client = client_als(PLAN)
    url = f"/ui-api/schedule/{zone.id}/copy-day"
    error = _fehler(
        _call(client, "POST", url, {"version": "0", "weekday": 9, "scope": "all"}),
        422,
        "validation",
    )
    assert error["field"] == "weekday"
    _fehler(
        _call(client, "POST", url, {"version": "0", "weekday": 1, "scope": "alle"}),
        422,
        "validation",
    )


# -- Plan auf andere Räume kopieren ------------------------------------------------------


def _ziel(session: Session, name: str, with_plan: bool = False) -> Zone:
    zone = create_zone(session, name)
    if with_plan:
        session.add(
            SchedulePoint(
                zone_id=zone.id,
                weekday=3,
                minute_of_day=30,
                setpoint_mode_id=_mode(session, "tag").id,
            )
        )
        session.flush()
    return zone


def test_copy_week_writes_all_targets_atomically_and_audits(
    client_als: Als, tenant_client: Als, session: Session
) -> None:
    zone = _raum(session)
    one = _ziel(session, "eins")
    two = _ziel(session, "zwei", with_plan=True)
    client = tenant_client(PLAN)
    listed = client.get(f"/ui-api/schedule/{zone.id}").json()["copy_targets"]
    versions = {t["id"]: t["version"] for t in listed}
    assert {t["id"]: t["has_plan"] for t in listed} == {one.id: False, two.id: True}
    response = _call(
        client,
        "POST",
        f"/ui-api/schedule/{zone.id}/copy-week",
        {
            "version": "0",
            "targets": [
                {"zone_id": one.id, "version": versions[one.id]},
                {"zone_id": two.id, "version": versions[two.id]},
            ],
        },
    )
    assert response.status_code == 200, response.text
    source_points = [(w, m, mode) for w, m, mode in _points(session, zone)]
    assert _points(session, one) == source_points
    assert _points(session, two) == source_points
    assert _audit(session, "schedule") == 2
    assert {t["id"]: t["has_plan"] for t in response.json()["copy_targets"]} == {
        one.id: True,
        two.id: True,
    }
    assert response.json()["changed"] is True


def test_copy_week_target_without_right_is_indistinguishable_and_all_or_nothing(
    client_als: Als, session: Session
) -> None:
    zone = _raum(session)
    allowed = _ziel(session, "erlaubt")
    read_only = _ziel(session, "nurlesen")
    client = client_als(
        [("zone.read", None), ("schedule.manage", zone.id), ("schedule.manage", allowed.id)]
    )
    url = f"/ui-api/schedule/{zone.id}/copy-week"

    def attempt(target_id: int) -> Any:
        return _call(
            client,
            "POST",
            url,
            {
                "version": "0",
                "targets": [
                    {"zone_id": allowed.id, "version": "0"},
                    {"zone_id": target_id, "version": "0"},
                ],
            },
        )

    hidden = attempt(read_only.id)
    unknown = attempt(999999)
    assert hidden.status_code == unknown.status_code == 404
    assert hidden.json() == unknown.json() and hidden.json()["code"] == "not_found"
    assert _points(session, allowed) == [] and _points(session, read_only) == []
    assert _audit(session, "schedule") == 0


def test_copy_week_target_stale_version_changes_nothing(client_als: Als, session: Session) -> None:
    zone = _raum(session)
    one = _ziel(session, "eins")
    two = _ziel(session, "zwei")
    client = client_als(PLAN)
    _fehler(
        _call(
            client,
            "POST",
            f"/ui-api/schedule/{zone.id}/copy-week",
            {
                "version": "0",
                "targets": [
                    {"zone_id": one.id, "version": "0"},
                    {"zone_id": two.id, "version": "77"},
                ],
            },
        ),
        409,
        "version_conflict",
    )
    assert _points(session, one) == [] and _points(session, two) == []


@pytest.mark.parametrize("shape", ["empty", "self", "duplicate"])
def test_copy_week_rejects_degenerate_target_lists(
    client_als: Als, session: Session, shape: str
) -> None:
    zone = _raum(session)
    one = _ziel(session, "eins")
    targets = {
        "empty": [],
        "self": [{"zone_id": zone.id, "version": "0"}],
        "duplicate": [{"zone_id": one.id, "version": "0"}] * 2,
    }[shape]
    error = _fehler(
        _call(
            client_als(PLAN),
            "POST",
            f"/ui-api/schedule/{zone.id}/copy-week",
            {"version": "0", "targets": targets},
        ),
        422,
        "validation",
    )
    assert error["field"] == "targets"
    assert _points(session, one) == []


# -- Urlaub ------------------------------------------------------------------------------


def test_vacation_lifecycle_across_the_spring_transition(client_als: Als, session: Session) -> None:
    _raum(session)
    client = client_als(PLAN)
    empty = client.get("/ui-api/vacation").json()
    assert empty == {"timezone": "Europe/Berlin", "can_manage": True, "vacation": None}
    created = _call(
        client,
        "POST",
        "/ui-api/vacation",
        {"start_date": "2030-03-30", "end_date": "2030-04-01", "setback_temperature_c": "16,0"},
    )
    assert created.status_code == 200, created.text
    vacation = created.json()["vacation"]
    # Ortstage 30.03. bis einschließlich 01.04.: Beginn MEZ, Ende MESZ (EU: 31.03.2030).
    assert vacation["starts_at"] == "2030-03-29T23:00:00+00:00"
    assert vacation["ends_at"] == "2030-04-01T22:00:00+00:00"
    assert (vacation["start_date"], vacation["end_date"]) == ("2030-03-30", "2030-04-01")
    assert vacation["setback_temperature_c"] == "16.0" and vacation["running"] is False
    row = session.query(Vacation).one()
    assert row.cancelled_at is None and row.created_by_user_id is not None
    assert _audit(session, "vacation") == 1
    assert session.query(ChangeLog).filter_by(change_type="vacation").count() == 1
    _fehler(
        _call(
            client,
            "POST",
            "/ui-api/vacation",
            {"start_date": "2031-01-01", "end_date": "2031-01-02", "setback_temperature_c": "16.0"},
        ),
        409,
        "vacation_exists",
    )
    reader = client_als([("zone.read", None)])
    assert reader.get("/ui-api/vacation").json()["can_manage"] is False
    ended = _call(client_als(PLAN), "DELETE", "/ui-api/vacation")
    assert ended.status_code == 200 and ended.json()["vacation"] is None
    session.refresh(row)
    assert row.cancelled_at is not None
    assert session.query(ChangeLog).filter_by(change_type="vacation_cancel").count() == 1
    _fehler(_call(client_als(PLAN), "DELETE", "/ui-api/vacation"), 409, "no_vacation")


@pytest.mark.parametrize(
    ("body", "field"),
    [
        (
            {"start_date": "morgen", "end_date": "2030-04-01", "setback_temperature_c": "16"},
            "start_date",
        ),
        ({"start_date": "2030-03-30", "end_date": "", "setback_temperature_c": "16"}, "end_date"),
        (
            {"start_date": "2030-03-30", "end_date": "2030-04-01", "setback_temperature_c": "kalt"},
            "temperature_c",
        ),
        (
            {"start_date": "2030-04-02", "end_date": "2030-04-01", "setback_temperature_c": "16"},
            "end_date",
        ),
        (
            {"start_date": "2030-03-30", "end_date": "2030-04-01", "setback_temperature_c": "99"},
            "temperatur",
        ),
    ],
)
def test_vacation_validation_is_422_and_stores_nothing(
    client_als: Als, session: Session, body: dict[str, str], field: str
) -> None:
    _raum(session)
    error = _fehler(_call(client_als(PLAN), "POST", "/ui-api/vacation", body), 422, "validation")
    assert error["field"] == field
    assert session.query(Vacation).count() == 0


# -- Abwesenheit -------------------------------------------------------------------------


def test_absence_lifecycle_for_tenants(tenant_client: Als, session: Session) -> None:
    zone = _raum(session)
    client = tenant_client([("zone.read", None), ("override.create", None)])
    state = client.get("/ui-api/absence").json()
    assert state["can_start"] is True and state["absence"] is None
    assert state["eligible_zone_ids"] == [zone.id]
    started = _call(
        client, "POST", "/ui-api/absence", {"temperature_c": "16,0", "return_on": "2030-01-01"}
    )
    assert started.status_code == 200, started.text
    absence = started.json()["absence"]
    assert absence["setback_temperature_c"] == "16.0"
    assert absence["ends_at"] == "2029-12-31T23:00:00+00:00"  # 01.01. 00:00 Ortszeit
    assert [z["id"] for z in absence["zones"]] == [zone.id]
    assert started.json()["can_start"] is False
    row = session.query(Absence).one()
    assert session.query(ZoneOverride).filter_by(absence_id=row.id).count() == 1
    assert _audit(session, "absence") == 1
    _fehler(
        _call(
            client, "POST", "/ui-api/absence", {"temperature_c": "16.0", "return_on": "2030-02-01"}
        ),
        409,
        "absence_running",
    )
    ended = _call(client, "POST", "/ui-api/absence/end")
    assert ended.status_code == 200 and ended.json()["absence"] is None
    session.refresh(row)
    assert row.cancelled_at is not None
    assert session.query(ZoneOverride).filter_by(absence_id=row.id, cancelled_at=None).count() == 0
    _fehler(_call(client, "POST", "/ui-api/absence/end"), 409, "no_absence")


def test_absence_end_closes_every_running_bracket(tenant_client: Als, session: Session) -> None:
    zone = _raum(session)
    client = tenant_client([("zone.read", None), ("override.create", None)])
    _call(client, "POST", "/ui-api/absence", {"temperature_c": "16.0", "return_on": "2030-01-01"})
    first = session.query(Absence).one()
    twin = Absence(
        starts_at=first.starts_at,
        ends_at=first.ends_at,
        setback_temperature_c=first.setback_temperature_c,
        created_by_user_id=first.created_by_user_id,
        source_id=first.source_id,
    )
    session.add(twin)
    session.flush()
    assert zone.id
    _call(client, "POST", "/ui-api/absence/end")
    assert session.query(Absence).filter(Absence.cancelled_at.is_(None)).count() == 0


@pytest.mark.parametrize(
    ("body", "status", "field"),
    [
        ({"temperature_c": "kalt", "return_on": "2030-01-01"}, 422, "temperature_c"),
        ({"temperature_c": "16", "return_on": "bald"}, 422, "return_on"),
        ({"temperature_c": "16", "return_on": "2020-01-01"}, 422, "ends_at"),
        ({"temperature_c": "99", "return_on": "2030-01-01"}, 422, "temperatur"),
    ],
)
def test_absence_validation(
    tenant_client: Als, session: Session, body: dict[str, str], status: int, field: str
) -> None:
    _raum(session)
    error = _fehler(
        _call(
            tenant_client([("zone.read", None), ("override.create", None)]),
            "POST",
            "/ui-api/absence",
            body,
        ),
        status,
        "validation",
    )
    assert error["field"] == field
    assert session.query(Absence).count() == 0 and session.query(ZoneOverride).count() == 0


def test_absence_without_any_overridable_room_is_403(tenant_client: Als, session: Session) -> None:
    _raum(session)
    client = tenant_client([("zone.read", None)])
    state = client.get("/ui-api/absence").json()
    assert state["can_start"] is False and state["eligible_zone_ids"] == []
    _fehler(
        _call(
            client, "POST", "/ui-api/absence", {"temperature_c": "16.0", "return_on": "2030-01-01"}
        ),
        403,
        "forbidden",
    )
    assert session.query(Absence).count() == 0


# -- Modi --------------------------------------------------------------------------------


def test_modes_crud_audit_and_locks(client_als: Als, session: Session) -> None:
    _raum(session)
    client = client_als(PLAN)
    listed = client.get("/ui-api/modes").json()["modes"]
    by_code = {m["code"]: m for m in listed}
    assert "Frostschutzmodus" in by_code["frost-wohnzimmer"]["delete_lock"]
    assert "Zeitpläne" in by_code["tag"]["delete_lock"]
    created = _call(
        client, "POST", "/ui-api/modes", {"code": "eco", "name": "Eco", "sort_order": 5}
    )
    assert created.status_code == 200, created.text
    eco = {m["code"]: m for m in created.json()["modes"]}["eco"]
    assert eco["sort_order"] == 5 and eco["delete_lock"] is None and eco["is_builtin"] is False
    assert _audit(session, "setpoint_mode") == 1
    error = _fehler(
        _call(client, "POST", "/ui-api/modes", {"code": "eco", "name": "Doppelt"}),
        422,
        "validation",
    )
    assert error["field"] == "code"
    renamed = _call(client, "PUT", f"/ui-api/modes/{eco['id']}", {"code": "eco", "name": "Sparen"})
    assert {m["code"]: m for m in renamed.json()["modes"]}["eco"]["name"] == "Sparen"
    assert _mode(session, "eco").name == "Sparen"
    clash = _fehler(
        _call(client, "PUT", f"/ui-api/modes/{eco['id']}", {"code": "tag", "name": "X"}),
        422,
        "validation",
    )
    assert clash["field"] == "code"
    locked = _fehler(
        _call(client, "DELETE", f"/ui-api/modes/{by_code['tag']['id']}"), 409, "mode_locked"
    )
    assert locked["field"] == "mode_id"
    removed = _call(client, "DELETE", f"/ui-api/modes/{eco['id']}")
    assert removed.status_code == 200
    assert "eco" not in {m["code"] for m in removed.json()["modes"]}
    assert session.query(SetpointMode).filter_by(code="eco").count() == 0
    for method in ("PUT", "DELETE"):
        _fehler(
            _call(client, method, "/ui-api/modes/999999", {"code": "x", "name": "x"}),
            404,
            "mode_not_found",
        )


# -- Aufbau ------------------------------------------------------------------------------


def test_schedule_router_carries_csrf_protection() -> None:
    from thermoctl.auth.dependencies import csrf_protection
    from thermoctl.web.ui_api_schedule import router

    assert csrf_protection in [d.dependency for d in router.dependencies]
    assert router.include_in_schema is False
