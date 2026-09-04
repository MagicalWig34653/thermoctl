# ruff: noqa: E501
import json
import logging
from datetime import datetime
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from thermoctl.db.models.device import (
    Device,
    DeviceCapabilityLink,
    DeviceProperty,
    DevicePropertyValue,
    ZoneDevice,
)
from thermoctl.db.models.lookup import DeviceCapability, DeviceRole, Integration, SensorStatus
from thermoctl.db.models.measurement import DeviceHealth, Measurement
from thermoctl.db.models.state import ZoneState
from thermoctl.db.models.zone import Zone
from thermoctl.domain.controller import execute_action
from thermoctl.domain.controller_channels import apply_read_channels
from thermoctl.domain.device_classes import (
    DeviceDescription,
    descriptions_from_bridge_list,
)
from thermoctl.domain.fault import NO_SOURCE, OK, sensor_state
from thermoctl.domain.reading import Reading, readings_from_payload
from thermoctl.domain.zone_settings import control_parameters
from thermoctl.integrations.mqtt.zigbee2mqtt import MessageKind, trim

log = logging.getLogger(__name__)


def _integration(session: Session) -> Integration:
    integration = session.scalar(select(Integration).where(Integration.code == "zigbee2mqtt"))
    if integration is None:  # pragma: no cover
        # Consistency check against the migration that creates this row. Reachable
        # only with a manually corrupted schema -- a test for this would have to
        # empty the lookup table, and would thereby test the migration, not us.
        raise RuntimeError("Anbindung zigbee2mqtt fehlt in der Nachschlagetabelle")
    return integration


def _device(session: Session, name: str, received_at: datetime) -> Device:
    integration = _integration(session)
    device = session.scalar(
        select(Device).where(
            Device.integration_id == integration.id,
            Device.external_id == name,
        )
    )
    if device is None:
        device = Device(
            integration_id=integration.id,
            external_id=name,
            display_name=name,
            is_enabled=True,
            first_seen_at=received_at,
        )
        session.add(device)
        session.flush()
    return device


def _process_device_list(session: Session, payload: bytes, received_at: datetime) -> None:
    try:
        descriptions = descriptions_from_bridge_list(payload)
    except ValueError:
        log.warning("Zigbee2MQTT-Geraeteliste ist ungültig")
        return

    capabilities = {
        capability.code: capability for capability in session.scalars(select(DeviceCapability))
    }
    unknown_ones: set[str] = set()
    for description in descriptions:
        _save_description(session, description, received_at, capabilities, unknown_ones)
    for code in sorted(unknown_ones):
        log.warning(
            "Geraetefaehigkeit fehlt in der Nachschlagetabelle",
            extra={"faehigkeitscode": code},
        )


def _save_description(
    session: Session,
    description: DeviceDescription,
    received_at: datetime,
    capabilities: dict[str, DeviceCapability],
    unknown_ones: set[str],
) -> None:
    device = _device(session, description.name, received_at)
    device.display_name = description.name
    device.model = description.model
    device.is_group = description.ist_group
    if device.first_seen_at is None:
        # Backfill for devices that were not created via ingest — since subproject 3
        # the interface can also create them, and there is no sighting there yet.
        # During ingest itself the value is already set (_geraet).
        device.first_seen_at = received_at

    session.execute(delete(DeviceCapabilityLink).where(DeviceCapabilityLink.device_id == device.id))
    for code in description.capabilities:
        capability = capabilities.get(code)
        if capability is None:
            unknown_ones.add(code)
            continue
        session.add(DeviceCapabilityLink(device_id=device.id, capability_id=capability.id))
    old_ids = select(DeviceProperty.id).where(DeviceProperty.device_id == device.id)
    session.execute(delete(DevicePropertyValue).where(DevicePropertyValue.property_id.in_(old_ids)))
    session.execute(delete(DeviceProperty).where(DeviceProperty.device_id == device.id))
    for property_description in description.properties:
        property_model = DeviceProperty(
            device_id=device.id,
            name=property_description.name,
            value_type=property_description.value_type,
            unit=property_description.unit,
            min_value=property_description.min_value,
            max_value=property_description.max_value,
            is_readable=property_description.is_readable,
            is_writable=property_description.is_writable,
        )
        session.add(property_model)
        session.flush()
        for sort_order, value in enumerate(property_description.values):
            session.add(DevicePropertyValue(property_id=property_model.id, value=value, sort_order=sort_order))


