from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from thermoctl.db.models.credential import Session_
from thermoctl.db.models.operations import AuditEvent


def test_anmeldung_mit_richtigem_passwort(client: TestClient, benutzer) -> None:
    antwort = client.post("/login", data={"username": "lino", "password": "passwort-lang-genug"},
                          follow_redirects=False)
    assert antwort.status_code == 303
    assert "thermoctl_session" in antwort.cookies


def test_anmeldung_mit_falschem_passwort_scheitert(client: TestClient, benutzer) -> None:
    antwort = client.post("/login", data={"username": "lino", "password": "falsch-aber-lang"})
    assert antwort.status_code == 401
    assert "thermoctl_session" not in antwort.cookies


def test_fehlermeldung_verraet_nicht_ob_der_benutzer_existiert(
    client: TestClient, benutzer
) -> None:
    a = client.post("/login", data={"username": "lino", "password": "falsch-aber-lang"})
    b = client.post("/login", data={"username": "gibtsnicht", "password": "falsch-aber-lang"})
    assert a.status_code == b.status_code == 401
    assert a.text == b.text


def test_cookie_ist_httponly_und_samesite(client: TestClient, benutzer) -> None:
    antwort = client.post("/login", data={"username": "lino", "password": "passwort-lang-genug"},
                          follow_redirects=False)
    kopf = antwort.headers["set-cookie"].lower()
    assert "httponly" in kopf
    assert "samesite=lax" in kopf


def test_cookie_enthaelt_nicht_den_gespeicherten_hash(client: TestClient, benutzer,
                                                      session: Session) -> None:
    antwort = client.post("/login", data={"username": "lino", "password": "passwort-lang-genug"},
                          follow_redirects=False)
    gespeichert = session.query(Session_).one().token_hash
    assert gespeichert not in antwort.headers["set-cookie"]


def test_inaktiver_benutzer_kommt_nicht_hinein(client: TestClient, benutzer,
                                               session: Session) -> None:
    benutzer.is_active = False
    session.flush()
    antwort = client.post(
        "/login", data={"username": "lino", "password": "passwort-lang-genug"}
    )
    assert antwort.status_code == 401


def test_abmelden_widerruft_die_sitzung(client: TestClient, benutzer, session: Session) -> None:
    client.post("/login", data={"username": "lino", "password": "passwort-lang-genug"})
    client.post("/logout")
    assert session.query(Session_).one().revoked_at is not None


def test_aenderung_ohne_csrf_token_wird_abgewiesen(client: TestClient, benutzer) -> None:
    client.post("/login", data={"username": "lino", "password": "passwort-lang-genug"})
    antwort = client.post("/logout", headers={"X-CSRF-Token": "falsch"})
    assert antwort.status_code == 403


def test_anmeldung_und_fehlversuch_landen_im_audit(client: TestClient, benutzer,
                                                   session: Session) -> None:
    client.post("/login", data={"username": "lino", "password": "falsch-aber-lang"})
    client.post("/login", data={"username": "lino", "password": "passwort-lang-genug"})
    aktionen = [e.action for e in session.query(AuditEvent).all()]
    assert "login_failed" in aktionen
    assert "login" in aktionen


def test_passwort_erscheint_in_keiner_antwort(client: TestClient, benutzer) -> None:
    antwort = client.post("/login", data={"username": "lino", "password": "passwort-lang-genug"})
    assert "passwort-lang-genug" not in antwort.text


def test_fehlversuche_werden_zunehmend_verzoegert(client, benutzer, monkeypatch) -> None:
    verzoegerungen: list[float] = []
    monkeypatch.setattr("thermoctl.web.auth_views.schlafen", verzoegerungen.append)
    for _ in range(3):
        client.post("/login", data={"username": "lino", "password": "falsch-aber-lang"})
    assert verzoegerungen == sorted(verzoegerungen)
    assert verzoegerungen[-1] > verzoegerungen[0]


def test_erfolgreiche_anmeldung_setzt_den_zaehler_zurueck(client, benutzer) -> None:
    client.post("/login", data={"username": "lino", "password": "falsch-aber-lang"})
    client.post("/login", data={"username": "lino", "password": "passwort-lang-genug"})
    from thermoctl.web.auth_views import FEHLVERSUCHE

    assert FEHLVERSUCHE.get("lino", 0) == 0
