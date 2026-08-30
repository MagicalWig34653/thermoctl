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
from thermoctl.db.models.measurement import Measurement
from thermoctl.db.models.zone import Zone
from thermoctl.domain.remote_control import boost, set_setpoint
from thermoctl.domain.schedule import resolved_setpoint
from thermoctl.domain.zones import set_operating_mode

log = logging.getLogger(__name__)

# Wie weit ein Druck auf "waermer" verstellt, wenn die Belegung nichts anderes sagt.
# Ein halbes Grad ist die Stufe, die auch der Thermostat auf der Startseite nimmt.
DEFAULT_STEP_K = Decimal("0.5")

# Wie viele der zuletzt gesehenen Tastendruecke die Oberflaeche zur Auswahl stellt.
# Genug, um bei einem Geraet mit drei Tasten alle Varianten (einfach, doppelt, halten)
# einmal gesehen zu haben, ohne dass die Liste unuebersichtlich wird.
SEEN_ACTIONS = 20


class ControllerError(ValueError):
    """Die Belegung ist unbrauchbar -- unbekannter Befehl, unbekanntes Geraet."""


@dataclass(frozen=True)
class Binding:
    """Eine Taste und was sie tut."""

    aktion: str
    command_code: str | None
    command_name: str | None
    step_k: Decimal | None
    last_seen: datetime | None


def gesehene_aktionen(session: Session, device: Device) -> list[Binding]:
    """Welche Tasten dieses Geraet geschickt hat -- belegte und noch unbelegte.

    Die Grundlage der ganzen Einrichtung: Ohne sie muesste jemand wissen, wie sein Modell
    seine Tasten nennt. Belegte Aktionen stehen auch dann da, wenn sie seit dem letzten
    Aufraeumen der Messwerte nicht mehr gesehen wurden -- sonst verschwaende eine
    funktionierende Belegung aus der Oberflaeche, nur weil die Taste eine Weile nicht
    gedrueckt wurde.
    """
    capability_id = session.scalar(
        select(DeviceCapability.id).where(DeviceCapability.code == "action")
    )
    last_seen: dict[str, datetime] = {}
    if capability_id is not None:
        for text, gemessen_am in session.execute(
            select(Measurement.value_text, Measurement.measured_at)
            .where(
                Measurement.device_id == device.id,
                Measurement.capability_id == capability_id,
                Measurement.value_text.is_not(None),
            )
            .order_by(Measurement.measured_at.desc(), Measurement.id.desc())
            .limit(SEEN_ACTIONS)
        ):
            if text is not None:
                last_seen.setdefault(text, gemessen_am)

    belegt = {
        aktion: (code, name, step)
        for aktion, code, name, step in session.execute(
            select(
                ControllerBinding.action_code,
                ControllerCommand.code,
                ControllerCommand.label,
                ControllerBinding.step_k,
            )
            .join(ControllerCommand, ControllerCommand.id == ControllerBinding.command_id)
            .where(ControllerBinding.device_id == device.id)
        )
    }
    alle = sorted(set(last_seen) | set(belegt))
    return [
        Binding(
            aktion=aktion,
            command_code=belegt.get(aktion, (None, None, None))[0],
            command_name=belegt.get(aktion, (None, None, None))[1],
            step_k=belegt.get(aktion, (None, None, None))[2],
            last_seen=last_seen.get(aktion),
        )
        for aktion in alle
    ]


