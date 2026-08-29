import pytest
from sqlalchemy.orm import Session

from tests.hilfen import benutzer_mit_rechten, quelle, zone_anlegen
from thermoctl.auth.tokens import token_ausstellen
from thermoctl.domain.authz import Forbidden


def test_token_klartext_erscheint_genau_einmal(session: Session) -> None:
    nutzer = benutzer_mit_rechten(session, "a", [("zone.read", None), ("token.self", None)])
    token, klartext = token_ausstellen(session, nutzer, "HA", [("zone.read", None)], None)
    assert klartext.startswith("tctl_")
    assert klartext not in (token.token_hash, token.prefix)


def test_token_mit_mehr_rechten_als_der_besitzer_wird_abgewiesen(session: Session) -> None:
    nutzer = benutzer_mit_rechten(session, "b", [("zone.read", None), ("token.self", None)])
    with pytest.raises(Forbidden):
        token_ausstellen(session, nutzer, "Zuviel", [("zone.manage", None)], None)


def test_token_mit_fremder_zone_wird_abgewiesen(session: Session) -> None:
    bad = zone_anlegen(session, "bad")
    kueche = zone_anlegen(session, "kueche")
    nutzer = benutzer_mit_rechten(session, "c", [("zone.read", bad.id), ("token.self", None)])
    with pytest.raises(Forbidden):
        token_ausstellen(session, nutzer, "Fremd", [("zone.read", kueche.id)], None)


def test_benutzerliste_braucht_user_manage(client_als) -> None:
    ohne = client_als([("zone.read", None)])
    assert ohne.get("/benutzer").status_code == 403
    mit = client_als([("user.manage", None)])
    assert mit.get("/benutzer").status_code == 200


def test_passwort_hash_erscheint_in_keiner_ansicht(client_als) -> None:
    antwort = client_als([("user.manage", None)]).get("/benutzer")
    assert "$argon2id$" not in antwort.text


@pytest.fixture(autouse=True)
def _actor_quelle(session: Session) -> None:
    """Die Quelle `web` legt in Produktion die Referenzdatenmigration an.

    `Base.metadata.create_all()` in der Fixture `engine` legt nur das Schema an, keine
    Referenzdaten — ohne diese Zeile scheitert jeder Audit-Eintrag der aendernden
    Ansichten an der NOT-NULL-Bedingung auf `audit_event.source_id`.
    """
    quelle(session, "web")


def _mit_csrf(client, session):  # type: ignore[no-untyped-def]
    """Kopfzeile mit gueltigem CSRF-Token fuer aendernde Anfragen."""
    from thermoctl.auth.csrf import csrf_token
    from thermoctl.auth.sessions import COOKIE_NAME
    from thermoctl.config import get_settings

    geheimnis = client.cookies[COOKIE_NAME]
    return {"X-CSRF-Token": csrf_token(geheimnis, get_settings().secret_key.get_secret_value())}


def test_benutzer_anlegen_ueber_die_oberflaeche(client_als, session: Session) -> None:
    c = client_als([("user.manage", None)])
    antwort = c.post(
        "/benutzer",
        data={"username": "neuling", "display_name": "Neuling",
              "password": "passwort-lang-genug", "gruppe_id": ""},
        headers=_mit_csrf(c, session),
        follow_redirects=False,
    )
    assert antwort.status_code == 303
    from sqlalchemy import select

    from thermoctl.db.models.identity import User

    assert session.scalar(select(User).where(User.username == "neuling")) is not None


def test_zu_kurzes_passwort_fuehrt_zurueck_ins_formular(client_als, session: Session) -> None:
    """Kein 500, keine leere Maske — und der Benutzername bleibt stehen."""
    c = client_als([("user.manage", None)])
    antwort = c.post(
        "/benutzer",
        data={"username": "kurzpass", "display_name": "Kurz", "password": "kurz",
              "gruppe_id": ""},
        headers=_mit_csrf(c, session),
    )
    assert antwort.status_code == 200
    assert "mindestens 12 Zeichen" in antwort.text
    assert "kurzpass" in antwort.text
    assert "kurz\"" not in antwort.text.replace('value="kurzpass"', "")


