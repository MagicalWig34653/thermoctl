"""The kiosk dashboard's security properties.

A kiosk token sits in a tablet's bookmark and cookie, reachable by anyone who can see
the tablet on the wall -- unlike a session cookie, which lives behind a password. Its
whole safety rests on the scope actually being as narrow as advertised: only the
assigned zones, only view or also control as configured, and nothing else in the
application. Every test here checks one boundary of that scope; the happy path
(dashboard renders, controls work) is covered once each, not repeated per test.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.helpers import (
    create_settings,
    create_zone,
    source,
    user_with_permissions,
    zone_with_schedule,
)
from thermoctl.auth.csrf import csrf_token
from thermoctl.auth.kiosk import KIOSK_COOKIE_NAME, KIOSK_CSRF_COOKIE_NAME
from thermoctl.auth.sessions import COOKIE_NAME, create_session
from thermoctl.auth.tokens import resolve_token, token_ausstellen
from thermoctl.config import get_settings
from thermoctl.db.base import utcnow
from thermoctl.db.models.credential import ApiToken
from thermoctl.db.models.override import ZoneOverride
from thermoctl.db.models.zone import ZoneSetpoint
from thermoctl.domain.administration import revoke_token
from thermoctl.domain.kiosk import KioskError, issue_kiosk_token, kiosk_scope
from thermoctl.domain.schedule import resolved_setpoint

# `token.manage` alone: this admin is deliberately *not* given `zone.read` etc. for
# every test -- several tests below rely on that to check the admin's own permissions
# limit what they can hand out.
_ADMIN_PERMISSIONS = [
    ("zone.read", None), ("setpoint.write", None), ("override.create", None),
    ("token.manage", None),
]


def _admin(session: Session, permissions: list[tuple[str, int | None]] = _ADMIN_PERMISSIONS):  # type: ignore[no-untyped-def]
    source(session, "kiosk")
    source(session, "web")
    return user_with_permissions(session, "kiosk-admin", permissions)


def _csrf_headers(plaintext: str) -> dict[str, str]:
    secret = get_settings().secret_key.get_secret_value()
    return {"X-CSRF-Token": csrf_token(plaintext, secret)}


def _with_kiosk_cookie(client: TestClient, plaintext: str) -> TestClient:
    client.cookies.set(KIOSK_COOKIE_NAME, plaintext)
    client.cookies.set(KIOSK_CSRF_COOKIE_NAME, csrf_token(
        plaintext, get_settings().secret_key.get_secret_value()
    ))
    return client


# --- Issuing ----------------------------------------------------------------------


def test_issuing_requires_the_token_manage_permission(
    client: TestClient, session: Session
) -> None:
    zone = create_zone(session, "flur")
    # `zone.read` only, no `token.manage`.
    other = user_with_permissions(session, "someone-else", [("zone.read", None)])
    _entry, secret = create_session(session, other, 3600)
    client.cookies.set(COOKIE_NAME, secret)
    headers = {"X-CSRF-Token": csrf_token(secret, get_settings().secret_key.get_secret_value())}
    response = client.post(
        "/kiosk-tokens", data={"name": "Flur", "zone_id": str(zone.id)}, headers=headers,
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_a_token_carries_no_more_than_its_issuer_holds(session: Session) -> None:
    """The admin page's own guard: `token_ausstellen` already enforces this, but a
    kiosk-specific message is what an admin should actually see."""
    zone = create_zone(session, "flur")
    # No `zone.read` for this admin at all.
    admin = _admin(session, [("token.manage", None)])
    with pytest.raises(KioskError):
        issue_kiosk_token(
            session, admin, "Flur", [zone.id], control_allowed=False, expires_at=None
        )


def test_issuing_without_a_zone_is_rejected(session: Session) -> None:
    admin = _admin(session)
    with pytest.raises(KioskError):
        issue_kiosk_token(session, admin, "Nichts", [], control_allowed=False, expires_at=None)


def test_the_plaintext_is_shown_once_on_the_admin_page(
    client: TestClient, session: Session
) -> None:
    zone = create_zone(session, "flur")
    admin = _admin(session)
    _entry, secret = create_session(session, admin, 3600)
    client.cookies.set(COOKIE_NAME, secret)
    headers = {"X-CSRF-Token": csrf_token(secret, get_settings().secret_key.get_secret_value())}

    response = client.post(
        "/kiosk-tokens",
        data={"name": "Flur-Tablet", "zone_id": str(zone.id), "control_allowed": "on"},
        headers=headers,
    )
    assert response.status_code == status.HTTP_200_OK
    assert "/kiosk/tctl_" in response.text

    token = session.query(ApiToken).filter_by(name="Flur-Tablet").one()
    assert token.is_kiosk is True
    zone_ids, control_allowed = kiosk_scope(session, token)
    assert zone_ids == [zone.id]
    assert control_allowed is True


def test_revoking_a_kiosk_token_makes_it_unusable(client: TestClient, session: Session) -> None:
    zone = create_zone(session, "flur")
    admin = _admin(session)
    token, plaintext = issue_kiosk_token(
        session, admin, "Flur", [zone.id], control_allowed=False, expires_at=None
    )
    _entry, secret = create_session(session, admin, 3600)
    client.cookies.set(COOKIE_NAME, secret)
    headers = {"X-CSRF-Token": csrf_token(secret, get_settings().secret_key.get_secret_value())}

    response = client.post(
        f"/kiosk-tokens/{token.id}/revoke", headers=headers, follow_redirects=False
    )
    assert response.status_code == status.HTTP_303_SEE_OTHER
    assert resolve_token(session, plaintext) is None


# --- The dashboard cannot be reached with anything but a kiosk token ---------------


def test_an_unknown_token_shows_the_invalid_page(client: TestClient) -> None:
    response = client.get("/kiosk/tctl_deadbeef_" + "x" * 43, follow_redirects=False)
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert KIOSK_COOKIE_NAME not in response.cookies


def test_a_revoked_token_is_rejected(client: TestClient, session: Session) -> None:
    zone = create_zone(session, "flur")
    admin = _admin(session)
    token, plaintext = issue_kiosk_token(
        session, admin, "Flur", [zone.id], control_allowed=False, expires_at=None
    )
    revoke_token(session, token, akteur_id=admin.id)

    response = client.get(f"/kiosk/{plaintext}")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_an_expired_token_is_rejected(client: TestClient, session: Session) -> None:
    zone = create_zone(session, "flur")
    admin = _admin(session)
    _token, plaintext = issue_kiosk_token(
        session, admin, "Flur", [zone.id], control_allowed=False,
        expires_at=utcnow() - timedelta(days=1),
    )

    response = client.get(f"/kiosk/{plaintext}")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_an_ordinary_api_token_does_not_work_as_a_kiosk_cookie(
    client: TestClient, session: Session
) -> None:
    """A kiosk token is otherwise an `ApiToken` -- but a developer's own bearer token
    must not double as a tablet's cookie just because the hash resolves."""
    zone = create_zone(session, "flur")
    admin = _admin(session)
    _token, plaintext = token_ausstellen(
        session, admin, "Entwickler-Token", [("zone.read", zone.id)], None
    )
    _with_kiosk_cookie(client, plaintext)

    response = client.get("/kiosk")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_the_entry_link_sets_a_cookie_and_redirects_without_the_token_in_the_url(
    client: TestClient, session: Session
) -> None:
    zone = create_zone(session, "flur")
    admin = _admin(session)
    _token, plaintext = issue_kiosk_token(
        session, admin, "Flur", [zone.id], control_allowed=False, expires_at=None
    )

    response = client.get(f"/kiosk/{plaintext}", follow_redirects=False)
    assert response.status_code == status.HTTP_303_SEE_OTHER
    assert response.headers["location"] == "/kiosk"
    assert response.cookies[KIOSK_COOKIE_NAME] == plaintext


