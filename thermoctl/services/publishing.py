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

**`send_fault_notice` below is deliberately not gated by `domain.fault_notice.
notice_enabled`.** That switch governs the generic delivery path (log and webhook,
`integrations/notification.py`); Home Assistant is a separate integration with its
own visibility into the plant regardless of whether the operator also wants a
webhook call for the same event, and turning the webhook off must not blind Home
Assistant as a side effect. The gate is applied once, by the caller
(`app.py::_shadow_loop`), only in front of the webhook path.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl import audit
from thermoctl.config import get_settings
from thermoctl.db.models.device import ControllerChannel, Device
from thermoctl.db.models.lookup import ChannelKind, DeviceCapability, SensorStatus
from thermoctl.db.models.measurement import Measurement
from thermoctl.db.models.operations import Setting
from thermoctl.db.models.state import ShadowDecision, ZoneState
from thermoctl.db.models.zone import SetpointMode, Zone, ZoneSetpoint
from thermoctl.domain.controller_channels import may_be_written
from thermoctl.domain.fault_notice import (
    FaultNotice,
    command_failure_notice,
    notification_audit_action,
)
from thermoctl.domain.schedule import end_of_next_switch, resolved_setpoint
from thermoctl.domain.self_regulating import SETPOINT_PROPERTY, valve_commands
from thermoctl.domain.switch_commands import switch_commands, thermostat_commands
from thermoctl.domain.zone_settings import PARAMETERS, control_parameters
from thermoctl.integrations.actuators import (
    Actuator,
    MerossSwitch,
    MqttPublisher,
    SwitchResult,
    Zigbee2MqttThermostat,
    Zigbee2MqttValve,
    switching_allowed,
    thermostat_payload,
)
from thermoctl.integrations.meross_mqtt import MerossCommandTransport, toggle_payload
from thermoctl.integrations.mqtt.publication import (
    DiscoveryMessage,
    armed_discovery,
    armed_topic,
    availability_topic,
    boost_discovery,
    fault_notice_discovery,
    fault_notice_topics,
    mode_discovery,
    mode_topics,
    parameter_discovery,
    parameter_topics,
    states_topics,
    timestamp_discovery,
    zone_discovery,
)
from thermoctl.services.device_commands import EXECUTED, FAILED, SUPPRESSED, record_command
from thermoctl.services.meross_session import MerossSessionCache
from thermoctl.services.meross_session import invalidate as invalidate_meross_session

log = logging.getLogger(__name__)


def _finish_database_work(session: Session) -> None:
    """Close the current transaction before waiting for an external system."""
    if session.in_transaction():
        session.commit()


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
    # Per self-regulating valve, the (payload, armed, outcome) last **logged** --
    # sent, withheld, or attempted and failed. `outcome` had to join this key
    # because a matching (payload, armed) alone
    # used to be treated as "nothing to do", which silently also swallowed a
    # *failed* attempt -- a broken broker connection then froze a zone's actuator
    # forever, because the boolean decision that would unstick it does not usually
    # change on its own. `outcome` fixes that: only a matching `EXECUTED` entry
    # means "already achieved, skip both the send and the log". Anything else
    # (never tried, or the last attempt failed) is retried on every armed cycle --
    # but a *repeated identical* outcome for the same command is still not logged
    # again, so a permanently unreachable device gets exactly one log entry per
    # failure episode, not one per minute. `armed` stays part of the key for the
    # same reason it always was: the same setpoint withheld during a dry run must
    # go out for real, and be logged again, the moment the plant is armed.
    valve_commands: dict[int, tuple[str, bool, str]] = field(default_factory=dict)
    # Per ordinary (non-self-regulating) actuator, the (heating, armed, outcome)
    # last **logged**. Same reasoning as `valve_commands` above.
    switch_commands: dict[int, tuple[bool, bool, str]] = field(default_factory=dict)
    # Per device, whether its last *attempted* switching command (valve or
    # ordinary actuator, any command kind) failed -- independent of `valve_commands`
    # and `switch_commands` above, which are keyed on the full `(payload, armed,
    # outcome)` tuple and therefore reset their own dedup whenever the setpoint or
    # the armed flag changes. A "Schaltbefehl gescheitert" notice must fire on the
    # transition itself even when those change at the same time, so it is tracked
    # here on its own, by `_note_command_outcome` below. Absent means "no attempt
    # made yet in this process" -- the same "not known to be failing" starting
    # point `domain.fault_notice.command_failure_notice` already treats `None` as.
    command_failures: dict[int, bool] = field(default_factory=dict)


