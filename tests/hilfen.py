"""Gemeinsame Testhilfen.

Wird von mehreren Testdateien benutzt und waechst mit dem Schema mit: jede Aufgabe,
die neue Entitaeten anlegt, ergaenzt hier ihre Anlegefunktion.
"""

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from fastapi import FastAPI
from fastapi.routing import APIRoute
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session
from starlette.routing import BaseRoute

from thermoctl.db.models.credential import ApiToken, ApiTokenPermission
from thermoctl.db.models.device import Device
from thermoctl.db.models.identity import AccessGroup, GroupPermission, User, UserAccessGroup
from thermoctl.db.models.lookup import (
    PERMISSIONS,
    ActorSource,
    DeviceCapability,
    DeviceRole,
    Integration,
    OperatingMode,
    Permission,
    SensorStatus,
)
from thermoctl.db.models.messwert import DeviceHealth, Measurement
from thermoctl.db.models.operations import Setting
from thermoctl.db.models.override import ZoneOverride
from thermoctl.db.models.passkey import UserPasskey
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.zone import SetpointMode, Zone, ZoneSetpoint
from thermoctl.db.models.zustand import ShadowDecision, ZoneState

# Eine verletzte CHECK-Bedingung kommt je nach Datenbank als andere Ausnahme an:
# SQLite meldet IntegrityError, MariaDB meldet Fehler 4025, den pymysql auf
# OperationalError abbildet. Die Bedingung greift in beiden Faellen — nur die
# Klasse unterscheidet sich. Verletzte UNIQUE-Bedingungen sind dagegen ueberall
# IntegrityError; dort diese Konstante nicht verwenden, sonst prueft der Test
# weniger als er soll.
CONSTRAINT_FEHLER = (IntegrityError, OperationalError)


def einstellungen_anlegen(
    session: Session,
    hysterese: Decimal = Decimal("0.30"),
    min_ein: int = 300,
    sitzungsdauer_s: int | None = None,
) -> Setting:
    zusatz: dict[str, int] = {}
    if sitzungsdauer_s is not None:
        zusatz["session_lifetime_seconds"] = sitzungsdauer_s
    einstellungen = Setting(
        id=1,
        # `eingebaut=True` wie in Produktion: Der Einrichtungsassistent legt den
        # Frostschutzmodus als eingebauten Modus an. Ohne das prueft jeder Test, der
        # diese Fixture benutzt, einen Zustand, den es in keiner echten Anlage gibt --
        # und genau daran ist die Reihenfolge der Loeschsperren unbemerkt geblieben.
        frost_protection_mode_id=modus_anlegen(session, "frost", eingebaut=True).id,
        default_hysteresis_k=hysterese,
        default_min_on_seconds=min_ein,
        **zusatz,
    )
    session.add(einstellungen)
    session.flush()
    return einstellungen


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


def alle_rechte_anlegen(session: Session) -> None:
    """Legt alle Rechte an, wie es die Migration in jeder echten Datenbank tut.

    Die Gruppenseite zeigt die Rechte, die es *gibt*. In einem Test, der nur die zwei
    Rechte anlegt, die er selbst braucht, zeigt sie folgerichtig zwei -- das sagt ueber
    die Seite nichts.
    """
    for code, _beschreibung, _zonenbezogen in PERMISSIONS:
        berechtigung(session, code)


def modus_anlegen(
    session: Session, code: str, name: str | None = None, *, eingebaut: bool = False
) -> SetpointMode:
    modus = SetpointMode(code=code, name=name or code.capitalize(), is_builtin=eingebaut)
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


def faehigkeit(session: Session, code: str) -> DeviceCapability:
    f = session.query(DeviceCapability).filter_by(code=code).one_or_none()
    if f is None:
        f = DeviceCapability(code=code, label=code)
        session.add(f)
        session.flush()
    return f


def geraet_anlegen(session: Session, external_id: str) -> Device:
    g = Device(integration_id=anbindung(session).id, external_id=external_id,
               display_name=external_id)
    session.add(g)
    session.flush()
    return g


# Die echten Beschreibungen und Geltungsbereiche, wie die Migration sie einspielt.
_RECHTE_AUS_DEM_MODELL = {
    code: (beschreibung, zonenbezogen)
    for code, beschreibung, zonenbezogen in PERMISSIONS
}


def berechtigung(session: Session, code: str, zonenbezogen: bool | None = None) -> Permission:
    """Legt ein Recht an, wie es die Migration tut -- mit seiner echten Beschreibung.

    Vorher stand als Beschreibung schlicht der Code. Das ist ein Zustand, den keine
    Instanz hat, und er verdeckte, dass die Gruppenseite nur Codes anzeigte: In den
    Tests sahen Code und Klartext gleich aus.
    """
    p = session.query(Permission).filter_by(code=code).one_or_none()
    if p is None:
        beschreibung, aus_modell = _RECHTE_AUS_DEM_MODELL.get(code, (code, False))
        p = Permission(
            code=code,
            description=beschreibung,
            is_zone_scoped=aus_modell if zonenbezogen is None else zonenbezogen,
        )
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
        # Auf 16 Zeichen gekuerzt: So lang ist die Spalte. SQLite nimmt laengere Werte
        # klaglos an, MariaDB weist sie ab — ein Test mit langem Benutzernamen schlaegt
        # sonst nur unter MariaDB fehl, und das sucht man an der falschen Stelle.
        prefix=f"pfx-{nutzer.username}"[:16],
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
def punkt(weekday: int, minute_of_day: int, modus_code: str) -> SchedulePoint:
    return SchedulePoint(
        weekday=weekday, minute_of_day=minute_of_day, setpoint_mode_id=0
    )