# --- Scope: exactly the assigned zones, nothing else -------------------------------


def test_the_dashboard_shows_only_the_assigned_zone(client: TestClient, session: Session) -> None:
    create_settings(session)
    assigned = create_zone(session, "flur")
    other = create_zone(session, "keller")
    admin = _admin(session)
    _token, plaintext = issue_kiosk_token(
        session, admin, "Flur", [assigned.id], control_allowed=False, expires_at=None
    )
    _with_kiosk_cookie(client, plaintext)

    response = client.get("/kiosk")
    assert response.status_code == status.HTTP_200_OK
    assert assigned.display_name in response.text
    assert other.display_name not in response.text


def test_a_token_cannot_reach_a_zone_outside_its_scope(
    client: TestClient, session: Session
) -> None:
    assigned = create_zone(session, "flur")
    other = zone_with_schedule(
        session, "keller",
        [(1, 0, "tag", Decimal("21.0")), (1, 1320, "nacht", Decimal("18.0"))],
    )
    admin = _admin(session)
    _token, plaintext = issue_kiosk_token(
        session, admin, "Flur", [assigned.id], control_allowed=True, expires_at=None
    )
    _with_kiosk_cookie(client, plaintext)

    setpoint = client.post(
        f"/kiosk/zones/{other.id}/setpoint",
        data={"mode_id": "1", "direction": "hoch"},
        headers=_csrf_headers(plaintext),
    )
    assert setpoint.status_code == status.HTTP_401_UNAUTHORIZED

    boost = client.post(
        f"/kiosk/zones/{other.id}/boost", headers=_csrf_headers(plaintext)
    )
    assert boost.status_code == status.HTTP_401_UNAUTHORIZED
    assert session.query(ZoneOverride).filter_by(zone_id=other.id).count() == 0


