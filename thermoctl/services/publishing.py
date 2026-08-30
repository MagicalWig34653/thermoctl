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

    angemeldet: dict[int, list[str]] = field(default_factory=dict)
    dienst_angemeldet: bool = False
    controller_values: dict[int, object] = field(default_factory=dict)


def _als_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, datetime):
        # With timezone: `device_class: timestamp` requires it, and a naive value
        # gets interpreted by Home Assistant as local time -- ours would be UTC.
        return value.replace(tzinfo=ZoneInfo("UTC")).isoformat()
    return str(value)


def _discovery_messages(session: Session, zone: Zone, praefix: str) -> list[DiscoveryMessage]:
    """Everything that appears for a zone in Home Assistant."""
    name = zone.display_name
    messages = [
        zone_discovery(zone.id, name, praefix=praefix),
        boost_discovery(zone.id, name, praefix),
        timestamp_discovery(zone.id, name, "last_switch", "Letzte Schaltung", praefix),
        timestamp_discovery(
            zone.id, name, "next_switch", "Nächster Moduswechsel", praefix
        ),
    ]
    for mode in session.scalars(select(SetpointMode).order_by(SetpointMode.sort_order)):
        messages.append(mode_discovery(zone.id, name, mode.id, mode.name, praefix))
    for description in PARAMETERS:
        messages.append(
            parameter_discovery(
                zone.id, name, description.name, description.label,
                description.minimum, description.maximum, description.step,
                description.einheit, praefix,
            )
        )
    return messages


async def cycle(
    session: Session,
    client: MqttPublisher,
    state: PublicationState,
    praefix: str,
    now: datetime,
) -> int:
    """One publication cycle. Returns the number of messages sent."""
    armed = switching_allowed(session)
    zones = list(session.scalars(select(Zone).order_by(Zone.id)))
    gesendet = 0

    # Availability first: it's the statement "whatever comes next is current".
    if await client.publishing(
        availability_topic(praefix), "online", switches=False, retained=True
    ):
        gesendet += 1

    if not state.dienst_angemeldet:
        message = armed_discovery(praefix)
        if await client.publishing(
            message.topic, message.payload, switches=False, retained=True
        ):
            state.dienst_angemeldet = True
            gesendet += 1
    if await client.publishing(
        armed_topic(praefix), _als_text(armed), switches=False, retained=True
    ):
        gesendet += 1

    for zone in zones:
        if zone.id in state.angemeldet:
            continue
        gesendet += await _register_zone(session, client, state, zone, praefix)

    gesendet += await _deregister_deleted(client, state, {zone.id for zone in zones})
    for zone in zones:
        gesendet += await _send_zone_state(session, client, zone, praefix, now)
    gesendet += await _send_controller_channels(session, client, state, get_settings().mqtt_base_topic, now)
    return gesendet


async def _register_zone(
    session: Session,
    client: MqttPublisher,
    state: PublicationState,
    zone: Zone,
    praefix: str,
) -> int:
    gesendet = 0
    gemeldet: list[str] = []
    for message in _discovery_messages(session, zone, praefix):
        if await client.publishing(
            message.topic, message.payload, switches=False, retained=True
        ):
            gemeldet.append(message.topic)
            gesendet += 1
    if gemeldet:
        state.angemeldet[zone.id] = gemeldet
        log.info(
            "Zone bei Home Assistant angemeldet",
            extra={"zone_id": zone.id, "entitaeten": len(gemeldet)},
        )
    return gesendet


async def _deregister_deleted(
    client: MqttPublisher,
    state: PublicationState,
    vorhandene: set[int],
) -> int:
    """The only reason to deregister: the zone doesn't exist anymore.

    Without this, a thermostat that nobody operates anymore would stay behind in Home
    Assistant — it would keep showing the last known value forever.
    """
    gesendet = 0
    for zone_id in sorted(set(state.angemeldet) - vorhandene):
        for topic in state.angemeldet[zone_id]:
            if await client.publishing(topic, "", switches=False, retained=True):
                gesendet += 1
        del state.angemeldet[zone_id]
        log.info("Geloeschte Zone bei Home Assistant abgemeldet", extra={"zone_id": zone_id})
    return gesendet


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
    if channel.zone_id is None:
        return None
    zone = session.get(Zone, channel.zone_id)
    if zone is None:  # pragma: no cover - the foreign key prevents this
        return None
    if kind.code == "zone_temperature":
        zone_state = session.get(ZoneState, zone.id)
        return zone_state.temperature_c if zone_state is not None else None
    if kind.code == "zone_setpoint":
        return resolved_setpoint(session, zone, now).temperature_c
    return None


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


def _wuerde_heizen(session: Session, zone_id: int) -> bool | None:
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
    praefix: str,
    now: datetime,
) -> int:
    """All state values of **one** zone.

    Callable individually, because a command from Home Assistant needs an immediate
    response: the climate card there is not optimistic, it waits for the state. If it
    only arrived on the next control cycle, the mode just chosen would jump back to the
    old one for a minute — and looked as if mode selection didn't work.
    """
    topics = states_topics(zone.id, praefix)
    state = session.get(ZoneState, zone.id)
    statuscode = "keine_quelle"
    if state is not None:
        statuscode = (
            session.scalar(
                select(SensorStatus.code).where(SensorStatus.id == state.sensor_status_id)
            )
            or statuscode
        )
    setpoint = resolved_setpoint(session, zone, now)
    values: list[tuple[str, str]] = [
        (topics.current_temperature, _als_text(state.temperature_c if state else None)),
        (topics.setpoint, _als_text(setpoint.temperature_c)),
        (topics.operating_mode, zone.operating_mode.code),
        (topics.sensor_state, statuscode),
        (topics.wuerde_heizen, _als_text(_wuerde_heizen(session, zone.id))),
        (topics.last_switch, _als_text(_last_switch(session, zone.id))),
        (topics.next_switch, _als_text(end_of_next_switch(session, zone, now))),
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
            (mode_topics(zone.id, mode.id, praefix)[0], _als_text(setpoints.get(mode.id)))
        )

    wirksam = control_parameters(session, zone)
    for description in PARAMETERS:
        values.append(
            (
                parameter_topics(zone.id, description.name, praefix)[0],
                _als_text(getattr(wirksam, description.name)),
            )
        )

    gesendet = 0
    for topic, value in values:
        # An empty value is not sent: in MQTT an empty payload deletes a retained
        # message, and "no reading yet" is something different from "this value
        # doesn't exist anymore".
        if value and await client.publishing(
            topic, value, switches=False, retained=True
        ):
            gesendet += 1
    return gesendet
