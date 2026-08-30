"""Die Regeln hinter Benutzer-, Gruppen- und Rechteverwaltung.

Zwei davon entscheiden darueber, ob eine laufende Anlage bedienbar bleibt:
Man kann sich nicht aussperren, und ein anlagenweites Recht laesst sich nicht auf eine
Zone einschraenken, in der es nie greifen wuerde.
"""

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.helpers import (
    create_user,
    create_zone,
    ensure_permission,
    source,
    token_with_permissions,
    user_with_permissions,
)
from thermoctl.db.models.identity import AccessGroup, GroupPermission, UserAccessGroup
from thermoctl.db.models.lookup import Permission
from thermoctl.db.models.operations import AuditEvent
from thermoctl.domain.administration import (
    AdministrationError,
    create_group,
    delete_group,
    grant_permission,
    revoke_permission,
    set_password,
    set_user_active,
)
from thermoctl.domain.administration import (
    create_user as domain_create_user,
)


@pytest.fixture(autouse=True)
def _source(session: Session) -> None:
    source(session, "web")


def test_benutzer_anlegen_schreibt_audit_und_gruppenzuordnung(session: Session) -> None:
    group = create_group(
        session, name="Bedienung", beschreibung=None, akteur_id=None
    )
    nutzer = domain_create_user(
        session, username="neu", display_name="Neu", password="passwort-lang-genug",
        group_ids=[group.id], akteur_id=None,
    )
    assignment = session.scalar(
        select(UserAccessGroup).where(UserAccessGroup.user_id == nutzer.id)
    )
    assert assignment is not None and assignment.access_group_id == group.id
    assert session.scalar(
        select(AuditEvent).where(AuditEvent.action == "user.created")
    ) is not None


def test_doppelter_benutzername_wird_abgewiesen(session: Session) -> None:
    domain_create_user(
        session, username="doppelt", display_name="Erst", password="passwort-lang-genug",
        group_ids=[], akteur_id=None,
    )
    with pytest.raises(AdministrationError, match="gibt es bereits"):
        domain_create_user(
            session, username="doppelt", display_name="Zweit",
            password="passwort-lang-genug", group_ids=[], akteur_id=None,
        )


def test_zu_kurzes_passwort_hinterlaesst_keinen_halben_benutzer(session: Session) -> None:
    """Genau der Fehler, der im Einrichtungsformular steckte: Die Ausnahme kam erst,
    nachdem schon geschrieben war."""
    from thermoctl.auth.passwords import PasswordTooShort
    from thermoctl.db.models.identity import User

    with pytest.raises(PasswordTooShort):
        domain_create_user(
            session, username="zukurz", display_name="Zu kurz", password="kurz",
            group_ids=[], akteur_id=None,
        )
    assert session.scalar(select(User).where(User.username == "zukurz")) is None


def test_letzter_verwalter_laesst_sich_nicht_deaktivieren(session: Session) -> None:
    """Ohne diese Regel genuegt ein Fehlgriff, um eine laufende Heizungssteuerung
    unbedienbar zu machen — mit Zugriff nur noch ueber die Datenbank."""
    administrator = user_with_permissions(session, "einziger", [("user.manage", None)])
    with pytest.raises(AdministrationError, match="letzte aktive Benutzer"):
        set_user_active(session, administrator, False, akteur_id=None)
    assert administrator.is_active is True


def test_vorletzter_verwalter_laesst_sich_deaktivieren(session: Session) -> None:
    """Die Sperre darf nur den wirklich letzten treffen — sonst waere sie im Weg."""
    first = user_with_permissions(session, "erster", [("user.manage", None)])
    user_with_permissions(session, "zweiter", [("user.manage", None)])
    set_user_active(session, first, False, akteur_id=None)
    assert first.is_active is False


def test_bereits_deaktivierter_zweiter_verwalter_zaehlt_nicht(session: Session) -> None:
    """Ein deaktivierter Verwalter kann niemanden reaktivieren — er zaehlt nicht mit."""
    active = user_with_permissions(session, "aktiv", [("user.manage", None)])
    inactive = user_with_permissions(session, "inaktiv", [("user.manage", None)])
    inactive.is_active = False
    session.flush()
    with pytest.raises(AdministrationError):
        set_user_active(session, active, False, akteur_id=None)


def test_deaktivierter_benutzer_laesst_sich_reaktivieren(session: Session) -> None:
    nutzer = create_user(session, "wieder-da")
    set_user_active(session, nutzer, False, akteur_id=None)
    set_user_active(session, nutzer, True, akteur_id=None)
    assert nutzer.is_active is True


def test_anlagenweites_recht_laesst_sich_nicht_auf_eine_zone_einschraenken(
    session: Session,
) -> None:
    """Das Modell haelt diese Zusicherung seit Teilprojekt 1 fest — hier wird sie eingeloest.

    `hat_recht()` fragt ein nicht zonenbezogenes Recht immer ohne Zonenangabe ab. Mit Zone
    vergeben stuende es in der Rechteliste und griffe nie. Eine Rechtevergabe, die
    aussieht, als haette sie gewirkt, ist schlimmer als eine abgelehnte.
    """
    ensure_permission(session, "user.manage", zone_scoped=False)
    zone = create_zone(session, "bad")
    group = create_group(session, name="Falsch", beschreibung=None, akteur_id=None)
    with pytest.raises(AdministrationError, match="ganze Anlage"):
        grant_permission(session, group, "user.manage", zone.id, akteur_id=None)


