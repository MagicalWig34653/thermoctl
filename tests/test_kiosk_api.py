"""`/kiosk-api`: der Kiosk-Principal der Svelte-Ansicht.

Geprüft werden die Grenzen, nicht der Normalfall allein: nur das Kiosk-Cookie öffnet
diese Schnittstelle (und öffnet sonst nichts), jeder Schreibaufruf trägt den Kiosk-CSRF,
fremde Räume sind unauffindbar und ununterscheidbar von unbekannten, und ein widerrufener
oder abgelaufener Token endet in 401 -- nie in einer Weiterleitung.
"""

from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.helpers import (
    create_settings,
    create_zone,
    create_zone_state,
    sensor_status_of,
    source,
    user_with_permissions,
    zone_with_schedule,
)
from thermoctl.auth.csrf import csrf_token
from thermoctl.auth.kiosk import KIOSK_COOKIE_NAME, KIOSK_CSRF_COOKIE_NAME
from thermoctl.auth.sessions import COOKIE_NAME, create_session
from thermoctl.auth.tokens import issue_token
from thermoctl.config import get_settings
from thermoctl.db.base import utcnow
from thermoctl.db.models.operations import AuditEvent, Setting
from thermoctl.db.models.override import ZoneOverride
from thermoctl.db.models.state import ShadowDecision
from thermoctl.db.models.zone import ZoneSetpoint
from thermoctl.domain.administration import revoke_token
from thermoctl.domain.kiosk import issue_kiosk_token
from thermoctl.domain.schedule import resolved_setpoint

_ADMIN_PERMISSIONS = [
    ("zone.read", None), ("setpoint.write", None), ("override.create", None),
    ("override.cancel", None), ("token.manage", None),
]
_POINTS = [(1, 0, "tag", Decimal("21.0")), (1, 1320, "nacht", Decimal("18.0"))]

_WRITES = [
    ("/rooms/{zone}/setpoint-step", {"direction": "up"}),
    ("/rooms/{zone}/boost", None),
    ("/rooms/{zone}/override/cancel", None),
]


def _admin(session: Session):  # type: ignore[no-untyped-def]
    source(session, "kiosk")
    source(session, "web")
    return user_with_permissions(session, "kiosk-admin", _ADMIN_PERMISSIONS)


def _csrf(plaintext: str) -> dict[str, str]:
    secret = get_settings().secret_key.get_secret_value()
    return {"X-CSRF-Token": csrf_token(plaintext, secret)}


def _cookie(client: TestClient, plaintext: str) -> TestClient:
    client.cookies.set(KIOSK_COOKIE_NAME, plaintext)
    client.cookies.set(
        KIOSK_CSRF_COOKIE_NAME,
        csrf_token(plaintext, get_settings().secret_key.get_secret_value()),
    )
    return client


def _setup(session: Session, *, control: bool = True):  # type: ignore[no-untyped-def]
    zone = zone_with_schedule(session, "flur", _POINTS)
    admin = _admin(session)
    token, plaintext = issue_kiosk_token(
        session, admin, "Flur", [zone.id], control_allowed=control, expires_at=None
    )
    return zone, admin, token, plaintext


# -- Schnittstelle ohne Kiosk-Cookie ---------------------------------------------------


def _all_calls(zone_id: int) -> list[tuple[str, str, dict[str, str] | None]]:
    calls: list[tuple[str, str, dict[str, str] | None]] = [("GET", "/kiosk-api/state", None)]
    calls += [("POST", "/kiosk-api" + path.format(zone=zone_id), body) for path, body in _WRITES]
    return calls


def _call(client: TestClient, method: str, path: str, body, headers=None):  # type: ignore[no-untyped-def]
    return client.request(method, path, json=body, headers=headers or {})


def test_no_cookie_is_401_everywhere(client: TestClient, session: Session) -> None:
    zone, *_ = _setup(session)
    for method, path, body in _all_calls(zone.id):
        response = _call(client, method, path, body)
        assert response.status_code == 401, path
        assert response.json() == {
            "detail": "Dieser Kiosk-Zugang ist nicht mehr gültig.",
            "code": "kiosk_token_invalid",
        }
        assert response.headers["cache-control"] == "no-store"