def _process_state(
    session: Session, name: str, payload: bytes, received_at: datetime
) -> None:
    readings = readings_from_payload(payload, received_at)
    if not readings:
        return
    device = _device(session, name, received_at)
    try:
        raw_values = json.loads(payload, parse_float=Decimal, parse_int=Decimal)
    except (json.JSONDecodeError, UnicodeDecodeError):  # pragma: no cover
        # Unreachable through this function: `readings_from_payload` above parsed the
        # very same bytes and returned nothing on a failure, so we are already gone.
        # Kept as the second parse's own guard -- it does not get to assume that the
        # first one ran.
        raw_values = {}
    if not isinstance(raw_values, dict):  # pragma: no cover - same reason
        raw_values = {}
    # Read before inserting: afterwards our own new reading would be the most recent
    # one, and the comparison below would compare the message with itself.
    last_pressed = _last_pressed(session, device.id)
    measured_at = readings[0].measured_at
    changed_values: dict[str, object] = {}
    for property_model in session.scalars(
        select(DeviceProperty).where(DeviceProperty.device_id == device.id)
    ):
        if property_model.name not in raw_values:
            continue
        raw = raw_values[property_model.name]
        if property_model.last_value_at is None or measured_at > property_model.last_value_at:
            changed_values[property_model.name] = raw
        if isinstance(raw, Decimal):
            property_model.last_value_number, property_model.last_value_text = raw, None
        elif isinstance(raw, bool):
            property_model.last_value_number, property_model.last_value_text = None, str(raw).lower()
        elif isinstance(raw, str):
            property_model.last_value_number, property_model.last_value_text = None, raw
        property_model.last_value_at = measured_at
    capabilities = {
        capability.code: capability for capability in session.scalars(select(DeviceCapability))
    }
    unknown_ones: set[str] = set()
    for reading in readings:
        capability = capabilities.get(reading.capability)
        if capability is None:
            unknown_ones.add(reading.capability)
            continue
        session.add(
            Measurement(
                device_id=device.id,
                capability_id=capability.id,
                value_numeric=reading.number,
                value_text=reading.text,
                measured_at=reading.measured_at,
                received_at=received_at,
            )
        )
    for code in sorted(unknown_ones):
        log.warning(
            "Messwertfähigkeit fehlt in der Nachschlagetabelle",
            extra={"faehigkeitscode": code},
        )

    healthy = session.get(DeviceHealth, device.id)
    if healthy is None:
        healthy = DeviceHealth(
            device_id=device.id,
            last_payload_at=received_at,
            payload_count=0,
        )
        session.add(healthy)
    healthy.last_payload_at = received_at
    healthy.payload_count += 1
    healthy.link_quality = _integer(readings, "link_quality", healthy.link_quality)
    healthy.battery_percent = _decimal_number(readings, "battery", healthy.battery_percent)
    device.last_seen_at = received_at
    _execute_button_press(session, device, readings, last_pressed, received_at)
    apply_read_channels(session, device, changed_values, received_at)


def _last_pressed(session: Session, device_id: int) -> datetime | None:
    """When this device last reported a button press -- before this message.

    The guard against duplicate execution: Zigbee2MQTT normally sends state messages
    without the retain flag, but a retained message gets redelivered on **every**
    reconnect. Without this comparison, a flaky network connection would trigger the
    same button press again every time -- and a boost nobody pressed only gets noticed
    once the room is too warm.
    """
    capability_id = session.scalar(
        select(DeviceCapability.id).where(DeviceCapability.code == "action")
    )
    if capability_id is None:
        return None
    return session.scalar(
        select(Measurement.measured_at)
        .where(
            Measurement.device_id == device_id,
            Measurement.capability_id == capability_id,
        )
        .order_by(Measurement.measured_at.desc(), Measurement.id.desc())
        .limit(1)
    )


def _execute_button_press(
    session: Session,
    device: Device,
    readings: list[Reading],
    last_seen: datetime | None,
    received_at: datetime,
) -> None:
    """Executes what a button press on a controller has bound to it."""
    press = next((b for b in readings if b.capability == "action" and b.text), None)
    if press is None:
        return
    if last_seen is not None and press.measured_at <= last_seen:
        log.debug(
            "Tastendruck bereits verarbeitet, wird übergangen",
            extra={"geraet": device.display_name, "aktion": press.text},
        )
        return
    assert press.text is not None
    execute_action(session, device, press.text, received_at)


def _decimal_number(
    readings: list[Reading], code: str, so_far: Decimal | None
) -> Decimal | None:
    return next(
        (b.number for b in readings if b.capability == code and b.number is not None),
        so_far,
    )


def _integer(readings: list[Reading], code: str, so_far: int | None) -> int | None:
    value = _decimal_number(readings, code, None)
    return int(value) if value is not None else so_far


