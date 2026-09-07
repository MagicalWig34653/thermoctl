# ruff: noqa: E501
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
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
from thermoctl.db.models.operations import Setting
from thermoctl.db.models.state import ZoneState
from thermoctl.db.models.zone import Zone
from thermoctl.domain.controller import execute_action
from thermoctl.domain.controller_channels import apply_read_channels
from thermoctl.domain.device_classes import (
    DeviceDescription,
    descriptions_from_bridge_list,
)
from thermoctl.domain.fault import NO_SOURCE, OK, sensor_state, stuck_reading
from thermoctl.domain.outdoor import outdoor_reading
from thermoctl.domain.reading import Reading, readings_from_payload
from thermoctl.domain.window_alarm import window_alarm_state
from thermoctl.domain.window_temperature_drop import (
    temperature_detection_cap_exceeded,
    temperature_detection_gap_within_tolerance,
    temperature_detection_still_holding,
    temperature_detection_still_silenced,
    temperature_drop_history_cutoff,
    window_open_suspected,
)
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


def _stuck(
    session: Session,
    zone: Zone,
    temperature: DeviceCapability,
    now: datetime,
    duration_hours: int,
) -> bool:
    """Whether the zone's current temperature source counts as `festhängend`.

    Only ever called while `sensor_state()` already reads `ok` for this zone (see
    `advance_zone_state` below) -- a missing or stale source has its own,
    established indicator and is not a case for this one (task instructions,
    section 4).
    """
    assert zone.temperature_source_device_id is not None  # guaranteed by the caller
    cutoff = now - timedelta(hours=duration_hours)
    history_covers_duration = (
        session.scalar(
            select(Measurement.id)
            .where(
                Measurement.device_id == zone.temperature_source_device_id,
                Measurement.capability_id == temperature.id,
                Measurement.measured_at <= cutoff,
            )
            .limit(1)
        )
        is not None
    )
    values = [
        value
        for value in session.scalars(
            select(Measurement.value_numeric)
            .where(
                Measurement.device_id == zone.temperature_source_device_id,
                Measurement.capability_id == temperature.id,
                Measurement.measured_at >= cutoff,
                Measurement.measured_at <= now,
                Measurement.value_numeric.is_not(None),
            )
        )
        if value is not None
    ]
    return stuck_reading(values, history_covers_duration=history_covers_duration)


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
    setting_row = session.get(Setting, 1)
    assert setting_row is not None, "setting-Zeile fehlt — Einrichtung unvollständig"
    # Computed once per cycle, not once per zone: there is exactly one outside for
    # the whole plant, and `window_alarm_state` below needs the same reading for
    # every zone evaluated this cycle.
    outdoor = outdoor_reading(session, setting_row, now)
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
        state.sensor_stuck = (
            code == OK
            and temperature is not None
            and _stuck(session, zone, temperature, now, setting_row.stuck_reading_hours)
        )
        (
            new_window_open,
            detected_by_temperature,
            new_silence_until,
            new_streak_started_at,
            new_last_detected_at,
        ) = _window_open(
            session,
            zone,
            contact,
            window_role,
            temperature,
            now,
            control_parameters(session, zone).sensor_timeout_seconds,
            state,
            setting_row,
        )
        # Only set a *new* clock when there isn't one already -- checking
        # `state.window_open_since is None` rather than "was the previous
        # cycle's `window_open` exactly `True`" survives an unknown (`None`)
        # cycle in between without resetting: a contact that goes stale mid-
        # alarm and then reports open again, still, must resume counting from
        # the original opening, not restart at the moment it happened to
        # recover. Only a *confirmed* closed window (`False`) clears the clock
        # -- `None` (the contact went stale or missing mid-cycle) must leave it
        # untouched, or `window_alarm_state` below would read "no window open"
        # and silently report an all-clear for an alarm that, for all this
        # cycle can tell, may still hold. See the cross-review finding this
        # fixed: a contact failing mid-alarm used to flip the alarm straight
        # to `False`.
        if new_window_open is True and state.window_open_since is None:
            state.window_open_since = now
        elif new_window_open is False:
            state.window_open_since = None
        state.window_open = new_window_open
        # Always in step with `new_window_open` above -- see the column's own
        # docstring in `db/models/state.py`. Never `True` while `new_window_open`
        # is not, since `_window_open` below only ever returns `True` for the
        # temperature flag together with `True` for the window state itself.
        state.window_open_by_temperature = detected_by_temperature
        # The cap-driven silence's own deadline -- see `domain.
        # window_temperature_drop`'s "feedback loop" section and `_window_open`
        # below. Carried over unchanged while still silenced, set fresh the
        # cycle the cap fires, and cleared (`None`) in every other case --
        # `_window_open` decides all three, this only stores its answer.
        state.window_temp_drop_silence_until = new_silence_until
        # The cumulative streak's own bookkeeping -- see `domain.
        # window_temperature_drop`'s "why the first version of the cap never
        # actually fired" section. Deliberately independent of
        # `window_open_since` above, which the hold reads and which this same
        # loop clears on any cycle the zone is not currently judged open;
        # these two persist across exactly that kind of brief interruption.
        state.window_temp_drop_streak_started_at = new_streak_started_at
        state.window_temp_drop_last_detected_at = new_last_detected_at
        state.window_alarm = window_alarm_state(
            window_open=new_window_open,
            window_open_since=state.window_open_since,
            now=now,
            open_after_minutes=setting_row.window_alarm_open_minutes,
            outdoor_status=outdoor.status,
            outdoor_temperature_c=outdoor.temperature_c,
            threshold_c=setting_row.window_alarm_outdoor_threshold_c,
        )
        state.updated_at = now