def test_passwort_erscheint_nie_in_der_antwort(client_als, session: Session) -> None:
    c = client_als([("user.manage", None)])
    antwort = c.post(
        "/benutzer",
        data={"username": "", "display_name": "X", "password": "ein-auffaelliges-geheimnis",
              "gruppe_id": ""},
        headers=_mit_csrf(c, session),
    )
    assert "ein-auffaelliges-geheimnis" not in antwort.text


def test_letzter_verwalter_kann_sich_nicht_selbst_deaktivieren(
    client_als, session: Session
) -> None:
    """Die Aussperrsperre wirkt auch ueber die Oberflaeche, nicht nur in der Domaene."""
    from sqlalchemy import select

    from thermoctl.db.models.identity import User

    c = client_als([("user.manage", None)])
    ich = session.scalar(select(User).where(User.username.like("web-%")))
    assert ich is not None
    antwort = c.post(
        f"/benutzer/{ich.id}/aktiv", data={"aktiv": "nein"},
        headers=_mit_csrf(c, session),
    )
    assert antwort.status_code == 200
    assert "letzte aktive Benutzer" in antwort.text
    assert ich.is_active is True


def test_unbekannter_benutzer_ergibt_404(client_als, session: Session) -> None:
    c = client_als([("user.manage", None)])
    antwort = c.post(
        "/benutzer/999999/aktiv", data={"aktiv": "ja"}, headers=_mit_csrf(c, session)
    )
    assert antwort.status_code == 404


def test_gruppe_anlegen_und_recht_vergeben(client_als, session: Session) -> None:
    from sqlalchemy import select

    from tests.hilfen import berechtigung
    from thermoctl.db.models.identity import AccessGroup, GroupPermission

    berechtigung(session, "zone.read", zonenbezogen=True)
    c = client_als([("group.manage", None)])
    assert c.post(
        "/gruppen", data={"name": "Gaeste", "description": "Nur schauen"},
        headers=_mit_csrf(c, session), follow_redirects=False,
    ).status_code == 303
    gruppe = session.scalar(select(AccessGroup).where(AccessGroup.name == "Gaeste"))
    assert gruppe is not None

    assert c.post(
        f"/gruppen/{gruppe.id}/rechte", data={"code": "zone.read", "zone_id": ""},
        headers=_mit_csrf(c, session), follow_redirects=False,
    ).status_code == 303
    assert session.scalar(
        select(GroupPermission).where(GroupPermission.access_group_id == gruppe.id)
    ) is not None


def test_anlagenweites_recht_auf_eine_zone_wird_in_der_ansicht_abgewiesen(
    client_als, session: Session
) -> None:
    from sqlalchemy import select

    from tests.hilfen import berechtigung, zone_anlegen
    from thermoctl.db.models.identity import AccessGroup

    berechtigung(session, "user.manage", zonenbezogen=False)
    zone = zone_anlegen(session, "bad-fuer-rechte")
    c = client_als([("group.manage", None)])
    c.post("/gruppen", data={"name": "Falsch", "description": ""},
           headers=_mit_csrf(c, session))
    gruppe = session.scalar(select(AccessGroup).where(AccessGroup.name == "Falsch"))
    assert gruppe is not None
    antwort = c.post(
        f"/gruppen/{gruppe.id}/rechte", data={"code": "user.manage", "zone_id": str(zone.id)},
        headers=_mit_csrf(c, session),
    )
    assert antwort.status_code == 200
    assert "ganze Anlage" in antwort.text


def test_token_ausstellen_zeigt_den_klartext_genau_einmal(client_als, session: Session) -> None:
    c = client_als([("token.self", None), ("zone.read", None)])
    antwort = c.post(
        "/tokens", data={"name": "Anzeigetafel", "code": "zone.read", "gueltig_tage": ""},
        headers=_mit_csrf(c, session),
    )
    assert antwort.status_code == 200
    assert "tctl_" in antwort.text
    # Beim naechsten Aufruf der Seite ist er weg — gespeichert wird nur der Hash.
    assert "tctl_" not in c.get("/tokens").text