def zone_mit_zeitplan(
    session: Session,
    name: str,
    punkte: list[tuple[int, int, str, Decimal]],
    betriebsart: str = "auto",
    frostschutz: Decimal = Decimal("16.0"),
    uebersteuerung: tuple[Decimal, datetime | None] | None = None,
) -> Zone:
    frost = modus_anlegen(session, f"frost-{name}", "Frostschutz")
    session.add(Setting(id=1, timezone="Europe/Berlin", frost_protection_mode_id=frost.id))
    art = session.query(OperatingMode).filter_by(code=betriebsart).one_or_none()
    if art is None:
        art = OperatingMode(code=betriebsart, label=betriebsart)
        session.add(art)
        session.flush()
    zone = Zone(name=name, display_name=name.capitalize(), operating_mode_id=art.id)
    session.add(zone)
    session.flush()
    session.add(ZoneSetpoint(
        zone_id=zone.id, setpoint_mode_id=frost.id, temperature_c=frostschutz
    ))
    for weekday, minute_of_day, modus_code, temperatur in punkte:
        modus = session.query(SetpointMode).filter_by(code=modus_code).one_or_none()
        if modus is None:
            modus = modus_anlegen(session, modus_code)
        session.add(ZoneSetpoint(
            zone_id=zone.id, setpoint_mode_id=modus.id, temperature_c=temperatur
        ))
        session.add(SchedulePoint(
            zone_id=zone.id, weekday=weekday, minute_of_day=minute_of_day,
            setpoint_mode_id=modus.id,
        ))
    if uebersteuerung is not None:
        temperatur, ende = uebersteuerung
        session.add(ZoneOverride(
            zone_id=zone.id, temperature_c=temperatur,
            starts_at=datetime(2026, 8, 31, 0, 0), ends_at=ende,
            source_id=quelle(session).id,
        ))
    session.flush()
    return zone


def alle_api_routen(app: FastAPI) -> list[APIRoute]:
    """Alle Routen der Anwendung, auch die aus eingebundenen Routern.

    Seit FastAPI 0.141 legt `include_router()` keine flache Liste mehr an: Statt der
    einzelnen Routen steht ein `_IncludedRouter` in `app.routes`, der den urspruenglichen
    Router unter `original_router` traegt. `app.routes` allein lieferte damit nur noch
    `/healthz` und die von FastAPI selbst erzeugten Seiten — die Waechter in
    `test_endpunktabdeckung.py` und `test_csrf.py` liefen ins Leere, ohne rot zu werden.

    Deshalb hier einmal zentral, mit Rekursion ueber `original_router`.
    """
    gefunden: list[APIRoute] = []

    def _durchgehen(routen: Sequence[BaseRoute]) -> None:
        for route in routen:
            eingebundener = getattr(route, "original_router", None)
            if eingebundener is not None:
                _durchgehen(eingebundener.routes)
            elif isinstance(route, APIRoute):
                gefunden.append(route)

    _durchgehen(app.routes)
    return gefunden


def messwert_anlegen(
    session: Session, geraet: Device, faehigkeit_id: int, *, wert: Decimal
) -> Measurement:
    zeitpunkt = datetime(2026, 8, 29, 8, 0)
    messwert = Measurement(
        device_id=geraet.id,
        capability_id=faehigkeit_id,
        value_numeric=wert,
        measured_at=zeitpunkt,
        received_at=zeitpunkt,
    )
    session.add(messwert)
    session.flush()
    return messwert


def geraetezustand_anlegen(session: Session, geraet: Device) -> DeviceHealth:
    zustand = DeviceHealth(
        device_id=geraet.id,
        last_payload_at=datetime(2026, 8, 29, 8, 0),
        payload_count=1,
    )
    session.add(zustand)
    session.flush()
    return zustand


def sensorstatus(session: Session, code: str = "ok") -> SensorStatus:
    status = session.query(SensorStatus).filter_by(code=code).one_or_none()
    if status is None:
        status = SensorStatus(code=code, label=code)
        session.add(status)
        session.flush()
    return status


def zonenzustand_anlegen(session: Session, zone: Zone) -> ZoneState:
    zustand = ZoneState(
        zone_id=zone.id,
        sensor_status_id=sensorstatus(session).id,
        updated_at=datetime(2026, 8, 29, 8, 0),
    )
    session.add(zustand)
    session.flush()
    return zustand


def schattenentscheidung_anlegen(session: Session, zone: Zone) -> ShadowDecision:
    entscheidung = ShadowDecision(
        decided_at=datetime(2026, 8, 29, 8, 0),
        zone_id=zone.id,
        setpoint_reason="Zeitplan",
        would_heat=False,
        outcome_code="aus",
        reason="Sollwert ist erreicht.",
    )
    session.add(entscheidung)
    session.flush()
    return entscheidung


def passkey_anlegen(
    session: Session, nutzer: User, credential_id: str = "kennung", sign_count: int = 0
) -> UserPasskey:
    """Ein hinterlegter Passkey. Der oeffentliche Schluessel ist hier ein Platzhalter —
    Tests, die wirklich pruefen, erzeugen ihn mit einem Software-Authenticator."""
    eintrag = UserPasskey(
        user_id=nutzer.id,
        credential_id=credential_id,
        public_key="platzhalter",
        sign_count=sign_count,
        bezeichnung=f"Passkey {credential_id}",
    )
    session.add(eintrag)
    session.flush()
    return eintrag