def test_a_session_cookie_is_not_a_kiosk_principal(client: TestClient, session: Session) -> None:
    zone, admin, *_ = _setup(session)
    _entry, secret = create_session(session, admin, 3600)
    client.cookies.set(COOKIE_NAME, secret)
    headers = {"X-CSRF-Token": csrf_token(secret, get_settings().secret_key.get_secret_value())}
    for method, path, body in _all_calls(zone.id):
        response = _call(client, method, path, body, headers)
        assert response.status_code == 401, path
        assert response.json()["code"] == "kiosk_token_invalid"
    assert session.query(ZoneOverride).count() == 0


def test_bearer_and_oauth_tokens_are_not_kiosk_principals(
    client: TestClient, session: Session
) -> None:
    zone, admin, _token, kiosk_plaintext = _setup(session)
    api_token, api_plaintext = issue_token(
        session, admin, "rest", [("zone.read", None), ("setpoint.write", None)], None
    )
    oauth_token, oauth_plaintext = issue_token(session, admin, "oauth", [("zone.read", None)], None)
    oauth_token.source = "oauth"
    session.flush()
    for plaintext in (api_plaintext, oauth_plaintext, kiosk_plaintext):
        headers = {"Authorization": f"Bearer {plaintext}", **_csrf(plaintext)}
        for method, path, body in _all_calls(zone.id):
            assert _call(client, method, path, body, headers).status_code == 401, path


def test_an_ordinary_api_token_in_the_kiosk_cookie_is_refused(
    client: TestClient, session: Session
) -> None:
    zone, admin, *_ = _setup(session)
    _token, plaintext = issue_token(session, admin, "rest", [("zone.read", zone.id)], None)
    _cookie(client, plaintext)
    response = client.get("/kiosk-api/state")
    assert response.status_code == 401
    assert response.json()["code"] == "kiosk_token_invalid"


def test_a_kiosk_cookie_reaches_neither_ui_api_nor_rest(
    client: TestClient, session: Session
) -> None:
    zone, _admin_user, _token, plaintext = _setup(session)
    _cookie(client, plaintext)
    paths = (
        "/ui-api/rooms", f"/ui-api/rooms/{zone.id}", "/api/v1/zones", f"/api/v1/zones/{zone.id}"
    )
    for path in paths:
        response = client.get(path)
        assert response.status_code in (401, 403, 404), (path, response.status_code)
        assert "Flur" not in response.text, path
    # ... und die Sollwerte blieben unberührt
    assert session.query(ZoneOverride).count() == 0
    response = client.post(
        f"/ui-api/rooms/{zone.id}/override/cancel", headers=_csrf(plaintext)
    )
    assert response.status_code in (401, 403, 404, 405)


# -- Lesen ------------------------------------------------------------------------------


def test_state_lists_only_the_rooms_in_scope_with_their_actions(
    client: TestClient, session: Session
) -> None:
    zone, admin, _token, plaintext = _setup(session)
    other = create_zone(session, "geheimzimmer")
    state = create_zone_state(session, zone)
    state.temperature_c = Decimal("19.40")
    state.measured_at = utcnow()
    state.sensor_stuck = True
    session.add(ShadowDecision(
        zone_id=zone.id, decided_at=utcnow(), temperature_c=Decimal("19.4"),
        setpoint_c=Decimal("21.0"), setpoint_reason="Test", would_heat=True,
        outcome_code="waere_geschaltet", reason="Test",
    ))
    session.flush()
    _cookie(client, plaintext)

    response = client.get("/kiosk-api/state")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert [room["id"] for room in body["rooms"]] == [zone.id]
    assert "Geheimzimmer" not in response.text and str(other.id) not in {
        str(room["id"]) for room in body["rooms"]
    }
    room = body["rooms"][0]
    assert room["name"] == "Flur"
    assert room["temperature"] == "19.40"
    assert room["measured_at"].endswith("+00:00")
    assert room["sensor_stuck"] is True
    assert room["sensor"] is None
    assert room["would_heat"] is True
    assert room["override"] is None
    assert room["emergency"] is None
    assert Decimal(room["setpoint"]["temperature"]) in (Decimal("21.0"), Decimal("18.0"))
    assert room["actions"] == {"setpoint": True, "boost": True, "cancel_override": False}
    assert body["timezone"] == "Europe/Berlin"