def test_zonenbezogenes_recht_darf_eine_zone_tragen(session: Session) -> None:
    ensure_permission(session, "zone.read", zone_scoped=True)
    zone = create_zone(session, "kueche")
    group = create_group(session, name="Kuechenleser", beschreibung=None, akteur_id=None)
    entry = grant_permission(session, group, "zone.read", zone.id, akteur_id=None)
    assert entry.zone_id == zone.id


def test_recht_zweimal_vergeben_ergibt_eine_zeile(session: Session) -> None:
    ensure_permission(session, "zone.read", zone_scoped=True)
    group = create_group(session, name="Doppelt", beschreibung=None, akteur_id=None)
    first = grant_permission(session, group, "zone.read", None, akteur_id=None)
    zweit = grant_permission(session, group, "zone.read", None, akteur_id=None)
    assert first.id == zweit.id


def test_unbekanntes_recht_wird_abgewiesen(session: Session) -> None:
    group = create_group(session, name="Leer", beschreibung=None, akteur_id=None)
    with pytest.raises(AdministrationError, match="gibt es nicht"):
        grant_permission(session, group, "gibt.es.nicht", None, akteur_id=None)


def test_eingebaute_gruppe_laesst_sich_nicht_loeschen(session: Session) -> None:
    group = AccessGroup(name="Verwaltung", is_builtin=True)
    session.add(group)
    session.flush()
    with pytest.raises(AdministrationError, match="eingebaute Gruppe"):
        delete_group(session, group, akteur_id=None)


def test_letzte_quelle_des_verwaltungsrechts_laesst_sich_nicht_entfernen(
    session: Session,
) -> None:
    """Weder ueber das Loeschen der Gruppe noch ueber das Entziehen des Rechts."""
    user_with_permissions(session, "verwalter", [("user.manage", None)])
    group = session.scalar(
        select(AccessGroup).where(AccessGroup.name == "gruppe-verwalter")
    )
    assert group is not None
    with pytest.raises(AdministrationError, match="einzige verbliebene"):
        delete_group(session, group, akteur_id=None)

    permission_id = session.scalar(select(Permission.id).where(Permission.code == "user.manage"))
    entry = session.scalar(
        select(GroupPermission).where(
            GroupPermission.access_group_id == group.id,
            GroupPermission.permission_id == permission_id,
        )
    )
    assert entry is not None
    with pytest.raises(AdministrationError, match="einzige verbliebene"):
        revoke_permission(session, entry, akteur_id=None)


def test_gruppe_ohne_verwaltungsrecht_laesst_sich_loeschen(session: Session) -> None:
    user_with_permissions(session, "chef", [("user.manage", None)])
    entbehrlich = create_group(
        session, name="Entbehrlich", beschreibung=None, akteur_id=None
    )
    delete_group(session, entbehrlich, akteur_id=None)
    assert session.get(AccessGroup, entbehrlich.id) is None


def test_passwort_setzen_aendert_den_hash_und_protokolliert(session: Session) -> None:
    from thermoctl.auth.passwords import verify_password

    nutzer = create_user(session, "wechsler")
    set_password(session, nutzer, "ein-neues-langes-passwort", akteur_id=None)
    assert verify_password("ein-neues-langes-passwort", nutzer.password_hash)
    entry = session.scalar(
        select(AuditEvent).where(AuditEvent.action == "user.password_changed")
    )
    assert entry is not None
    assert "ein-neues-langes-passwort" not in (entry.summary + (entry.detail or "")), (
        "Ein Passwort darf nie im Audit-Protokoll landen."
    )


def test_leerer_gruppenname_wird_abgewiesen(session: Session) -> None:
    with pytest.raises(AdministrationError, match="nicht leer"):
        create_group(session, name="   ", beschreibung=None, akteur_id=None)


def test_leerer_benutzername_wird_abgewiesen(session: Session) -> None:
    with pytest.raises(AdministrationError, match="nicht leer"):
        domain_create_user(
            session, username="  ", display_name="X", password="passwort-lang-genug",
            group_ids=[], akteur_id=None,
        )


def test_zweimal_widerrufen_aendert_den_zeitpunkt_nicht(session: Session) -> None:
    """Ein zweiter Klick darf den Widerrufszeitpunkt nicht nach hinten schieben — er ist
    die Antwort auf 'seit wann gilt das Token nicht mehr?'."""
    from thermoctl.domain.administration import revoke_token

    nutzer = create_user(session, "tokenbesitzer")
    token = token_with_permissions(session, nutzer, [])
    revoke_token(session, token, akteur_id=None)
    first = token.revoked_at
    revoke_token(session, token, akteur_id=None)
    assert token.revoked_at == first