@dataclass(frozen=True)
class _TemperatureWindowJudgement:
    """Everything `_window_open_from_temperature` decides in one cycle.

    `streak_started_at`/`last_detected_at` are the cumulative streak's own
    bookkeeping (`zone_state.window_temp_drop_streak_started_at`/`_last_
    detected_at`) -- see `domain.window_temperature_drop`'s module docstring
    for why these are deliberately independent of `window_open_since`.
    """

    open: bool
    silence_until: datetime | None
    streak_started_at: datetime | None
    last_detected_at: datetime | None


def _window_open(
    session: Session,
    zone: Zone,
    contact: DeviceCapability | None,
    window_role: DeviceRole | None,
    temperature: DeviceCapability | None,
    now: datetime,
    timeout_s: int,
    previous_state: ZoneState | None,
    setting_row: Setting,
) -> tuple[bool | None, bool, datetime | None, datetime | None, datetime | None]:
    """The zone's window state, whether it came from the temperature guess, the
    cap-driven silence deadline to persist (`zone_state.
    window_temp_drop_silence_until`, `None` unless currently silenced), and the
    cumulative streak's own bookkeeping (`zone_state.
    window_temp_drop_streak_started_at`/`_last_detected_at`, `None` outside the
    temperature path).

    A real window contact, once assigned to the zone, decides **exclusively** --
    task instruction, restated here because it is easy to get backwards: even a
    currently-unknown contact reading (stale, unreachable) must not fall through
    to the temperature guess, or a zone with a merely offline contact would
    silently start being judged by a completely different method mid-outage.
    Only a zone with *no* contact assigned at all reaches
    `_window_open_from_temperature` below, and only if the zone's own switch
    (`Zone.window_temp_drop_detection_enabled`) is on -- the project owner's
    explicit default is off. Silence and the streak bookkeeping are purely
    temperature-side concerns and are always cleared (`None`) outside that
    path -- a zone that later loses its temperature source, its switch, or
    gains a contact must not carry a stale deadline or streak forward that
    nothing will ever read again.
    """
    if contact is not None and window_role is not None:
        device_ids = list(
            session.scalars(
                select(ZoneDevice.device_id).where(
                    ZoneDevice.zone_id == zone.id,
                    ZoneDevice.device_role_id == window_role.id,
                )
            )
        )
        if device_ids:
            contact_open = _contact_window_open(session, device_ids, contact, now, timeout_s)
            return contact_open, False, None, None, None

    if not zone.window_temp_drop_detection_enabled:
        # No contact, and the temperature-based approximation is off -- unknown,
        # exactly like every zone without any window detection before this
        # feature existed. Treated by the control logic like closed, same as the
        # contact-less case below always was: otherwise a plant with no window
        # contacts and the switch off could fundamentally never heat.
        return None, False, None, None, None

    judgement = _window_open_from_temperature(
        session, zone, temperature, now, previous_state, setting_row
    )
    return (
        judgement.open,
        judgement.open,
        judgement.silence_until,
        judgement.streak_started_at,
        judgement.last_detected_at,
    )


def _contact_window_open(
    session: Session,
    device_ids: list[int],
    contact: DeviceCapability,
    now: datetime,
    timeout_s: int,
) -> bool | None:
    unknown = False
    for device_id in device_ids:
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


