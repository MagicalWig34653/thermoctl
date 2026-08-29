"""Die Regeln hinter Benutzer-, Gruppen- und Rechteverwaltung.

Zwei davon entscheiden darueber, ob eine laufende Anlage bedienbar bleibt:
Man kann sich nicht aussperren, und ein anlagenweites Recht laesst sich nicht auf eine
Zone einschraenken, in der es nie greifen wuerde.
"""

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.hilfen import (
    benutzer_anlegen,
    benutzer_mit_rechten,
    berechtigung,
    quelle,
    token_mit_rechten,
    zone_anlegen,
)
from thermoctl.db.models.identity import AccessGroup, GroupPermission, UserAccessGroup
from thermoctl.db.models.lookup import Permission
from thermoctl.db.models.operations import AuditEvent
from thermoctl.domain.verwaltung import (
    Verwaltungsfehler,
    benutzer_aktiv_setzen,
    gruppe_anlegen,
    gruppe_loeschen,
    passwort_setzen,
    recht_entziehen,
    recht_vergeben,
)
from thermoctl.domain.verwaltung import (
    benutzer_anlegen as domaene_benutzer_anlegen,
)


@pytest.fixture(autouse=True)
def _quelle(session: Session) -> None:
    quelle(session, "web")


def test_benutzer_anlegen_schreibt_audit_und_gruppenzuordnung(session: Session) -> None:
    gruppe = gruppe_anlegen(
        session, name="Bedienung", beschreibung=None, akteur_id=None
    )
    nutzer = domaene_benutzer_anlegen(
        session, username="neu", display_name="Neu", passwort="passwort-lang-genug",
        gruppen_ids=[gruppe.id], akteur_id=None,
    )
    zuordnung = session.scalar(
        select(UserAccessGroup).where(UserAccessGroup.user_id == nutzer.id)
    )
    assert zuordnung is not None and zuordnung.access_group_id == gruppe.id
    assert session.scalar(
        select(AuditEvent).where(AuditEvent.action == "user.created")
    ) is not None


def test_doppelter_benutzername_wird_abgewiesen(session: Session) -> None:
    domaene_benutzer_anlegen(
        session, username="doppelt", display_name="Erst", passwort="passwort-lang-genug",
        gruppen_ids=[], akteur_id=None,
    )
    with pytest.raises(Verwaltungsfehler, match="gibt es bereits"):
        domaene_benutzer_anlegen(
            session, username="doppelt", display_name="Zweit",
            passwort="passwort-lang-genug", gruppen_ids=[], akteur_id=None,
        )


def test_zu_kurzes_passwort_hinterlaesst_keinen_halben_benutzer(session: Session) -> None:
    """Genau der Fehler, der im Einrichtungsformular steckte: Die Ausnahme kam erst,
    nachdem schon geschrieben war."""
    from thermoctl.auth.passwords import PasswordTooShort
    from thermoctl.db.models.identity import User

    with pytest.raises(PasswordTooShort):
        domaene_benutzer_anlegen(
            session, username="zukurz", display_name="Zu kurz", passwort="kurz",
            gruppen_ids=[], akteur_id=None,
        )
    assert session.scalar(select(User).where(User.username == "zukurz")) is None


def test_letzter_verwalter_laesst_sich_nicht_deaktivieren(session: Session) -> None:
    """Ohne diese Regel genuegt ein Fehlgriff, um eine laufende Heizungssteuerung
    unbedienbar zu machen — mit Zugriff nur noch ueber die Datenbank."""
    verwalter = benutzer_mit_rechten(session, "einziger", [("user.manage", None)])
    with pytest.raises(Verwaltungsfehler, match="letzte aktive Benutzer"):
        benutzer_aktiv_setzen(session, verwalter, False, akteur_id=None)
    assert verwalter.is_active is True


def test_vorletzter_verwalter_laesst_sich_deaktivieren(session: Session) -> None:
    """Die Sperre darf nur den wirklich letzten treffen — sonst waere sie im Weg."""
    erster = benutzer_mit_rechten(session, "erster", [("user.manage", None)])
    benutzer_mit_rechten(session, "zweiter", [("user.manage", None)])
    benutzer_aktiv_setzen(session, erster, False, akteur_id=None)
    assert erster.is_active is False


def test_bereits_deaktivierter_zweiter_verwalter_zaehlt_nicht(session: Session) -> None:
    """Ein deaktivierter Verwalter kann niemanden reaktivieren — er zaehlt nicht mit."""
    aktiv = benutzer_mit_rechten(session, "aktiv", [("user.manage", None)])
    inaktiv = benutzer_mit_rechten(session, "inaktiv", [("user.manage", None)])
    inaktiv.is_active = False
    session.flush()
    with pytest.raises(Verwaltungsfehler):
        benutzer_aktiv_setzen(session, aktiv, False, akteur_id=None)


def test_deaktivierter_benutzer_laesst_sich_reaktivieren(session: Session) -> None:
    nutzer = benutzer_anlegen(session, "wieder-da")
    benutzer_aktiv_setzen(session, nutzer, False, akteur_id=None)
    benutzer_aktiv_setzen(session, nutzer, True, akteur_id=None)
    assert nutzer.is_active is True


