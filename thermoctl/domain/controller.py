"""Controllers: what a button press on the wall does.

A controller -- an Aqara W100, say -- sends its button presses as an `action` field in
the state message. Which values appear there is decided by Zigbee2MQTT per model: one
sends `single_plus`, the next `button_1_single`, the one after that `up_open`. A table
of these names in the source code would be exactly the hardcoding this project is built
against -- and wrong for every device not yet listed in it.

**That is why nothing is guessed, only listened for.** Every button press ends up in
`measurement` like any other reading. The interface shows which actions a device has
actually sent, and lets them be bound to commands. Whoever connects a new model presses
every button once and maps what they see; nobody needs a datasheet for that.

**What a button is allowed to trigger is deliberately kept small:** warmer, colder,
bring the next schedule point forward, off, automatic. Everything else belongs at a
place where you can see what you are doing -- not on a button someone presses in
passing.

**Warmer and colder adjust the mode that currently applies** -- the same as the
thermostat in Home Assistant and on the start page. An override would be gone after the
next schedule point, and the room would cool back down on its own; someone pressing +1
degree on the wall does not mean "for the next half hour".
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

# How far a press on "warmer" adjusts things when the binding says nothing else.
# Half a degree is the step the thermostat on the start page also uses.
DEFAULT_STEP_K = Decimal("0.5")

# How many of the most recently seen button presses the interface offers for selection.
# Enough to have seen every variant (single, double, hold) once on a three-button
# device, without the list becoming cluttered.
SEEN_ACTIONS = 20


class ControllerError(ValueError):
    """The binding is unusable -- unknown command, unknown device."""


@dataclass(frozen=True)
class Binding:
    """A button and what it does."""

    aktion: str
    command_code: str | None
    command_name: str | None
    step_k: Decimal | None
    last_seen: datetime | None


def gesehene_aktionen(session: Session, device: Device) -> list[Binding]:
    """Which buttons this device has sent -- both bound and still unbound ones.

    The foundation of the whole setup: without it, someone would have to know what
    their model calls its buttons. Bound actions still show up even if they have not
    been seen since the last cleanup of readings -- otherwise a working binding would
    vanish from the interface just because the button was not pressed for a while.
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
    """Binds a button -- or deletes the binding if `befehl_code` is None."""
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
            # The same bound as on the setpoint itself. A quarter-degree step would be
            # a promise broken on the first press -- the domain rejects a setpoint
            # with two decimal places.
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
    """The zones in which this device hangs as a controller."""
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
    """Executes whatever is bound to this button. Returns the zones affected.

    An unbound button does nothing and is not an error: most devices send more actions
    than anyone wants to bind -- every hold and every release. A warning on every press
    would be noise.
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
    if command is None:  # pragma: no cover - foreign key prevents this
        raise ControllerError("Die Belegung zeigt auf einen Befehl, den es nicht gibt.")

    zones = _zone(session, device)
    if not zones:
        # A controller without a zone is not a fault but an unfinished setup -- and the
        # most common reason why "the button does nothing".
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
    else:  # pragma: no cover - the lookup table only knows the five above
        raise ControllerError(f"Unbekannter Befehl: {command_code}")