def test_a_view_only_token_gets_no_actions(client: TestClient, session: Session) -> None:
    _zone, _admin_user, _token, plaintext = _setup(session, control=False)
    _cookie(client, plaintext)
    room = client.get("/kiosk-api/state").json()["rooms"][0]
    assert room["actions"] == {"setpoint": False, "boost": False, "cancel_override": False}
    assert room["temperature"] is None and room["would_heat"] is None
    assert room["sensor"] is None and room["sensor_stuck"] is False


def test_a_failing_sensor_and_an_emergency_banner_are_reported(
    client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    zone, _admin_user, _token, plaintext = _setup(session)
    state = create_zone_state(session, zone)
    stale = sensor_status_of(session, "stale")
    state.sensor_status_id = stale.id
    session.flush()
    banner = SimpleNamespace(headline="Notbetrieb aktiv", detail="Ersatz läuft.")
    monkeypatch.setattr(
        "thermoctl.web.kiosk_api.zone_emergency_view",
        lambda *_a, **_k: SimpleNamespace(banner=banner),
    )
    _cookie(client, plaintext)
    room = client.get("/kiosk-api/state").json()["rooms"][0]
    assert room["sensor"] == {"code": "stale", "label": "stale"}
    assert room["emergency"] == {"headline": "Notbetrieb aktiv", "detail": "Ersatz läuft."}


def test_a_running_override_is_reported_and_cancellable(
    client: TestClient, session: Session
) -> None:
    zone, _admin_user, _token, plaintext = _setup(session)
    session.add(ZoneOverride(
        zone_id=zone.id, temperature_c=Decimal("23.0"), starts_at=utcnow() - timedelta(hours=1),
        ends_at=utcnow() + timedelta(hours=1), source_id=source(session, "web").id,
    ))
    session.add(ZoneOverride(  # später beginnend: läuft noch nicht
        zone_id=zone.id, temperature_c=Decimal("25.0"), starts_at=utcnow() + timedelta(hours=5),
        ends_at=None, source_id=source(session, "web").id,
    ))
    session.flush()
    _cookie(client, plaintext)
    room = client.get("/kiosk-api/state").json()["rooms"][0]
    assert room["override"]["temperature"] == "23.0"
    assert room["override"]["ends_at"].endswith("+00:00")
    assert room["actions"]["cancel_override"] is True


def test_unlimited_override_has_no_end(client: TestClient, session: Session) -> None:
    zone, _admin_user, _token, plaintext = _setup(session)
    session.add(ZoneOverride(
        zone_id=zone.id, temperature_c=Decimal("23.0"), starts_at=utcnow() - timedelta(hours=1),
        ends_at=None, source_id=source(session, "web").id,
    ))
    session.flush()
    _cookie(client, plaintext)
    assert client.get("/kiosk-api/state").json()["rooms"][0]["override"]["ends_at"] is None


def test_an_incomplete_setup_is_503_not_a_crash(client: TestClient, session: Session) -> None:
    zone, _admin_user, _token, plaintext = _setup(session)
    session.delete(session.get(Setting, 1))
    session.flush()
    _cookie(client, plaintext)
    for method, path, body in _all_calls(zone.id):
        response = _call(client, method, path, body, _csrf(plaintext))
        assert response.status_code == 503, path
        assert response.json()["code"] == "setup_incomplete"


# -- Schreiben --------------------------------------------------------------------------


def test_setpoint_step_changes_the_mode_setpoint_and_is_audited(
    client: TestClient, session: Session
) -> None:
    zone, _admin_user, _token, plaintext = _setup(session)
    before = resolved_setpoint(session, zone, utcnow())
    _cookie(client, plaintext)

    up = client.post(
        f"/kiosk-api/rooms/{zone.id}/setpoint-step", json={"direction": "up"},
        headers=_csrf(plaintext),
    )
    assert up.status_code == 200
    assert Decimal(up.json()["setpoint"]["temperature"]) == before.temperature_c + Decimal("0.5")
    row = session.query(ZoneSetpoint).filter_by(
        zone_id=zone.id, setpoint_mode_id=before.mode_id
    ).one()
    assert row.temperature_c == before.temperature_c + Decimal("0.5")

    down = client.post(
        f"/kiosk-api/rooms/{zone.id}/setpoint-step", json={"direction": "down"},
        headers=_csrf(plaintext),
    )
    assert down.status_code == 200
    session.expire_all()
    assert session.query(ZoneSetpoint).filter_by(
        zone_id=zone.id, setpoint_mode_id=before.mode_id
    ).one().temperature_c == before.temperature_c
    events = session.query(AuditEvent).filter_by(object_type="zone_setpoint").all()
    assert len(events) == 2
    assert {e.source_id for e in events} == {source(session, "kiosk").id}


def test_boost_creates_a_kiosk_override_and_cancel_ends_it(
    client: TestClient, session: Session
) -> None:
    zone, _admin_user, token, plaintext = _setup(session)
    _cookie(client, plaintext)

    boosted = client.post(f"/kiosk-api/rooms/{zone.id}/boost", headers=_csrf(plaintext))
    assert boosted.status_code == 200
    assert boosted.json()["override"] is not None
    assert boosted.json()["actions"]["cancel_override"] is True
    entry = session.query(ZoneOverride).filter_by(zone_id=zone.id).one()
    assert entry.created_by_token_id == token.id
    assert entry.source_id == source(session, "kiosk").id
    assert entry.cancelled_at is None

    cancelled = client.post(f"/kiosk-api/rooms/{zone.id}/override/cancel", headers=_csrf(plaintext))
    assert cancelled.status_code == 200
    assert cancelled.json()["override"] is None
    session.expire_all()
    assert session.query(ZoneOverride).filter_by(zone_id=zone.id).one().cancelled_at is not None


def test_cancel_without_running_override_is_a_harmless_noop(
    client: TestClient, session: Session
) -> None:
    zone, *_rest, plaintext = _setup(session)
    _cookie(client, plaintext)
    response = client.post(f"/kiosk-api/rooms/{zone.id}/override/cancel", headers=_csrf(plaintext))
    assert response.status_code == 200
    assert response.json()["override"] is None


def test_the_domain_rejection_is_422_and_changes_nothing(
    client: TestClient, session: Session
) -> None:
    zone = zone_with_schedule(
        session, "flur", [(1, 0, "tag", Decimal("34.8")), (1, 1320, "nacht", Decimal("34.8"))]
    )
    admin = _admin(session)
    _token, plaintext = issue_kiosk_token(
        session, admin, "Flur", [zone.id], control_allowed=True, expires_at=None
    )
    _cookie(client, plaintext)
    response = client.post(
        f"/kiosk-api/rooms/{zone.id}/setpoint-step", json={"direction": "up"},
        headers=_csrf(plaintext),
    )
    assert response.status_code == 422
    assert response.json()["code"] == "rejected"
    assert "zwischen" in response.json()["detail"]
    assert session.query(AuditEvent).filter_by(object_type="zone_setpoint").count() == 0


def test_boost_without_a_schedule_is_422(client: TestClient, session: Session) -> None:
    create_settings(session)
    zone = create_zone(session, "flur")
    admin = _admin(session)
    _token, plaintext = issue_kiosk_token(
        session, admin, "Flur", [zone.id], control_allowed=True, expires_at=None
    )
    _cookie(client, plaintext)
    response = client.post(f"/kiosk-api/rooms/{zone.id}/boost", headers=_csrf(plaintext))
    assert response.status_code == 422
    assert response.json()["code"] == "rejected"
    assert "Zeitplan" in response.json()["detail"]
    assert session.query(ZoneOverride).count() == 0


def test_an_unknown_direction_is_a_validation_error(client: TestClient, session: Session) -> None:
    zone, *_rest, plaintext = _setup(session)
    _cookie(client, plaintext)
    for body in ({"direction": "sideways"}, {}, None):
        response = client.post(
            f"/kiosk-api/rooms/{zone.id}/setpoint-step", json=body, headers=_csrf(plaintext)
        )
        assert response.status_code == 422
        assert response.json() == {"detail": "Die Eingabe ist ungültig.", "code": "validation"}
    assert session.query(AuditEvent).filter_by(object_type="zone_setpoint").count() == 0


# -- Rechte und Umfang ------------------------------------------------------------------


@pytest.mark.parametrize(("path", "body"), _WRITES)
def test_a_view_only_token_is_forbidden_to_write(
    client: TestClient, session: Session, path: str, body: dict[str, str] | None
) -> None:
    zone, *_rest, plaintext = _setup(session, control=False)
    _cookie(client, plaintext)
    response = client.post(
        "/kiosk-api" + path.format(zone=zone.id), json=body, headers=_csrf(plaintext)
    )
    assert response.status_code == 403
    assert response.json() == {"detail": "Dafür fehlt die Berechtigung.", "code": "forbidden"}
    assert session.query(ZoneOverride).count() == 0
    assert session.query(AuditEvent).filter_by(object_type="zone_setpoint").count() == 0


@pytest.mark.parametrize(("path", "body"), _WRITES)
def test_a_foreign_or_unknown_zone_is_the_same_404(
    client: TestClient, session: Session, path: str, body: dict[str, str] | None
) -> None:
    _zone, *_rest, plaintext = _setup(session)
    foreign = zone_with_schedule_other(session)
    _cookie(client, plaintext)
    answers = [
        client.post(
            "/kiosk-api" + path.format(zone=zone_id), json=body, headers=_csrf(plaintext)
        )
        for zone_id in (foreign, 999_999)
    ]
    assert [a.status_code for a in answers] == [404, 404]
    assert answers[0].json() == answers[1].json() == {
        "detail": "Raum nicht gefunden.", "code": "not_found",
    }
    assert "Fremd" not in answers[0].text
    assert session.query(ZoneOverride).count() == 0
    assert session.query(AuditEvent).filter_by(object_type="zone_setpoint").count() == 0


def zone_with_schedule_other(session: Session) -> int:
    """Eine Zone, die der Token nicht erreicht (andere Nummer, anderer Name)."""
    zone = create_zone(session, "fremd")
    return zone.id


# -- CSRF -------------------------------------------------------------------------------


@pytest.mark.parametrize(("path", "body"), _WRITES)
def test_a_write_without_the_csrf_header_is_403_json(
    client: TestClient, session: Session, path: str, body: dict[str, str] | None
) -> None:
    zone, *_rest, plaintext = _setup(session)
    _cookie(client, plaintext)
    for headers in ({}, {"X-CSRF-Token": "falsch"}):
        response = client.post(
            "/kiosk-api" + path.format(zone=zone.id), json=body, headers=headers
        )
        assert response.status_code == 403
        assert response.json() == {"detail": "Ungültiges CSRF-Token", "code": "csrf"}
    assert session.query(ZoneOverride).count() == 0
    assert session.query(AuditEvent).filter_by(object_type="zone_setpoint").count() == 0


def test_the_session_csrf_token_does_not_count_for_the_kiosk(
    client: TestClient, session: Session
) -> None:
    zone, admin, _token, plaintext = _setup(session)
    _entry, secret = create_session(session, admin, 3600)
    _cookie(client, plaintext)
    headers = {"X-CSRF-Token": csrf_token(secret, get_settings().secret_key.get_secret_value())}
    response = client.post(f"/kiosk-api/rooms/{zone.id}/boost", headers=headers)
    assert response.status_code == 403
    assert response.json()["code"] == "csrf"


# -- Widerruf und Ablauf ----------------------------------------------------------------


def test_a_revoked_token_is_401_for_reads_and_writes(
    client: TestClient, session: Session
) -> None:
    zone, admin, token, plaintext = _setup(session)
    _cookie(client, plaintext)
    assert client.get("/kiosk-api/state").status_code == 200
    revoke_token(session, token, actor_id=admin.id)
    for method, path, body in _all_calls(zone.id):
        response = _call(client, method, path, body, _csrf(plaintext))
        assert response.status_code == 401, path
        assert response.json()["code"] == "kiosk_token_invalid"
    assert session.query(ZoneOverride).count() == 0


def test_an_expired_token_is_401(client: TestClient, session: Session) -> None:
    zone = zone_with_schedule(session, "flur", _POINTS)
    admin = _admin(session)
    token, plaintext = issue_kiosk_token(
        session, admin, "Flur", [zone.id], control_allowed=True,
        expires_at=utcnow() + timedelta(hours=1),
    )
    _cookie(client, plaintext)
    assert client.get("/kiosk-api/state").status_code == 200
    token.expires_at = utcnow() - timedelta(seconds=1)
    session.flush()
    for method, path, body in _all_calls(zone.id):
        assert _call(client, method, path, body, _csrf(plaintext)).status_code == 401, path


def test_the_redeem_link_and_the_html_kiosk_are_unchanged(
    client: TestClient, session: Session
) -> None:
    _zone, _admin_user, _token, plaintext = _setup(session)
    redeemed = client.get(f"/kiosk/{plaintext}", follow_redirects=False)
    assert redeemed.status_code == 303
    assert redeemed.headers["location"].endswith("/kiosk")
    assert client.get("/kiosk").status_code == 200