# --- Scope: view-only cannot control -----------------------------------------------


def test_a_view_only_token_cannot_adjust_the_setpoint(
    client: TestClient, session: Session
) -> None:
    zone = zone_with_schedule(
        session, "flur",
        [(1, 0, "tag", Decimal("21.0")), (1, 1320, "nacht", Decimal("18.0"))],
    )
    admin = _admin(session)
    _token, plaintext = issue_kiosk_token(
        session, admin, "Flur", [zone.id], control_allowed=False, expires_at=None
    )
    _with_kiosk_cookie(client, plaintext)

    response = client.post(
        f"/kiosk/zones/{zone.id}/setpoint",
        data={"mode_id": "1", "direction": "hoch"},
        headers=_csrf_headers(plaintext),
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_a_view_only_token_cannot_boost(client: TestClient, session: Session) -> None:
    zone = zone_with_schedule(
        session, "flur",
        [(1, 0, "tag", Decimal("21.0")), (1, 1320, "nacht", Decimal("18.0"))],
    )
    admin = _admin(session)
    _token, plaintext = issue_kiosk_token(
        session, admin, "Flur", [zone.id], control_allowed=False, expires_at=None
    )
    _with_kiosk_cookie(client, plaintext)

    response = client.post(
        f"/kiosk/zones/{zone.id}/boost", headers=_csrf_headers(plaintext)
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert session.query(ZoneOverride).count() == 0


# --- The control-allowed path actually works ---------------------------------------


def test_a_control_allowed_token_can_adjust_the_setpoint(
    client: TestClient, session: Session
) -> None:
    zone = zone_with_schedule(
        session, "flur",
        [(1, 0, "tag", Decimal("21.0")), (1, 1320, "nacht", Decimal("18.0"))],
    )
    admin = _admin(session)
    _token, plaintext = issue_kiosk_token(
        session, admin, "Flur", [zone.id], control_allowed=True, expires_at=None
    )
    _with_kiosk_cookie(client, plaintext)
    # Whichever mode is actually in effect right now (day or night, depending on the
    # wall clock the suite happens to run at) -- `set_setpoint` adjusts that one, not
    # a mode the caller names. Read it the same way the domain does, before acting.
    before = resolved_setpoint(session, zone, utcnow())
    assert before.mode_id is not None

    response = client.post(
        f"/kiosk/zones/{zone.id}/setpoint",
        data={"direction": "hoch"},
        headers=_csrf_headers(plaintext),
        follow_redirects=False,
    )
    assert response.status_code == status.HTTP_303_SEE_OTHER
    updated = session.query(ZoneSetpoint).filter_by(
        zone_id=zone.id, setpoint_mode_id=before.mode_id
    ).one()
    assert updated.temperature_c == before.temperature_c + Decimal("0.5")


def test_a_control_allowed_token_can_boost(client: TestClient, session: Session) -> None:
    zone = zone_with_schedule(
        session, "flur",
        [(1, 0, "tag", Decimal("21.0")), (1, 1320, "nacht", Decimal("18.0"))],
    )
    admin = _admin(session)
    _token, plaintext = issue_kiosk_token(
        session, admin, "Flur", [zone.id], control_allowed=True, expires_at=None
    )
    _with_kiosk_cookie(client, plaintext)

    response = client.post(
        f"/kiosk/zones/{zone.id}/boost", headers=_csrf_headers(plaintext), follow_redirects=False
    )
    assert response.status_code == status.HTTP_303_SEE_OTHER
    entry = session.query(ZoneOverride).filter_by(zone_id=zone.id).one()
    assert entry.created_by_token_id is not None


# --- CSRF ---------------------------------------------------------------------------


def test_a_mutation_without_the_csrf_header_is_rejected(
    client: TestClient, session: Session
) -> None:
    zone = zone_with_schedule(
        session, "flur",
        [(1, 0, "tag", Decimal("21.0")), (1, 1320, "nacht", Decimal("18.0"))],
    )
    admin = _admin(session)
    _token, plaintext = issue_kiosk_token(
        session, admin, "Flur", [zone.id], control_allowed=True, expires_at=None
    )
    _with_kiosk_cookie(client, plaintext)

    response = client.post(f"/kiosk/zones/{zone.id}/boost")
    assert response.status_code == status.HTTP_403_FORBIDDEN


# --- No path from a kiosk cookie into the administration --------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/users", "/groups", "/tokens", "/kiosk-tokens", "/devices", "/audit",
        "/settings", "/interfaces", "/control",
    ],
)
def test_a_kiosk_cookie_reaches_no_administration_page(
    path: str, client: TestClient, session: Session
) -> None:
    """The kiosk cookie is not a session cookie -- it authenticates nothing that
    `aktueller_principal` looks for, so every logged-in-only page must still demand
    an actual login."""
    zone = create_zone(session, "flur")
    admin = _admin(session)
    _token, plaintext = issue_kiosk_token(
        session, admin, "Flur", [zone.id], control_allowed=True, expires_at=None
    )
    _with_kiosk_cookie(client, plaintext)

    response = client.get(path, follow_redirects=False)
    assert response.status_code in (
        status.HTTP_401_UNAUTHORIZED, status.HTTP_303_SEE_OTHER,
    )
    if response.status_code == status.HTTP_303_SEE_OTHER:
        assert response.headers["location"] in ("/login", "/setup")


