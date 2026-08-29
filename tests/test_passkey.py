"""Die beiden WebAuthn-Zeremonien, mit einem echten Software-Authenticator.

`soft-webauthn` erzeugt richtige Signaturen. Das ist der Unterschied zwischen einem Test,
der belegt, dass ein Passkey funktioniert, und einem, der nur belegt, dass alles abgelehnt
wird — Letzteres waere auch dann gruen, wenn die Anmeldung gar nicht gebaut waere.
"""


import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.hilfen import benutzer_anlegen, quelle
from tests.webauthn_geraet import WebAuthnGeraet
from thermoctl.config import Settings, get_settings
from thermoctl.db.models.identity import User
from thermoctl.db.models.operations import AuditEvent
from thermoctl.db.models.passkey import PasskeyChallenge, UserPasskey
from thermoctl.domain import passkey as passkey_modul
from thermoctl.domain.passkey import (
    PasskeyFehler,
    anmeldung_beginnen,
    anmeldung_pruefen,
    registrierung_abschliessen,
    registrierung_beginnen,
)

RP_ID = "localhost"
ORIGIN = "https://localhost"


@pytest.fixture
def einstellungen() -> Settings:
    return Settings(
        _env_file=None, database_url="sqlite://", secret_key="p" * 32,
        passkey_rp_id=RP_ID, passkey_origin=ORIGIN,
    )


@pytest.fixture(autouse=True)
def _quelle(session: Session) -> None:
    quelle(session, "web")


def _registrieren(
    session: Session, einstellungen: Settings, nutzer: User, geraet: WebAuthnGeraet
) -> UserPasskey:
    argumente = registrierung_beginnen(session, einstellungen, nutzer)
    antwort = geraet.registrieren(argumente, ORIGIN)
    return registrierung_abschliessen(
        session, einstellungen, nutzer, antwort, "Testgerät"
    )


def test_ein_registrierter_passkey_meldet_wirklich_an(
    session: Session, einstellungen: Settings
) -> None:
    """Der Gegenbeweis: Ohne ihn belegte die Suite nur, dass alles abgelehnt wird."""
    nutzer = benutzer_anlegen(session, "passkey-nutzer")
    geraet = WebAuthnGeraet()
    eintrag = _registrieren(session, einstellungen, nutzer, geraet)
    assert eintrag.bezeichnung == "Testgerät"

    argumente = anmeldung_beginnen(session, einstellungen)
    antwort = geraet.anmelden(argumente, ORIGIN)
    angemeldet = anmeldung_pruefen(session, einstellungen, antwort)
    assert angemeldet.id == nutzer.id


def test_challenge_wird_auch_bei_einem_fehlschlag_verbraucht(
    session: Session, einstellungen: Settings
) -> None:
    """Eine wiederverwendbare Challenge hebt den Schutz auf, den sie geben soll."""
    nutzer = benutzer_anlegen(session, "einmal-nutzer")
    geraet = WebAuthnGeraet()
    _registrieren(session, einstellungen, nutzer, geraet)

    argumente = anmeldung_beginnen(session, einstellungen)
    antwort = geraet.anmelden(argumente, ORIGIN)

    assert anmeldung_pruefen(session, einstellungen, antwort).id == nutzer.id
    # Genau dieselbe Antwort ein zweites Mal — die Challenge ist verbraucht.
    with pytest.raises(PasskeyFehler):
        anmeldung_pruefen(session, einstellungen, antwort)


def test_challenge_der_anmeldung_taugt_nicht_fuer_eine_registrierung(
    session: Session, einstellungen: Settings
) -> None:
    """Ohne die Bindung an die Zeremonie liesse sich eine Challenge zweckentfremden."""
    nutzer = benutzer_anlegen(session, "zweck-nutzer")
    geraet = WebAuthnGeraet()
    argumente = anmeldung_beginnen(session, einstellungen)

    # Der Authenticator legt einen neuen Schluessel an, aber mit der Challenge der
    # Anmeldung — die Registrierung muss das erkennen.
    registrierungsargumente = registrierung_beginnen(session, einstellungen, nutzer)
    registrierungsargumente["challenge"] = argumente["challenge"]
    antwort = geraet.registrieren(registrierungsargumente, ORIGIN)

    with pytest.raises(PasskeyFehler, match="anmeldung"):
        registrierung_abschliessen(
            session, einstellungen, nutzer, antwort, "Falsch"
        )