def test_token_ohne_namen_wird_abgewiesen(client_als, session: Session) -> None:
    c = client_als([("token.self", None)])
    antwort = c.post(
        "/tokens", data={"name": "  ", "code": "", "gueltig_tage": ""},
        headers=_mit_csrf(c, session),
    )
    assert antwort.status_code == 200
    assert "braucht einen Namen" in antwort.text


def test_token_mit_zuviel_rechten_wird_verstaendlich_abgewiesen(
    client_als, session: Session
) -> None:
    from tests.hilfen import berechtigung

    berechtigung(session, "zone.manage", zonenbezogen=True)
    c = client_als([("token.self", None)])
    antwort = c.post(
        "/tokens", data={"name": "Zuviel", "code": "zone.manage", "gueltig_tage": ""},
        headers=_mit_csrf(c, session),
    )
    assert antwort.status_code == 200
    assert "kann kein Token" in antwort.text


def test_fremdes_token_ist_nicht_auffindbar(client_als, session: Session) -> None:
    """404 statt 403 — sonst verriete die Antwort, welche Kennungen es gibt."""
    from tests.hilfen import benutzer_mit_rechten, token_mit_rechten

    fremder = benutzer_mit_rechten(session, "fremder", [("token.self", None)])
    fremdes = token_mit_rechten(session, fremder, [])
    c = client_als([("token.self", None)])
    antwort = c.post(
        f"/tokens/{fremdes.id}/widerrufen", headers=_mit_csrf(c, session)
    )
    assert antwort.status_code == 404
    assert fremdes.revoked_at is None


def test_eigenes_token_laesst_sich_widerrufen(client_als, session: Session) -> None:
    c = client_als([("token.self", None)])
    c.post("/tokens", data={"name": "Weg damit", "code": "", "gueltig_tage": "30"},
           headers=_mit_csrf(c, session))
    from sqlalchemy import select

    from thermoctl.db.models.credential import ApiToken

    token = session.scalar(select(ApiToken).where(ApiToken.name == "Weg damit"))
    assert token is not None
    assert c.post(
        f"/tokens/{token.id}/widerrufen", headers=_mit_csrf(c, session),
        follow_redirects=False,
    ).status_code == 303
    assert token.revoked_at is not None


def test_eigenes_passwort_aendern_ohne_user_manage(client_als, session: Session) -> None:
    """Wer nur sich selbst betrifft, braucht kein Verwaltungsrecht — sonst koennte
    niemand sein eigenes Passwort wechseln, ohne Verwalter zu sein."""
    from sqlalchemy import select

    from thermoctl.auth.passwords import verify_password
    from thermoctl.db.models.identity import User

    c = client_als([("zone.read", None)])
    ich = session.scalar(select(User).where(User.username.like("web-%")).order_by(User.id.desc()))
    assert ich is not None
    antwort = c.post(
        f"/benutzer/{ich.id}/passwort", data={"password": "mein-neues-langes-passwort"},
        headers=_mit_csrf(c, session), follow_redirects=False,
    )
    assert antwort.status_code == 303
    assert verify_password("mein-neues-langes-passwort", ich.password_hash)


def test_fremdes_passwort_aendern_braucht_user_manage(client_als, session: Session) -> None:
    from tests.hilfen import benutzer_anlegen

    fremder = benutzer_anlegen(session, "fremdes-passwort")
    c = client_als([("zone.read", None)])
    antwort = c.post(
        f"/benutzer/{fremder.id}/passwort", data={"password": "egal-was-hier-steht"},
        headers=_mit_csrf(c, session),
    )
    assert antwort.status_code == 403


