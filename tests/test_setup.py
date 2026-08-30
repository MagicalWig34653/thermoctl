from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from thermoctl.db.models.identity import AccessGroup, User
from thermoctl.db.models.operations import Setting
from thermoctl.setup import einrichtung_noetig, setup_token_erzeugen


def test_ohne_benutzer_ist_einrichtung_noetig(session: Session) -> None:
    assert einrichtung_noetig(session) is True


def test_mit_benutzer_ist_sie_nicht_mehr_noetig(session: Session, user) -> None:
    assert einrichtung_noetig(session) is False


def test_setup_ohne_token_wird_abgewiesen(client: TestClient, session: Session) -> None:
    response = client.post("/setup", data={"username": "lino", "display_name": "Lino",
                                          "password": "passwort-lang-genug",
                                          "timezone": "Europe/Berlin", "setup_token": ""})
    assert response.status_code == 403
    assert session.query(User).count() == 0


def test_setup_mit_falschem_token_wird_abgewiesen(client: TestClient, session: Session) -> None:
    setup_token_erzeugen(session)
    response = client.post("/setup", data={"username": "lino", "display_name": "Lino",
                                          "password": "passwort-lang-genug",
                                          "timezone": "Europe/Berlin",
                                          "setup_token": "erraten"})
    assert response.status_code == 403
    assert session.query(User).count() == 0


def test_setup_legt_verwalter_gruppen_und_einstellungen_an(client: TestClient,
                                                           session: Session) -> None:
    marker = setup_token_erzeugen(session)
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


def test_setup_token_ist_nur_einmal_verwendbar(client: TestClient, session: Session) -> None:
    marker = setup_token_erzeugen(session)
    daten = {"username": "a", "display_name": "A", "password": "passwort-lang-genug",
             "timezone": "Europe/Berlin", "setup_token": marker}
    client.post("/setup", data=daten)
    zweite = client.post("/setup", data={**daten, "username": "b"})
    # Genau 404, nicht "irgendein Fehler": Die Einrichtung ist danach dauerhaft
    # geschlossen und nicht bloss verboten — wer sie aufruft, soll nicht erfahren, dass es
    # sie ueberhaupt gab. Ein `in (403, 404)` haette einen Wechsel nicht bemerkt.
    assert zweite.status_code == 404
    assert session.query(User).count() == 1


def test_setup_ist_nach_abschluss_geschlossen(client: TestClient, session: Session,
                                              user) -> None:
    assert client.get("/setup").status_code == 404


def test_erster_benutzer_ist_verwalter(client: TestClient, session: Session) -> None:
    marker = setup_token_erzeugen(session)
    client.post("/setup", data={"username": "lino", "display_name": "Lino",
                                "password": "passwort-lang-genug",
                                "timezone": "Europe/Berlin", "setup_token": marker})
    from thermoctl.domain.authz import has_permission, principal_for_user

    nutzer = session.query(User).one()
    p = principal_for_user(session, nutzer)
    assert has_permission(p, "user.manage") is True
    assert has_permission(p, "setting.manage") is True


def test_setup_token_erscheint_nicht_im_klartext_in_der_datenbank(session: Session) -> None:
    from thermoctl.db.models.credential import SetupToken

    marker = setup_token_erzeugen(session)
    assert session.query(SetupToken).one().token_hash != marker


def test_setup_mit_zu_kurzem_passwort_fuehrt_zum_formular_zurueck(
    client: TestClient, session: Session
) -> None:
    """PasswordTooShort darf nicht als 500 beim Aufrufer ankommen -- es ist ein
    Eingabefehler, keine Stoerung des Dienstes. Bereits ausgefuellte Felder (ausser
    dem Passwort) bleiben im Formular erhalten."""
    marker = setup_token_erzeugen(session)
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

    zweiter_versuch = client.post(
        "/setup",
        data={"username": "lino", "display_name": "Lino",
              "password": "passwort-lang-genug", "timezone": "Europe/Berlin",
              "setup_token": marker},
        follow_redirects=False,
    )
    assert zweiter_versuch.status_code == 303
    assert session.query(User).count() == 1


def test_einrichtung_ist_auch_in_der_domaene_nur_einmal_moeglich(session: Session) -> None:
    """Die Ansicht prueft schon — die Domaene prueft trotzdem selbst.

    Der Einrichtungsassistent legt den ersten Verwalter an. Waere die Sperre nur in der
    Ansicht, genuegte ein zweiter Aufrufweg, um an ihr vorbei einen weiteren anzulegen.
    """
    import pytest

    from thermoctl.setup import einrichtung_durchfuehren, setup_token_erzeugen

    marker = setup_token_erzeugen(session)
    einrichtung_durchfuehren(
        session, username="erster", display_name="Erster",
        password="passwort-lang-genug", timezone_name="Europe/Berlin", token=marker,
    )
    second_marker = setup_token_erzeugen(session)
    with pytest.raises(PermissionError, match="bereits abgeschlossen"):
        einrichtung_durchfuehren(
            session, username="zweiter", display_name="Zweiter",
            password="passwort-lang-genug", timezone_name="Europe/Berlin", token=second_marker,
        )
    assert session.query(User).count() == 1


def test_start_leitet_ohne_benutzer_zur_einrichtung(client: TestClient) -> None:
    """Ohne einen einzigen Benutzer fuehrt das Anmeldeformular nirgendwohin. Wer die
    Adresse des Dienstes eingibt, gehoert zur Einrichtung."""
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/setup"


def test_anmeldeformular_leitet_ohne_benutzer_zur_einrichtung(client: TestClient) -> None:
    response = client.get("/login", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/setup"


def test_die_weiterleitung_endet_wirklich_bei_der_einrichtung(client: TestClient) -> None:
    """Ein Kreis zwischen / , /login und /setup waere der naheliegende Fehler: Die
    Statuszeile der einzelnen Antwort wuerde ihn nicht zeigen, ein verfolgter Aufruf
    schon."""
    response = client.get("/", follow_redirects=True)
    assert response.status_code == 200
    assert response.url.path == "/setup"


def test_mit_benutzer_bleibt_der_gewohnte_weg(client: TestClient, user) -> None:
    """Gegenprobe zu den drei Faellen oben. Ohne sie waeren sie auch von einer Fassung
    erfuellt, die immer zur Einrichtung schickt -- und die haette den Dienst nach
    abgeschlossener Einrichtung unbenutzbar gemacht."""
    start = client.get("/", follow_redirects=False)
    assert start.status_code == 303
    assert start.headers["location"] == "/login"

    form = client.get("/login")
    assert form.status_code == 200
    assert "/setup" not in form.headers.get("location", "")


def test_post_login_ohne_benutzer_wird_nicht_weitergeleitet(
    client: TestClient, monkeypatch
) -> None:
    """Absichtlich nur GET weitergeleitet: Ein abgeschickter Anmeldeversuch soll seine
    gleichlautende Fehlermeldung behalten, statt am Weiterleitungsziel erkennen zu
    lassen, was der Dienst ueber den Benutzernamen weiss."""
    monkeypatch.setattr("thermoctl.web.auth_views.schlafen", lambda _: None)
    response = client.post(
        "/login",
        data={"username": "gibtsnicht", "password": "falsch-aber-lang"},
        follow_redirects=False,
    )
    assert response.status_code != 303
    assert "Benutzername oder Passwort falsch" in response.text
