from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

import thermoctl.web.auth_views
from tests.helpers import create_settings
from thermoctl.auth.csrf import CSRF_COOKIE_NAME, csrf_token
from thermoctl.auth.sessions import create_session
from thermoctl.config import Settings
from thermoctl.db.base import utcnow
from thermoctl.db.models.credential import Session_
from thermoctl.db.models.operations import AuditEvent


def test_login_with_the_correct_password(client: TestClient, user) -> None:
    response = client.post("/login", data={"username": "lino", "password": "passwort-lang-genug"},
                          follow_redirects=False)
    assert response.status_code == 303
    assert "thermoctl_session" in response.cookies


def test_login_with_the_wrong_password_fails(client: TestClient, user) -> None:
    response = client.post("/login", data={"username": "lino", "password": "falsch-aber-lang"})
    assert response.status_code == 401
    assert "thermoctl_session" not in response.cookies


def test_the_error_message_does_not_reveal_whether_the_user_exists(
    client: TestClient, user
) -> None:
    a = client.post("/login", data={"username": "lino", "password": "falsch-aber-lang"})
    b = client.post("/login", data={"username": "gibtsnicht", "password": "falsch-aber-lang"})
    assert a.status_code == b.status_code == 401
    assert a.text == b.text


def test_cookie_is_httponly_and_samesite(client: TestClient, user) -> None:
    response = client.post("/login", data={"username": "lino", "password": "passwort-lang-genug"},
                          follow_redirects=False)
    header = response.headers["set-cookie"].lower()
    assert "httponly" in header
    assert "samesite=lax" in header


def test_cookie_does_not_contain_the_stored_hash(client: TestClient, user,
                                                      session: Session) -> None:
    response = client.post("/login", data={"username": "lino", "password": "passwort-lang-genug"},
                          follow_redirects=False)
    stored = session.query(Session_).one().token_hash
    assert stored not in response.headers["set-cookie"]


def test_an_inactive_user_cannot_get_in(client: TestClient, user,
                                               session: Session) -> None:
    user.is_active = False
    session.flush()
    response = client.post(
        "/login", data={"username": "lino", "password": "passwort-lang-genug"}
    )
    assert response.status_code == 401


def test_logging_out_revokes_the_session(client: TestClient, user, session: Session) -> None:
    client.post("/login", data={"username": "lino", "password": "passwort-lang-genug"})
    # The application itself hands out the valid token through its own,
    # non-httpOnly cookie -- exactly what HTMX in the interface would read
    # and send back as a header.
    token = client.cookies[CSRF_COOKIE_NAME]
    client.post("/logout", headers={"X-CSRF-Token": token})
    assert session.query(Session_).one().revoked_at is not None


def test_a_logout_without_a_csrf_token_is_not_carried_out(
    client: TestClient, user, session: Session
) -> None:
    """Refused as before -- but no longer a dead end.

    These two tests asserted a plain 403, and that was exactly the behaviour reported
    from use: with a stale page, the way *out* of it was blocked as well. `/logout` is
    a recovery path now: the request is still not carried out (the session row keeps
    its `revoked_at`), the cookies are cleared, and the browser lands on the login
    form.
    """
    client.post("/login", data={"username": "lino", "password": "passwort-lang-genug"})

    response = client.post("/logout", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login?stale=1"
    assert session.query(Session_).one().revoked_at is None, (
        "Ohne gueltiges Token darf keine Sitzung serverseitig widerrufen werden"
    )


def test_a_logout_with_a_token_from_a_foreign_session_revokes_nothing(
    client: TestClient, user, session: Session, settings: Settings
) -> None:
    """A foreign token proves nothing, so it must not end somebody else's session.

    What it may do is clear the cookies of the browser it came from -- the deliberate
    concession that keeps a stale page from stranding its user. Whose session dies is
    the point here: nobody's.
    """
    client.post("/login", data={"username": "lino", "password": "passwort-lang-genug"})
    _foreign_session, foreign_secret = create_session(session, user, 3600)
    foreign_token = csrf_token(foreign_secret, settings.secret_key.get_secret_value())

    response = client.post(
        "/logout", headers={"X-CSRF-Token": foreign_token}, follow_redirects=False
    )

    assert response.status_code == 303
    assert [s.revoked_at for s in session.query(Session_).all()] == [None, None]


def test_login_and_failed_attempt_land_in_the_audit_log(client: TestClient, user,
                                                   session: Session) -> None:
    client.post("/login", data={"username": "lino", "password": "falsch-aber-lang"})
    client.post("/login", data={"username": "lino", "password": "passwort-lang-genug"})
    actions = [e.action for e in session.query(AuditEvent).all()]
    assert "login_failed" in actions
    assert "login" in actions


def test_the_password_appears_in_no_response(client: TestClient, user) -> None:
    response = client.post("/login", data={"username": "lino", "password": "passwort-lang-genug"})
    assert "passwort-lang-genug" not in response.text


def test_failed_attempts_are_increasingly_delayed(client, user, monkeypatch) -> None:
    delays: list[float] = []
    monkeypatch.setattr("thermoctl.web.auth_views.sleep", delays.append)
    for _ in range(3):
        client.post("/login", data={"username": "lino", "password": "falsch-aber-lang"})
    assert delays == sorted(delays)
    assert delays[-1] > delays[0]


def test_a_successful_login_resets_the_counter(client, user) -> None:
    client.post("/login", data={"username": "lino", "password": "falsch-aber-lang"})
    client.post("/login", data={"username": "lino", "password": "passwort-lang-genug"})
    from thermoctl.web.auth_views import FEHLVERSUCHE

    assert FEHLVERSUCHE.get("lino", 0) == 0


def test_session_duration_comes_from_the_settings_row(
    client: TestClient, user, session: Session
) -> None:
    create_settings(session, session_duration_s=3600)
    # Truncated to whole seconds: MariaDB stores DATETIME with no precision
    # spec at second resolution and drops the fractional part. A
    # microsecond-precise comparison would fail there by milliseconds,
    # without anything actually being wrong.
    before_login = utcnow().replace(microsecond=0)
    client.post(
        "/login", data={"username": "lino", "password": "passwort-lang-genug"},
        follow_redirects=False,
    )
    after_login = utcnow().replace(microsecond=0) + timedelta(seconds=1)
    expiry = session.query(Session_).one().expires_at
    # 3600 s from the settings row instead of the built-in 14-day default.
    assert (before_login + timedelta(seconds=3600)) <= expiry
    assert expiry <= (after_login + timedelta(seconds=3600))

def test_password_verification_runs_even_for_an_unknown_user(
    client: TestClient, user, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Otherwise the response time would reveal which accounts exist.

    Argon2id is deliberately slow. If verification were skipped for an
    unknown username, the request would be measurably faster than for an
    existing one -- regardless of the message and wait time being the same.
    The test counts the calls instead of measuring time: timing measurements
    in tests are unreliable, but the underlying cause can be checked directly.
    """
    calls: list[str] = []
    real_verify = thermoctl.web.auth_views.verify_password

    def counting(plaintext: str, hash_value: str) -> bool:
        calls.append(hash_value)
        return real_verify(plaintext, hash_value)

    monkeypatch.setattr("thermoctl.web.auth_views.verify_password", counting)

    client.post("/login", data={"username": "gibtsnicht", "password": "falsch-aber-lang"})
    assert len(calls) == 1, "no check happened for an unknown user"

    calls.clear()
    client.post("/login", data={"username": "lino", "password": "falsch-aber-lang"})
    assert len(calls) == 1, "no check happened for a known user"
