from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from thermoctl import audit
from thermoctl.db.models.operations import Setting
from thermoctl.db.models.override import ZoneOverride
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.zone import SetpointMode, Zone, ZoneSetpoint

# Die eine Sollwertgrenze des Projekts. Sie gilt fuer Modus-Sollwerte wie fuer
# Uebersteuerungen und fuer alle vier Adapter -- Oberflaeche, REST, MCP und die
# Home-Assistant-Karte lesen sie von hier.
#
# Die Untergrenze lag bis hierher bei 5 Grad, dann kurz bei 1. Sie liegt jetzt bei
# **minus 20**: Ein Sollwert im Minusbereich heisst "hier wird nicht geheizt, und zwar
# wirklich nicht" -- fuer eine Garage, einen Schuppen oder einen Raum, den man nur
# ueberwachen und nicht temperieren will. Mit einem Sollwert von 1 Grad heizt die Anlage
# immer noch, sobald es kaelter wird; das ist etwas anderes.
#
# Minus 20 und nicht beliebig tief: Darunter liegt kein Wunsch mehr, sondern ein
# Tippfehler oder eine kaputte Nutzlast, und die soll weiter auffallen. Es ist zugleich
# der Bereich, den uebliche Zigbee-Sensoren melden.
#
# **Das ist eine Grenze der Eingabe, keine der Physik.** Wer einen Sollwert unter etwa
# 4 Grad setzt, nimmt einfrierende Leitungen in Kauf; die Software haelt ihn davon nicht
# mehr ab. Der Frostschutz bleibt ein eigener Modus und greift weiter bei ausgefallenem
# Sensor und Betriebsart "Aus".
MINDESTTEMPERATUR_C = Decimal("-20.0")
HOECHSTTEMPERATUR_C = Decimal("35.0")


# Bewusst NICHT `frozen=True`: Python haengt einer Ausnahme beim Werfen ihren
# Traceback an, und eine eingefrorene Dataclass verweigert genau das. Der Fehler
# faellt erst auf, wenn die Ausnahme tief genug durchgereicht wird — bei uns durch
# die Abhaengigkeitsaufloesung von FastAPI — und aeussert sich dann als
# `FrozenInstanceError` statt als der Fehler, den man sucht.
@dataclass
class Domaenenfehler(Exception):
    feld: str
    meldung: str


def _moduswerte_pruefen(
    session: Session, *, code: str, name: str, sort_order: int, modus_id: int | None = None
) -> tuple[str, str, int]:
    code = code.strip()
    name = name.strip()
    if not code:
        raise Domaenenfehler("code", "Der technische Code darf nicht leer sein.")
    if len(code) > 32:
        raise Domaenenfehler("code", "Der technische Code darf höchstens 32 Zeichen haben.")
    if not name:
        raise Domaenenfehler("name", "Der Name darf nicht leer sein.")
    if len(name) > 64:
        raise Domaenenfehler("name", "Der Name darf höchstens 64 Zeichen haben.")
    vorhandene_id = session.scalar(select(SetpointMode.id).where(SetpointMode.code == code))
    if vorhandene_id is not None and vorhandene_id != modus_id:
        raise Domaenenfehler("code", "Dieser technische Code ist bereits vergeben.")
    return code, name, sort_order


def modus_anlegen(
    session: Session,
    *,
    code: str,
    name: str,
    sort_order: int,
    user_id: int,
    quelle: str = "web",
) -> SetpointMode:
    code, name, sort_order = _moduswerte_pruefen(
        session, code=code, name=name, sort_order=sort_order
    )
    modus = SetpointMode(code=code, name=name, sort_order=sort_order)
    session.add(modus)
    session.flush()
    audit.record(
        session,
        source=quelle,
        action="create",
        object_type="setpoint_mode",
        object_id=str(modus.id),
        summary=f"Sollwert-Modus '{modus.name}' angelegt",
        user_id=user_id,
    )
    return modus


def modus_aendern(
    session: Session,
    modus: SetpointMode,
    *,
    code: str,
    name: str,
    sort_order: int,
    user_id: int,
    quelle: str = "web",
) -> None:
    code, name, sort_order = _moduswerte_pruefen(
        session, code=code, name=name, sort_order=sort_order, modus_id=modus.id
    )
    modus.code = code
    modus.name = name
    modus.sort_order = sort_order
    audit.record(
        session,
        source=quelle,
        action="update",
        object_type="setpoint_mode",
        object_id=str(modus.id),
        summary=f"Sollwert-Modus '{modus.name}' geändert",
        user_id=user_id,
    )


