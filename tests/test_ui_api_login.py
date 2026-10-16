"""JSON-Anmeldung und -Abmeldung der Svelte-Oberfläche."""

from fastapi.testclient import TestClient

from thermoctl.auth.csrf import CSRF_COOKIE_NAME
from thermoctl.auth.sessions import COOKIE_NAME
from thermoctl.db.models.identity import User


def test_login_sets_both_cookies_and_opens_session(client: TestClient, user: User) -> None:
    response = client.post(
        "/ui-api/login", json={"username": "lino", "password": "passwort-lang-genug"}
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert COOKIE_NAME in response.cookies
    assert CSRF_COOKIE_NAME in response.cookies
    assert client.get("/ui-api/bootstrap").status_code == 200


def test_wrong_password_is_json_401_without_cookies(client: TestClient, user: User) -> None:
    response = client.post("/ui-api/login", json={"username": "lino", "password": "falsch"})
    assert response.status_code == 401
    assert response.json() == {"detail": "Benutzername oder Passwort falsch."}
    assert COOKIE_NAME not in response.cookies


def test_unknown_user_gets_the_same_answer(client: TestClient, user: User) -> None:
    response = client.post("/ui-api/login", json={"username": "gibtsnicht", "password": "x"})
    assert response.status_code == 401
    assert response.json() == {"detail": "Benutzername oder Passwort falsch."}


def test_logout_revokes_session_and_clears_cookies(client: TestClient, user: User) -> None:
    client.post("/ui-api/login", json={"username": "lino", "password": "passwort-lang-genug"})
    token = client.cookies[CSRF_COOKIE_NAME]
    old_cookie = client.cookies[COOKIE_NAME]
    response = client.post("/ui-api/logout", headers={"X-CSRF-Token": token})
    assert response.status_code == 200
    client.cookies.set(COOKIE_NAME, old_cookie)
    assert client.get("/ui-api/bootstrap").status_code == 401


def test_logout_with_stale_token_clears_cookies_as_json(client: TestClient, user: User) -> None:
    client.post("/ui-api/login", json={"username": "lino", "password": "passwort-lang-genug"})
    response = client.post("/ui-api/logout", headers={"X-CSRF-Token": "veraltet"})
    assert response.status_code == 403
    assert response.json()["stale"] is True
    assert "max-age=0" in response.headers["set-cookie"].lower()


def test_oversized_login_input_is_the_neutral_401_and_audits_no_long_id(
    client: TestClient, user: User, session
) -> None:
    from sqlalchemy import select

    from thermoctl.db.models.operations import AuditEvent

    for body in (
        {"username": "u" * 65, "password": "x"},
        {"username": "lino", "password": "p" * 1025},
    ):
        response = client.post("/ui-api/login", json=body)
        assert response.status_code == 401
        assert response.json() == {"detail": "Benutzername oder Passwort falsch."}
    ids = session.scalars(select(AuditEvent.object_id)).all()
    assert all(i is None or len(i) <= 64 for i in ids)
    # the limits themselves are still accepted
    ok = client.post("/ui-api/login", json={"username": "lino", "password": "passwort-lang-genug"})
    assert ok.status_code == 200
