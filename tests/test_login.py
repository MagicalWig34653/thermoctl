import ast
import asyncio
import threading
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

import thermoctl.web.auth_views
from tests.helpers import create_settings
from thermoctl.auth.csrf import CSRF_COOKIE_NAME, CSRF_HEADER, csrf_token
from thermoctl.auth.sessions import COOKIE_NAME, create_session
from thermoctl.config import Settings
from thermoctl.db.base import utcnow
from thermoctl.db.models.credential import Session_
from thermoctl.db.models.operations import AuditEvent

# Beim Import festgehalten, bevor die autouse-Fixture aus conftest.py die Wartezeit
# fuer jeden Test durch eine Attrappe ersetzt. Ein Test, der die echte Wartezeit
# pruefen will, braucht genau diese Fassung -- sonst misst er die Attrappe.
_ECHTE_WARTEZEIT = thermoctl.web.auth_views.sleep


def _csrf(client: TestClient, settings: Settings) -> dict[str, str]:
    """Der Token, den die Oberflaeche aus dem Cookie liest und mitschickt.

    Nach einer echten Anmeldung steht er im CSRF-Cookie. Setzt ein Test seine
    Sitzung dagegen unmittelbar (client_als), gibt es das Cookie nicht -- dann
    wird der Token aus dem Sitzungsgeheimnis abgeleitet, so wie der Server es tut.
    """
    token = client.cookies.get(CSRF_COOKIE_NAME)
    if token is None:
        geheimnis = client.cookies.get(COOKIE_NAME)
        assert geheimnis is not None
        token = csrf_token(geheimnis, settings.secret_key.get_secret_value())
    return {CSRF_HEADER: token}


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


