"""Gemeinsame Testhilfen.

Wird von mehreren Testdateien benutzt und waechst mit dem Schema mit: jede Aufgabe,
die neue Entitaeten anlegt, ergaenzt hier ihre Anlegefunktion.
"""

from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from thermoctl.db.models.credential import ApiToken, ApiTokenPermission
from thermoctl.db.models.device import Device
from thermoctl.db.models.identity import AccessGroup, GroupPermission, User, UserAccessGroup
from thermoctl.db.models.lookup import (
    ActorSource,
    DeviceRole,
    Integration,
    OperatingMode,
    Permission,
)
from thermoctl.db.models.zone import SetpointMode, Zone

# Eine verletzte CHECK-Bedingung kommt je nach Datenbank als andere Ausnahme an:
# SQLite meldet IntegrityError, MariaDB meldet Fehler 4025, den pymysql auf
# OperationalError abbildet. Die Bedingung greift in beiden Faellen — nur die
# Klasse unterscheidet sich. Verletzte UNIQUE-Bedingungen sind dagegen ueberall
# IntegrityError; dort diese Konstante nicht verwenden, sonst prueft der Test
# weniger als er soll.
CONSTRAINT_FEHLER = (IntegrityError, OperationalError)


def betriebsart(session: Session, code: str = "auto") -> OperatingMode:
    art = session.query(OperatingMode).filter_by(code=code).one_or_none()
    if art is None:
        art = OperatingMode(code=code, label=code)
        session.add(art)
        session.flush()
    return art


def zone_anlegen(session: Session, name: str) -> Zone:
    zone = Zone(name=name, display_name=name.capitalize(),
                operating_mode_id=betriebsart(session).id)
    session.add(zone)
    session.flush()
    return zone


def modus_anlegen(session: Session, code: str, name: str | None = None) -> SetpointMode:
    modus = SetpointMode(code=code, name=name or code.capitalize())
    session.add(modus)
    session.flush()
    return modus


def quelle(session: Session, code: str = "web") -> ActorSource:
    q = session.query(ActorSource).filter_by(code=code).one_or_none()
    if q is None:
        q = ActorSource(code=code, label=code)
        session.add(q)
        session.flush()
    return q


def anbindung(session: Session, code: str = "zigbee2mqtt") -> Integration:
    a = session.query(Integration).filter_by(code=code).one_or_none()
    if a is None:
        a = Integration(code=code, label=code)
        session.add(a)
        session.flush()
    return a


def rolle(session: Session, code: str) -> DeviceRole:
    r = session.query(DeviceRole).filter_by(code=code).one_or_none()
    if r is None:
        r = DeviceRole(code=code, label=code)
        session.add(r)
        session.flush()
    return r


def geraet_anlegen(session: Session, external_id: str) -> Device:
    g = Device(integration_id=anbindung(session).id, external_id=external_id,
               display_name=external_id)
    session.add(g)
    session.flush()
    return g


def berechtigung(session: Session, code: str, zonenbezogen: bool = False) -> Permission:
    p = session.query(Permission).filter_by(code=code).one_or_none()
    if p is None:
        p = Permission(code=code, description=code, is_zone_scoped=zonenbezogen)
        session.add(p)
        session.flush()
    return p


def benutzer_anlegen(session: Session, name: str) -> User:
    nutzer = User(username=name, display_name=name.upper(), password_hash="platzhalter")
    session.add(nutzer)
    session.flush()
    return nutzer


def _gruppe_mit_rechten(
    session: Session, name: str, rechte: list[tuple[str, int | None]]
) -> AccessGroup:
    gruppe = AccessGroup(name=name)
    session.add(gruppe)
    session.flush()
    for code, zone_id in rechte:
        berechtigung_obj = berechtigung(session, code, zonenbezogen=zone_id is not None)
        session.add(
            GroupPermission(
                access_group_id=gruppe.id, permission_id=berechtigung_obj.id, zone_id=zone_id
            )
        )
    session.flush()
    return gruppe


def benutzer_mit_rechten(
    session: Session,
    name: str,
    rechte: list[tuple[str, int | None]],
    zweite_gruppe: list[tuple[str, int | None]] | None = None,
) -> User:
    """Legt einen Benutzer an und haengt ihn an eine (bzw. zwei) Zugriffsgruppe(n) mit den
    uebergebenen ``(code, zone_id)``-Rechten."""
    nutzer = benutzer_anlegen(session, name)
    gruppe = _gruppe_mit_rechten(session, f"gruppe-{name}", rechte)
    session.add(UserAccessGroup(user_id=nutzer.id, access_group_id=gruppe.id))
    if zweite_gruppe is not None:
        gruppe2 = _gruppe_mit_rechten(session, f"gruppe-{name}-2", zweite_gruppe)
        session.add(UserAccessGroup(user_id=nutzer.id, access_group_id=gruppe2.id))
    session.flush()
    return nutzer


def token_mit_rechten(
    session: Session, nutzer: User, rechte: list[tuple[str, int | None]]
) -> ApiToken:
    """Legt ein API-Token fuer ``nutzer`` an und traegt die uebergebenen Rechte ein."""
    token = ApiToken(
        user_id=nutzer.id,
        name=f"token-{nutzer.username}",
        prefix=f"pfx-{nutzer.username}",
        token_hash=f"hash-{nutzer.username}",
    )
    session.add(token)
    session.flush()
    for code, zone_id in rechte:
        berechtigung_obj = berechtigung(session, code, zonenbezogen=zone_id is not None)
        session.add(
            ApiTokenPermission(
                api_token_id=token.id, permission_id=berechtigung_obj.id, zone_id=zone_id
            )
        )
    session.flush()
    return token