def _note_command_outcome(
    state: PublicationState,
    notices: list[FaultNotice],
    session: Session,
    device: Device,
    outcome: str,
    setting_row: Setting | None,
) -> None:
    """Turns one switching attempt's outcome into a "Schaltbefehl gescheitert"
    notice, on the transition only -- called at every point in this module that
    just attempted (or deliberately withheld) a command towards `device`.

    `SUPPRESSED` (dry run, or an integration this version cannot switch through at
    all) leaves `state.command_failures` untouched: nothing was actually attempted,
    so nothing was learned about whether the device is currently reachable, and a
    disarmed plant must not silently clear -- or raise -- an alert about hardware it
    never touched.

    `setting_row` decides the audit action via `notification_audit_action` --
    "sent" only when the notice kind is actually switched on, "suppressed"
    otherwise. The audit entry itself is still written either way: that the
    command failed belongs in the log regardless of whether anyone was told.
    """
    if outcome == SUPPRESSED:
        return
    before_failed = state.command_failures.get(device.id)
    after_failed = outcome == FAILED
    notice = command_failure_notice(
        f"schaltbefehl:{device.id}", device.display_name, before_failed, after_failed
    )
    state.command_failures[device.id] = after_failed
    if notice is not None:
        audit.record(
            session,
            source="system",
            action=notification_audit_action(notice.kind, setting_row),
            object_type="fault",
            object_id=notice.key,
            summary=notice.title,
            detail=notice.text,
        )
        notices.append(notice)


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
        fault_notice_discovery(zone.id, name, prefix),
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


