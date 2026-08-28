import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tests.hilfen import berechtigung, zone_anlegen
from thermoctl.db.models.identity import AccessGroup, GroupPermission, User, UserAccessGroup


def test_benutzername_ist_eindeutig(session: Session) -> None:
    session.add(User(username="lino", display_name="Lino", password_hash="x"))
    session.flush()
    session.add(User(username="lino", display_name="Zweiter", password_hash="y"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_benutzer_kann_in_mehreren_gruppen_sein(session: Session) -> None:
    nutzer = User(username="a", display_name="A", password_hash="x")
    session.add(nutzer)
    for name in ("Verwaltung", "Bedienung"):
        gruppe = AccessGroup(name=name)
        session.add(gruppe)
        session.flush()
        session.add(UserAccessGroup(user_id=nutzer.id, access_group_id=gruppe.id))
    session.flush()
    assert session.query(UserAccessGroup).filter_by(user_id=nutzer.id).count() == 2


def test_recht_anlagenweit_und_zonenbezogen_nebeneinander(session: Session) -> None:
    """NULL in zone_id heisst anlagenweit; beides darf nebeneinander stehen."""
    gruppe = AccessGroup(name="Gemischt")
    session.add(gruppe)
    session.flush()
    lesen = berechtigung(session, "zone.read", zonenbezogen=True)
    zone = zone_anlegen(session, "bad")
    session.add(GroupPermission(access_group_id=gruppe.id, permission_id=lesen.id, zone_id=None))
    session.add(GroupPermission(access_group_id=gruppe.id, permission_id=lesen.id, zone_id=zone.id))
    session.flush()
    assert session.query(GroupPermission).filter_by(access_group_id=gruppe.id).count() == 2


def test_dieselbe_zuordnung_zweimal_ist_ausgeschlossen(session: Session) -> None:
    gruppe = AccessGroup(name="Doppelt")
    session.add(gruppe)
    session.flush()
    lesen = berechtigung(session, "zone.read", zonenbezogen=True)
    zone = zone_anlegen(session, "kueche")
    for _ in range(2):
        session.add(
            GroupPermission(access_group_id=gruppe.id, permission_id=lesen.id, zone_id=zone.id)
        )
    with pytest.raises(IntegrityError):
        session.flush()


def test_gruppe_wird_mit_ihren_rechten_geloescht(session: Session) -> None:
    gruppe = AccessGroup(name="Weg")
    session.add(gruppe)
    session.flush()
    session.add(
        GroupPermission(
            access_group_id=gruppe.id, permission_id=berechtigung(session, "audit.read").id
        )
    )
    session.flush()
    session.delete(gruppe)
    session.flush()
    assert session.query(GroupPermission).filter_by(access_group_id=gruppe.id).count() == 0