# --- Remaining branches: no cookie at all, malformed input, domain errors ---------


def test_the_dashboard_without_any_cookie_shows_the_invalid_page(client: TestClient) -> None:
    """Not "wrong cookie" (covered above via the ordinary-API-token case) but no
    cookie whatsoever -- the first thing a browser that never visited /kiosk/... sends."""
    response = client.get("/kiosk")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.parametrize("action", ["setpoint", "boost"])
def test_a_mutation_without_any_kiosk_cookie_shows_the_invalid_page(
    action: str, client: TestClient, session: Session
) -> None:
    zone = create_zone(session, "flur")
    response = client.post(f"/kiosk/zones/{zone.id}/{action}")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_the_entry_cookie_is_capped_by_the_token_s_own_expiry(
    client: TestClient, session: Session
) -> None:
    """Distinct from an already-expired token (rejected outright, tested above): a
    token that still has time left must not hand out a cookie that outlives it."""
    zone = create_zone(session, "flur")
    admin = _admin(session)
    _token, plaintext = issue_kiosk_token(
        session, admin, "Flur", [zone.id], control_allowed=False,
        expires_at=utcnow() + timedelta(days=3),
    )

    response = client.get(f"/kiosk/{plaintext}", follow_redirects=False)
    assert response.status_code == status.HTTP_303_SEE_OTHER
    assert response.cookies[KIOSK_COOKIE_NAME] == plaintext


