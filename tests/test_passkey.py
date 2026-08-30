"""Die beiden WebAuthn-Zeremonien, mit einem echten Software-Authenticator.

`soft-webauthn` erzeugt richtige Signaturen. Das ist der Unterschied zwischen einem Test,
der belegt, dass ein Passkey funktioniert, und einem, der nur belegt, dass alles abgelehnt
wird — Letzteres waere auch dann gruen, wenn die Anmeldung gar nicht gebaut waere.
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


def _registrieren(
    session: Session, passkey_settings: Settings, nutzer: User, device: WebAuthnDevice
) -> UserPasskey:
    argumente = begin_registration(session, passkey_settings, nutzer)
    response = device.registrieren(argumente, ORIGIN)
    return finish_registration(
        session, passkey_settings, nutzer, response, "Testgerät"
    )


def test_ein_registrierter_passkey_meldet_wirklich_an(
    session: Session, passkey_settings: Settings
) -> None:
    """Der Gegenbeweis: Ohne ihn belegte die Suite nur, dass alles abgelehnt wird."""
    nutzer = create_user(session, "passkey-nutzer")
    device = WebAuthnDevice()
    entry = _registrieren(session, passkey_settings, nutzer, device)
    assert entry.bezeichnung == "Testgerät"

    argumente = begin_authentication(session, passkey_settings)
    response = device.log_in(argumente, ORIGIN)
    angemeldet = verify_authentication(session, passkey_settings, response)
    assert angemeldet.id == nutzer.id


def test_challenge_wird_auch_bei_einem_fehlschlag_verbraucht(
    session: Session, passkey_settings: Settings
) -> None:
    """Eine wiederverwendbare Challenge hebt den Schutz auf, den sie geben soll."""
    nutzer = create_user(session, "einmal-nutzer")
    device = WebAuthnDevice()
    _registrieren(session, passkey_settings, nutzer, device)

    argumente = begin_authentication(session, passkey_settings)
    response = device.log_in(argumente, ORIGIN)

    assert verify_authentication(session, passkey_settings, response).id == nutzer.id
    # Genau dieselbe Antwort ein zweites Mal — die Challenge ist verbraucht.
    with pytest.raises(PasskeyError):
        verify_authentication(session, passkey_settings, response)


def test_challenge_der_anmeldung_taugt_nicht_fuer_eine_registrierung(
    session: Session, passkey_settings: Settings
) -> None:
    """Ohne die Bindung an die Zeremonie liesse sich eine Challenge zweckentfremden."""
    nutzer = create_user(session, "zweck-nutzer")
    device = WebAuthnDevice()
    argumente = begin_authentication(session, passkey_settings)

    # Der Authenticator legt einen neuen Schluessel an, aber mit der Challenge der
    # Anmeldung — die Registrierung muss das erkennen.
    registrierungsargumente = begin_registration(session, passkey_settings, nutzer)
    registrierungsargumente["challenge"] = argumente["challenge"]
    response = device.registrieren(registrierungsargumente, ORIGIN)

    with pytest.raises(PasskeyError, match="anmeldung"):
        finish_registration(
            session, passkey_settings, nutzer, response, "Falsch"
        )


def test_abgelaufene_challenge_wird_abgewiesen(
    session: Session, passkey_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import timedelta

    nutzer = create_user(session, "spaet-nutzer")
    device = WebAuthnDevice()
    _registrieren(session, passkey_settings, nutzer, device)

    argumente = begin_authentication(session, passkey_settings)
    response = device.log_in(argumente, ORIGIN)
    # Die Zeile altern lassen, statt zu warten.
    entry = session.scalars(select(PasskeyChallenge)).one()
    entry.created_at = entry.created_at - timedelta(minutes=5)
    session.flush()

    with pytest.raises(PasskeyError, match="abgelaufen"):
        verify_authentication(session, passkey_settings, response)


def test_zurueckgefallener_zaehler_beendet_die_anmeldung(
    session: Session, passkey_settings: Settings
) -> None:
    """Der einzige Hinweis auf einen geklonten Authenticator, den das Verfahren kennt."""
    nutzer = create_user(session, "klon-nutzer")
    device = WebAuthnDevice()
    entry = _registrieren(session, passkey_settings, nutzer, device)

    argumente = begin_authentication(session, passkey_settings)
    response = device.log_in(argumente, ORIGIN)
    # Der Dienst hat schon einen hoeheren Stand gesehen, als das Geraet jetzt meldet.
    entry.sign_count = 9999
    session.flush()

    with pytest.raises(PasskeyError):
        verify_authentication(session, passkey_settings, response)


def test_gesperrtes_konto_wird_erst_nach_der_signatur_geprueft(
    session: Session, passkey_settings: Settings
) -> None:
    """Dieselbe Reihenfolge wie im Passwortweg — sonst liesse sich am Verhalten ablesen,
    welche Konten es gibt."""
    nutzer = create_user(session, "gesperrt-nutzer")
    device = WebAuthnDevice()
    _registrieren(session, passkey_settings, nutzer, device)
    nutzer.is_active = False
    session.flush()

    argumente = begin_authentication(session, passkey_settings)
    response = device.log_in(argumente, ORIGIN)
    with pytest.raises(PasskeyError):
        verify_authentication(session, passkey_settings, response)

    protokoll = session.scalars(
        select(AuditEvent).where(AuditEvent.action == "login_failed")
    ).all()
    assert protokoll and "gesperrt" in (protokoll[-1].detail or "").lower()


def test_fremde_origin_wird_abgewiesen(
    session: Session, passkey_settings: Settings
) -> None:
    """Der Schutz gegen nachgemachte Seiten. Faellt er weg, ist ein Passkey ein Passwort."""
    nutzer = create_user(session, "origin-nutzer")
    device = WebAuthnDevice()
    _registrieren(session, passkey_settings, nutzer, device)

    argumente = begin_authentication(session, passkey_settings)
    # Der Authenticator antwortet einer anderen Seite.
    response = device.log_in(argumente, "https://boese.example")
    with pytest.raises(PasskeyError):
        verify_authentication(session, passkey_settings, response)


def test_unbekannter_passkey_wird_abgewiesen(
    session: Session, passkey_settings: Settings
) -> None:
    nutzer = create_user(session, "unbekannt-nutzer")
    device = WebAuthnDevice()
    _registrieren(session, passkey_settings, nutzer, device)

    fremdes = WebAuthnDevice()
    argumente = begin_registration(session, passkey_settings, nutzer)
    fremdes.registrieren(argumente, ORIGIN)

    login = begin_authentication(session, passkey_settings)
    response = fremdes.log_in(login, ORIGIN)
    with pytest.raises(PasskeyError):
        verify_authentication(session, passkey_settings, response)


def test_derselbe_passkey_laesst_sich_nicht_zweimal_hinterlegen(
    session: Session, passkey_settings: Settings
) -> None:
    nutzer = create_user(session, "doppelt-nutzer")
    device = WebAuthnDevice()
    _registrieren(session, passkey_settings, nutzer, device)
    with pytest.raises(PasskeyError, match="bereits hinterlegt"):
        _registrieren(session, passkey_settings, nutzer, device)


def test_alte_challenges_werden_aufgeraeumt(
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


def test_ohne_relying_party_id_gibt_es_die_wege_nicht(client: TestClient) -> None:
    """Passkeys sind entweder eingerichtet oder gar nicht da — nicht halb."""
    get_settings.cache_clear()
    assert client.post("/passkey/authentication/options").status_code == 404
    assert client.post("/passkey/authentication/verify", json={}).status_code == 404


@pytest.fixture
def mit_passkeys(monkeypatch: pytest.MonkeyPatch, passkey_settings) -> None:  # type: ignore[no-untyped-def]
    """Schaltet Passkeys fuer die HTTP-Tests ein."""
    monkeypatch.setenv("THERMOCTL_PASSKEY_RP_ID", RP_ID)
    monkeypatch.setenv("THERMOCTL_PASSKEY_ORIGIN", ORIGIN)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _csrf(client: TestClient) -> dict[str, str]:
    from thermoctl.auth.csrf import CSRF_HEADER, csrf_token
    from thermoctl.auth.sessions import COOKIE_NAME

    geheimnis = client.cookies[COOKIE_NAME]
    return {CSRF_HEADER: csrf_token(geheimnis, get_settings().secret_key.get_secret_value())}


def test_anmeldeargumente_kommen_ohne_anmeldung(
    client: TestClient, mit_passkeys: None
) -> None:
    """Die Argumente muss jeder holen koennen — sonst gaebe es keinen Weg herein."""
    response = client.post("/passkey/authentication/options")
    assert response.status_code == 200
    argumente = response.json()
    assert argumente["rpId"] == RP_ID
    assert argumente["challenge"]
    # Ohne `allowCredentials`: Eine Liste verriete, ob es ein Konto gibt und wie viele
    # Passkeys es hat.
    assert not argumente.get("allowCredentials")


def test_unsinnige_anmeldung_wird_einheitlich_abgelehnt(
    client: TestClient, mit_passkeys: None
) -> None:
    """Gleiche Antwort fuer jeden Fehlschlag — sonst liesse sich daran ablesen, welche
    Konten es gibt."""
    for payload in ({}, {"id": "gibtesnicht"}, {"response": {}}):
        response = client.post("/passkey/authentication/verify", json=payload)
        assert response.status_code == 401, payload
        assert response.json()["meldung"] == "Die Anmeldung war nicht erfolgreich."


def test_anmeldung_ueber_den_http_weg(
    client: TestClient, session: Session, mit_passkeys: None
) -> None:
    """Der ganze Weg, wie ihn der Browser geht — bis zum gesetzten Sitzungscookie."""
    from thermoctl.auth.sessions import COOKIE_NAME

    nutzer = create_user(session, "http-nutzer")
    device = WebAuthnDevice()
    passkey_settings = get_settings()
    _registrieren(session, passkey_settings, nutzer, device)

    argumente = client.post("/passkey/authentication/options").json()
    response = client.post(
        "/passkey/authentication/verify", json=device.log_in(argumente, ORIGIN)
    )
    assert response.status_code == 200
    assert response.json() == {"status": "angemeldet", "weiter": "/"}
    assert client.cookies.get(COOKIE_NAME)
    # Und die Sitzung traegt wirklich: eine geschuetzte Seite antwortet jetzt.
    assert client.get("/passkeys").status_code == 200


def test_registrierung_braucht_eine_anmeldung(
    client: TestClient, mit_passkeys: None
) -> None:
    assert client.post("/passkey/registration/options").status_code == 401
    assert client.post("/passkey/registration/verify", json={}).status_code == 401


def test_registrierung_ueber_den_http_weg(
    client_als, session: Session, mit_passkeys: None
) -> None:
    c = client_als([("zone.read", None)])
    argumente = c.post(
        "/passkey/registration/options", headers=_csrf(c)
    ).json()
    device = WebAuthnDevice()
    payload = device.registrieren(argumente, ORIGIN)
    payload["bezeichnung"] = "Mein Telefon"
    response = c.post("/passkey/registration/verify", json=payload, headers=_csrf(c))
    assert response.status_code == 200, response.text
    assert response.json()["bezeichnung"] == "Mein Telefon"
    assert session.scalars(select(UserPasskey)).all()


def test_fremder_passkey_laesst_sich_nicht_entfernen(
    client_als, session: Session, mit_passkeys: None
) -> None:
    """404 statt 403 — sonst verriete die Antwort, welche Kennungen es gibt."""
    from tests.helpers import create_passkey

    fremder = create_user(session, "fremder-passkeybesitzer")
    entry = create_passkey(session, fremder, "fremde-kennung")
    c = client_als([("zone.read", None)])
    response = c.post(f"/passkeys/{entry.id}/remove", headers=_csrf(c))
    assert response.status_code == 404
    assert session.get(UserPasskey, entry.id) is not None


def test_eigener_passkey_laesst_sich_entfernen(
    client_als, session: Session, mit_passkeys: None
) -> None:
    from thermoctl.db.models.identity import User as Konto

    c = client_als([("zone.read", None)])
    argumente = c.post("/passkey/registration/options", headers=_csrf(c)).json()
    payload = WebAuthnDevice().registrieren(argumente, ORIGIN)
    payload["bezeichnung"] = "Weg damit"
    c.post("/passkey/registration/verify", json=payload, headers=_csrf(c))

    entry = session.scalars(
        select(UserPasskey).where(UserPasskey.bezeichnung == "Weg damit")
    ).one()
    assert c.post(
        f"/passkeys/{entry.id}/remove", headers=_csrf(c), follow_redirects=False
    ).status_code == 303
    assert session.get(UserPasskey, entry.id) is None
    assert session.scalars(select(Konto)).all()


def test_passkeyseite_ohne_einrichtung_erklaert_es(client_als) -> None:
    """Ohne Relying-Party-ID keine Schaltflaeche, die nichts tun kann — sondern ein Satz,
    der sagt, was fehlt."""
    get_settings.cache_clear()
    response = client_als([("zone.read", None)]).get("/passkeys")
    assert response.status_code == 200
    assert "THERMOCTL_PASSKEY_RP_ID" in response.text


def test_anmeldeseite_bietet_passkeys_nur_mit_einrichtung(
    client: TestClient, user, monkeypatch: pytest.MonkeyPatch
) -> None:
    # `benutzer`: ohne einen leitet /login zur Einrichtung weiter, und die Pruefung
    # unten waere gruen, ohne je das Anmeldeformular gesehen zu haben.
    get_settings.cache_clear()
    assert "passkey-anmelden" not in client.get("/login").text

    monkeypatch.setenv("THERMOCTL_PASSKEY_RP_ID", RP_ID)
    get_settings.cache_clear()
    assert "passkey-anmelden" in client.get("/login").text
    get_settings.cache_clear()
