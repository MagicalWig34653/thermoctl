"""Bediengeraete: was ein Tastendruck an der Wand bewirkt.

Ein Bediengeraet -- etwa ein Aqara W100 -- schickt seine Tastendruecke als Feld `action`
in der Zustandsnachricht. Welche Werte darin stehen, entscheidet Zigbee2MQTT je Modell:
der eine schickt `single_plus`, der naechste `button_1_single`, der uebernaechste
`up_open`. Eine Tabelle dieser Namen im Quelltext waere genau die harte Verdrahtung,
gegen die dieses Projekt gebaut ist -- und fuer jedes Geraet falsch, das noch nicht darin
steht.

**Deshalb wird nicht geraten, sondern zugehoert.** Jeder Tastendruck landet wie jeder
andere Messwert in `measurement`. Die Oberflaeche zeigt, welche Aktionen ein Geraet
tatsaechlich geschickt hat, und laesst sie belegen. Wer ein neues Modell anschliesst,
drueckt einmal jede Taste und ordnet zu, was er sieht; ein Datenblatt braucht dafuer
niemand.

**Was eine Taste ausloesen darf, ist bewusst klein gehalten:** waermer, kaelter, die
naechste Schaltung vorziehen, aus, automatisch. Alles Weitere gehoert an eine Stelle, an
der man sieht, was man tut -- nicht auf einen Knopf, den jemand im Vorbeigehen drueckt.

**Waermer und kaelter verstellen den Modus, der gerade gilt** -- dasselbe wie der
Thermostat in Home Assistant und auf der Startseite. Eine Uebersteuerung waere nach dem
naechsten Schaltpunkt weg, und der Raum kuehlte ohne Zutun wieder aus; wer an der Wand
+1 Grad drueckt, meint nicht "fuer die naechste halbe Stunde".
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.db.models.device import ControllerBinding, Device, ZoneDevice
from thermoctl.db.models.lookup import ControllerCommand, DeviceCapability, DeviceRole
from thermoctl.db.models.messwert import Measurement
from thermoctl.db.models.zone import Zone
from thermoctl.domain.fernbedienung import boost, sollwert_setzen
from thermoctl.domain.schedule import aufgeloester_sollwert
from thermoctl.domain.zonen import betriebsart_setzen

log = logging.getLogger(__name__)

# Wie weit ein Druck auf "waermer" verstellt, wenn die Belegung nichts anderes sagt.
# Ein halbes Grad ist die Stufe, die auch der Thermostat auf der Startseite nimmt.
STANDARDSCHRITT_K = Decimal("0.5")

# Wie viele der zuletzt gesehenen Tastendruecke die Oberflaeche zur Auswahl stellt.
# Genug, um bei einem Geraet mit drei Tasten alle Varianten (einfach, doppelt, halten)
# einmal gesehen zu haben, ohne dass die Liste unuebersichtlich wird.
GESEHENE_AKTIONEN = 20


class Bediengeraetefehler(ValueError):
    """Die Belegung ist unbrauchbar -- unbekannter Befehl, unbekanntes Geraet."""


@dataclass(frozen=True)
class Belegung:
    """Eine Taste und was sie tut."""

    aktion: str
    befehl_code: str | None
    befehl_name: str | None
    schritt_k: Decimal | None
    zuletzt_gesehen: datetime | None


def gesehene_aktionen(session: Session, geraet: Device) -> list[Belegung]:
    """Welche Tasten dieses Geraet geschickt hat -- belegte und noch unbelegte.

    Die Grundlage der ganzen Einrichtung: Ohne sie muesste jemand wissen, wie sein Modell
    seine Tasten nennt. Belegte Aktionen stehen auch dann da, wenn sie seit dem letzten
    Aufraeumen der Messwerte nicht mehr gesehen wurden -- sonst verschwaende eine
    funktionierende Belegung aus der Oberflaeche, nur weil die Taste eine Weile nicht
    gedrueckt wurde.
    """
    faehigkeit_id = session.scalar(
        select(DeviceCapability.id).where(DeviceCapability.code == "action")
    )
    zuletzt: dict[str, datetime] = {}
    if faehigkeit_id is not None:
        for text, gemessen_am in session.execute(
            select(Measurement.value_text, Measurement.measured_at)
            .where(
                Measurement.device_id == geraet.id,
                Measurement.capability_id == faehigkeit_id,
                Measurement.value_text.is_not(None),
            )
            .order_by(Measurement.measured_at.desc(), Measurement.id.desc())
            .limit(GESEHENE_AKTIONEN)
        ):
            if text is not None:
                zuletzt.setdefault(text, gemessen_am)

    belegt = {
        aktion: (code, name, schritt)
        for aktion, code, name, schritt in session.execute(
            select(
                ControllerBinding.action_code,
                ControllerCommand.code,
                ControllerCommand.label,
                ControllerBinding.step_k,
            )
            .join(ControllerCommand, ControllerCommand.id == ControllerBinding.command_id)
            .where(ControllerBinding.device_id == geraet.id)
        )
    }
    alle = sorted(set(zuletzt) | set(belegt))
    return [
        Belegung(
            aktion=aktion,
            befehl_code=belegt.get(aktion, (None, None, None))[0],
            befehl_name=belegt.get(aktion, (None, None, None))[1],
            schritt_k=belegt.get(aktion, (None, None, None))[2],
            zuletzt_gesehen=zuletzt.get(aktion),
        )
        for aktion in alle
    ]


def belegung_setzen(
    session: Session,
    geraet: Device,
    aktion: str,
    befehl_code: str | None,
    schritt_k: Decimal | None = None,
) -> None:
    """Belegt eine Taste -- oder loescht die Belegung, wenn `befehl_code` None ist."""
    vorhanden = session.scalars(
        select(ControllerBinding).where(
            ControllerBinding.device_id == geraet.id,
            ControllerBinding.action_code == aktion,
        )
    ).first()
    if befehl_code is None:
        if vorhanden is not None:
            session.delete(vorhanden)
            session.flush()
        return

    befehl = session.scalars(
        select(ControllerCommand).where(ControllerCommand.code == befehl_code)
    ).first()
    if befehl is None:
        raise Bediengeraetefehler(f"Den Befehl '{befehl_code}' gibt es nicht.")
    if schritt_k is not None and schritt_k <= 0:
        raise Bediengeraetefehler("Die Schrittweite muss groesser als null sein.")
    if schritt_k is not None:
        exponent = schritt_k.as_tuple().exponent
        if isinstance(exponent, int) and exponent < -1:
            # Dieselbe Grenze wie beim Sollwert selbst. Ein Viertelgrad Schrittweite
            # waere eine Zusage, die beim ersten Druck gebrochen wuerde -- die Domaene
            # weist einen Sollwert mit zwei Nachkommastellen ab.
            raise Bediengeraetefehler(
                "Die Schrittweite darf höchstens eine Nachkommastelle haben."
            )
    if vorhanden is None:
        session.add(
            ControllerBinding(
                device_id=geraet.id,
                action_code=aktion,
                command_id=befehl.id,
                step_k=schritt_k,
            )
        )
    else:
        vorhanden.command_id = befehl.id
        vorhanden.step_k = schritt_k
    session.flush()


def _zonen(session: Session, geraet: Device) -> list[Zone]:
    """Die Zonen, in denen dieses Geraet als Bediengeraet haengt."""
    return list(
        session.scalars(
            select(Zone)
            .join(ZoneDevice, ZoneDevice.zone_id == Zone.id)
            .join(DeviceRole, DeviceRole.id == ZoneDevice.device_role_id)
            .where(ZoneDevice.device_id == geraet.id, DeviceRole.code == "controller")
            .order_by(Zone.id)
        )
    )


def aktion_ausfuehren(
    session: Session, geraet: Device, aktion: str, jetzt: datetime, *, quelle: str = "system"
) -> list[str]:
    """Fuehrt aus, was fuer diese Taste belegt ist. Gibt die betroffenen Zonen zurueck.

    Eine unbelegte Taste tut nichts und ist kein Fehler: Die meisten Geraete schicken
    mehr Aktionen, als jemand belegen will -- jedes Halten und jedes Loslassen. Eine
    Warnung je Druck waere Laerm.
    """
    belegung = session.scalars(
        select(ControllerBinding).where(
            ControllerBinding.device_id == geraet.id,
            ControllerBinding.action_code == aktion,
        )
    ).first()
    if belegung is None:
        return []
    befehl = session.get(ControllerCommand, belegung.command_id)
    if befehl is None:  # pragma: no cover - Fremdschluessel haelt dagegen
        raise Bediengeraetefehler("Die Belegung zeigt auf einen Befehl, den es nicht gibt.")

    zonen = _zonen(session, geraet)
    if not zonen:
        # Ein Bediengeraet ohne Zone ist keine Stoerung, sondern eine unfertige
        # Einrichtung -- und der haeufigste Grund, warum "die Taste tut nichts".
        log.info(
            "Tastendruck ohne Zone verworfen",
            extra={"geraet": geraet.display_name, "aktion": aktion},
        )
        return []

    for zone in zonen:
        _auf_zone_anwenden(session, zone, befehl.code, belegung.step_k, jetzt, quelle)
        log.info(
            "Tastendruck ausgefuehrt",
            extra={
                "geraet": geraet.display_name,
                "aktion": aktion,
                "befehl": befehl.code,
                "zone_id": zone.id,
            },
        )
    return [zone.name for zone in zonen]


def _auf_zone_anwenden(
    session: Session,
    zone: Zone,
    befehl_code: str,
    schritt_k: Decimal | None,
    jetzt: datetime,
    quelle: str,
) -> None:
    if befehl_code in ("setpoint_up", "setpoint_down"):
        schritt = schritt_k or STANDARDSCHRITT_K
        jetziger = aufgeloester_sollwert(session, zone, jetzt).temperature_c
        neu = jetziger + (schritt if befehl_code == "setpoint_up" else -schritt)
        sollwert_setzen(session, zone, neu, jetzt, quelle=quelle)
    elif befehl_code == "boost":
        boost(session, zone, jetzt, quelle=quelle)
    elif befehl_code == "mode_off":
        betriebsart_setzen(session, zone, "off", akteur_id=None, quelle=quelle)
    elif befehl_code == "mode_auto":
        betriebsart_setzen(session, zone, "auto", akteur_id=None, quelle=quelle)
    else:  # pragma: no cover - die Nachschlagetabelle kennt nur die fuenf oben
        raise Bediengeraetefehler(f"Unbekannter Befehl: {befehl_code}")
