"""The plant-wide outdoor temperature: one source, not one per zone.

Modelled exactly like a zone's own temperature source
(`zone.temperature_source_device_id`) -- picked from the known Zigbee2MQTT devices
with a `temperature` capability, read through the same ingest path every other
measurement already comes in through -- except that this one column lives on
`setting` instead of on a zone, because there is exactly one outside for the whole
plant.

Age and failure are judged the same way a zone's sensor is judged
(`domain.fault.sensor_state`): no source configured and "the source stopped
reporting" both collapse into `NO_SOURCE`/`VERALTET` -- there is no meaningful
difference between them from an operator's point of view, and the existing zone
convention already treats them the same way.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl import audit
from thermoctl.db.models.device import Device
from thermoctl.db.models.lookup import DeviceCapability
from thermoctl.db.models.measurement import Measurement
from thermoctl.db.models.operations import Setting
from thermoctl.domain.device_assignment import TEMPERATURE_SOURCE, check_capability
from thermoctl.domain.fault import sensor_state


@dataclass(frozen=True)
class OutdoorReading:
    """The outdoor temperature as it stands right now.

    `status` is one of `domain.fault.OK`, `VERALTET` or `NO_SOURCE` -- never
    guessed from `temperature_c` alone, so a caller can never mistake "no reading"
    for "it is exactly zero degrees outside".
    """

    status: str
    temperature_c: Decimal | None
    measured_at: datetime | None


def outdoor_reading(session: Session, setting_row: Setting, now: datetime) -> OutdoorReading:
    """The current outdoor reading, freshly derived -- nothing here is cached.

    Cheap enough to compute on every call (one device lookup, one indexed
    measurement query): unlike a zone's temperature, which is advanced once per
    cycle into `zone_state` because `decide()` needs a stable snapshot, nothing in
    the control loop reads this value at all. Every caller (the start page, the
    MQTT publication cycle, `domain.window_alarm`) gets the same live answer
    instead of a second, potentially stale copy of the truth.
    """
    device_id = setting_row.outdoor_temperature_source_device_id
    measurement = None
    if device_id is not None:
        temperature = session.scalar(
            select(DeviceCapability).where(DeviceCapability.code == "temperature")
        )
        if temperature is not None:
            measurement = session.scalar(
                select(Measurement)
                .where(
                    Measurement.device_id == device_id,
                    Measurement.capability_id == temperature.id,
                )
                .order_by(Measurement.measured_at.desc(), Measurement.id.desc())
                .limit(1)
            )
    status = sensor_state(
        measurement.measured_at if measurement is not None else None,
        now,
        setting_row.default_sensor_timeout_seconds,
    )
    return OutdoorReading(
        status,
        measurement.value_numeric if measurement is not None else None,
        measurement.measured_at if measurement is not None else None,
    )


def set_outdoor_temperature_source(
    session: Session,
    setting_row: Setting,
    device: Device | None,
    *,
    actor_id: int | None,
    source: str = "web",
) -> None:
    """Picks (or clears) the plant-wide outdoor temperature source.

    Exactly the same check `device_assignment.set_temperature_source` runs for a
    zone's own source -- reused, not duplicated, so the two can never quietly
    drift apart on what "can act as a temperature source" means.
    """
    if device is not None:
        check_capability(session, device, TEMPERATURE_SOURCE)
    setting_row.outdoor_temperature_source_device_id = device.id if device is not None else None
    audit.record(
        session,
        source=source,
        action="assign" if device is not None else "unassign",
        object_type="outdoor_temperature_source",
        object_id="1",
        summary=(
            f"Außentemperaturquelle auf '{device.display_name}' gesetzt"
            if device is not None
            else "Außentemperaturquelle gelöst"
        ),
        user_id=actor_id,
    )
