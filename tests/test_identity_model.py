import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tests.helpers import create_zone, ensure_permission
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
        group = AccessGroup(name=name)
        session.add(group)
        session.flush()
        session.add(UserAccessGroup(user_id=nutzer.id, access_group_id=group.id))
    session.flush()
    assert session.query(UserAccessGroup).filter_by(user_id=nutzer.id).count() == 2


def test_recht_anlagenweit_und_zonenbezogen_nebeneinander(session: Session) -> None:
    """NULL in zone_id heisst anlagenweit; beides darf nebeneinander stehen."""
    group = AccessGroup(name="Gemischt")
    session.add(group)
    session.flush()
    read_only = ensure_permission(session, "zone.read", zone_scoped=True)
    zone = create_zone(session, "bad")
    session.add(GroupPermission(access_group_id=group.id, permission_id=read_only.id, zone_id=None))
    session.add(
        GroupPermission(access_group_id=group.id, permission_id=read_only.id, zone_id=zone.id)
    )
    session.flush()
    assert session.query(GroupPermission).filter_by(access_group_id=group.id).count() == 2


def test_dieselbe_zuordnung_zweimal_ist_ausgeschlossen(session: Session) -> None:
    group = AccessGroup(name="Doppelt")
    session.add(group)
    session.flush()
    read_only = ensure_permission(session, "zone.read", zone_scoped=True)
    zone = create_zone(session, "kueche")
    for _ in range(2):
        session.add(
            GroupPermission(access_group_id=group.id, permission_id=read_only.id, zone_id=zone.id)
        )
    with pytest.raises(IntegrityError):
        session.flush()


def test_gruppe_wird_mit_ihren_rechten_geloescht(session: Session) -> None:
    group = AccessGroup(name="Weg")
    session.add(group)
    session.flush()
    session.add(
        GroupPermission(
            access_group_id=group.id, permission_id=ensure_permission(session, "audit.read").id
        )
    )
    session.flush()
    session.delete(group)
    session.flush()
    assert session.query(GroupPermission).filter_by(access_group_id=group.id).count() == 0
