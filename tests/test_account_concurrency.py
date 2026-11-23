"""Gleichzeitige Änderung am Konto: die Antworten, deterministisch und auf jeder Datenbank.

`test_administration_race_http.py` erzeugt die Kollision wirklich (nur MariaDB, und welche
Anfrage verliert, entscheidet der Zufall). Hier wird derselbe Fehler der Datenbank (1020) an
der Stelle eingespielt, an der sie ihn meldet, damit jeder Zweig -- 409 `concurrent_change`,
Wiederholung beim Abmelden, Neulesen beim Auflösen des Cookies, „als fehlgeschlagen“ bei der
Anmeldung -- auf SQLite wie auf MariaDB jedes Mal durchlaufen wird.
"""

import asyncio
from collections.abc import Callable
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import InvalidRequestError, OperationalError
from sqlalchemy.orm import Session

import thermoctl.web.ui_api_access as access
from tests.helpers import create_all_permissions, create_settings, source
from thermoctl.auth.csrf import CSRF_HEADER, csrf_token
from thermoctl.auth.sessions import COOKIE_NAME, create_session
from thermoctl.auth.tokens import issue_token
from thermoctl.config import get_settings
from thermoctl.db.base import utcnow
from thermoctl.db.models.credential import ApiToken, Session_
from thermoctl.db.models.identity import User
from thermoctl.db.models.passkey import UserPasskey
from thermoctl.web.shared.login import attempt_login

Als = Callable[[list[tuple[str, int | None]]], TestClient]
OWN = [("token.self", None)]


@pytest.fixture(autouse=True)
def _base(session: Session) -> None:
    create_settings(session)
    source(session, "web")
    create_all_permissions(session)


def _collision(*_args: object, **_kwargs: object) -> None:
    raise OperationalError("COMMIT", {}, Exception(1020, "Record has changed since last read"))


def _other_error(*_args: object, **_kwargs: object) -> None:
    raise OperationalError("COMMIT", {}, Exception(1146, "Table doesn't exist"))


def _call(client: TestClient, method: str, url: str, body: dict[str, Any] | None = None) -> Any:
    cookie = client.cookies.get(COOKIE_NAME)
    assert cookie is not None
    headers = {CSRF_HEADER: csrf_token(cookie, get_settings().secret_key.get_secret_value())}
    return client.request(method, url, json=body, headers=headers)


def _me(client: TestClient, session: Session) -> User:
    user = session.get(User, client.get("/ui-api/bootstrap").json()["user_id"])
    assert user is not None
    return user


# -- die Endpunkte: Kollision beim Festschreiben = 409, nichts geändert ------------------


