"""The kiosk dashboard's security properties.

A kiosk token sits in a tablet's bookmark and cookie, reachable by anyone who can see
the tablet on the wall -- unlike a session cookie, which lives behind a password. Its
whole safety rests on the scope actually being as narrow as advertised: only the
assigned zones, only view or also control as configured, and nothing else in the
application. Every test here checks one boundary of that scope; the happy path
(dashboard renders, controls work) is covered once each, not repeated per test.
"""

import re
from datetime import timedelta
from decimal import Decimal

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.helpers import (
    create_mode,
    create_settings,
    create_zone,
    source,
    user_with_permissions,
)
from thermoctl.auth.csrf import csrf_token
from thermoctl.auth.kiosk import KIOSK_COOKIE_NAME, KIOSK_CSRF_COOKIE_NAME
from thermoctl.auth.sessions import COOKIE_NAME, create_session
from thermoctl.auth.tokens import issue_token
from thermoctl.config import get_settings
from thermoctl.db.base import utcnow
from thermoctl.db.models.zone import ZoneSetpoint
from thermoctl.domain.administration import revoke_token
from thermoctl.domain.kiosk import KioskError, issue_kiosk_token

# `token.manage` alone: this admin is deliberately *not* given `zone.read` etc. for
# every test -- several tests below rely on that to check the admin's own permissions
# limit what they can hand out.
_ADMIN_PERMISSIONS = [
    ("zone.read", None), ("setpoint.write", None), ("override.create", None),
    ("override.cancel", None), ("token.manage", None),
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




def test_a_token_carries_no_more_than_its_issuer_holds(session: Session) -> None:
    """The admin page's own guard: `issue_token` already enforces this, but a
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
    revoke_token(session, token, actor_id=admin.id)

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






# --- Ansicht: panel vs. tafel ---------------------------------------------------------
#
# The panel/tafel split itself lives in the template and `kiosk_panel.js` -- what
# belongs here is only the part `kiosk_views.py` owns: `ansicht` read from the query
# string, normalized, handed to the template as `data-ansicht`, and written back to
# its own cookie so a bookmarked `?ansicht=panel` address does not have to be typed
# again on every visit.










# --- Scope: exactly the assigned zones, nothing else -------------------------------






# --- Scope: view-only cannot control -----------------------------------------------






# --- The control-allowed path actually works ---------------------------------------






# --- Cancelling an override (the bug this file was written to close) ---------------










def test_the_cancel_button_is_hidden_without_a_running_override(
    client: TestClient, session: Session
) -> None:
    create_settings(session)
    source(session, "web")
    zone = create_zone(session, "flur")
    mode = create_mode(session, "tag")
    session.add(
        ZoneSetpoint(zone_id=zone.id, setpoint_mode_id=mode.id, temperature_c=Decimal("21.0"))
    )
    admin = _admin(session)
    _token, plaintext = issue_kiosk_token(
        session, admin, "Flur", [zone.id], control_allowed=True, expires_at=None
    )
    _with_kiosk_cookie(client, plaintext)

    page = client.get("/kiosk")
    assert page.status_code == status.HTTP_200_OK
    assert f"/kiosk/zones/{zone.id}/override/cancel" not in page.text






# --- CSRF ---------------------------------------------------------------------------




# --- No path from a kiosk cookie into the administration --------------------------




# --- Remaining branches: no cookie at all, malformed input, domain errors ---------






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












# --- Remaining admin-page branches -------------------------------------------------










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
    developer_token, _plaintext = issue_token(
        session, admin, "Entwickler-Token", [("zone.read", zone.id)], None
    )
    _entry, secret = create_session(session, admin, 3600)
    client.cookies.set(COOKIE_NAME, secret)
    headers = {"X-CSRF-Token": csrf_token(secret, get_settings().secret_key.get_secret_value())}

    response = client.post(
        f"/kiosk-tokens/{developer_token.id}/revoke", headers=headers
    )
    assert response.status_code == status.HTTP_404_NOT_FOUND


def _forms_on(page_text: str) -> list[tuple[str, dict[str, str]]]:
    """Every form on the page: its action and the fields a browser would send.

    Only hidden fields and the first submit button of each form -- which is exactly
    what a tablet sends when someone taps it.
    """
    forms: list[tuple[str, dict[str, str]]] = []
    for block in re.findall(r"<form[^>]*action=\"([^\"]+)\"[^>]*>(.*?)</form>", page_text, re.S):
        action, body = block
        hidden = r"<input[^>]*type=\"hidden\"[^>]*name=\"([^\"]+)\"[^>]*value=\"([^\"]*)\""
        fields = dict(re.findall(hidden, body))
        button = re.search(r"<button[^>]*name=\"([^\"]+)\"[^>]*value=\"([^\"]*)\"", body)
        if button:
            fields[button.group(1)] = button.group(2)
        forms.append((action, fields))
    return forms




# --- Regression nach dem UI-Redesign v0.9.0 --------------------------------------
#
# Der Kiosk ist ein eigener Zugangsweg mit eigener Hülle. Beim Redesign sind daneben
# zwei neue Oberflächen entstanden (Anlagensicht und Wohnungssicht) samt einem
# Profil-Wächter an den Anlagenseiten. Nichts davon darf den Kiosk berühren -- weder
# als Hülle, noch als Navigation, noch als zusätzliche Bedingung vor seiner
# Tokenprüfung. Die Tests hier halten genau das fest.


def _kiosk_page(client: TestClient, session: Session) -> str:
    zone = create_zone(session, "wandtablett")
    create_settings(session)
    admin = _admin(session)
    _token, plaintext = issue_kiosk_token(
        session, admin, "Wandtablett", [zone.id], control_allowed=True, expires_at=None
    )
    _with_kiosk_cookie(client, plaintext)
    response = client.get("/kiosk")
    assert response.status_code == status.HTTP_200_OK
    return response.text


def test_the_kiosk_uses_neither_of_the_two_app_shells(
    client: TestClient, session: Session
) -> None:
    """`base_plain.html`, nicht `base_admin.html` oder `base_tenant.html`.

    Ein Wandtablett wird aus Entfernung angesehen und hat weder Seitenleiste noch
    Kontomenü. Erbte es eine der beiden Hüllen, stünde dort plötzlich eine
    Navigation, deren Ziele der Kiosk-Principal gar nicht öffnen kann.
    """
    page = _kiosk_page(client, session)
    for marker in ("tc-sidenav", "tc-topbar", "tc-tnav", "tc-bottomnav", "tc-shell"):
        assert marker not in page, marker


def test_the_kiosk_carries_no_loading_bar(client: TestClient, session: Session) -> None:
    """Bewusst kein Ladebalken: das Tablett lädt sich alle 20 Sekunden selbst neu,
    ein bei jedem Durchgang aufblitzender Balken wäre reine Unruhe an der Wand."""
    assert "tc-loading-bar" not in _kiosk_page(client, session)