def test_gruppe_loeschen_ueber_die_oberflaeche(client_als, session: Session) -> None:
    from sqlalchemy import select

    from thermoctl.db.models.identity import AccessGroup

    c = client_als([("group.manage", None)])
    c.post("/gruppen", data={"name": "Entbehrlich", "description": ""},
           headers=_mit_csrf(c, session))
    gruppe = session.scalar(select(AccessGroup).where(AccessGroup.name == "Entbehrlich"))
    assert gruppe is not None
    assert c.post(
        f"/gruppen/{gruppe.id}/loeschen", headers=_mit_csrf(c, session),
        follow_redirects=False,
    ).status_code == 303
    assert session.get(AccessGroup, gruppe.id) is None


def test_recht_entziehen_ueber_die_oberflaeche(client_als, session: Session) -> None:
    from sqlalchemy import select

    from tests.hilfen import berechtigung
    from thermoctl.db.models.identity import AccessGroup, GroupPermission

    berechtigung(session, "device.read", zonenbezogen=True)
    c = client_als([("group.manage", None)])
    c.post("/gruppen", data={"name": "Rechteweg", "description": ""},
           headers=_mit_csrf(c, session))
    gruppe = session.scalar(select(AccessGroup).where(AccessGroup.name == "Rechteweg"))
    assert gruppe is not None
    c.post(f"/gruppen/{gruppe.id}/rechte", data={"code": "device.read", "zone_id": ""},
           headers=_mit_csrf(c, session))
    eintrag = session.scalar(
        select(GroupPermission).where(GroupPermission.access_group_id == gruppe.id)
    )
    assert eintrag is not None
    assert c.post(
        f"/gruppen/{gruppe.id}/rechte/{eintrag.id}/loeschen",
        headers=_mit_csrf(c, session), follow_redirects=False,
    ).status_code == 303
    assert session.get(GroupPermission, eintrag.id) is None


def test_rechteintrag_einer_fremden_gruppe_ergibt_404(client_als, session: Session) -> None:
    from sqlalchemy import select

    from tests.hilfen import berechtigung
    from thermoctl.db.models.identity import AccessGroup, GroupPermission

    berechtigung(session, "device.read", zonenbezogen=True)
    c = client_als([("group.manage", None)])
    c.post("/gruppen", data={"name": "Eine", "description": ""}, headers=_mit_csrf(c, session))
    c.post("/gruppen", data={"name": "Andere", "description": ""}, headers=_mit_csrf(c, session))
    eine = session.scalar(select(AccessGroup).where(AccessGroup.name == "Eine"))
    andere = session.scalar(select(AccessGroup).where(AccessGroup.name == "Andere"))
    assert eine is not None and andere is not None
    c.post(f"/gruppen/{eine.id}/rechte", data={"code": "device.read", "zone_id": ""},
           headers=_mit_csrf(c, session))
    eintrag = session.scalar(
        select(GroupPermission).where(GroupPermission.access_group_id == eine.id)
    )
    assert eintrag is not None
    antwort = c.post(
        f"/gruppen/{andere.id}/rechte/{eintrag.id}/loeschen", headers=_mit_csrf(c, session)
    )
    assert antwort.status_code == 404


def test_unbekannte_gruppe_ergibt_404(client_als, session: Session) -> None:
    c = client_als([("group.manage", None)])
    assert c.post("/gruppen/999999/loeschen", headers=_mit_csrf(c, session)).status_code == 404
    assert c.post(
        "/gruppen/999999/rechte", data={"code": "zone.read", "zone_id": ""},
        headers=_mit_csrf(c, session),
    ).status_code == 404


def test_doppelter_gruppenname_bleibt_im_formular(client_als, session: Session) -> None:
    c = client_als([("group.manage", None)])
    c.post("/gruppen", data={"name": "Zweimal", "description": ""},
           headers=_mit_csrf(c, session))
    antwort = c.post("/gruppen", data={"name": "Zweimal", "description": ""},
                     headers=_mit_csrf(c, session))
    assert antwort.status_code == 200
    assert "gibt es bereits" in antwort.text