def set_binding(
    session: Session,
    device: Device,
    aktion: str,
    command_code: str | None,
    step_k: Decimal | None = None,
) -> None:
    """Belegt eine Taste -- oder loescht die Belegung, wenn `befehl_code` None ist."""
    vorhanden = session.scalars(
        select(ControllerBinding).where(
            ControllerBinding.device_id == device.id,
            ControllerBinding.action_code == aktion,
        )
    ).first()
    if command_code is None:
        if vorhanden is not None:
            session.delete(vorhanden)
            session.flush()
        return

    command = session.scalars(
        select(ControllerCommand).where(ControllerCommand.code == command_code)
    ).first()
    if command is None:
        raise ControllerError(f"Den Befehl '{command_code}' gibt es nicht.")
    if step_k is not None and step_k <= 0:
        raise ControllerError("Die Schrittweite muss groesser als null sein.")
    if step_k is not None:
        exponent = step_k.as_tuple().exponent
        if isinstance(exponent, int) and exponent < -1:
            # Dieselbe Grenze wie beim Sollwert selbst. Ein Viertelgrad Schrittweite
            # waere eine Zusage, die beim ersten Druck gebrochen wuerde -- die Domaene
            # weist einen Sollwert mit zwei Nachkommastellen ab.
            raise ControllerError(
                "Die Schrittweite darf höchstens eine Nachkommastelle haben."
            )
    if vorhanden is None:
        session.add(
            ControllerBinding(
                device_id=device.id,
                action_code=aktion,
                command_id=command.id,
                step_k=step_k,
            )
        )
    else:
        vorhanden.command_id = command.id
        vorhanden.step_k = step_k
    session.flush()


def _zone(session: Session, device: Device) -> list[Zone]:
    """Die Zonen, in denen dieses Geraet als Bediengeraet haengt."""
    return list(
        session.scalars(
            select(Zone)
            .join(ZoneDevice, ZoneDevice.zone_id == Zone.id)
            .join(DeviceRole, DeviceRole.id == ZoneDevice.device_role_id)
            .where(ZoneDevice.device_id == device.id, DeviceRole.code == "controller")
            .order_by(Zone.id)
        )
    )


def execute_aktion(
    session: Session, device: Device, aktion: str, now: datetime, *, source: str = "system"
) -> list[str]:
    """Fuehrt aus, was fuer diese Taste belegt ist. Gibt die betroffenen Zonen zurueck.

    Eine unbelegte Taste tut nichts und ist kein Fehler: Die meisten Geraete schicken
    mehr Aktionen, als jemand belegen will -- jedes Halten und jedes Loslassen. Eine
    Warnung je Druck waere Laerm.
    """
    binding = session.scalars(
        select(ControllerBinding).where(
            ControllerBinding.device_id == device.id,
            ControllerBinding.action_code == aktion,
        )
    ).first()
    if binding is None:
        return []
    command = session.get(ControllerCommand, binding.command_id)
    if command is None:  # pragma: no cover - Fremdschluessel haelt dagegen
        raise ControllerError("Die Belegung zeigt auf einen Befehl, den es nicht gibt.")

    zones = _zone(session, device)
    if not zones:
        # Ein Bediengeraet ohne Zone ist keine Stoerung, sondern eine unfertige
        # Einrichtung -- und der haeufigste Grund, warum "die Taste tut nichts".
        log.info(
            "Tastendruck ohne Zone verworfen",
            extra={"geraet": device.display_name, "aktion": aktion},
        )
        return []

    for zone in zones:
        _auf_zone_anwenden(session, zone, command.code, binding.step_k, now, source)
        log.info(
            "Tastendruck ausgefuehrt",
            extra={
                "geraet": device.display_name,
                "aktion": aktion,
                "befehl": command.code,
                "zone_id": zone.id,
            },
        )
    return [zone.name for zone in zones]


def _auf_zone_anwenden(
    session: Session,
    zone: Zone,
    command_code: str,
    step_k: Decimal | None,
    now: datetime,
    source: str,
) -> None:
    if command_code in ("setpoint_up", "setpoint_down"):
        step = step_k or DEFAULT_STEP_K
        jetziger = resolved_setpoint(session, zone, now).temperature_c
        neu = jetziger + (step if command_code == "setpoint_up" else -step)
        set_setpoint(session, zone, neu, now, source=source)
    elif command_code == "boost":
        boost(session, zone, now, source=source)
    elif command_code == "mode_off":
        set_operating_mode(session, zone, "off", akteur_id=None, source=source)
    elif command_code == "mode_auto":
        set_operating_mode(session, zone, "auto", akteur_id=None, source=source)
    else:  # pragma: no cover - die Nachschlagetabelle kennt nur die fuenf oben
        raise ControllerError(f"Unbekannter Befehl: {command_code}")