def test_anlagenweites_recht_laesst_sich_nicht_auf_eine_zone_einschraenken(
    session: Session,
) -> None:
    """Das Modell haelt diese Zusicherung seit Teilprojekt 1 fest — hier wird sie eingeloest.

    `hat_recht()` fragt ein nicht zonenbezogenes Recht immer ohne Zonenangabe ab. Mit Zone
    vergeben stuende es in der Rechteliste und griffe nie. Eine Rechtevergabe, die
    aussieht, als haette sie gewirkt, ist schlimmer als eine abgelehnte.
    """
    berechtigung(session, "user.manage", zonenbezogen=False)
    zone = zone_anlegen(session, "bad")
    gruppe = gruppe_anlegen(session, name="Falsch", beschreibung=None, akteur_id=None)
    with pytest.raises(Verwaltungsfehler, match="ganze Anlage"):
        recht_vergeben(session, gruppe, "user.manage", zone.id, akteur_id=None)


def test_zonenbezogenes_recht_darf_eine_zone_tragen(session: Session) -> None:
    berechtigung(session, "zone.read", zonenbezogen=True)
    zone = zone_anlegen(session, "kueche")
    gruppe = gruppe_anlegen(session, name="Kuechenleser", beschreibung=None, akteur_id=None)
    eintrag = recht_vergeben(session, gruppe, "zone.read", zone.id, akteur_id=None)
    assert eintrag.zone_id == zone.id


def test_recht_zweimal_vergeben_ergibt_eine_zeile(session: Session) -> None:
    berechtigung(session, "zone.read", zonenbezogen=True)
    gruppe = gruppe_anlegen(session, name="Doppelt", beschreibung=None, akteur_id=None)
    erst = recht_vergeben(session, gruppe, "zone.read", None, akteur_id=None)
    zweit = recht_vergeben(session, gruppe, "zone.read", None, akteur_id=None)
    assert erst.id == zweit.id


def test_unbekanntes_recht_wird_abgewiesen(session: Session) -> None:
    gruppe = gruppe_anlegen(session, name="Leer", beschreibung=None, akteur_id=None)
    with pytest.raises(Verwaltungsfehler, match="gibt es nicht"):
        recht_vergeben(session, gruppe, "gibt.es.nicht", None, akteur_id=None)


def test_eingebaute_gruppe_laesst_sich_nicht_loeschen(session: Session) -> None:
    gruppe = AccessGroup(name="Verwaltung", is_builtin=True)
    session.add(gruppe)
    session.flush()
    with pytest.raises(Verwaltungsfehler, match="eingebaute Gruppe"):
        gruppe_loeschen(session, gruppe, akteur_id=None)


def test_letzte_quelle_des_verwaltungsrechts_laesst_sich_nicht_entfernen(
    session: Session,
) -> None:
    """Weder ueber das Loeschen der Gruppe noch ueber das Entziehen des Rechts."""
    benutzer_mit_rechten(session, "verwalter", [("user.manage", None)])
    gruppe = session.scalar(
        select(AccessGroup).where(AccessGroup.name == "gruppe-verwalter")
    )
    assert gruppe is not None
    with pytest.raises(Verwaltungsfehler, match="einzige verbliebene"):
        gruppe_loeschen(session, gruppe, akteur_id=None)

    recht_id = session.scalar(select(Permission.id).where(Permission.code == "user.manage"))
    eintrag = session.scalar(
        select(GroupPermission).where(
            GroupPermission.access_group_id == gruppe.id,
            GroupPermission.permission_id == recht_id,
        )
    )
    assert eintrag is not None
    with pytest.raises(Verwaltungsfehler, match="einzige verbliebene"):
        recht_entziehen(session, eintrag, akteur_id=None)


def test_gruppe_ohne_verwaltungsrecht_laesst_sich_loeschen(session: Session) -> None:
    benutzer_mit_rechten(session, "chef", [("user.manage", None)])
    entbehrlich = gruppe_anlegen(
        session, name="Entbehrlich", beschreibung=None, akteur_id=None
    )
    gruppe_loeschen(session, entbehrlich, akteur_id=None)
    assert session.get(AccessGroup, entbehrlich.id) is None


def test_passwort_setzen_aendert_den_hash_und_protokolliert(session: Session) -> None:
    from thermoctl.auth.passwords import verify_password

    nutzer = benutzer_anlegen(session, "wechsler")
    passwort_setzen(session, nutzer, "ein-neues-langes-passwort", akteur_id=None)
    assert verify_password("ein-neues-langes-passwort", nutzer.password_hash)
    eintrag = session.scalar(
        select(AuditEvent).where(AuditEvent.action == "user.password_changed")
    )
    assert eintrag is not None
    assert "ein-neues-langes-passwort" not in (eintrag.summary + (eintrag.detail or "")), (
        "Ein Passwort darf nie im Audit-Protokoll landen."
    )


def test_leerer_gruppenname_wird_abgewiesen(session: Session) -> None:
    with pytest.raises(Verwaltungsfehler, match="nicht leer"):
        gruppe_anlegen(session, name="   ", beschreibung=None, akteur_id=None)


def test_leerer_benutzername_wird_abgewiesen(session: Session) -> None:
    with pytest.raises(Verwaltungsfehler, match="nicht leer"):
        domaene_benutzer_anlegen(
            session, username="  ", display_name="X", passwort="passwort-lang-genug",
            gruppen_ids=[], akteur_id=None,
        )


def test_zweimal_widerrufen_aendert_den_zeitpunkt_nicht(session: Session) -> None:
    """Ein zweiter Klick darf den Widerrufszeitpunkt nicht nach hinten schieben — er ist
    die Antwort auf 'seit wann gilt das Token nicht mehr?'."""
    from thermoctl.domain.verwaltung import token_widerrufen

    nutzer = benutzer_anlegen(session, "tokenbesitzer")
    token = token_mit_rechten(session, nutzer, [])
    token_widerrufen(session, token, akteur_id=None)
    zuerst = token.revoked_at
    token_widerrufen(session, token, akteur_id=None)
    assert token.revoked_at == zuerst