def loeschsperre(session: Session, modus: SetpointMode) -> str | None:
    """Warum dieser Modus nicht geloescht werden darf — oder None, wenn er darf.

    Die Reihenfolge ist Absicht: **Der Frostschutz wird zuerst geprueft.** Der
    Einrichtungsassistent legt ihn mit `is_builtin=True` an, also trifft die allgemeine
    Sperre ebenfalls zu — und wuerde sie zuerst greifen, bekaeme in jeder echten Anlage
    genau der wichtigste Modus die nichtssagende Meldung 'die Anwendung braucht ihn'
    statt der Begruendung, die zaehlt: Er ist die Rueckfallebene bei Sensorausfall.
    """
    einstellungen = session.get(Setting, 1)
    if einstellungen is not None and einstellungen.frost_protection_mode_id == modus.id:
        return (
            "Der Frostschutzmodus kann nicht gelöscht werden — er ist die Rückfallebene, "
            "wenn ein Sensor ausfällt."
        )
    if modus.is_builtin:
        return "Eingebaute Modi können nicht gelöscht werden, weil die Anwendung sie benötigt."
    verwendungen = sum(
        session.scalar(select(func.count()).select_from(modell).where(spalte == modus.id)) or 0
        for modell, spalte in (
            (SchedulePoint, SchedulePoint.setpoint_mode_id),
            (ZoneOverride, ZoneOverride.setpoint_mode_id),
        )
    )
    if verwendungen:
        return (
            "Dieser Modus kann nicht gelöscht werden, weil Zeitpläne oder historische "
            "Übersteuerungen ihn noch verwenden."
        )
    return None


def modus_loeschen(
    session: Session, modus: SetpointMode, *, user_id: int, quelle: str = "web"
) -> None:
    sperre = loeschsperre(session, modus)
    if sperre is not None:
        raise Domaenenfehler("modus", sperre)
    # Sollwerte besitzen ohne ihren Modus keine Bedeutung. Sie werden hier bewusst in
    # derselben Transaktion entfernt; Zeitpläne und Historie verhindern dagegen oben
    # das Löschen, weil ihre Aussage erhalten bleiben muss.
    session.execute(delete(ZoneSetpoint).where(ZoneSetpoint.setpoint_mode_id == modus.id))
    modus_id = modus.id
    modus_name = modus.name
    session.delete(modus)
    audit.record(
        session,
        source=quelle,
        action="delete",
        object_type="setpoint_mode",
        object_id=str(modus_id),
        summary=f"Sollwert-Modus '{modus_name}' gelöscht",
        user_id=user_id,
    )


def _grad(wert: Decimal) -> str:
    """`1,0` -- mit Komma, weil die Meldung dem Benutzer angezeigt wird."""
    return f"{wert:.1f}".replace(".", ",")


def temperatur_pruefen(temperatur: Decimal) -> Decimal:
    if not temperatur.is_finite():
        raise Domaenenfehler("temperatur", "Der Sollwert muss eine endliche Zahl sein.")
    if temperatur < MINDESTTEMPERATUR_C or temperatur > HOECHSTTEMPERATUR_C:
        # Aus den Konstanten gebaut, nicht abgeschrieben: Eine Meldung, die die Grenze
        # noch einmal nennt, weicht beim naechsten Verschieben von ihr ab.
        raise Domaenenfehler(
            "temperatur",
            f"Der Sollwert muss zwischen {_grad(MINDESTTEMPERATUR_C)} und "
            f"{_grad(HOECHSTTEMPERATUR_C)} °C liegen.",
        )
    exponent = temperatur.as_tuple().exponent
    if isinstance(exponent, int) and exponent < -1:
        raise Domaenenfehler(
            "temperatur", "Der Sollwert darf höchstens eine Nachkommastelle haben."
        )
    return temperatur.quantize(Decimal("0.1"))


def sollwerte_aendern(
    session: Session,
    zone: Zone,
    werte: dict[int, Decimal | None],
    *,
    # `None` heisst: niemand ist angemeldet. Das trifft auf einen Befehl aus Home
    # Assistant zu, hinter dem kein Konto steht. Frueher stand hier `int`, und die
    # Adapter reichten `principal.user_id or 0` durch -- eine Kennung, die es nicht
    # gibt und an der MariaDB den Fremdschluessel des Audit-Eintrags verweigert.
    user_id: int | None,
    quelle: str = "web",
) -> None:
    # Erst alle Werte pruefen, dann irgendeine Zeile anfassen. Die Ansicht faengt den
    # Domaenenfehler und zeigt das Formular erneut; ohne diese Reihenfolge wuerde eine
    # fruehere gueltige Eingabe trotz eines spaeteren Fehlers gespeichert.
    gepruefte_werte = {
        modus_id: temperatur_pruefen(temperatur) if temperatur is not None else None
        for modus_id, temperatur in werte.items()
    }
    vorhandene = {
        zeile.setpoint_mode_id: zeile
        for zeile in session.scalars(
            select(ZoneSetpoint).where(ZoneSetpoint.zone_id == zone.id)
        )
    }
    geaendert = False
    for modus_id, temperatur in gepruefte_werte.items():
        zeile = vorhandene.get(modus_id)
        if temperatur is None:
            if zeile is not None:
                session.delete(zeile)
                geaendert = True
            continue
        temperatur = temperatur_pruefen(temperatur)
        if zeile is None:
            session.add(
                ZoneSetpoint(
                    zone_id=zone.id, setpoint_mode_id=modus_id, temperature_c=temperatur
                )
            )
            geaendert = True
        elif zeile.temperature_c != temperatur:
            zeile.temperature_c = temperatur
            geaendert = True
    if geaendert:
        audit.record(
            session,
            source=quelle,
            action="update",
            object_type="zone_setpoint",
            object_id=str(zone.id),
            summary=f"Sollwerte für Zone '{zone.display_name}' geändert",
            user_id=user_id,
        )
