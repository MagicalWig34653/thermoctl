"""Unauthentisierte Schnittstellen der Svelte-Oberfläche: Einrichtung, Anmeldeoptionen,
Rücksprungziel von Passwort- und Passkey-Anmeldung."""

import logging
from collections.abc import Iterator
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.helpers import create_user, source
from tests.webauthn_device import WebAuthnDevice
from thermoctl.auth.sessions import COOKIE_NAME
from thermoctl.config import get_settings
from thermoctl.db.base import utcnow
from thermoctl.db.models.credential import SetupToken
from thermoctl.db.models.identity import User
from thermoctl.db.models.operations import Setting
from thermoctl.domain.passkey import begin_registration, finish_registration
from thermoctl.setup import SETUP_TOKEN_LIFETIME, create_setup_token

PASSWORD = "passwort-lang-genug"


def _body(token: str, **override: str) -> dict[str, str]:
    body = {
        "username": "lino", "display_name": "Lino", "password": PASSWORD,
        "timezone": "Europe/Berlin", "setup_token": token,
    }
    body.update(override)
    return body


# -- GET /ui-api/setup ------------------------------------------------------------

def test_setup_status_is_open_without_a_user_and_says_nothing_else(
    client: TestClient, session: Session
) -> None:
    response = client.get("/ui-api/setup")
    assert response.status_code == 200
    assert response.json() == {"open": True}


def test_setup_status_is_404_once_a_user_exists(client: TestClient, user: User) -> None:
    response = client.get("/ui-api/setup")
    assert response.status_code == 404
    assert response.json() == {"detail": "Die Einrichtung ist bereits abgeschlossen."}


# -- POST /ui-api/setup -----------------------------------------------------------

def test_setup_creates_the_first_administrator_and_closes_itself(
    client: TestClient, session: Session
) -> None:
    token = create_setup_token(session)
    response = client.post("/ui-api/setup", json=_body(token))
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert session.query(User).count() == 1
    assert session.get(Setting, 1).timezone == "Europe/Berlin"  # type: ignore[union-attr]
    # Permanently closed: status and a second attempt, even with a fresh valid token.
    assert client.get("/ui-api/setup").status_code == 404
    second = create_setup_token(session)
    again = client.post("/ui-api/setup", json=_body(second, username="zweiter"))
    assert again.status_code == 404
    assert session.query(User).count() == 1
    # And the new account really signs in through the app's own login.
    login = client.post("/ui-api/login", json={"username": "lino", "password": PASSWORD})
    assert login.status_code == 200


def test_setup_trims_names_and_token(client: TestClient, session: Session) -> None:
    token = create_setup_token(session)
    response = client.post(
        "/ui-api/setup",
        json=_body(f" {token} ", username=" lino ", display_name=" Lino ", timezone=" UTC "),
    )
    assert response.status_code == 200
    created = session.query(User).one()
    assert (created.username, created.display_name) == ("lino", "Lino")


@pytest.mark.parametrize("token", ["", "erraten"])
def test_setup_with_missing_or_wrong_token_is_403_on_the_token_field(
    client: TestClient, session: Session, token: str
) -> None:
    create_setup_token(session)
    response = client.post("/ui-api/setup", json=_body(token))
    assert response.status_code == 403
    assert response.json()["field"] == "setup_token"
    assert session.query(User).count() == 0


def test_consumed_expired_and_unknown_tokens_are_indistinguishable(
    client: TestClient, session: Session
) -> None:
    unknown = client.post("/ui-api/setup", json=_body("unbekannt")).json()
    expired_token = create_setup_token(session)
    marker = session.query(SetupToken).one()
    marker.created_at = utcnow() - SETUP_TOKEN_LIFETIME - timedelta(minutes=1)
    session.flush()
    expired = client.post("/ui-api/setup", json=_body(expired_token)).json()
    marker.created_at = utcnow()
    marker.consumed_at = utcnow()
    session.flush()
    consumed = client.post("/ui-api/setup", json=_body(expired_token)).json()
    assert unknown == expired == consumed
    assert session.query(User).count() == 0


def test_a_too_short_password_is_a_field_error_and_keeps_the_token_usable(
    client: TestClient, session: Session
) -> None:
    token = create_setup_token(session)
    response = client.post("/ui-api/setup", json=_body(token, password="kurz"))
    assert response.status_code == 422
    assert response.json()["field"] == "password"
    assert "kurz" not in response.text
    assert session.query(User).count() == 0
    assert client.post("/ui-api/setup", json=_body(token)).status_code == 200


@pytest.mark.parametrize("zone", ["Mars/Olympus", "../etc/passwd", "/etc/localtime", "Europe"])
def test_an_unknown_timezone_is_a_field_error_and_writes_nothing(
    client: TestClient, session: Session, zone: str
) -> None:
    token = create_setup_token(session)
    response = client.post("/ui-api/setup", json=_body(token, timezone=zone))
    assert response.status_code == 422
    assert response.json()["field"] == "timezone"
    assert session.query(User).count() == 0
    assert session.get(Setting, 1) is None


@pytest.mark.parametrize(
    ("field", "value"),
    [("username", "  "), ("display_name", ""), ("timezone", " "),
     ("username", "x" * 65), ("display_name", "x" * 129), ("timezone", "x" * 65)],
)
def test_blank_or_overlong_fields_are_field_errors(
    client: TestClient, session: Session, field: str, value: str
) -> None:
    token = create_setup_token(session)
    response = client.post("/ui-api/setup", json=_body(token, **{field: value}))
    assert response.status_code == 422
    assert response.json()["field"] == field
    assert session.query(User).count() == 0


