"""The two WebAuthn ceremonies, with a real software authenticator.

`soft-webauthn` produces genuine signatures. That is the difference between a
test that proves a passkey works, and one that only proves everything gets
rejected — the latter would also stay green even if login had never been built
at all.
"""


import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.helpers import create_user, source
from tests.webauthn_device import WebAuthnDevice
from thermoctl.config import Settings, get_settings
from thermoctl.db.models.identity import User
from thermoctl.db.models.operations import AuditEvent
from thermoctl.db.models.passkey import PasskeyChallenge, UserPasskey
from thermoctl.domain import passkey as passkey_modul
from thermoctl.domain.passkey import (
    PasskeyError,
    begin_authentication,
    begin_registration,
    finish_registration,
    verify_authentication,
)

RP_ID = "localhost"
ORIGIN = "https://localhost"


@pytest.fixture
def passkey_settings() -> Settings:
    return Settings(
        _env_file=None, database_url="sqlite://", secret_key="p" * 32,
        passkey_rp_id=RP_ID, passkey_origin=ORIGIN,
    )


@pytest.fixture(autouse=True)
def _source(session: Session) -> None:
    source(session, "web")


def _register(
    session: Session, passkey_settings: Settings, user_record: User, device: WebAuthnDevice
) -> UserPasskey:
    argumente = begin_registration(session, passkey_settings, user_record)
    response = device.register(argumente, ORIGIN)
    return finish_registration(
        session, passkey_settings, user_record, response, "Testgerät"
    )


def test_a_registered_passkey_really_logs_in(
    session: Session, passkey_settings: Settings
) -> None:
    """The counter-proof: without it, the suite only proved that everything gets rejected."""
    user_record = create_user(session, "passkey-nutzer")
    device = WebAuthnDevice()
    entry = _register(session, passkey_settings, user_record, device)
    assert entry.label == "Testgerät"

    argumente = begin_authentication(session, passkey_settings)
    response = device.log_in(argumente, ORIGIN)
    registered = verify_authentication(session, passkey_settings, response)
    assert registered.id == user_record.id


def test_a_challenge_is_consumed_even_on_failure(
    session: Session, passkey_settings: Settings
) -> None:
    """A reusable challenge cancels out the protection it is supposed to provide."""
    user_record = create_user(session, "einmal-nutzer")
    device = WebAuthnDevice()
    _register(session, passkey_settings, user_record, device)

    argumente = begin_authentication(session, passkey_settings)
    response = device.log_in(argumente, ORIGIN)

    assert verify_authentication(session, passkey_settings, response).id == user_record.id
    # The exact same response a second time — the challenge is spent.
    with pytest.raises(PasskeyError):
        verify_authentication(session, passkey_settings, response)


def test_a_login_challenge_is_no_good_for_a_registration(
    session: Session, passkey_settings: Settings
) -> None:
    """Without binding it to the ceremony, a challenge could be repurposed for the wrong use."""
    user_record = create_user(session, "zweck-nutzer")
    device = WebAuthnDevice()
    argumente = begin_authentication(session, passkey_settings)

    # The authenticator creates a new key, but with the login's challenge — the
    # registration must detect this.
    registrierungsargumente = begin_registration(session, passkey_settings, user_record)
    registrierungsargumente["challenge"] = argumente["challenge"]
    response = device.register(registrierungsargumente, ORIGIN)

    with pytest.raises(PasskeyError, match="anmeldung"):
        finish_registration(
            session, passkey_settings, user_record, response, "Falsch"
        )