def _window_open_from_temperature(
    session: Session,
    zone: Zone,
    temperature: DeviceCapability | None,
    now: datetime,
    previous_state: ZoneState | None,
    setting_row: Setting,
) -> _TemperatureWindowJudgement:
    """Whether a contact-less zone's own temperature currently suggests an open
    window, and the bookkeeping to carry forward (see `_window_open` above and
    `_TemperatureWindowJudgement`). The window state itself is always a
    definite `True`/`False`, never unknown: unlike a contact that can go
    stale, there is no separate "cannot currently tell" state here, only "not
    enough history to say" (`window_open_suspected`'s own `False`).

    See `domain.window_temperature_drop`'s module docstring for the full
    reasoning behind every part below: the drop trigger, the bounded hold that
    ends a triggered suspicion without relying on a recovery signal that a
    withheld heat demand would make circular, and the cap-plus-silence
    (measured off its own cumulative streak, not off the hold's clock) that
    closes the feedback loop the hold alone leaves open.
    """
    if temperature is None or zone.temperature_source_device_id is None:
        return _TemperatureWindowJudgement(False, None, None, None)

    previous_silence_until = (
        previous_state.window_temp_drop_silence_until if previous_state is not None else None
    )
    if temperature_detection_still_silenced(previous_silence_until, now):
        # Standing down regardless of the current temperature -- the whole
        # point of the cap. Carries the silence deadline and the streak
        # bookkeeping forward unchanged: by the time the silence lapses,
        # `last_detected_at` will be far older than the gap tolerance, so
        # whatever triggers next is correctly treated as a new streak without
        # any special-casing here.
        previous_streak_started_at = (
            previous_state.window_temp_drop_streak_started_at
            if previous_state is not None
            else None
        )
        previous_last_detected_at = (
            previous_state.window_temp_drop_last_detected_at
            if previous_state is not None
            else None
        )
        return _TemperatureWindowJudgement(
            False, previous_silence_until, previous_streak_started_at, previous_last_detected_at
        )

    already_open = previous_state is not None and previous_state.window_open_by_temperature
    still_holding = already_open and temperature_detection_still_holding(
        previous_state.window_open_since,  # type: ignore[union-attr]
        now,
        hold_minutes=setting_row.window_temp_drop_hold_minutes,
    )

    previous_streak_started_at = (
        previous_state.window_temp_drop_streak_started_at if previous_state is not None else None
    )
    previous_last_detected_at = (
        previous_state.window_temp_drop_last_detected_at if previous_state is not None else None
    )

    streak_started_at: datetime
    last_detected_at: datetime
    if still_holding:
        # Still within the hold -- this is unambiguously the same streak
        # continuing, confirmed by the hold itself, not merely inferred from a
        # gap: no tolerance check needed or wanted here, only the fresh
        # re-check right after a hold lapses (below) can ever actually flicker.
        streak_started_at = previous_streak_started_at if previous_streak_started_at else now
        last_detected_at = now
    else:
        cutoff = temperature_drop_history_cutoff(
            now, setting_row.window_temp_drop_window_minutes
        )
        history_covers_duration = (
            session.scalar(
                select(Measurement.id)
                .where(
                    Measurement.device_id == zone.temperature_source_device_id,
                    Measurement.capability_id == temperature.id,
                    Measurement.measured_at <= cutoff,
                )
                .limit(1)
            )
            is not None
        )
        values = [
            value
            for value in session.scalars(
                select(Measurement.value_numeric)
                .where(
                    Measurement.device_id == zone.temperature_source_device_id,
                    Measurement.capability_id == temperature.id,
                    Measurement.measured_at >= cutoff,
                    Measurement.measured_at <= now,
                    Measurement.value_numeric.is_not(None),
                )
                .order_by(Measurement.measured_at.asc(), Measurement.id.asc())
            )
            if value is not None
        ]
        detected = window_open_suspected(
            values,
            history_covers_duration=history_covers_duration,
            drop_threshold_k=setting_row.window_temp_drop_threshold_k,
        )

        if not detected:
            # Not detected this cycle -- but the cumulative streak is not
            # necessarily over: a brief, tolerated interruption (see `domain.
            # window_temperature_drop.temperature_detection_gap_within_
            # tolerance`) must not reset it, only a genuinely long gap should.
            # Carry the bookkeeping forward unchanged; the next actual
            # detection, if any, decides whether the gap since
            # `last_detected_at` was too long.
            return _TemperatureWindowJudgement(
                False, None, previous_streak_started_at, previous_last_detected_at
            )

        # Detected via a fresh check right after the hold lapsed -- exactly
        # the moment a single noisy miss could have reset everything under the
        # old design. Decide whether this continues the existing cumulative
        # streak (the gap since it was last detected is still within
        # tolerance) or starts a brand new one.
        if previous_streak_started_at is not None and temperature_detection_gap_within_tolerance(
            previous_last_detected_at,
            now,
            gap_tolerance_minutes=setting_row.window_temp_drop_gap_tolerance_minutes,
        ):
            streak_started_at = previous_streak_started_at
        else:
            streak_started_at = now
        last_detected_at = now

    if temperature_detection_cap_exceeded(
        streak_started_at,
        now,
        max_suspected_minutes=setting_row.window_temp_drop_max_suspected_minutes,
    ):
        silence_until = now + timedelta(minutes=setting_row.window_temp_drop_silence_minutes)
        # The cap fired and a silence begins -- reset the streak bookkeeping to
        # a clean slate, so whatever triggers next after the silence starts
        # counting from zero (see the module docstring).
        return _TemperatureWindowJudgement(False, silence_until, None, None)

    return _TemperatureWindowJudgement(True, None, streak_started_at, last_detected_at)