async def send_fault_notice(
    client: MqttPublisher, notice: FaultNotice, prefix: str
) -> None:
    """Publishes a transition for Home Assistant without propagating failures.

    This is intentionally called after the control transaction has closed. Both
    messages are non-switching and bypass the actuator latches: a dry run must still
    report why its computed control behavior changed.
    """
    try:
        zone_id = int(notice.key.removeprefix("sensor:"))
        topics = fault_notice_topics(zone_id, prefix)
        state = "ON" if notice.severity == "stoerung" else "OFF"
        attributes = json.dumps(
            {
                "schluessel": notice.key,
                "schwere": notice.severity,
                "titel": notice.title,
                "text": notice.text,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        await client.publishing(
            topics.attributes, attributes, switches=False, retained=True
        )
        await client.publishing(topics.state, state, switches=False, retained=True)
    except Exception:
        log.exception(
            "Störungsmeldung konnte nicht an Home Assistant gesendet werden",
            extra={"schluessel": notice.key},
        )


async def cycle(
    session: Session,
    client: MqttPublisher,
    state: PublicationState,
    prefix: str,
    now: datetime,
    *,
    source: str = "system",
    meross_transport: MerossCommandTransport | None = None,
    meross_session_cache: MerossSessionCache | None = None,
    meross_switching_allowed: bool = False,
    notices: list[FaultNotice] | None = None,
) -> int:
    """One publication cycle. Returns the number of messages sent.

    `notices` collects any "Schaltbefehl gescheitert" transition raised this
    cycle -- appended to in place, the same object the caller passed in, so the
    return type stays `int` for every existing caller that does not care.
    `app.py::_shadow_loop` passes its own list and dispatches what lands in it
    after this whole cycle's `session_scope` has closed, exactly like the sensor
    and bridge notices it already collects the same way.

    `meross_transport` is signed in (or not) before this function is ever called --
    see `app.py`'s `_shadow_loop` and `services/meross_session.py`. Passing `None`
    here (no account configured, or the cloud refused the sign-in) is not this
    function's problem to solve: every attempt to reach a Meross actuator this cycle
    is recorded as `failed` in the command log, same as any other failed send.
    `meross_session_cache`, when given, is where a failed attempt is flagged so the
    *next* cycle signs in again rather than trusting a connection already known bad.

    `meross_switching_allowed` is the frozen, start-of-process bolt for the Meross
    path -- see `MerossSwitch`'s docstring in `integrations/actuators.py`. Defaults
    to `False` (dry run), the same safe default `MqttClient.__init__` chooses for
    its own `switching_allowed`: a caller that forgets to pass this must get a
    plant that never really switches, not one that does. `app.py` passes
    `app.state.sending_allowed` here -- the very same value already frozen for
    `MqttClient` at startup, since both bolts exist to answer the identical
    question ("was the plant armed when this process started").
    """
    armed = switching_allowed(session)
    zones = list(session.scalars(select(Zone).order_by(Zone.id)))
    sent_count = 0
    notice_sink: list[FaultNotice] = [] if notices is None else notices
    # Fetched once per cycle, not once per notice: the audit action
    # (`notification_audit_action`) needs to know whether the notice kind is
    # switched on at the moment the fault is recorded, and `setting` does not
    # change mid-cycle.
    setting_row = session.get(Setting, 1)

    # Availability first: it's the statement "whatever comes next is current".
    _finish_database_work(session)
    if await client.publishing(
        availability_topic(prefix), "online", switches=False, retained=True
    ):
        sent_count += 1

    if not state.service_registered:
        message = armed_discovery(prefix)
        _finish_database_work(session)
        if await client.publishing(
            message.topic, message.payload, switches=False, retained=True
        ):
            state.service_registered = True
            sent_count += 1
    _finish_database_work(session)
    if await client.publishing(
        armed_topic(prefix), _as_text(armed), switches=False, retained=True
    ):
        sent_count += 1

    for zone in zones:
        sent_count += await _register_zone(session, client, state, zone, prefix)

    sent_count += await _deregister_deleted(client, state, {zone.id for zone in zones})
    for zone in zones:
        sent_count += await _send_zone_state(session, client, zone, prefix, now)
    sent_count += await _send_controller_channels(session, client, state, get_settings().mqtt_base_topic, now)
    for zone in zones:
        sent_count += await _send_self_regulating_valves(
            session,
            client,
            state,
            get_settings().mqtt_base_topic,
            zone,
            now,
            source,
            notice_sink,
            setting_row,
        )
    for zone in zones:
        sent_count += await _send_actuator_switches(
            session,
            client,
            state,
            get_settings().mqtt_base_topic,
            zone,
            now,
            source,
            meross_transport,
            meross_session_cache,
            meross_switching_allowed,
            notice_sink,
            setting_row,
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
    notices: list[FaultNotice],
    setting_row: Setting | None,
) -> int:
    """Tells every self-regulating valve of this zone what to aim for.

    **These messages move a valve.** They carry `switches=True` and go through
    `switching_allowed` -- the same two bolts as an on/off command, because the
    physical effect is the same. A setpoint written to a thermostatic valve is not a
    display value, and treating it as one would be a way around the dry run.

    Only what changed is sent -- and logged, but the two are no longer gated by the
    same test. `state.valve_commands` holds the `(payload, armed, outcome)` last
    *logged* for this device (see the field's docstring on `PublicationState` for
    why `outcome` had to join the key). Skipping the send entirely only happens when
    the last logged outcome for this exact `(payload, armed)` was `EXECUTED` --
    already achieved, nothing to do. Any other last outcome (never tried, or a
    failed attempt) means this cycle tries again -- a stuck actuator must keep being
    given the chance to recover once the broker or the device comes back. The log
    entry itself is still deduplicated: a repeated identical outcome for the same
    command is not written again, so a persistently unreachable device produces one
    log entry per failure episode, not one every cycle.
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
        last_entry = state.valve_commands.get(command.device.id)

        if not armed:
            new_entry: tuple[str, bool, str] = (payload, armed, SUPPRESSED)
            if last_entry != new_entry:
                state.valve_commands[command.device.id] = new_entry
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

        if last_entry == (payload, armed, EXECUTED):
            # Already achieved for this exact setpoint -- nothing to send, nothing
            # to log.
            continue

        outcome: str
        error: str | None
        try:
            _finish_database_work(session)
            executed = await client.publishing(topic, payload, switches=True)
        except Exception as exc:
            # A broken broker connection must not abort the whole cycle -- other
            # zones and the rest of this cycle (shadow decisions, retention) still
            # need to run. The failure is recorded instead of raised.
            outcome, error = FAILED, str(exc)
        else:
            outcome, error = (
                (EXECUTED, None)
                if executed
                else (FAILED, "MQTT-Client hat die Veroeffentlichung abgewiesen")
            )

        _note_command_outcome(state, notices, session, command.device, outcome, setting_row)

        new_entry = (payload, armed, outcome)
        if new_entry == last_entry:
            # The attempt above still happened -- that is the fix: a stuck actuator
            # keeps being retried every cycle. But the outcome is unchanged from
            # what is already on record, and writing an identical log line every
            # cycle would bury the one fact that matters (when the failure began)
            # in noise within a day.
            continue
        state.valve_commands[command.device.id] = new_entry
        if outcome == EXECUTED:
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
                error=error,
                reason=command.reason,
            )
    return sent


# Integration codes an ordinary (switch-capability) actuator can actually be switched
# through today.
#
# Meross switches through a `MerossCommandTransport` that must already be signed in --
# `app.py`'s `_shadow_loop` gets one from `services/meross_session.py::ensure_transport`
# *before* the transaction this cycle runs in ever opens, and hands it in here as
# `meross_transport` (`None` when no account is configured or the cloud refused the
# sign-in). That split exists because signing in is itself an HTTP call to the
# manufacturer's cloud: an earlier version awaited it from inside this same
# `session_scope` and locked the whole SQLite file for its duration, answering
# unrelated requests with 500 and 401 (`_run_detached_meross_refresh`'s docstring in
# `app.py` carries the same lesson for the device-list reconciliation).
_WIRED_INTEGRATIONS = frozenset({"zigbee2mqtt", "meross"})


def _outcome_of(result: SwitchResult) -> str:
    """The command-log outcome code a `SwitchResult` maps to.

    A `None` error means the adapter withheld the command itself, at the dry-run
    bolt -- that is `SUPPRESSED`, not `FAILED`. Anything else that did not execute
    carries a real error and is `FAILED`.
    """
    if result.executed:
        return EXECUTED
    if result.errors is None:
        return SUPPRESSED
    return FAILED


def _record_switch_outcome(
    session: Session,
    *,
    now: datetime,
    source: str,
    zone: Zone,
    device: Device,
    command_name: str,
    payload: str,
    result: SwitchResult,
    reason: str,
) -> bool:
    """Writes one command-log entry for a switching attempt. Returns whether it sent.

    Shared between the `switch` and `thermostat` command kinds below -- both adapters
    report the same three outcomes through `SwitchResult`, and the command log does
    not need to know which kind of device it was told about.
    """
    outcome = _outcome_of(result)
    record_command(
        session,
        now=now,
        source=source,
        zone=zone,
        device=device,
        command=command_name,
        payload=payload,
        outcome=outcome,
        error=result.errors if outcome == FAILED else None,
        reason=reason,
    )
    return outcome == EXECUTED


def _latest_decision(session: Session, zone_id: int) -> tuple[bool, str] | None:
    """The most recent `heating`/`reason` this zone's control loop decided on.

    Not passed in: `shadow_run.cycle()` and this publication cycle are two separate
    calls (see `app.py::_shadow_loop`), the same reason `_would_heat` above reads it
    back from the database rather than taking it as a parameter. `None` when no
    decision has ever been logged for this zone -- a zone the control loop has never
    run for has nothing here to act on, and inventing a state (heating or not) would
    be a decision this module has no business making.
    """
    row = session.execute(
        select(ShadowDecision.would_heat, ShadowDecision.reason)
        .where(ShadowDecision.zone_id == zone_id)
        .order_by(ShadowDecision.decided_at.desc(), ShadowDecision.id.desc())
        .limit(1)
    ).first()
    return (row.would_heat, row.reason) if row is not None else None


async def _send_actuator_switches(
    session: Session,
    client: MqttPublisher,
    state: PublicationState,
    base: str,
    zone: Zone,
    now: datetime,
    source: str,
    meross_transport: MerossCommandTransport | None,
    meross_session_cache: MerossSessionCache | None,
    meross_switching_allowed: bool,
    notices: list[FaultNotice],
    setting_row: Setting | None,
) -> int:
    """Turns every ordinary (non-self-regulating) actuator of a zone on or off.

    The decision comes from `regelung.entscheiden()`, by way of the zone's latest
    `shadow_decision` row (`_latest_decision` above) -- hysteresis, minimum
    switching duration, window-open and the valve protection run are already
    resolved into `would_heat` there; this function only carries that decision to
    the device, unchanged, exactly as instructed. Reaching an adapter goes through
    `switching_allowed` (`integrations/actuators.py`) inside its own `switching()` --
    the same dry-run bolt `_send_self_regulating_valves` goes through, because the
    physical effect is the same. The MQTT client's own second bolt applies on top of
    that, unconditionally, inside `client.publishing()`.

    Two kinds of ordinary actuator, covered in turn below: a plain on/off switch
    (`switch_commands()`, `Zigbee2MqttValve` or `MerossSwitch`) and a Zigbee2MQTT
    thermostatic valve run by thermoctl's own hysteresis instead of its own regulation
    loop (`thermostat_commands()`, `Zigbee2MqttThermostat`) -- the latter has no on/off
    output at all, so `switching(True)` also needs the zone's current target
    (`resolved_setpoint()`, the same source `domain/self_regulating.py` uses for a
    self-regulating valve).

    A repeated identical outcome for the same decision is still not logged (or, for
    a settled `EXECUTED` outcome, not even attempted) again -- see
    `PublicationState.switch_commands`, and `_send_self_regulating_valves` above for
    the same reasoning spelled out for the self-regulating case. Any *other* last
    outcome -- never tried, or a failed attempt -- is retried on every armed cycle
    regardless: a relay stuck on `failed` must keep being given the chance to
    recover once the broker, or the Meross cloud, comes back, and finding A's fix
    for `_send_self_regulating_valves` applies here identically. Because that cache
    starts empty after every restart, the first cycle after a restart always acts
    (sends for real once armed, logs a withheld attempt otherwise) for every
    actuator, regardless of what was last sent before the restart: **what a real
    device is currently doing is not remembered across a restart, and a relay or
    valve left in an earlier state without being told again would silently stay
    there.** The alternative -- staying silent until the decision changes -- trades
    that for fewer redundant commands after every restart; deliberately not chosen
    here, for the same reason `_send_self_regulating_valves` and the Home Assistant
    registration above already resend unconditionally after one.

    `meross_switching_allowed` is the Meross path's own frozen, start-of-process
    bolt -- the counterpart of `MqttClient`'s `_switching_allowed` for the
    Zigbee2MQTT path, which `client.publishing()` already enforces unconditionally.
    Without an equivalent here, the runtime-only `switching_allowed(session)` check
    inside `MerossSwitch.switching()` would be the *only* bolt on the one path that
    drives real hardware today -- a single forgotten caller away from switching
    live, exactly the risk `MqttClient.__init__`'s docstring gives as the reason for
    having two.
    """
    armed = switching_allowed(session)
    sent = 0
    latest = _latest_decision(session, zone.id)
    if latest is None:
        return 0
    heating, reason = latest

    for command in switch_commands(session, zone):
        device = command.device
        last_entry = state.switch_commands.get(device.id)

        if command.integration_code not in _WIRED_INTEGRATIONS:
            outcome = SUPPRESSED if not armed else FAILED
            _note_command_outcome(state, notices, session, device, outcome, setting_row)
            new_entry = (heating, armed, outcome)
            if new_entry == last_entry:
                continue
            state.switch_commands[device.id] = new_entry
            log.error(
                "Aktor an nicht verdrahteter Anbindung wird nicht geschaltet",
                extra={
                    "zone_id": zone.id,
                    "geraet": device.display_name,
                    "anbindung": command.integration_code,
                },
            )
            record_command(
                session,
                now=now,
                source=source,
                zone=zone,
                device=device,
                command="switch",
                payload=json.dumps({"state": "ON" if heating else "OFF"}),
                outcome=outcome,
                error=(
                    None
                    if not armed
                    else (
                        f"Anbindung {command.integration_code!r} ist fuer Schaltbefehle "
                        "in dieser Fassung nicht verdrahtet"
                    )
                ),
                reason=reason,
            )
            continue

        if last_entry == (heating, armed, EXECUTED):
            continue

        actuator: Actuator
        if command.integration_code == "zigbee2mqtt":
            actuator = Zigbee2MqttValve(session, client, base, device.external_id)
            payload = json.dumps({"state": "ON" if heating else "OFF"})
        else:
            # The only other member of `_WIRED_INTEGRATIONS`. `meross_transport` is
            # `None` when no account is configured or the cloud rejected the sign-in
            # this cycle -- `MerossSwitch.switching()` reports that as a failed
            # attempt on its own, it does not need a special case here.
            actuator = MerossSwitch(
                session,
                meross_transport,
                device.external_id,
                frozen_switching_allowed=meross_switching_allowed,
            )
            payload = json.dumps(toggle_payload(0, heating))

        _finish_database_work(session)
        result = await actuator.switching(heating)
        outcome = _outcome_of(result)
        if (
            command.integration_code == "meross"
            and not result.executed
            and result.errors is not None
            and meross_session_cache is not None
        ):
            # A real attempt was made (not just withheld by a dry-run bolt) and it
            # did not work -- the cached connection might be the reason, so the next
            # cycle signs in again instead of trusting it for the rest of its TTL.
            invalidate_meross_session(meross_session_cache)
        _note_command_outcome(state, notices, session, device, outcome, setting_row)
        new_entry = (heating, armed, outcome)
        if new_entry == last_entry:
            # Same outcome already on record for this decision -- the attempt above
            # still happened (the retry that fixes finding A), but logging it again
            # would only repeat what is already known.
            continue
        state.switch_commands[device.id] = new_entry
        if _record_switch_outcome(
            session,
            now=now,
            source=source,
            zone=zone,
            device=device,
            command_name="switch",
            payload=payload,
            result=result,
            reason=reason,
        ):
            sent += 1
            log.info(
                "Aktor geschaltet",
                extra={
                    "zone_id": zone.id,
                    "geraet": device.display_name,
                    "zustand": "an" if heating else "aus",
                    "begruendung": reason,
                },
            )

    thermostat_setpoint: Decimal | None = None
    for thermostat_command in thermostat_commands(session, zone):
        device = thermostat_command.device
        last_entry = state.switch_commands.get(device.id)

        if thermostat_command.integration_code != "zigbee2mqtt":
            # Not observed on real hardware today (only Zigbee2MQTT reports the
            # `thermostat` capability, see `services/ingest.py`), guarded anyway so a
            # future integration cannot end up silently unswitched the way the
            # blocker this replaces once did.
            outcome = SUPPRESSED if not armed else FAILED
            _note_command_outcome(state, notices, session, device, outcome, setting_row)
            new_entry = (heating, armed, outcome)
            if new_entry == last_entry:
                continue
            state.switch_commands[device.id] = new_entry
            log.error(
                "Thermostatventil an nicht verdrahteter Anbindung wird nicht geschaltet",
                extra={
                    "zone_id": zone.id,
                    "geraet": device.display_name,
                    "anbindung": thermostat_command.integration_code,
                },
            )
            record_command(
                session,
                now=now,
                source=source,
                zone=zone,
                device=device,
                command="thermostat",
                payload=json.dumps({"heating": heating}),
                outcome=outcome,
                error=(
                    None
                    if not armed
                    else (
                        f"Anbindung {thermostat_command.integration_code!r} ist fuer "
                        "Thermostatbefehle in dieser Fassung nicht verdrahtet"
                    )
                ),
                reason=reason,
            )
            continue

        if last_entry == (heating, armed, EXECUTED):
            continue

        if thermostat_setpoint is None:
            thermostat_setpoint = resolved_setpoint(session, zone, now).temperature_c

        actuator = Zigbee2MqttThermostat(
            session,
            client,
            base,
            device.external_id,
            thermostat_setpoint,
            has_system_mode=thermostat_command.has_system_mode,
        )
        payload = json.dumps(
            thermostat_payload(
                heating,
                thermostat_setpoint,
                has_system_mode=thermostat_command.has_system_mode,
            )
        )
        _finish_database_work(session)
        result = await actuator.switching(heating)
        outcome = _outcome_of(result)
        _note_command_outcome(state, notices, session, device, outcome, setting_row)
        new_entry = (heating, armed, outcome)
        if new_entry == last_entry:
            continue
        state.switch_commands[device.id] = new_entry
        if _record_switch_outcome(
            session,
            now=now,
            source=source,
            zone=zone,
            device=device,
            command_name="thermostat",
            payload=payload,
            result=result,
            reason=reason,
        ):
            sent += 1
            log.info(
                "Thermostatventil geschaltet",
                extra={
                    "zone_id": zone.id,
                    "geraet": device.display_name,
                    "zustand": "an" if heating else "aus",
                    "sollwert": str(thermostat_setpoint),
                    "begruendung": reason,
                },
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
    reported = state.registered.setdefault(zone.id, [])
    for message in _discovery_messages(session, zone, prefix):
        if message.topic in reported:
            continue
        _finish_database_work(session)
        if await client.publishing(
            message.topic, message.payload, switches=False, retained=True
        ):
            reported.append(message.topic)
            sent_count += 1
    if sent_count:
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
        _finish_database_work(session)
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
        if value:
            _finish_database_work(session)
        if value and await client.publishing(topic, value, switches=False, retained=True):
            sent_count += 1
    return sent_count
