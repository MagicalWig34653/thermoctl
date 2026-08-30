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


def test_anmeldung_mit_richtigem_passwort(client: TestClient, user) -> None:
    response = client.post("/login", data={"username": "lino", "password": "passwort-lang-genug"},
                          follow_redirects=False)
    assert response.status_code == 303
    assert "thermoctl_session" in response.cookies


def test_anmeldung_mit_falschem_passwort_scheitert(client: TestClient, user) -> None:
    response = client.post("/login", data={"username": "lino", "password": "falsch-aber-lang"})
    assert response.status_code == 401
    assert "thermoctl_session" not in response.cookies


def test_fehlermeldung_verraet_nicht_ob_der_benutzer_existiert(
    client: TestClient, user
) -> None:
    a = client.post("/login", data={"username": "lino", "password": "falsch-aber-lang"})
    b = client.post("/login", data={"username": "gibtsnicht", "password": "falsch-aber-lang"})
    assert a.status_code == b.status_code == 401
    assert a.text == b.text


def test_cookie_ist_httponly_und_samesite(client: TestClient, user) -> None:
    response = client.post("/login", data={"username": "lino", "password": "passwort-lang-genug"},
                          follow_redirects=False)
    kopf = response.headers["set-cookie"].lower()
    assert "httponly" in kopf
    assert "samesite=lax" in kopf


def test_cookie_enthaelt_nicht_den_gespeicherten_hash(client: TestClient, user,
                                                      session: Session) -> None:
    response = client.post("/login", data={"username": "lino", "password": "passwort-lang-genug"},
                          follow_redirects=False)
    stored = session.query(Session_).one().token_hash
    assert stored not in response.headers["set-cookie"]


def test_inaktiver_benutzer_kommt_nicht_hinein(client: TestClient, user,
                                               session: Session) -> None:
    user.is_active = False
    session.flush()
    response = client.post(
        "/login", data={"username": "lino", "password": "passwort-lang-genug"}
    )
    assert response.status_code == 401


def test_abmelden_widerruft_die_sitzung(client: TestClient, user, session: Session) -> None:
    client.post("/login", data={"username": "lino", "password": "passwort-lang-genug"})
    # Das gueltige Token liefert die Anwendung selbst ueber ein eigenes, nicht
    # httpOnly-Cookie aus — genau das, was HTMX in der Oberflaeche lesen und als
    # Header mitschicken wuerde.
    token = client.cookies[CSRF_COOKIE_NAME]
    client.post("/logout", headers={"X-CSRF-Token": token})
    assert session.query(Session_).one().revoked_at is not None


def test_aenderung_ohne_csrf_token_wird_abgewiesen(client: TestClient, user) -> None:
    client.post("/login", data={"username": "lino", "password": "passwort-lang-genug"})
    response = client.post("/logout")
    assert response.status_code == 403


def test_aenderung_mit_token_aus_fremder_sitzung_wird_abgewiesen(
    client: TestClient, user, session: Session, settings: Settings
) -> None:
    client.post("/login", data={"username": "lino", "password": "passwort-lang-genug"})
    _foreign_session, fremdes_secret = create_session(session, user, 3600)
    fremdes_token = csrf_token(fremdes_secret, settings.secret_key.get_secret_value())
    response = client.post("/logout", headers={"X-CSRF-Token": fremdes_token})
    assert response.status_code == 403


def test_anmeldung_und_fehlversuch_landen_im_audit(client: TestClient, user,
                                                   session: Session) -> None:
    client.post("/login", data={"username": "lino", "password": "falsch-aber-lang"})
    client.post("/login", data={"username": "lino", "password": "passwort-lang-genug"})
    aktionen = [e.action for e in session.query(AuditEvent).all()]
    assert "login_failed" in aktionen
    assert "login" in aktionen


def test_passwort_erscheint_in_keiner_antwort(client: TestClient, user) -> None:
    response = client.post("/login", data={"username": "lino", "password": "passwort-lang-genug"})
    assert "passwort-lang-genug" not in response.text


def test_fehlversuche_werden_zunehmend_verzoegert(client, user, monkeypatch) -> None:
    verzoegerungen: list[float] = []
    monkeypatch.setattr("thermoctl.web.auth_views.schlafen", verzoegerungen.append)
    for _ in range(3):
        client.post("/login", data={"username": "lino", "password": "falsch-aber-lang"})
    assert verzoegerungen == sorted(verzoegerungen)
    assert verzoegerungen[-1] > verzoegerungen[0]


def test_erfolgreiche_anmeldung_setzt_den_zaehler_zurueck(client, user) -> None:
    client.post("/login", data={"username": "lino", "password": "falsch-aber-lang"})
    client.post("/login", data={"username": "lino", "password": "passwort-lang-genug"})
    from thermoctl.web.auth_views import FEHLVERSUCHE

    assert FEHLVERSUCHE.get("lino", 0) == 0


def test_sitzungsdauer_kommt_aus_der_einstellungszeile(
    client: TestClient, user, session: Session
) -> None:
    create_settings(session, session_duration_s=3600)
    # Auf ganze Sekunden abgeschnitten: MariaDB speichert DATETIME ohne
    # Praezisionsangabe sekundengenau und verwirft die Bruchteile. Ein
    # mikrosekundengenauer Vergleich schluege dort um Millisekunden fehl,
    # ohne dass fachlich etwas falsch waere.
    before_login = utcnow().replace(microsecond=0)
    client.post(
        "/login", data={"username": "lino", "password": "passwort-lang-genug"},
        follow_redirects=False,
    )
    after_login = utcnow().replace(microsecond=0) + timedelta(seconds=1)
    expiry = session.query(Session_).one().expires_at
    # 3600 s aus der Einstellungszeile statt der eingebauten 14-Tage-Vorgabe.
    assert (before_login + timedelta(seconds=3600)) <= expiry
    assert expiry <= (after_login + timedelta(seconds=3600))

def test_passwortpruefung_laeuft_auch_bei_unbekanntem_benutzer(
    client: TestClient, user, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sonst verraet die Antwortzeit, welche Konten es gibt.

    Argon2id ist absichtlich langsam. Wuerde die Pruefung bei einem unbekannten
    Benutzernamen uebersprungen, waere die Anfrage messbar schneller als fuer einen
    existierenden -- unabhaengig davon, dass Meldung und Wartezeit gleich sind. Der
    Test zaehlt die Aufrufe, statt Zeiten zu messen: Zeitmessungen in Tests sind
    unzuverlaessig, die Ursache laesst sich aber direkt pruefen.
    """
    aufrufe: list[str] = []
    echtes_verify = thermoctl.web.auth_views.verify_password

    def counting(plaintext: str, hash_value: str) -> bool:
        aufrufe.append(hash_value)
        return echtes_verify(plaintext, hash_value)

    monkeypatch.setattr("thermoctl.web.auth_views.verify_password", counting)

    client.post("/login", data={"username": "gibtsnicht", "password": "falsch-aber-lang"})
    assert len(aufrufe) == 1, "bei unbekanntem Benutzer wurde nicht geprueft"

    aufrufe.clear()
    client.post("/login", data={"username": "lino", "password": "falsch-aber-lang"})
    assert len(aufrufe) == 1, "bei bekanntem Benutzer wurde nicht geprueft"