def _process_availability(
    session: Session, name: str, payload: bytes, received_at: datetime
) -> None:
    try:
        data = json.loads(payload)
    # Parentheses, even though Python 3.14 no longer requires them here (PEP 758):
    # without them the line looks exactly like the Python 2 form, which meant
    # something different -- there, the second name bound the exception instead of
    # catching a second class. Whoever misreads this once looks for the bug in the
    # wrong place.
    except (json.JSONDecodeError, UnicodeDecodeError):
        log.warning("Zigbee2MQTT-Erreichbarkeit ist kein gültiges JSON")
        return
    if not isinstance(data, dict) or not isinstance(data.get("state"), str):
        log.warning("Zigbee2MQTT-Erreichbarkeit enthält keinen Zustand")
        return
    device = _device(session, name, received_at)
    healthy = session.get(DeviceHealth, device.id)
    if healthy is None:
        healthy = DeviceHealth(
            device_id=device.id,
            last_payload_at=received_at,
            payload_count=0,
        )
        session.add(healthy)
    healthy.availability = data["state"]


def process_message(
    session: Session,
    topic: str,
    payload: bytes,
    *,
    base: str,
    received_at: datetime,
) -> None:
    """Writes a received Zigbee2MQTT message into the database."""
    trimmed = trim(topic, base)
    if trimmed.kind == MessageKind.DEVICE_LIST:
        _process_device_list(session, payload, received_at)
    elif trimmed.kind == MessageKind.DEVICE_STATE:
        assert trimmed.device_name is not None
        _process_state(session, trimmed.device_name, payload, received_at)
    elif trimmed.kind == MessageKind.AVAILABILITY:
        assert trimmed.device_name is not None
        _process_availability(session, trimmed.device_name, payload, received_at)
    else:
        log.info(
            "Zigbee2MQTT-Nachricht wird nicht verarbeitet",
            extra={"nachrichtenart": trimmed.kind.value, "topic": topic},
        )


def advance_zone_state(session: Session, now: datetime) -> None:
    """Derives the current state of all zones from their temperature source."""
    temperature = session.scalar(
        select(DeviceCapability).where(DeviceCapability.code == "temperature")
    )
    contact = session.scalar(select(DeviceCapability).where(DeviceCapability.code == "contact"))
    window_role = session.scalar(
        select(DeviceRole).where(DeviceRole.code == "window_contact")
    )
    status_ids = {status.code: status.id for status in session.scalars(select(SensorStatus))}
    for zone in session.scalars(select(Zone)):
        measurement = None
        if zone.temperature_source_device_id is not None and temperature is not None:
            measurement = session.scalar(
                select(Measurement)
                .where(
                    Measurement.device_id == zone.temperature_source_device_id,
                    Measurement.capability_id == temperature.id,
                )
                .order_by(Measurement.measured_at.desc(), Measurement.id.desc())
                .limit(1)
            )
        code = (
            NO_SOURCE
            if zone.temperature_source_device_id is None
            else sensor_state(
                measurement.measured_at if measurement is not None else None,
                now,
                control_parameters(session, zone).sensor_timeout_seconds,
            )
        )
        status_id = status_ids.get(code)
        if status_id is None:  # pragma: no cover
            # As above: consistency check against the migration, not against input.
            raise RuntimeError(f"Sensorstatus {code} fehlt in der Nachschlagetabelle")
        state = session.get(ZoneState, zone.id)
        if state is None:
            state = ZoneState(
                zone_id=zone.id,
                sensor_status_id=status_id,
                updated_at=now,
            )
            session.add(state)
        state.temperature_c = measurement.value_numeric if measurement is not None else None
        state.measured_at = measurement.measured_at if measurement is not None else None
        state.sensor_status_id = status_id
        state.window_open = _window_open(
            session,
            zone,
            contact,
            window_role,
            now,
            control_parameters(session, zone).sensor_timeout_seconds,
        )
        state.updated_at = now


def _window_open(
    session: Session,
    zone: Zone,
    contact: DeviceCapability | None,
    window_role: DeviceRole | None,
    now: datetime,
    timeout_s: int,
) -> bool | None:
    if contact is None or window_role is None:
        return None
    devices_ids = list(
        session.scalars(
            select(ZoneDevice.device_id).where(
                ZoneDevice.zone_id == zone.id,
                ZoneDevice.device_role_id == window_role.id,
            )
        )
    )
    if not devices_ids:
        # Unknown is treated by the control logic like closed. Otherwise a plant
        # without window contacts could fundamentally never heat.
        return None

    unknown = False
    for device_id in devices_ids:
        measurement = session.scalar(
            select(Measurement)
            .where(
                Measurement.device_id == device_id,
                Measurement.capability_id == contact.id,
            )
            .order_by(Measurement.measured_at.desc(), Measurement.id.desc())
            .limit(1)
        )
        if (
            measurement is None
            or sensor_state(measurement.measured_at, now, timeout_s) != OK
            or measurement.value_text not in {"true", "false"}
        ):
            unknown = True
            continue
        # Zigbee2MQTT reports `contact=true` for closed and `false` for open. The
        # inversion deliberately stays here, so it isn't done again in every
        # consumer, possibly inconsistently.
        if measurement.value_text == "false":
            return True
    return None if unknown else False
