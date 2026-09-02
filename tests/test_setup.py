from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from thermoctl.db.base import utcnow
from thermoctl.db.models.credential import SetupToken
from thermoctl.db.models.identity import AccessGroup, User
from thermoctl.db.models.operations import Setting
from thermoctl.setup import (
    SETUP_TOKEN_LIFETIME,
    create_setup_token,
    run_setup,
    setup_needed,
)


def test_without_a_user_setup_is_needed(session: Session) -> None:
    assert setup_needed(session) is True


def test_with_a_user_it_is_no_longer_needed(session: Session, user) -> None:
    assert setup_needed(session) is False


def test_setup_without_a_token_is_rejected(client: TestClient, session: Session) -> None:
    response = client.post("/setup", data={"username": "lino", "display_name": "Lino",
                                          "password": "passwort-lang-genug",
                                          "timezone": "Europe/Berlin", "setup_token": ""})
    assert response.status_code == 403
    assert session.query(User).count() == 0


def test_setup_with_the_wrong_token_is_rejected(client: TestClient, session: Session) -> None:
    create_setup_token(session)
    response = client.post("/setup", data={"username": "lino", "display_name": "Lino",
                                          "password": "passwort-lang-genug",
                                          "timezone": "Europe/Berlin",
                                          "setup_token": "erraten"})
    assert response.status_code == 403
    assert session.query(User).count() == 0


def test_setup_creates_the_administrator_groups_and_settings(client: TestClient,
                                                           session: Session) -> None:
    marker = create_setup_token(session)
    response = client.post("/setup", data={"username": "lino", "display_name": "Lino",
                                          "password": "passwort-lang-genug",
                                          "timezone": "Europe/Berlin", "setup_token": marker},
                          follow_redirects=False)
    assert response.status_code == 303
    assert session.query(User).count() == 1
    assert {g.name for g in session.query(AccessGroup)} == {
        "Verwaltung", "Bedienung", "Nur lesen", "Integration"
    }
    assert session.get(Setting, 1) is not None


def test_setup_token_can_only_be_used_once(client: TestClient, session: Session) -> None:
    marker = create_setup_token(session)
    data = {"username": "a", "display_name": "A", "password": "passwort-lang-genug",
             "timezone": "Europe/Berlin", "setup_token": marker}
    client.post("/setup", data=data)
    second = client.post("/setup", data={**data, "username": "b"})
    # Exactly 404, not "some error": setup is permanently closed after this,
    # not merely forbidden — anyone requesting it should not learn that it
    # ever existed at all. An `in (403, 404)` would not have noticed a switch
    # between the two.
    assert second.status_code == 404
    assert session.query(User).count() == 1


def test_setup_is_closed_after_completion(client: TestClient, session: Session,
                                              user) -> None:
    assert client.get("/setup").status_code == 404


def test_the_first_user_is_an_administrator(client: TestClient, session: Session) -> None:
    marker = create_setup_token(session)
    client.post("/setup", data={"username": "lino", "display_name": "Lino",
                                "password": "passwort-lang-genug",
                                "timezone": "Europe/Berlin", "setup_token": marker})
    from thermoctl.domain.authz import has_permission, principal_for_user

    user = session.query(User).one()
    p = principal_for_user(session, user)
    assert has_permission(p, "user.manage") is True
    assert has_permission(p, "setting.manage") is True


def test_setup_token_does_not_appear_in_plaintext_in_the_database(session: Session) -> None:
    from thermoctl.db.models.credential import SetupToken

    marker = create_setup_token(session)
    assert session.query(SetupToken).one().token_hash != marker


def test_setup_with_a_too_short_password_returns_to_the_form(
    client: TestClient, session: Session
) -> None:
    """PasswordTooShort must not reach the caller as a 500 -- it is an input
    error, not a service fault. Fields already filled in (except the
    password) are kept in the form."""
    marker = create_setup_token(session)
    response = client.post(
        "/setup",
        data={"username": "lino", "display_name": "Lino", "password": "zukurz",
              "timezone": "Europe/Berlin", "setup_token": marker},
    )
    assert response.status_code == 200
    assert "mindestens" in response.text
    assert 'value="lino"' in response.text
    assert "zukurz" not in response.text
    assert session.query(User).count() == 0
    assert session.query(AccessGroup).count() == 0

    second_attempt = client.post(
        "/setup",
        data={"username": "lino", "display_name": "Lino",
              "password": "passwort-lang-genug", "timezone": "Europe/Berlin",
              "setup_token": marker},
        follow_redirects=False,
    )
    assert second_attempt.status_code == 303
    assert session.query(User).count() == 1


