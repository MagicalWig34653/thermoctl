"""The one impure lookup `domain.temperature_source_health` needs but must not do.

That module is a reine Funktion by design (no clock, no database, no network --
see its module docstring) and is handed the moment of the last successful
external-temperature write as plain input. This module is where that moment
actually comes from: a query over `device_command`, the same table
`services/device_commands.py` reads for the audit log and `services/
publishing.py` writes to on every attempt.
"""

import json
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.db.models.device import Device, DeviceCapabilityLink, ZoneDevice
from thermoctl.db.models.lookup import CommandOutcome, DeviceCapability, DeviceRole
from thermoctl.db.models.measurement import Measurement
from thermoctl.db.models.state import DeviceCommand
from thermoctl.db.models.zone import Zone
from thermoctl.domain.self_regulating import TEMPERATURE_PROPERTIES
from thermoctl.domain.temperature_source_health import ThermostatCandidate

# Only a command that actually reached the device counts as a write that could
# have produced an echo. A dry-run attempt (`suppressed`) never left the
# service, and a `failed` attempt is not known to have arrived either -- both
# would understate how long the device has been echoing, never overstate it,
# so treating them as "no write" is the conservative direction (Grundsatz 7).
_EXECUTED = "executed"

# `record_command` in `services/publishing.py::_send_self_regulating_valves`
# always writes under this literal command name for a self-regulating valve's
# setpoint/temperature message -- the only place any of `TEMPERATURE_PROPERTIES`
# is ever written. Matching on it narrows the scan to exactly that write path
# instead of parsing the payload of every command in the log.
_COMMAND_NAME = "setpoint"


def last_external_temperature_write_at(session: Session, device_id: int) -> datetime | None:
    """When thermoctl last *successfully* wrote an external temperature to this device.

    `None` means either this device has never received such a write, or the
    payload of every candidate row failed to parse as the JSON object it is
    always written as -- both are treated identically by the caller: no
    confirmed write means no echo to wait out, so this device's own reading
    can be trusted immediately (subject to the usual staleness check). This
    is the conservative direction (Grundsatz 7): understating how recently a
    write happened can only delay treating a device as a replacement, never
    make one look independent it is not.

    The candidate rows are already narrowed by device and command name in the
    query; only the JSON parse below can still fail, and only for a row this
    code did not itself write (a manually edited row, or a future command
    shape) -- reachable only through data this function does not control,
    not through any path this codebase takes today.
    """
    rows = session.scalars(
        select(DeviceCommand)
        .join(CommandOutcome, CommandOutcome.id == DeviceCommand.outcome_id)
        .where(
            DeviceCommand.device_id == device_id,
            DeviceCommand.command == _COMMAND_NAME,
            CommandOutcome.code == _EXECUTED,
        )
        .order_by(DeviceCommand.sent_at.desc(), DeviceCommand.id.desc())
    )
    for row in rows:
        try:
            payload = json.loads(row.payload)
        except (TypeError, ValueError):  # pragma: no cover -- see docstring above
            continue
        if isinstance(payload, dict) and any(name in payload for name in TEMPERATURE_PROPERTIES):
            return row.sent_at
    return None


def zone_candidates(session: Session, zone: Zone, now: datetime) -> list[ThermostatCandidate]:
    """Every thermostat-actuator assignment of `zone` as a replacement candidate.

    **Same eligibility rule as `domain.sensor_failure_policy._assignment`**:
    role `actuator`, capability `thermostat` present, capability `switch`
    absent -- deliberately kept in lockstep with that check rather than
    re-derived, so a device that is not a valid backup-offset target there
    cannot quietly become a replacement-source candidate here. A plain
    switching actuator (no `thermostat` capability at all) never reaches this
    query in the first place; there is no confirmed mixed-capability device in
    the plant today (`lokal/plaene/0.11.0-geraetevertrag.md` (d)), but the
    exclusion costs nothing and guards against ever guessing for one.

    Each candidate's `local_temperature_c`/`measured_at` is that device's own
    most recent `temperature`-capability `Measurement` -- the same one
    `services/ingest.py` already stores for every device regardless of
    whether it is the zone's selected source (plan 1.3, last paragraph: no
    change to ingest needed for this). `last_external_write_at` comes from
    `last_external_temperature_write_at` above.
    """
    def _capability(code: str) -> DeviceCapability | None:
        return session.scalar(select(DeviceCapability).where(DeviceCapability.code == code))

    actuator = session.scalar(select(DeviceRole).where(DeviceRole.code == "actuator"))
    thermostat = _capability("thermostat")
    switch = _capability("switch")
    # Unlike `actuator`/`thermostat`, a missing `temperature` capability row is
    # not "nothing can ever qualify" -- it only means no `Measurement` could
    # possibly exist for it yet (the capability is created lazily the first
    # measurement ever arrives, see `services/ingest.py`). Falling back to `[]`
    # here would wrongly report an eligible, freshly-assigned thermostat as
    # "no candidates at all" instead of "a candidate with no reading yet".
    temperature = _capability("temperature")
    if actuator is None or thermostat is None:
        return []

    switch_device_ids = (
        set(
            session.scalars(
                select(DeviceCapabilityLink.device_id).where(
                    DeviceCapabilityLink.capability_id == switch.id
                )
            )
        )
        if switch is not None
        else set()
    )

    assignments = session.execute(
        select(ZoneDevice, Device)
        .join(Device, Device.id == ZoneDevice.device_id)
        .join(
            DeviceCapabilityLink,
            (DeviceCapabilityLink.device_id == Device.id)
            & (DeviceCapabilityLink.capability_id == thermostat.id),
        )
        .where(ZoneDevice.zone_id == zone.id, ZoneDevice.device_role_id == actuator.id)
        .order_by(ZoneDevice.sort_order, ZoneDevice.id)
    ).all()

    candidates: list[ThermostatCandidate] = []
    for assignment, device in assignments:
        if device.id in switch_device_ids:
            continue
        measurement = (
            session.scalar(
                select(Measurement)
                .where(
                    Measurement.device_id == device.id,
                    Measurement.capability_id == temperature.id,
                )
                .order_by(Measurement.measured_at.desc(), Measurement.id.desc())
                .limit(1)
            )
            if temperature is not None
            else None
        )
        offset = assignment.temperature_backup_offset_k
        candidates.append(
            ThermostatCandidate(
                device_id=device.id,
                device_name=device.display_name,
                local_temperature_c=measurement.value_numeric if measurement is not None else None,
                measured_at=measurement.measured_at if measurement is not None else None,
                offset_k=offset if offset is not None else Decimal("0"),
                last_external_write_at=last_external_temperature_write_at(session, device.id),
            )
        )
    return candidates