def test_the_dashboard_shows_a_shadow_decision_once_one_exists(
    client: TestClient, session: Session
) -> None:
    from thermoctl.db.models.state import ShadowDecision

    create_settings(session)
    zone = create_zone(session, "flur")
    session.add(ShadowDecision(
        zone_id=zone.id, decided_at=utcnow(), temperature_c=Decimal("20.0"),
        setpoint_c=Decimal("21.0"), setpoint_reason="Test", would_heat=True,
        outcome_code="waere_geschaltet", reason="Test",
    ))
    session.flush()
    admin = _admin(session)
    _token, plaintext = issue_kiosk_token(
        session, admin, "Flur", [zone.id], control_allowed=False, expires_at=None
    )
    _with_kiosk_cookie(client, plaintext)

    response = client.get("/kiosk")
    assert response.status_code == status.HTTP_200_OK
    assert "Heizt" in response.text


def test_an_unknown_direction_is_rejected_with_a_message(
    client: TestClient, session: Session
) -> None:
    zone = zone_with_schedule(
        session, "flur",
        [(1, 0, "tag", Decimal("21.0")), (1, 1320, "nacht", Decimal("18.0"))],
    )
    admin = _admin(session)
    _token, plaintext = issue_kiosk_token(
        session, admin, "Flur", [zone.id], control_allowed=True, expires_at=None
    )
    _with_kiosk_cookie(client, plaintext)

    response = client.post(
        f"/kiosk/zones/{zone.id}/setpoint",
        data={"direction": "seitwaerts"},
        headers=_csrf_headers(plaintext),
    )
    assert response.status_code == status.HTTP_200_OK
    assert "Unbekannte Richtung" in response.text


def test_a_setpoint_beyond_the_domain_limit_shows_up_as_an_error(
    client: TestClient, session: Session
) -> None:
    """Not a kiosk-specific rule -- `set_setpoint` enforces the same bound for every
    adapter. What is kiosk-specific is only that the rejection has to surface on a
    page nobody is logged into, instead of vanishing into a 500."""
    zone = zone_with_schedule(
        session, "flur",
        [(1, 0, "tag", Decimal("34.8")), (1, 1320, "nacht", Decimal("34.8"))],
    )
    admin = _admin(session)
    _token, plaintext = issue_kiosk_token(
        session, admin, "Flur", [zone.id], control_allowed=True, expires_at=None
    )
    _with_kiosk_cookie(client, plaintext)

    response = client.post(
        f"/kiosk/zones/{zone.id}/setpoint",
        data={"direction": "hoch"},
        headers=_csrf_headers(plaintext),
        follow_redirects=True,
    )
    assert response.status_code == status.HTTP_200_OK
    assert "zwischen" in response.text