def test_setup_is_also_possible_only_once_in_the_domain(session: Session) -> None:
    """The view already checks — the domain checks again anyway.

    The setup wizard creates the first administrator. If the lock lived only
    in the view, a second call path would be enough to create another one
    past it.
    """
    import pytest

    from thermoctl.setup import create_setup_token, run_setup

    marker = create_setup_token(session)
    run_setup(
        session, username="erster", display_name="Erster",
        password="passwort-lang-genug", timezone_name="Europe/Berlin", token=marker,
    )
    second_marker = create_setup_token(session)
    with pytest.raises(PermissionError, match="bereits abgeschlossen"):
        run_setup(
            session, username="zweiter", display_name="Zweiter",
            password="passwort-lang-genug", timezone_name="Europe/Berlin", token=second_marker,
        )
    assert session.query(User).count() == 1


def test_the_start_page_redirects_to_setup_without_a_user(client: TestClient) -> None:
    """Without a single user, the login form leads nowhere. Anyone entering the
    service's address belongs at setup."""
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/setup"


def test_the_login_form_redirects_to_setup_without_a_user(client: TestClient) -> None:
    response = client.get("/login", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/setup"


def test_the_redirect_really_ends_at_setup(client: TestClient) -> None:
    """A cycle between / , /login and /setup would be the obvious bug: the
    status line of a single response would not show it, but a followed
    request would."""
    response = client.get("/", follow_redirects=True)
    assert response.status_code == 200
    assert response.url.path == "/setup"


def test_with_a_user_the_usual_path_remains(client: TestClient, user) -> None:
    """Counter-check for the three cases above. Without it, they would also be
    satisfied by a version that always redirects to setup -- and that would
    have made the service unusable once setup was complete."""
    start = client.get("/", follow_redirects=False)
    assert start.status_code == 303
    assert start.headers["location"] == "/login"

    form = client.get("/login")
    assert form.status_code == 200
    assert "/setup" not in form.headers.get("location", "")


def test_post_login_without_a_user_is_not_redirected(
    client: TestClient, monkeypatch
) -> None:
    """Deliberately only GET is redirected: a submitted login attempt should
    keep its identical error message, rather than letting the redirect
    target give away what the service knows about the username."""
    async def ohne_wartezeit(_: float) -> None:
        return None

    monkeypatch.setattr("thermoctl.web.auth_views.sleep", ohne_wartezeit)
    response = client.post(
        "/login",
        data={"username": "gibtsnicht", "password": "falsch-aber-lang"},
        follow_redirects=False,
    )
    assert response.status_code != 303
    assert "Benutzername oder Passwort falsch" in response.text


def test_an_expired_setup_token_no_longer_sets_anything_up(session: Session) -> None:
    """The one secret this project writes to the log must stop working quickly.

    It is written there on purpose -- the log is the only channel through which the
    operator gets it. What made that worse than necessary was that it never expired:
    read out of an old log, or out of a forwarded log aggregation, it still created
    the first administrator weeks later. Now it does not.
    """
    plaintext = create_setup_token(session)
    marke = session.query(SetupToken).one()
    marke.created_at = utcnow() - SETUP_TOKEN_LIFETIME - timedelta(minutes=1)
    session.flush()

    with pytest.raises(PermissionError, match="abgelaufenes"):
        run_setup(
            session,
            username="lino",
            display_name="Lino",
            password="ein-langes-passwort",
            timezone_name="Europe/Berlin",
            token=plaintext,
        )


def test_a_fresh_setup_token_still_works(session: Session) -> None:
    """The counter-test: an hour is plenty for whoever just started the container.

    Without this, the test above would also pass if the token had stopped working
    altogether.
    """
    plaintext = create_setup_token(session)

    benutzer = run_setup(
        session,
        username="lino",
        display_name="Lino",
        password="ein-langes-passwort",
        timezone_name="Europe/Berlin",
        token=plaintext,
    )

    assert benutzer.username == "lino"