def test_a_collision_at_the_commit_is_a_409_and_changes_nothing(
    client_als: Als, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = client_als(OWN)
    user = _me(client, session)
    other_session = create_session(session, user, 3600)[0].id
    token, _plain = issue_token(session, user, "t", OWN, None)
    passkey = UserPasskey(user_id=user.id, credential_id="c" * 20, public_key="k", label="x")
    session.add(passkey)
    session.commit()  # the failing request rolls back; what the test set up must survive

    for method, url, body in [
        ("POST", "/ui-api/account/sessions/revoke-others", None),
        ("POST", f"/ui-api/account/sessions/{other_session}/revoke", None),
        ("PUT", f"/ui-api/access/tokens/{token.id}/permissions",
         {"permissions": [{"code": "token.self"}]}),
        ("POST", "/ui-api/access/tokens", {"name": "neu", "permissions": [{"code": "token.self"}]}),
        ("DELETE", f"/ui-api/account/passkeys/{passkey.id}", None),
    ]:
        with monkeypatch.context() as patched:
            patched.setattr(session, "commit", _collision)
            reply = _call(client, method, url, body)
        assert reply.status_code == 409, (url, reply.text)
        assert reply.json()["code"] == "concurrent_change", url

    entry = session.get(Session_, other_session)
    assert entry is not None and entry.revoked_at is None
    assert session.get(UserPasskey, passkey.id) is not None
    assert session.scalar(select(ApiToken).where(ApiToken.name == "neu")) is None


def test_a_token_that_is_gone_when_the_lock_is_taken_is_a_404(
    client_als: Als, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deleted or taken over between the first look and the locked read: not found, no 500."""
    client = client_als(OWN)
    user = _me(client, session)
    token, _plain = issue_token(session, user, "t", OWN, None)
    session.commit()
    monkeypatch.setattr(access, "_locked_row", lambda *args, **kwargs: None)

    reply = _call(
        client, "PUT", f"/ui-api/access/tokens/{token.id}/permissions",
        {"permissions": [{"code": "token.self"}]},
    )

    assert reply.status_code == 404 and reply.json()["code"] == "not_found"


# -- Abmelden: Wiederholung, danach „noch angemeldet“ ------------------------------------


def test_logout_retries_after_a_collision_and_ends_the_session(
    client_als: Als, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = client_als(OWN)
    session.commit()
    real_commit, calls = session.commit, []

    def first_collides() -> None:
        calls.append(1)
        if len(calls) == 1:
            _collision()
        real_commit()

    monkeypatch.setattr(session, "commit", first_collides)

    reply = _call(client, "POST", "/ui-api/logout")

    assert reply.status_code == 200 and reply.json() == {"ok": True}
    assert len(calls) == 2
    ended = session.scalars(select(Session_).where(Session_.revoked_at.is_not(None))).all()
    assert len(ended) == 1


def test_logout_that_keeps_colliding_says_so_and_does_not_pretend(
    client_als: Als, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = client_als(OWN)
    session.commit()
    monkeypatch.setattr(session, "commit", _collision)

    reply = _call(client, "POST", "/ui-api/logout")

    assert reply.status_code == 409 and reply.json()["code"] == "concurrent_change"
    assert "noch angemeldet" in reply.json()["detail"]


def test_logout_of_an_already_ended_session_is_a_success(client_als: Als) -> None:
    client = client_als(OWN)
    cookie = client.cookies.get(COOKIE_NAME)
    assert _call(client, "POST", "/ui-api/logout").status_code == 200
    client.cookies.set(COOKIE_NAME, cookie)  # the browser still sends the old cookie
    assert _call(client, "POST", "/ui-api/logout").status_code == 200


# -- Cookie auflösen: Zeitstempel dort schreiben, wo eine Kollision behandelt wird -------


def _idle(session: Session, user: User) -> None:
    for entry in session.scalars(select(Session_).where(Session_.user_id == user.id)):
        entry.last_seen_at = utcnow() - timedelta(minutes=5)
    session.commit()


def test_a_collision_while_resolving_the_cookie_is_read_again(
    client_als: Als, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = client_als(OWN)
    _idle(session, _me(client, session))
    real_flush, calls = session.flush, []

    def first_collides(*args: Any, **kwargs: Any) -> None:
        # Only the write of the time stamp collides, not the autoflush of the query before it.
        if session.dirty and not calls:
            calls.append(1)
            _collision()
        real_flush(*args, **kwargs)

    monkeypatch.setattr(session, "flush", first_collides)

    assert client.get("/ui-api/account").status_code == 200


def test_a_cookie_that_keeps_colliding_is_a_409_not_a_500(
    client_als: Als, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = client_als(OWN)
    _idle(session, _me(client, session))
    real_flush = session.flush

    def collides_on_the_write(*args: Any, **kwargs: Any) -> None:
        if session.dirty:
            _collision()
        real_flush(*args, **kwargs)

    monkeypatch.setattr(session, "flush", collides_on_the_write)

    assert client.get("/ui-api/account").status_code == 409


def test_another_database_error_while_resolving_the_cookie_is_not_swallowed(
    client_als: Als, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = client_als(OWN)
    _idle(session, _me(client, session))
    real_flush = session.flush

    def breaks_on_the_write(*args: Any, **kwargs: Any) -> None:
        if session.dirty:
            _other_error()
        real_flush(*args, **kwargs)

    monkeypatch.setattr(session, "flush", breaks_on_the_write)

    with pytest.raises(OperationalError):
        client.get("/ui-api/account")


# -- Anmeldung: Kollision oder gelöschtes Konto nach der Prüfung = fehlgeschlagen --------


def _request() -> Any:
    return SimpleNamespace(headers={}, client=None)


def test_a_collision_when_the_account_is_read_again_counts_as_a_failed_login(
    user: User, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(session, "refresh", _collision)

    assert asyncio.run(attempt_login(_request(), session, "lino", "passwort-lang-genug")) is None


def test_an_account_deleted_after_the_check_counts_as_a_failed_login(
    user: User, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    def gone(*_args: object, **_kwargs: object) -> None:
        raise InvalidRequestError("Could not refresh instance")

    monkeypatch.setattr(session, "refresh", gone)

    assert asyncio.run(attempt_login(_request(), session, "lino", "passwort-lang-genug")) is None


def test_another_database_error_when_the_account_is_read_again_is_not_swallowed(
    user: User, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(session, "refresh", _other_error)

    with pytest.raises(OperationalError):
        asyncio.run(attempt_login(_request(), session, "lino", "passwort-lang-genug"))