def test_gruppe_mit_dem_letzten_verwaltungsrecht_bleibt_stehen(
    client_als, session: Session
) -> None:
    """Auch ueber die Oberflaeche laesst sich die letzte Quelle nicht entfernen."""
    from sqlalchemy import select

    from thermoctl.db.models.identity import AccessGroup

    c = client_als([("group.manage", None), ("user.manage", None)])
    gruppe = session.scalar(
        select(AccessGroup).where(AccessGroup.name.like("gruppe-web-%")).order_by(
            AccessGroup.id.desc()
        )
    )
    assert gruppe is not None
    antwort = c.post(f"/gruppen/{gruppe.id}/loeschen", headers=_mit_csrf(c, session))
    assert antwort.status_code == 200
    assert "einzige verbliebene" in antwort.text
    assert session.get(AccessGroup, gruppe.id) is not None


def test_zu_kurzes_passwort_beim_eigenen_wechsel_bleibt_im_formular(
    client_als, session: Session
) -> None:
    from sqlalchemy import select

    from thermoctl.db.models.identity import User

    c = client_als([("user.manage", None)])
    ich = session.scalar(select(User).where(User.username.like("web-%")).order_by(User.id.desc()))
    assert ich is not None
    antwort = c.post(
        f"/benutzer/{ich.id}/passwort", data={"password": "kurz"},
        headers=_mit_csrf(c, session),
    )
    assert antwort.status_code == 200
    assert "mindestens 12 Zeichen" in antwort.text


def test_benutzer_deaktivieren_und_wieder_aktivieren(client_als, session: Session) -> None:
    from tests.hilfen import benutzer_anlegen

    anderer = benutzer_anlegen(session, "kommt-und-geht")
    c = client_als([("user.manage", None)])
    assert c.post(
        f"/benutzer/{anderer.id}/aktiv", data={"aktiv": "nein"},
        headers=_mit_csrf(c, session), follow_redirects=False,
    ).status_code == 303
    assert anderer.is_active is False
    assert c.post(
        f"/benutzer/{anderer.id}/aktiv", data={"aktiv": "ja"},
        headers=_mit_csrf(c, session), follow_redirects=False,
    ).status_code == 303
    assert anderer.is_active is True


def test_passwortwechsel_fuer_unbekannten_benutzer_ergibt_404(
    client_als, session: Session
) -> None:
    c = client_als([("user.manage", None)])
    antwort = c.post(
        "/benutzer/999999/passwort", data={"password": "ein-langes-passwort-hier"},
        headers=_mit_csrf(c, session),
    )
    assert antwort.status_code == 404


def test_letztes_verwaltungsrecht_laesst_sich_nicht_entziehen(
    client_als, session: Session
) -> None:
    """Der zweite Weg in dieselbe Sackgasse: nicht die Gruppe loeschen, sondern ihr das
    Recht nehmen. Beide muessen gesperrt sein, sonst ist die Sperre umgehbar."""
    from sqlalchemy import select

    from thermoctl.db.models.identity import AccessGroup, GroupPermission
    from thermoctl.db.models.lookup import Permission

    c = client_als([("group.manage", None), ("user.manage", None)])
    gruppe = session.scalar(
        select(AccessGroup).where(AccessGroup.name.like("gruppe-web-%")).order_by(
            AccessGroup.id.desc()
        )
    )
    assert gruppe is not None
    recht_id = session.scalar(select(Permission.id).where(Permission.code == "user.manage"))
    eintrag = session.scalar(
        select(GroupPermission).where(
            GroupPermission.access_group_id == gruppe.id,
            GroupPermission.permission_id == recht_id,
        )
    )
    assert eintrag is not None
    antwort = c.post(
        f"/gruppen/{gruppe.id}/rechte/{eintrag.id}/loeschen", headers=_mit_csrf(c, session)
    )
    assert antwort.status_code == 200
    assert "einzige verbliebene" in antwort.text
    assert session.get(GroupPermission, eintrag.id) is not None