def test_an_expired_challenge_is_refused(
    session: Session, passkey_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import timedelta

    user_record = create_user(session, "spaet-nutzer")
    device = WebAuthnDevice()
    _register(session, passkey_settings, user_record, device)

    argumente = begin_authentication(session, passkey_settings)
    response = device.log_in(argumente, ORIGIN)
    # Age the row instead of waiting.
    entry = session.scalars(select(PasskeyChallenge)).one()
    entry.created_at = entry.created_at - timedelta(minutes=5)
    session.flush()

    with pytest.raises(PasskeyError, match="abgelaufen"):
        verify_authentication(session, passkey_settings, response)


def test_a_counter_that_went_backwards_ends_the_login(
    session: Session, passkey_settings: Settings
) -> None:
    """The only sign of a cloned authenticator that the procedure knows about."""
    user_record = create_user(session, "klon-nutzer")
    device = WebAuthnDevice()
    entry = _register(session, passkey_settings, user_record, device)

    argumente = begin_authentication(session, passkey_settings)
    response = device.log_in(argumente, ORIGIN)
    # The service has already seen a higher count than the device is reporting now.
    entry.sign_count = 9999
    session.flush()

    with pytest.raises(PasskeyError):
        verify_authentication(session, passkey_settings, response)


def test_a_disabled_account_is_checked_only_after_the_signature(
    session: Session, passkey_settings: Settings
) -> None:
    """The same order as in the password path — otherwise the behavior would
    reveal which accounts exist."""
    user_record = create_user(session, "gesperrt-nutzer")
    device = WebAuthnDevice()
    _register(session, passkey_settings, user_record, device)
    user_record.is_active = False
    session.flush()

    argumente = begin_authentication(session, passkey_settings)
    response = device.log_in(argumente, ORIGIN)
    with pytest.raises(PasskeyError):
        verify_authentication(session, passkey_settings, response)

    log = session.scalars(
        select(AuditEvent).where(AuditEvent.action == "login_failed")
    ).all()
    assert log and "gesperrt" in (log[-1].detail or "").lower()


def test_a_foreign_origin_is_refused(
    session: Session, passkey_settings: Settings
) -> None:
    """The protection against imitation sites. Without it, a passkey is just a password."""
    user_record = create_user(session, "origin-nutzer")
    device = WebAuthnDevice()
    _register(session, passkey_settings, user_record, device)

    argumente = begin_authentication(session, passkey_settings)
    # The authenticator responds to a different site.
    response = device.log_in(argumente, "https://boese.example")
    with pytest.raises(PasskeyError):
        verify_authentication(session, passkey_settings, response)


def test_an_unknown_passkey_is_refused(
    session: Session, passkey_settings: Settings
) -> None:
    user_record = create_user(session, "unbekannt-nutzer")
    device = WebAuthnDevice()
    _register(session, passkey_settings, user_record, device)

    fremdes = WebAuthnDevice()
    argumente = begin_registration(session, passkey_settings, user_record)
    fremdes.register(argumente, ORIGIN)

    login = begin_authentication(session, passkey_settings)
    response = fremdes.log_in(login, ORIGIN)
    with pytest.raises(PasskeyError):
        verify_authentication(session, passkey_settings, response)


def test_the_same_passkey_cannot_be_stored_twice(
    session: Session, passkey_settings: Settings
) -> None:
    user_record = create_user(session, "doppelt-nutzer")
    device = WebAuthnDevice()
    _register(session, passkey_settings, user_record, device)
    with pytest.raises(PasskeyError, match="bereits hinterlegt"):
        _register(session, passkey_settings, user_record, device)


def test_old_challenges_are_cleaned_up(
    session: Session, passkey_settings: Settings
) -> None:
    from datetime import timedelta

    begin_authentication(session, passkey_settings)
    begin_authentication(session, passkey_settings)
    for entry in session.scalars(select(PasskeyChallenge)):
        entry.created_at = entry.created_at - timedelta(hours=1)
    session.flush()
    frisch = begin_authentication(session, passkey_settings)

    entfernt = passkey_modul.cleanup_old_challenges(session)
    assert entfernt == 2
    uebrig = session.scalars(select(PasskeyChallenge)).all()
    assert len(uebrig) == 1 and uebrig[0].challenge == frisch["challenge"]


def test_without_a_relying_party_id_the_routes_do_not_exist(client: TestClient) -> None:
    """Passkeys are either set up or not there at all — never halfway."""
    get_settings.cache_clear()
    assert client.post("/passkey/authentication/options").status_code == 404
    assert client.post("/passkey/authentication/verify", json={}).status_code == 404


@pytest.fixture
def mit_passkeys(monkeypatch: pytest.MonkeyPatch, passkey_settings) -> None:  # type: ignore[no-untyped-def]
    """Enables passkeys for the HTTP tests."""
    monkeypatch.setenv("THERMOCTL_PASSKEY_RP_ID", RP_ID)
    monkeypatch.setenv("THERMOCTL_PASSKEY_ORIGIN", ORIGIN)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _csrf(client: TestClient) -> dict[str, str]:
    from thermoctl.auth.csrf import CSRF_HEADER, csrf_token
    from thermoctl.auth.sessions import COOKIE_NAME

    secret = client.cookies[COOKIE_NAME]
    return {CSRF_HEADER: csrf_token(secret, get_settings().secret_key.get_secret_value())}


def test_authentication_options_are_served_without_being_logged_in(
    client: TestClient, mit_passkeys: None
) -> None:
    """Everyone must be able to fetch the arguments — otherwise there would be no
    way in."""
    response = client.post("/passkey/authentication/options")
    assert response.status_code == 200
    argumente = response.json()
    assert argumente["rpId"] == RP_ID
    assert argumente["challenge"]
    # Without `allowCredentials`: a list would reveal whether an account exists
    # and how many passkeys it has.
    assert not argumente.get("allowCredentials")


def test_a_nonsensical_login_is_refused_uniformly(
    client: TestClient, mit_passkeys: None
) -> None:
    """Same response for every failure — otherwise it would reveal which accounts exist."""
    for payload in ({}, {"id": "gibtesnicht"}, {"response": {}}):
        response = client.post("/passkey/authentication/verify", json=payload)
        assert response.status_code == 401, payload
        assert response.json()["notice"] == "Die Anmeldung war nicht erfolgreich."


def test_logging_in_through_the_http_route(
    client: TestClient, session: Session, mit_passkeys: None
) -> None:
    """The whole path the way the browser takes it — up to the session cookie being set."""
    from thermoctl.auth.sessions import COOKIE_NAME

    user_record = create_user(session, "http-nutzer")
    device = WebAuthnDevice()
    passkey_settings = get_settings()
    _register(session, passkey_settings, user_record, device)

    argumente = client.post("/passkey/authentication/options").json()
    response = client.post(
        "/passkey/authentication/verify", json=device.log_in(argumente, ORIGIN)
    )
    assert response.status_code == 200
    assert response.json() == {"status": "signed_in", "redirect": "/"}
    assert client.cookies.get(COOKIE_NAME)
    # And the session really carries: a protected page now responds.
    assert client.get("/passkeys").status_code == 200


def test_registration_requires_being_logged_in(
    client: TestClient, mit_passkeys: None
) -> None:
    assert client.post("/passkey/registration/options").status_code == 401
    assert client.post("/passkey/registration/verify", json={}).status_code == 401


def test_registering_through_the_http_route(
    client_als, session: Session, mit_passkeys: None
) -> None:
    c = client_als([("zone.read", None)])
    argumente = c.post(
        "/passkey/registration/options", headers=_csrf(c)
    ).json()
    device = WebAuthnDevice()
    payload = device.register(argumente, ORIGIN)
    payload["label"] = "Mein Telefon"
    response = c.post("/passkey/registration/verify", json=payload, headers=_csrf(c))
    assert response.status_code == 200, response.text
    assert response.json()["label"] == "Mein Telefon"
    assert session.scalars(select(UserPasskey)).all()


def test_a_foreign_passkey_cannot_be_removed(
    client_als, session: Session, mit_passkeys: None
) -> None:
    """404 instead of 403 — otherwise the response would reveal which ids exist."""
    from tests.helpers import create_passkey

    fremder = create_user(session, "fremder-passkeybesitzer")
    entry = create_passkey(session, fremder, "fremde-kennung")
    c = client_als([("zone.read", None)])
    response = c.post(f"/passkeys/{entry.id}/remove", headers=_csrf(c))
    assert response.status_code == 404
    assert session.get(UserPasskey, entry.id) is not None


def test_your_own_passkey_can_be_removed(
    client_als, session: Session, mit_passkeys: None
) -> None:
    from thermoctl.db.models.identity import User as Konto

    c = client_als([("zone.read", None)])
    argumente = c.post("/passkey/registration/options", headers=_csrf(c)).json()
    payload = WebAuthnDevice().register(argumente, ORIGIN)
    payload["label"] = "Weg damit"
    c.post("/passkey/registration/verify", json=payload, headers=_csrf(c))

    entry = session.scalars(
        select(UserPasskey).where(UserPasskey.label == "Weg damit")
    ).one()
    assert c.post(
        f"/passkeys/{entry.id}/remove", headers=_csrf(c), follow_redirects=False
    ).status_code == 303
    assert session.get(UserPasskey, entry.id) is None
    assert session.scalars(select(Konto)).all()


def test_the_passkey_page_explains_a_missing_setup(client_als) -> None:
    """Without a relying-party id, no button that could do nothing -- instead a
    sentence saying what is missing."""
    get_settings.cache_clear()
    response = client_als([("zone.read", None)]).get("/passkeys")
    assert response.status_code == 200
    assert "THERMOCTL_PASSKEY_RP_ID" in response.text


def test_the_login_page_offers_passkeys_only_when_set_up(
    client: TestClient, user, monkeypatch: pytest.MonkeyPatch
) -> None:
    # `benutzer`: without one, /login redirects to setup, and the check below
    # would be green without ever having seen the login form.
    get_settings.cache_clear()
    assert "passkey-login" not in client.get("/login").text

    monkeypatch.setenv("THERMOCTL_PASSKEY_RP_ID", RP_ID)
    get_settings.cache_clear()
    assert "passkey-login" in client.get("/login").text
    get_settings.cache_clear()