def test_a_malformed_body_is_422_and_changes_nothing(
    client: TestClient, session: Session
) -> None:
    create_setup_token(session)
    assert client.post("/ui-api/setup", json={"username": "x"}).status_code == 422
    assert client.post("/ui-api/setup", content=b"kein json").status_code == 422
    assert session.query(User).count() == 0


def test_the_token_never_reaches_the_log_or_the_response(
    client: TestClient, session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    token = create_setup_token(session)
    with caplog.at_level(logging.DEBUG):
        refused = client.post("/ui-api/setup", json=_body(token, setup_token="falsch-" + token))
        ok = client.post("/ui-api/setup", json=_body(token))
    assert refused.status_code == 403
    assert ok.status_code == 200
    assert token not in caplog.text
    assert token not in refused.text + ok.text
    assert PASSWORD not in caplog.text + refused.text + ok.text


def test_setup_with_a_foreign_session_cookie_and_no_csrf_header_is_refused(
    client: TestClient, session: Session
) -> None:
    token = create_setup_token(session)
    client.cookies.set(COOKIE_NAME, "irgendeine-sitzung")
    response = client.post("/ui-api/setup", json=_body(token))
    assert response.status_code == 403
    assert response.json()["stale"] is True
    assert session.query(User).count() == 0


def test_setup_works_behind_an_ingress_prefix(
    client_with_prefix: TestClient, session: Session
) -> None:
    token = create_setup_token(session)
    assert client_with_prefix.get("/ui-api/setup").status_code == 200
    assert client_with_prefix.post("/ui-api/setup", json=_body(token)).status_code == 200


# -- GET /ui-api/login-options ----------------------------------------------------

def test_login_options_without_passkey_configuration(client: TestClient) -> None:
    get_settings.cache_clear()
    response = client.get("/ui-api/login-options")
    assert response.status_code == 200
    assert response.json() == {"passkeys_available": False}


@pytest.fixture
def with_passkeys(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("THERMOCTL_PASSKEY_RP_ID", "localhost")
    monkeypatch.setenv("THERMOCTL_PASSKEY_ORIGIN", "https://localhost")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_login_options_with_passkey_configuration(
    client: TestClient, with_passkeys: None
) -> None:
    assert client.get("/ui-api/login-options").json() == {"passkeys_available": True}


# -- Rücksprungziel ---------------------------------------------------------------

GOOD_TARGET = "/oauth/consent?request=abc123"
BAD_TARGETS = [
    "//evil.example/x", "https://evil.example/", "/\\evil.example", "javascript:alert(1)",
    "/login", "/setup", "/%2e%2e/x", "/a/../b", "relativ", "",
]


def test_password_login_returns_only_the_server_checked_target(
    client: TestClient, user: User
) -> None:
    ok = client.post(
        "/ui-api/login",
        json={"username": "lino", "password": PASSWORD, "return_to": GOOD_TARGET},
    )
    assert ok.json() == {"ok": True, "return_to": GOOD_TARGET}


@pytest.mark.parametrize("target", BAD_TARGETS)
def test_password_login_drops_every_unsafe_target(
    client: TestClient, user: User, target: str
) -> None:
    ok = client.post(
        "/ui-api/login",
        json={"username": "lino", "password": PASSWORD, "return_to": target},
    )
    assert ok.status_code == 200
    assert ok.json() == {"ok": True}


def test_a_failed_login_leaks_no_target(client: TestClient, user: User) -> None:
    refused = client.post(
        "/ui-api/login",
        json={"username": "lino", "password": "falsch", "return_to": GOOD_TARGET},
    )
    assert refused.status_code == 401
    assert "return_to" not in refused.json()


def _passkey_login(
    client: TestClient, session: Session, **extra: object
) -> dict[str, object]:
    source(session, "web")
    user_record = create_user(session, "passkey-nutzer")
    device = WebAuthnDevice()
    settings = get_settings()
    options = begin_registration(session, settings, user_record)
    finish_registration(
        session, settings, user_record, device.register(options, "https://localhost"), "Gerät"
    )
    arguments = client.post("/passkey/authentication/options").json()
    assertion = device.log_in(arguments, "https://localhost")
    response = client.post("/passkey/authentication/verify", json={**assertion, **extra})
    assert response.status_code == 200
    result: dict[str, object] = response.json()
    return result


def test_passkey_login_returns_only_the_server_checked_target(
    client: TestClient, session: Session, with_passkeys: None
) -> None:
    result = _passkey_login(client, session, return_to=GOOD_TARGET)
    assert result == {"status": "signed_in", "redirect": "/", "return_to": GOOD_TARGET}


@pytest.mark.parametrize("target", [*BAD_TARGETS, 5, None, ["/x"]])
def test_passkey_login_drops_every_unsafe_target(
    client: TestClient, session: Session, with_passkeys: None, target: object
) -> None:
    result = _passkey_login(client, session, return_to=target)
    assert result == {"status": "signed_in", "redirect": "/"}


def test_passkey_login_with_a_target_but_a_bad_assertion_stays_refused(
    client: TestClient, with_passkeys: None
) -> None:
    response = client.post(
        "/passkey/authentication/verify", json={"id": "x", "return_to": GOOD_TARGET}
    )
    assert response.status_code == 401
    assert "return_to" not in response.text


@pytest.mark.parametrize(
    ("field", "limit"),
    [("username", 64), ("display_name", 128), ("timezone", 64), ("password", 1024),
     ("setup_token", 256)],
)
def test_setup_input_over_the_limit_is_a_422_on_that_field(
    client: TestClient, field: str, limit: int
) -> None:
    body = _body("irgendein-token", **{field: "a" * (limit + 1)})
    response = client.post("/ui-api/setup", json=body)
    assert response.status_code == 422
    assert response.json()["field"] == field
