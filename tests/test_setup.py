from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from thermoctl.db.models.identity import AccessGroup, User
from thermoctl.db.models.operations import Setting
from thermoctl.setup import einrichtung_noetig, setup_token_erzeugen


def test_ohne_benutzer_ist_einrichtung_noetig(session: Session) -> None:
    assert einrichtung_noetig(session) is True


def test_mit_benutzer_ist_sie_nicht_mehr_noetig(session: Session, benutzer) -> None:
    assert einrichtung_noetig(session) is False


def test_setup_ohne_token_wird_abgewiesen(client: TestClient, session: Session) -> None:
    antwort = client.post("/setup", data={"username": "lino", "display_name": "Lino",
                                          "password": "passwort-lang-genug",
                                          "timezone": "Europe/Berlin", "setup_token": ""})
    assert antwort.status_code == 403
    assert session.query(User).count() == 0


def test_setup_mit_falschem_token_wird_abgewiesen(client: TestClient, session: Session) -> None:
    setup_token_erzeugen(session)
    antwort = client.post("/setup", data={"username": "lino", "display_name": "Lino",
                                          "password": "passwort-lang-genug",
                                          "timezone": "Europe/Berlin",
                                          "setup_token": "erraten"})
    assert antwort.status_code == 403
    assert session.query(User).count() == 0


def test_setup_legt_verwalter_gruppen_und_einstellungen_an(client: TestClient,
                                                           session: Session) -> None:
    marke = setup_token_erzeugen(session)
    antwort = client.post("/setup", data={"username": "lino", "display_name": "Lino",
                                          "password": "passwort-lang-genug",
                                          "timezone": "Europe/Berlin", "setup_token": marke},
                          follow_redirects=False)
    assert antwort.status_code == 303
    assert session.query(User).count() == 1
    assert {g.name for g in session.query(AccessGroup)} == {
        "Verwaltung", "Bedienung", "Nur lesen", "Integration"
    }
    assert session.get(Setting, 1) is not None


def test_setup_token_ist_nur_einmal_verwendbar(client: TestClient, session: Session) -> None:
    marke = setup_token_erzeugen(session)
    daten = {"username": "a", "display_name": "A", "password": "passwort-lang-genug",
             "timezone": "Europe/Berlin", "setup_token": marke}
    client.post("/setup", data=daten)
    zweite = client.post("/setup", data={**daten, "username": "b"})
    assert zweite.status_code in (403, 404)
    assert session.query(User).count() == 1


def test_setup_ist_nach_abschluss_geschlossen(client: TestClient, session: Session,
                                              benutzer) -> None:
    assert client.get("/setup").status_code == 404


def test_erster_benutzer_ist_verwalter(client: TestClient, session: Session) -> None:
    marke = setup_token_erzeugen(session)
    client.post("/setup", data={"username": "lino", "display_name": "Lino",
                                "password": "passwort-lang-genug",
                                "timezone": "Europe/Berlin", "setup_token": marke})
    from thermoctl.domain.authz import hat_recht, principal_fuer_benutzer

    nutzer = session.query(User).one()
    p = principal_fuer_benutzer(session, nutzer)
    assert hat_recht(p, "user.manage") is True
    assert hat_recht(p, "setting.manage") is True


def test_setup_token_erscheint_nicht_im_klartext_in_der_datenbank(session: Session) -> None:
    from thermoctl.db.models.credential import SetupToken

    marke = setup_token_erzeugen(session)
    assert session.query(SetupToken).one().token_hash != marke


def test_setup_mit_zu_kurzem_passwort_fuehrt_zum_formular_zurueck(
    client: TestClient, session: Session
) -> None:
    """PasswordTooShort darf nicht als 500 beim Aufrufer ankommen -- es ist ein
    Eingabefehler, keine Stoerung des Dienstes. Bereits ausgefuellte Felder (ausser
    dem Passwort) bleiben im Formular erhalten."""
    marke = setup_token_erzeugen(session)
    antwort = client.post(
        "/setup",
        data={"username": "lino", "display_name": "Lino", "password": "zukurz",
              "timezone": "Europe/Berlin", "setup_token": marke},
    )
    assert antwort.status_code == 200
    assert "mindestens" in antwort.text
    assert 'value="lino"' in antwort.text
    assert "zukurz" not in antwort.text
    assert session.query(User).count() == 0
    assert session.query(AccessGroup).count() == 0

    zweiter_versuch = client.post(
        "/setup",
        data={"username": "lino", "display_name": "Lino",
              "password": "passwort-lang-genug", "timezone": "Europe/Berlin",
              "setup_token": marke},
        follow_redirects=False,
    )
    assert zweiter_versuch.status_code == 303
    assert session.query(User).count() == 1
