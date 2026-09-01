# ruff: noqa: E501
"""Publish our own state — and register the zones with Home Assistant.

The contract lives in `integrations/mqtt/veroeffentlichung.py`: topics, discovery
payloads, registration and deregistration, with tests. This is the caller.

**This also runs in the dry run** — deliberately. A state message doesn't move
anything, and an integration that can only be tried out after arming can no longer be
checked safely at exactly the moment when an error would still be harmless. Whoever
wants to set up the plant in Home Assistant, turn the thermostat, and check whether the
setpoint arrives should be able to do that beforehand.

**The dry run no longer appears in the zone's name.** It used to be there because it
was visible — and was wrong for exactly that reason: Home Assistant derives the entity
id from the name the first time it appears. A zone that first showed up during the dry
run was then called `climate.thermoctl_zone_1_trockenlauf` forever, even once armed.
Instead, a dedicated entity for the whole service now says so (`binary_sensor`,
"control armed"), and the zones keep their id across the whole transition.

**Everything persistent goes out with the retain flag** — registrations as well as
states. Without that, Home Assistant shows an empty card after every restart until
this service sends something the next time; with a one-minute control cycle that's a
minute of confusion, and when switching a mode it looked as if the command had been
swallowed.

**Only what no longer exists gets deregistered.** A deleted zone gets the empty payload
on each of its config topics; otherwise a thermostat that belongs to nobody anymore
would stay behind in Home Assistant.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.config import get_settings
from thermoctl.db.models.device import ControllerChannel, Device
from thermoctl.db.models.lookup import ChannelKind, DeviceCapability, SensorStatus
from thermoctl.db.models.measurement import Measurement
from thermoctl.db.models.state import ShadowDecision, ZoneState
from thermoctl.db.models.zone import SetpointMode, Zone, ZoneSetpoint
from thermoctl.domain.controller_channels import may_be_written
from thermoctl.domain.schedule import end_of_next_switch, resolved_setpoint
from thermoctl.domain.self_regulating import SETPOINT_PROPERTY, valve_commands
from thermoctl.domain.zone_settings import PARAMETERS, control_parameters
from thermoctl.integrations.actuators import MqttPublisher, switching_allowed
from thermoctl.integrations.mqtt.publication import (
    DiscoveryMessage,
    armed_discovery,
    armed_topic,
    availability_topic,
    boost_discovery,
    mode_discovery,
    mode_topics,
    parameter_discovery,
    parameter_topics,
    states_topics,
    timestamp_discovery,
    zone_discovery,
)
from thermoctl.services.device_commands import EXECUTED, FAILED, SUPPRESSED, record_command

log = logging.getLogger(__name__)


@dataclass
class PublicationState:
    """Which config topics this run has sent, per zone.

    The state lives in the process, not in the database: it describes what *this* run
    has sent. After a restart it is empty, and the first cycle registers everything
    again -- which is correct, because nobody knows whether the messages from back then
    are still sitting at the broker.

    The topics instead of just the zone ids, so that a deleted zone can be fully
    deregistered: it has its own entity per mode and per control parameter, and their
    config topics could no longer be derived afterwards -- the modes of the deleted
    zone are then nowhere to be found anymore.
    """

    registered: dict[int, list[str]] = field(default_factory=dict)
    service_registered: bool = False
    controller_values: dict[int, object] = field(default_factory=dict)
    # Per self-regulating valve, the (payload, armed) last acted on -- sent,
    # withheld, or attempted and failed. Resending (or re-logging) an unchanged one
    # every cycle would fill the radio with commands that change nothing -- and a
    # Zigbee device that gets a command every few seconds costs battery for it --
    # and would make the command log unreadable within a day. `armed` is part of
    # the key: the same setpoint that was withheld during a dry run must go out
    # for real, and be logged again, the moment the plant is armed.
    valve_commands: dict[int, tuple[str, bool]] = field(default_factory=dict)


def _as_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, datetime):
        # With timezone: `device_class: timestamp` requires it, and a naive value
        # gets interpreted by Home Assistant as local time -- ours would be UTC.
        return value.replace(tzinfo=ZoneInfo("UTC")).isoformat()
    return str(value)


def _discovery_messages(session: Session, zone: Zone, prefix: str) -> list[DiscoveryMessage]:
    """Everything that appears for a zone in Home Assistant."""
    name = zone.display_name
    messages = [
        zone_discovery(zone.id, name, prefix=prefix),
        boost_discovery(zone.id, name, prefix),
        timestamp_discovery(zone.id, name, "last_switch", "Letzte Schaltung", prefix),
        timestamp_discovery(
            zone.id, name, "next_switch", "Nächster Moduswechsel", prefix
        ),
    ]
    for mode in session.scalars(select(SetpointMode).order_by(SetpointMode.sort_order)):
        messages.append(mode_discovery(zone.id, name, mode.id, mode.name, prefix))
    for description in PARAMETERS:
        messages.append(
            parameter_discovery(
                zone.id, name, description.name, description.label,
                description.minimum, description.maximum, description.step,
                description.unit, prefix,
            )
        )
    return messages


async def cycle(
    session: Session,
    client: MqttPublisher,
    state: PublicationState,
    prefix: str,
    now: datetime,
    *,
    source: str = "system",
) -> int:
    """One publication cycle. Returns the number of messages sent."""
    armed = switching_allowed(session)
    zones = list(session.scalars(select(Zone).order_by(Zone.id)))
    sent_count = 0

    # Availability first: it's the statement "whatever comes next is current".
    if await client.publishing(
        availability_topic(prefix), "online", switches=False, retained=True
    ):
        sent_count += 1

    if not state.service_registered:
        message = armed_discovery(prefix)
        if await client.publishing(
            message.topic, message.payload, switches=False, retained=True
        ):
            state.service_registered = True
            sent_count += 1
    if await client.publishing(
        armed_topic(prefix), _as_text(armed), switches=False, retained=True
    ):
        sent_count += 1

    for zone in zones:
        if zone.id in state.registered:
            continue
        sent_count += await _register_zone(session, client, state, zone, prefix)

    sent_count += await _deregister_deleted(client, state, {zone.id for zone in zones})
    for zone in zones:
        sent_count += await _send_zone_state(session, client, zone, prefix, now)
    sent_count += await _send_controller_channels(session, client, state, get_settings().mqtt_base_topic, now)
    for zone in zones:
        sent_count += await _send_self_regulating_valves(
            session, client, state, get_settings().mqtt_base_topic, zone, now, source
        )
    return sent_count


async def _send_self_regulating_valves(
    session: Session,
    client: MqttPublisher,
    state: PublicationState,
    base: str,
    zone: Zone,
    now: datetime,
    source: str,
) -> int:
    """Tells every self-regulating valve of this zone what to aim for.

    **These messages move a valve.** They carry `switches=True` and go through
    `switching_allowed` -- the same two bolts as an on/off command, because the
    physical effect is the same. A setpoint written to a thermostatic valve is not a
    display value, and treating it as one would be a way around the dry run.

    Only what changed is sent -- and logged. The setpoint stands still for hours at
    a time; a log entry every cycle, armed or not, would be unreadable within a day.
    `state.valve_commands` therefore gates both the send and the log entry: it is
    updated whenever an entry is written, regardless of outcome (sent, withheld, or
    failed), so a repeated identical outcome does not repeat the entry. Only a
    changed payload -- or a changed outcome for the same payload -- writes again.
    """
    armed = switching_allowed(session)
    sent = 0
    for command in valve_commands(session, zone, now):
        payload_values: dict[str, object] = {
            SETPOINT_PROPERTY: float(command.setpoint_c)
        }
        if command.temperature_property is not None and command.temperature_c is not None:
            payload_values[command.temperature_property] = float(command.temperature_c)
        payload = json.dumps(payload_values, ensure_ascii=False, separators=(",", ":"))
        topic = f"{base.rstrip('/')}/{command.device.external_id}/set"
        cache_key = (payload, armed)
        if state.valve_commands.get(command.device.id) == cache_key:
            continue

        if not armed:
            state.valve_commands[command.device.id] = cache_key
            record_command(
                session,
                now=now,
                source=source,
                zone=zone,
                device=command.device,
                command="setpoint",
                payload=payload,
                outcome=SUPPRESSED,
                reason=command.reason,
            )
            continue

        try:
            executed = await client.publishing(topic, payload, switches=True)
        except Exception as exc:
            # A broken broker connection must not abort the whole cycle -- other
            # zones and the rest of this cycle (shadow decisions, retention) still
            # need to run. The failure is recorded instead of raised.
            state.valve_commands[command.device.id] = cache_key
            record_command(
                session,
                now=now,
                source=source,
                zone=zone,
                device=command.device,
                command="setpoint",
                payload=payload,
                outcome=FAILED,
                error=str(exc),
                reason=command.reason,
            )
            continue
        state.valve_commands[command.device.id] = cache_key
        if executed:
            sent += 1
            log.info(
                "Selbstregelndes Ventil gestellt",
                extra={
                    "zone_id": zone.id,
                    "geraet": command.device.display_name,
                    "sollwert": str(command.setpoint_c),
                    "begruendung": command.reason,
                },
            )
            record_command(
                session,
                now=now,
                source=source,
                zone=zone,
                device=command.device,
                command="setpoint",
                payload=payload,
                outcome=EXECUTED,
                reason=command.reason,
            )
        else:
            record_command(
                session,
                now=now,
                source=source,
                zone=zone,
                device=command.device,
                command="setpoint",
                payload=payload,
                outcome=FAILED,
                error="MQTT-Client hat die Veroeffentlichung abgewiesen",
                reason=command.reason,
            )
    return sent


async def _register_zone(
    session: Session,
    client: MqttPublisher,
    state: PublicationState,
    zone: Zone,
    prefix: str,
) -> int:
    sent_count = 0
    reported: list[str] = []
    for message in _discovery_messages(session, zone, prefix):
        if await client.publishing(
            message.topic, message.payload, switches=False, retained=True
        ):
            reported.append(message.topic)
            sent_count += 1
    if reported:
        state.registered[zone.id] = reported
        log.info(
            "Zone bei Home Assistant angemeldet",
            extra={"zone_id": zone.id, "entitaeten": len(reported)},
        )
    return sent_count


async def _deregister_deleted(
    client: MqttPublisher,
    state: PublicationState,
    existing: set[int],
) -> int:
    """The only reason to deregister: the zone doesn't exist anymore.

    Without this, a thermostat that nobody operates anymore would stay behind in Home
    Assistant — it would keep showing the last known value forever.
    """
    sent_count = 0
    for zone_id in sorted(set(state.registered) - existing):
        for topic in state.registered[zone_id]:
            if await client.publishing(topic, "", switches=False, retained=True):
                sent_count += 1
        del state.registered[zone_id]
        log.info("Geloeschte Zone bei Home Assistant abgemeldet", extra={"zone_id": zone_id})
    return sent_count


def _channel_value(session: Session, channel: ControllerChannel, kind: ChannelKind, now: datetime) -> object | None:
    if kind.code == "fixed":
        return channel.fixed_number if channel.fixed_number is not None else channel.fixed_text
    if kind.code == "sensor_temperature" and channel.source_device_id is not None:
        capability_id = session.scalar(select(DeviceCapability.id).where(DeviceCapability.code == "temperature"))
        return session.scalar(select(Measurement.value_numeric).where(
            Measurement.device_id == channel.source_device_id,
            Measurement.capability_id == capability_id,
            Measurement.value_numeric.is_not(None),
        ).order_by(Measurement.measured_at.desc(), Measurement.id.desc()).limit(1))
    if channel.zone_id is None:  # pragma: no cover - configure_channel demands a zone
        # Guard, not a case: a zone kind without a zone is rejected at creation time.
        # It stays because the alternative is `session.get(Zone, None)`, a lookup on a
        # NULL primary key that SQLAlchemy rightly warns about.
        return None
    zone = session.get(Zone, channel.zone_id)
    if zone is None:  # pragma: no cover - the foreign key prevents this
        return None
    if kind.code == "zone_temperature":
        zone_state = session.get(ZoneState, zone.id)
        return zone_state.temperature_c if zone_state is not None else None
    if kind.code == "zone_setpoint":
        return resolved_setpoint(session, zone, now).temperature_c
    # pragma: no cover - every write kind is handled above; this catches a future one
    # that someone adds to WRITE_KINDS without handling it here. Sending nothing is
    # the safe direction: a display stays stale, no value is invented.
    return None  # pragma: no cover


async def _send_controller_channels(
    session: Session, client: MqttPublisher, state: PublicationState, base: str, now: datetime
) -> int:
    """Sends changed display values; every send explicitly stays non-switching."""
    sent = 0
    rows = session.execute(
        select(ControllerChannel, ChannelKind, Device)
        .join(ChannelKind, ChannelKind.id == ControllerChannel.kind_id)
        .join(Device, Device.id == ControllerChannel.device_id)
        .where(ControllerChannel.direction == "write")
    )
    for channel, kind, device in rows:
        # Second check, deliberately the same function as at creation time: a role
        # can change after the channel has been set up.
        if not may_be_written(session, device):
            log.error("Unsicherer Schreibkanal wird nicht gesendet", extra={"geraet": device.display_name})
            continue
        value = _channel_value(session, channel, kind, now)
        if value is None or state.controller_values.get(channel.id) == value:
            continue
        payload_value: object = float(value) if isinstance(value, Decimal) else value
        payload = json.dumps({channel.property_name: payload_value}, ensure_ascii=False, separators=(",", ":"))
        if await client.publishing(f"{base.rstrip('/')}/{device.external_id}/set", payload, switches=False):
            state.controller_values[channel.id] = value
            sent += 1
    return sent


def _last_switch(session: Session, zone_id: int) -> datetime | None:
    """When the decision last flipped — not when it was last computed.

    `previous_would_heat` is in the shadow log anyway; without the comparison, "last
    switch" would be the last control cycle, i.e. always "a minute ago".
    """
    return session.scalar(
        select(ShadowDecision.decided_at)
        .where(
            ShadowDecision.zone_id == zone_id,
            ShadowDecision.previous_would_heat.is_not(None),
            ShadowDecision.previous_would_heat != ShadowDecision.would_heat,
        )
        .order_by(ShadowDecision.decided_at.desc(), ShadowDecision.id.desc())
        .limit(1)
    )


def _would_heat(session: Session, zone_id: int) -> bool | None:
    return session.scalar(
        select(ShadowDecision.would_heat)
        .where(ShadowDecision.zone_id == zone_id)
        .order_by(ShadowDecision.decided_at.desc(), ShadowDecision.id.desc())
        .limit(1)
    )


async def _send_zone_state(
    session: Session,
    client: MqttPublisher,
    zone: Zone,
    prefix: str,
    now: datetime,
) -> int:
    """All state values of **one** zone.

    Callable individually, because a command from Home Assistant needs an immediate
    response: the climate card there is not optimistic, it waits for the state. If it
    only arrived on the next control cycle, the mode just chosen would jump back to the
    old one for a minute — and looked as if mode selection didn't work.
    """
    topics = states_topics(zone.id, prefix)
    state = session.get(ZoneState, zone.id)
    status_code = "keine_quelle"
    if state is not None:
        status_code = (
            session.scalar(
                select(SensorStatus.code).where(SensorStatus.id == state.sensor_status_id)
            )
            or status_code
        )
    setpoint = resolved_setpoint(session, zone, now)
    values: list[tuple[str, str]] = [
        (topics.current_temperature, _as_text(state.temperature_c if state else None)),
        (topics.setpoint, _as_text(setpoint.temperature_c)),
        (topics.operating_mode, zone.operating_mode.code),
        (topics.sensor_state, status_code),
        (topics.would_heat, _as_text(_would_heat(session, zone.id))),
        (topics.last_switch, _as_text(_last_switch(session, zone.id))),
        (topics.next_switch, _as_text(end_of_next_switch(session, zone, now))),
    ]

    setpoints: dict[int, Decimal] = {
        mode_id: temperature
        for mode_id, temperature in session.execute(
            select(ZoneSetpoint.setpoint_mode_id, ZoneSetpoint.temperature_c).where(
                ZoneSetpoint.zone_id == zone.id
            )
        )
    }
    for mode in session.scalars(select(SetpointMode).order_by(SetpointMode.sort_order)):
        values.append(
            (mode_topics(zone.id, mode.id, prefix)[0], _as_text(setpoints.get(mode.id)))
        )

    effective = control_parameters(session, zone)
    for description in PARAMETERS:
        values.append(
            (
                parameter_topics(zone.id, description.name, prefix)[0],
                _as_text(getattr(effective, description.name)),
            )
        )

    sent_count = 0
    for topic, value in values:
        # An empty value is not sent: in MQTT an empty payload deletes a retained
        # message, and "no reading yet" is something different from "this value
        # doesn't exist anymore".
        if value and await client.publishing(
            topic, value, switches=False, retained=True
        ):
            sent_count += 1
    return sent_count