def test_abgelaufene_challenge_wird_abgewiesen(
    session: Session, einstellungen: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import timedelta

    nutzer = benutzer_anlegen(session, "spaet-nutzer")
    geraet = WebAuthnGeraet()
    _registrieren(session, einstellungen, nutzer, geraet)

    argumente = anmeldung_beginnen(session, einstellungen)
    antwort = geraet.anmelden(argumente, ORIGIN)
    # Die Zeile altern lassen, statt zu warten.
    eintrag = session.scalars(select(PasskeyChallenge)).one()
    eintrag.created_at = eintrag.created_at - timedelta(minutes=5)
    session.flush()

    with pytest.raises(PasskeyFehler, match="abgelaufen"):
        anmeldung_pruefen(session, einstellungen, antwort)


def test_zurueckgefallener_zaehler_beendet_die_anmeldung(
    session: Session, einstellungen: Settings
) -> None:
    """Der einzige Hinweis auf einen geklonten Authenticator, den das Verfahren kennt."""
    nutzer = benutzer_anlegen(session, "klon-nutzer")
    geraet = WebAuthnGeraet()
    eintrag = _registrieren(session, einstellungen, nutzer, geraet)

    argumente = anmeldung_beginnen(session, einstellungen)
    antwort = geraet.anmelden(argumente, ORIGIN)
    # Der Dienst hat schon einen hoeheren Stand gesehen, als das Geraet jetzt meldet.
    eintrag.sign_count = 9999
    session.flush()

    with pytest.raises(PasskeyFehler):
        anmeldung_pruefen(session, einstellungen, antwort)


def test_gesperrtes_konto_wird_erst_nach_der_signatur_geprueft(
    session: Session, einstellungen: Settings
) -> None:
    """Dieselbe Reihenfolge wie im Passwortweg — sonst liesse sich am Verhalten ablesen,
    welche Konten es gibt."""
    nutzer = benutzer_anlegen(session, "gesperrt-nutzer")
    geraet = WebAuthnGeraet()
    _registrieren(session, einstellungen, nutzer, geraet)
    nutzer.is_active = False
    session.flush()

    argumente = anmeldung_beginnen(session, einstellungen)
    antwort = geraet.anmelden(argumente, ORIGIN)
    with pytest.raises(PasskeyFehler):
        anmeldung_pruefen(session, einstellungen, antwort)

    protokoll = session.scalars(
        select(AuditEvent).where(AuditEvent.action == "login_failed")
    ).all()
    assert protokoll and "gesperrt" in (protokoll[-1].detail or "").lower()


def test_fremde_origin_wird_abgewiesen(
    session: Session, einstellungen: Settings
) -> None:
    """Der Schutz gegen nachgemachte Seiten. Faellt er weg, ist ein Passkey ein Passwort."""
    nutzer = benutzer_anlegen(session, "origin-nutzer")
    geraet = WebAuthnGeraet()
    _registrieren(session, einstellungen, nutzer, geraet)

    argumente = anmeldung_beginnen(session, einstellungen)
    # Der Authenticator antwortet einer anderen Seite.
    antwort = geraet.anmelden(argumente, "https://boese.example")
    with pytest.raises(PasskeyFehler):
        anmeldung_pruefen(session, einstellungen, antwort)


def test_unbekannter_passkey_wird_abgewiesen(
    session: Session, einstellungen: Settings
) -> None:
    nutzer = benutzer_anlegen(session, "unbekannt-nutzer")
    geraet = WebAuthnGeraet()
    _registrieren(session, einstellungen, nutzer, geraet)

    fremdes = WebAuthnGeraet()
    argumente = registrierung_beginnen(session, einstellungen, nutzer)
    fremdes.registrieren(argumente, ORIGIN)

    anmelde = anmeldung_beginnen(session, einstellungen)
    antwort = fremdes.anmelden(anmelde, ORIGIN)
    with pytest.raises(PasskeyFehler):
        anmeldung_pruefen(session, einstellungen, antwort)


def test_derselbe_passkey_laesst_sich_nicht_zweimal_hinterlegen(
    session: Session, einstellungen: Settings
) -> None:
    nutzer = benutzer_anlegen(session, "doppelt-nutzer")
    geraet = WebAuthnGeraet()
    _registrieren(session, einstellungen, nutzer, geraet)
    with pytest.raises(PasskeyFehler, match="bereits hinterlegt"):
        _registrieren(session, einstellungen, nutzer, geraet)


def test_alte_challenges_werden_aufgeraeumt(
    session: Session, einstellungen: Settings
) -> None:
    from datetime import timedelta

    anmeldung_beginnen(session, einstellungen)
    anmeldung_beginnen(session, einstellungen)
    for eintrag in session.scalars(select(PasskeyChallenge)):
        eintrag.created_at = eintrag.created_at - timedelta(hours=1)
    session.flush()
    frisch = anmeldung_beginnen(session, einstellungen)

    entfernt = passkey_modul.alte_challenges_aufraeumen(session)
    assert entfernt == 2
    uebrig = session.scalars(select(PasskeyChallenge)).all()
    assert len(uebrig) == 1 and uebrig[0].challenge == frisch["challenge"]


def test_ohne_relying_party_id_gibt_es_die_wege_nicht(client: TestClient) -> None:
    """Passkeys sind entweder eingerichtet oder gar nicht da — nicht halb."""
    get_settings.cache_clear()
    assert client.post("/passkey/anmeldung/argumente").status_code == 404
    assert client.post("/passkey/anmeldung/pruefen", json={}).status_code == 404


@pytest.fixture
def mit_passkeys(monkeypatch: pytest.MonkeyPatch, settings) -> None:  # type: ignore[no-untyped-def]
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
    antwort = client.post("/passkey/anmeldung/argumente")
    assert antwort.status_code == 200
    argumente = antwort.json()
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
    for nutzlast in ({}, {"id": "gibtesnicht"}, {"response": {}}):
        antwort = client.post("/passkey/anmeldung/pruefen", json=nutzlast)
        assert antwort.status_code == 401, nutzlast
        assert antwort.json()["meldung"] == "Die Anmeldung war nicht erfolgreich."


def test_anmeldung_ueber_den_http_weg(
    client: TestClient, session: Session, mit_passkeys: None
) -> None:
    """Der ganze Weg, wie ihn der Browser geht — bis zum gesetzten Sitzungscookie."""
    from thermoctl.auth.sessions import COOKIE_NAME

    nutzer = benutzer_anlegen(session, "http-nutzer")
    geraet = WebAuthnGeraet()
    einstellungen = get_settings()
    _registrieren(session, einstellungen, nutzer, geraet)

    argumente = client.post("/passkey/anmeldung/argumente").json()
    antwort = client.post(
        "/passkey/anmeldung/pruefen", json=geraet.anmelden(argumente, ORIGIN)
    )
    assert antwort.status_code == 200
    assert antwort.json() == {"status": "angemeldet", "weiter": "/"}
    assert client.cookies.get(COOKIE_NAME)
    # Und die Sitzung traegt wirklich: eine geschuetzte Seite antwortet jetzt.
    assert client.get("/passkeys").status_code == 200


def test_registrierung_braucht_eine_anmeldung(
    client: TestClient, mit_passkeys: None
) -> None:
    assert client.post("/passkey/registrierung/argumente").status_code == 401
    assert client.post("/passkey/registrierung/pruefen", json={}).status_code == 401


def test_registrierung_ueber_den_http_weg(
    client_als, session: Session, mit_passkeys: None
) -> None:
    c = client_als([("zone.read", None)])
    argumente = c.post(
        "/passkey/registrierung/argumente", headers=_csrf(c)
    ).json()
    geraet = WebAuthnGeraet()
    nutzlast = geraet.registrieren(argumente, ORIGIN)
    nutzlast["bezeichnung"] = "Mein Telefon"
    antwort = c.post("/passkey/registrierung/pruefen", json=nutzlast, headers=_csrf(c))
    assert antwort.status_code == 200, antwort.text
    assert antwort.json()["bezeichnung"] == "Mein Telefon"
    assert session.scalars(select(UserPasskey)).all()


def test_fremder_passkey_laesst_sich_nicht_entfernen(
    client_als, session: Session, mit_passkeys: None
) -> None:
    """404 statt 403 — sonst verriete die Antwort, welche Kennungen es gibt."""
    from tests.hilfen import passkey_anlegen

    fremder = benutzer_anlegen(session, "fremder-passkeybesitzer")
    eintrag = passkey_anlegen(session, fremder, "fremde-kennung")
    c = client_als([("zone.read", None)])
    antwort = c.post(f"/passkeys/{eintrag.id}/entfernen", headers=_csrf(c))
    assert antwort.status_code == 404
    assert session.get(UserPasskey, eintrag.id) is not None


def test_eigener_passkey_laesst_sich_entfernen(
    client_als, session: Session, mit_passkeys: None
) -> None:
    from thermoctl.db.models.identity import User as Konto

    c = client_als([("zone.read", None)])
    argumente = c.post("/passkey/registrierung/argumente", headers=_csrf(c)).json()
    nutzlast = WebAuthnGeraet().registrieren(argumente, ORIGIN)
    nutzlast["bezeichnung"] = "Weg damit"
    c.post("/passkey/registrierung/pruefen", json=nutzlast, headers=_csrf(c))

    eintrag = session.scalars(
        select(UserPasskey).where(UserPasskey.bezeichnung == "Weg damit")
    ).one()
    assert c.post(
        f"/passkeys/{eintrag.id}/entfernen", headers=_csrf(c), follow_redirects=False
    ).status_code == 303
    assert session.get(UserPasskey, eintrag.id) is None
    assert session.scalars(select(Konto)).all()


def test_passkeyseite_ohne_einrichtung_erklaert_es(client_als) -> None:
    """Ohne Relying-Party-ID keine Schaltflaeche, die nichts tun kann — sondern ein Satz,
    der sagt, was fehlt."""
    get_settings.cache_clear()
    antwort = client_als([("zone.read", None)]).get("/passkeys")
    assert antwort.status_code == 200
    assert "THERMOCTL_PASSKEY_RP_ID" in antwort.text


def test_anmeldeseite_bietet_passkeys_nur_mit_einrichtung(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    get_settings.cache_clear()
    assert "passkey-anmelden" not in client.get("/login").text

    monkeypatch.setenv("THERMOCTL_PASSKEY_RP_ID", RP_ID)
    get_settings.cache_clear()
    assert "passkey-anmelden" in client.get("/login").text
    get_settings.cache_clear()