def test_boosting_a_zone_without_a_schedule_shows_up_as_an_error(
    client: TestClient, session: Session
) -> None:
    create_settings(session)
    zone = create_zone(session, "flur")
    admin = _admin(session)
    _token, plaintext = issue_kiosk_token(
        session, admin, "Flur", [zone.id], control_allowed=True, expires_at=None
    )
    _with_kiosk_cookie(client, plaintext)

    response = client.post(
        f"/kiosk/zones/{zone.id}/boost", headers=_csrf_headers(plaintext), follow_redirects=True
    )
    assert response.status_code == status.HTTP_200_OK
    assert "Zeitplan" in response.text


# --- Remaining admin-page branches -------------------------------------------------


def test_issuing_with_an_unparseable_zone_id_is_rejected(
    client: TestClient, session: Session
) -> None:
    admin = _admin(session)
    _entry, secret = create_session(session, admin, 3600)
    client.cookies.set(COOKIE_NAME, secret)
    headers = {"X-CSRF-Token": csrf_token(secret, get_settings().secret_key.get_secret_value())}

    response = client.post(
        "/kiosk-tokens", data={"name": "Flur", "zone_id": "keine-zahl"}, headers=headers,
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST


def test_issuing_without_a_name_is_rejected_with_a_form_error(
    client: TestClient, session: Session
) -> None:
    zone = create_zone(session, "flur")
    admin = _admin(session)
    _entry, secret = create_session(session, admin, 3600)
    client.cookies.set(COOKIE_NAME, secret)
    headers = {"X-CSRF-Token": csrf_token(secret, get_settings().secret_key.get_secret_value())}

    response = client.post(
        "/kiosk-tokens", data={"name": "  ", "zone_id": str(zone.id)}, headers=headers,
    )
    assert response.status_code == status.HTTP_200_OK
    assert "braucht einen Namen" in response.text


def test_issuing_beyond_the_admin_s_own_scope_shows_a_form_error(
    client: TestClient, session: Session
) -> None:
    """The HTTP path for `test_a_token_carries_no_more_than_its_issuer_holds`: an
    admin with `token.manage` but no `zone.read` cannot hand out a kiosk token for a
    zone they cannot see themselves."""
    zone = create_zone(session, "flur")
    admin = _admin(session, [("token.manage", None)])
    _entry, secret = create_session(session, admin, 3600)
    client.cookies.set(COOKIE_NAME, secret)
    headers = {"X-CSRF-Token": csrf_token(secret, get_settings().secret_key.get_secret_value())}

    response = client.post(
        "/kiosk-tokens", data={"name": "Flur", "zone_id": str(zone.id)}, headers=headers,
    )
    assert response.status_code == status.HTTP_200_OK
    assert "das Recht fehlt ihm selbst" in response.text


def test_revoking_an_unknown_token_id_is_not_found(
    client: TestClient, session: Session
) -> None:
    admin = _admin(session)
    _entry, secret = create_session(session, admin, 3600)
    client.cookies.set(COOKIE_NAME, secret)
    headers = {"X-CSRF-Token": csrf_token(secret, get_settings().secret_key.get_secret_value())}

    response = client.post("/kiosk-tokens/999999/revoke", headers=headers)
    assert response.status_code == status.HTTP_404_NOT_FOUND


def test_revoking_an_ordinary_api_token_via_the_kiosk_endpoint_is_not_found(
    client: TestClient, session: Session
) -> None:
    """The kiosk revoke endpoint must not double as a back door to revoke a
    developer's own API token."""
    admin = _admin(session)
    zone = create_zone(session, "flur")
    developer_token, _plaintext = token_ausstellen(
        session, admin, "Entwickler-Token", [("zone.read", zone.id)], None
    )
    _entry, secret = create_session(session, admin, 3600)
    client.cookies.set(COOKIE_NAME, secret)
    headers = {"X-CSRF-Token": csrf_token(secret, get_settings().secret_key.get_secret_value())}

    response = client.post(
        f"/kiosk-tokens/{developer_token.id}/revoke", headers=headers
    )
    assert response.status_code == status.HTTP_404_NOT_FOUND