def test_logout_redirects_a_browser_directly_to_login_and_clears_both_cookies(
    client: TestClient, user
) -> None:
    client.post("/login", data={"username": "lino", "password": "passwort-lang-genug"})
    token = client.cookies[CSRF_COOKIE_NAME]

    response = client.post(
        "/logout", headers={"X-CSRF-Token": token}, follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    cookies = response.headers.get_list("set-cookie")
    assert any(COOKIE_NAME in entry and "Max-Age=0" in entry for entry in cookies)
    assert any(CSRF_COOKIE_NAME in entry and "Max-Age=0" in entry for entry in cookies)


def test_boosted_logout_uses_a_visible_browser_redirect(client: TestClient, user) -> None:
    client.post("/login", data={"username": "lino", "password": "passwort-lang-genug"})
    token = client.cookies[CSRF_COOKIE_NAME]

    response = client.post(
        "/logout",
        headers={"X-CSRF-Token": token, "HX-Request": "true", "HX-Boosted": "true"},
        follow_redirects=False,
    )

    assert response.status_code == 204
    assert response.headers["HX-Redirect"] == "/login"
    assert "location" not in response.headers
    cookies = response.headers.get_list("set-cookie")
    assert any(COOKIE_NAME in entry and "Max-Age=0" in entry for entry in cookies)
    assert any(CSRF_COOKIE_NAME in entry and "Max-Age=0" in entry for entry in cookies)


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

    async def merken(seconds: float) -> None:
        delays.append(seconds)

    monkeypatch.setattr("thermoctl.web.auth_views.sleep", merken)
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


def test_the_login_delay_does_not_block_the_event_loop() -> None:
    """The wait must yield the loop, not stop it.

    Login runs on the same event loop as the control cycle. A blocking `time.sleep`
    here -- which is what stood here -- stops that loop for everyone: an
    unauthenticated caller could hold up heating decisions just by posting to /login
    repeatedly, and the actuators would sit in their last state while the controller
    is stalled. The test therefore lets the real delay run and checks that a second
    task got to run during it.
    """
    async def szenario() -> int:
        ticks = 0

        async def ticker() -> None:
            nonlocal ticks
            while True:
                await asyncio.sleep(0.005)
                ticks += 1

        laeuft = asyncio.create_task(ticker())
        await _ECHTE_WARTEZEIT(0.1)
        laeuft.cancel()
        return ticks

    assert asyncio.run(szenario()) > 0


def test_the_password_check_does_not_run_on_the_event_loop(
    client: TestClient, user, monkeypatch
) -> None:
    """Argon2id is deliberately expensive, and that cost must be paid off the loop.

    Same reason as the delay above, and it applies to every login attempt, not just
    to repeated ones -- an unknown username is checked against a throwaway hash and
    costs exactly as much.
    """
    threads: list[threading.Thread] = []

    def merken(passwort: str, hash_wert: str) -> bool:
        threads.append(threading.current_thread())
        return False

    monkeypatch.setattr("thermoctl.web.auth_views.verify_password", merken)
    client.post("/login", data={"username": "lino", "password": "falsch-aber-lang"})

    assert threads
    assert all(t is not threading.main_thread() for t in threads)


def test_the_failure_counter_cannot_grow_without_bound(
    client: TestClient, user, monkeypatch
) -> None:
    """The counter is keyed by whatever name was typed -- an attacker picks the keys.

    Without a bound, a stream of invented usernames grows the dict for as long as the
    process runs. Nobody would notice until the machine did.
    """
    monkeypatch.setattr(thermoctl.web.auth_views, "_MAX_FEHLVERSUCHE_EINTRAEGE", 3)
    thermoctl.web.auth_views.FEHLVERSUCHE.clear()

    for nummer in range(10):
        client.post(
            "/login", data={"username": f"erfunden-{nummer}", "password": "falsch-aber-lang"}
        )

    assert len(thermoctl.web.auth_views.FEHLVERSUCHE) == 3
    # Behalten wird das Juengste: der Zaehler soll das gerade laufende Raten bremsen.
    assert "erfunden-9" in thermoctl.web.auth_views.FEHLVERSUCHE


def test_changing_the_own_password_ends_the_other_sessions_only(
    client: TestClient, user, session: Session, settings: Settings
) -> None:
    """Whoever changes their password because a cookie was stolen must lose that cookie.

    The opposite was the documented behaviour, with the argument that a password change
    is usually not a reaction to a suspicion. When it *is* one, leaving the other
    sessions alive defeats the point -- and nothing told the user it had happened. The
    browser doing the change stays logged in, so the change is not a self-lockout.
    """
    client.post(
        "/login", data={"username": "lino", "password": "passwort-lang-genug"}
    )
    fremde, _geheim = create_session(session, user, 3600)
    eigene_kennung = session.query(Session_).filter(
        Session_.id != fremde.id
    ).one().id

    antwort = client.post(
        f"/users/{user.id}/password",
        data={"password": "ein-anderes-langes-passwort"},
        headers=_csrf(client, settings),
        follow_redirects=False,
    )

    assert antwort.status_code == 303
    session.expire_all()
    assert session.get(Session_, fremde.id).revoked_at is not None
    assert session.get(Session_, eigene_kennung).revoked_at is None


def test_an_administrative_reset_ends_every_session_of_that_account(
    client_als, session: Session, user, settings: Settings
) -> None:
    """Resetting somebody else's password spares nothing -- that is the point.

    The exception exists so the browser in front of the person changing their own
    password keeps working. An administrator resetting a foreign account has no such
    browser to spare, and sparing one would leave exactly the access the reset is
    meant to cut.
    """
    fremde_a, _a = create_session(session, user, 3600)
    fremde_b, _b = create_session(session, user, 3600)
    verwaltung = client_als([("user.manage", None)])

    antwort = verwaltung.post(
        f"/users/{user.id}/password",
        data={"password": "von-der-verwaltung-gesetzt"},
        headers=_csrf(verwaltung, settings),
        follow_redirects=False,
    )

    assert antwort.status_code == 303
    session.expire_all()
    assert session.get(Session_, fremde_a.id).revoked_at is not None
    assert session.get(Session_, fremde_b.id).revoked_at is not None


def test_ending_the_other_sessions_without_changing_the_password(
    client: TestClient, user, session: Session, settings: Settings
) -> None:
    """A lost device should not force a password change to cut it loose.

    `set_password` claimed in its docstring that a dedicated way to do this existed.
    It did not, and this is it.
    """
    client.post(
        "/login", data={"username": "lino", "password": "passwort-lang-genug"}
    )
    verlorenes_geraet, _geheim = create_session(session, user, 3600)
    eigene_kennung = session.query(Session_).filter(
        Session_.id != verlorenes_geraet.id
    ).one().id

    antwort = client.post("/users/sessions/revoke-others", headers=_csrf(client, settings))

    assert antwort.status_code == 200
    session.expire_all()
    assert session.get(Session_, verlorenes_geraet.id).revoked_at is not None
    assert session.get(Session_, eigene_kennung).revoked_at is None
    # Und das Passwort ist unveraendert: die Anmeldung geht weiterhin.
    assert client.post(
        "/login", data={"username": "lino", "password": "passwort-lang-genug"},
        follow_redirects=False,
    ).status_code == 303


def test_every_cookie_the_service_sets_honours_the_secure_flag() -> None:
    """No cookie may be exempt from `secure_cookies`, not even a new one.

    The flag is off by default on purpose -- initial setup over `http://` would fail
    otherwise -- so behind TLS it has to be switched on, and then *every* cookie must
    follow. One that was written without it would go out unencrypted while the
    operator believes the setting covers them. Today all of them honour it; this test
    is here so the next one does too, in a file nobody thought to check.
    """
    verstoesse: list[str] = []
    for quelle in Path("thermoctl").rglob("*.py"):
        baum = ast.parse(quelle.read_text(encoding="utf-8"))
        for knoten in ast.walk(baum):
            if not isinstance(knoten, ast.Call):
                continue
            aufruf = knoten.func
            if not (isinstance(aufruf, ast.Attribute) and aufruf.attr == "set_cookie"):
                continue
            benannt = {stichwort.arg for stichwort in knoten.keywords}
            if "secure" not in benannt:
                verstoesse.append(f"{quelle}:{knoten.lineno}")

    assert not verstoesse, (
        "Diese set_cookie-Aufrufe setzen kein secure=: " + ", ".join(verstoesse)
    )
