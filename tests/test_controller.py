"""Controllers: what a button press on the wall does.

The core of it is that **nothing here is guessed**. What a device calls its
buttons is decided per model by Zigbee2MQTT; the service records what
actually arrived, and the binding lives in the database. These tests check
both halves: listening and executing.
"""

import json
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.helpers import (
    create_device,
    create_mode,
    create_settings,
    create_zone,
    role,
    source,
)
from thermoctl.db.models.device import ControllerBinding, ZoneDevice
from thermoctl.db.models.lookup import ControllerCommand, DeviceCapability
from thermoctl.db.models.measurement import Measurement
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.zone import ZoneSetpoint
from thermoctl.domain.controller import (
    DEFAULT_STEP_K,
    ControllerError,
    execute_aktion,
    gesehene_aktionen,
    set_binding,
)
from thermoctl.domain.schedule import resolved_setpoint
from thermoctl.services.ingest import process_message

MONDAY_EIGHT = datetime(2026, 8, 31, 8, 0)


def _commands(session: Session) -> None:
    """The lookup table that a migration fills in a real installation."""
    from thermoctl.db.models.lookup import CONTROLLER_COMMANDS

    for code, label in CONTROLLER_COMMANDS:
        session.add(ControllerCommand(code=code, label=label))
    session.add(DeviceCapability(code="action", label="Tastendruck"))
    session.flush()


def _installation(session: Session):
    """A zone with a schedule and a controller attached to it."""
    create_settings(session).timezone = "UTC"
    source(session, "system")
    _commands(session)
    zone = create_zone(session, "wandzone")
    day = create_mode(session, "tag")
    night = create_mode(session, "nacht")
    session.add_all(
        [
            SchedulePoint(zone_id=zone.id, weekday=1, minute_of_day=0, setpoint_mode_id=day.id),
            SchedulePoint(
                zone_id=zone.id, weekday=1, minute_of_day=1320, setpoint_mode_id=night.id
            ),
            ZoneSetpoint(zone_id=zone.id, setpoint_mode_id=day.id, temperature_c=Decimal("21.0")),
            ZoneSetpoint(
                zone_id=zone.id, setpoint_mode_id=night.id, temperature_c=Decimal("18.0")
            ),
        ]
    )
    device = create_device(session, "wandschalter")
    session.add(
        ZoneDevice(
            zone_id=zone.id,
            device_id=device.id,
            device_role_id=role(session, "controller").id,
        )
    )
    session.flush()
    return zone, device


def test_a_bound_button_changes_the_active_mode(session: Session) -> None:
    """Not as an override: that would be gone after the next schedule point,
    and the room would cool down again on its own."""
    zone, device = _installation(session)
    set_binding(session, device, "single_plus", "setpoint_up")

    affected = execute_aktion(session, device, "single_plus", MONDAY_EIGHT)

    assert affected == [zone.name]
    assert resolved_setpoint(session, zone, MONDAY_EIGHT).temperature_c == Decimal("21.5")


def test_the_step_size_can_be_set_per_button(session: Session) -> None:
    zone, device = _installation(session)
    set_binding(session, device, "hold_minus", "setpoint_down", Decimal("2.0"))

    execute_aktion(session, device, "hold_minus", MONDAY_EIGHT)

    assert resolved_setpoint(session, zone, MONDAY_EIGHT).temperature_c == Decimal("19.0")
    # Counter-check: without its own step size, the default applies.
    set_binding(session, device, "single_minus", "setpoint_down")
    execute_aktion(session, device, "single_minus", MONDAY_EIGHT)
    assert resolved_setpoint(session, zone, MONDAY_EIGHT).temperature_c == (
        Decimal("19.0") - DEFAULT_STEP_K
    )


def test_boost_and_operating_mode_can_be_bound_to_buttons(session: Session) -> None:
    zone, device = _installation(session)
    from tests.helpers import operating_mode

    operating_mode(session, "off")
    set_binding(session, device, "single_center", "boost")
    set_binding(session, device, "hold_center", "mode_off")

    execute_aktion(session, device, "single_center", MONDAY_EIGHT)
    assert resolved_setpoint(session, zone, MONDAY_EIGHT).reason.startswith("Uebersteuerung")

    execute_aktion(session, device, "hold_center", MONDAY_EIGHT)
    assert zone.operating_mode.code == "off"

    # And back again -- otherwise the button would be a one-way street.
    set_binding(session, device, "double_center", "mode_auto")
    execute_aktion(session, device, "double_center", MONDAY_EIGHT)
    assert zone.operating_mode.code == "auto"


def test_an_unbound_button_does_nothing_and_is_not_an_error(session: Session) -> None:
    """Most devices send more actions than anyone wants to bind -- every hold
    and every release. A warning per press would be noise."""
    zone, device = _installation(session)
    before = resolved_setpoint(session, zone, MONDAY_EIGHT).temperature_c

    assert execute_aktion(session, device, "release_plus", MONDAY_EIGHT) == []
    assert resolved_setpoint(session, zone, MONDAY_EIGHT).temperature_c == before


def test_a_controller_without_a_zone_does_nothing(session: Session) -> None:
    """The most common reason 'the button does nothing' -- and therefore logged."""
    create_settings(session)
    source(session, "system")
    _commands(session)
    device = create_device(session, "herrenloser-schalter")
    set_binding(session, device, "single_plus", "setpoint_up")

    assert execute_aktion(session, device, "single_plus", MONDAY_EIGHT) == []


def test_seen_actions_come_from_what_actually_arrived(session: Session) -> None:
    """The core of it: nobody guesses what a model calls its buttons."""
    zone, device = _installation(session)
    process_message(
        session,
        f"zigbee2mqtt/{device.external_id}",
        json.dumps({"action": "button_1_single", "battery": 90}).encode(),
        base="zigbee2mqtt",
        received_at=MONDAY_EIGHT,
    )

    actions = gesehene_aktionen(session, device)

    assert [a.aktion for a in actions] == ["button_1_single"]
    assert actions[0].command_code is None
    assert actions[0].last_seen is not None


def test_a_bound_button_stays_visible_without_a_fresh_press(session: Session) -> None:
    """Otherwise a working binding would disappear from the interface as
    soon as measurement cleanup deleted the last press."""
    _zone, device = _installation(session)
    set_binding(session, device, "nie_wieder_gedrueckt", "boost")

    actions = gesehene_aktionen(session, device)

    assert [a.aktion for a in actions] == ["nie_wieder_gedrueckt"]
    assert actions[0].command_name == "Nächste Schaltung vorziehen"
    assert actions[0].last_seen is None


def test_a_button_press_from_a_real_message_takes_effect(session: Session) -> None:
    """The whole path: MQTT message, measurement, execution."""
    zone, device = _installation(session)
    set_binding(session, device, "single_plus", "setpoint_up")

    process_message(
        session,
        f"zigbee2mqtt/{device.external_id}",
        json.dumps({"action": "single_plus"}).encode(),
        base="zigbee2mqtt",
        received_at=MONDAY_EIGHT,
    )

    assert resolved_setpoint(session, zone, MONDAY_EIGHT).temperature_c == Decimal("21.5")


def test_the_same_message_twice_takes_effect_only_once(session: Session) -> None:
    """A retained message is redelivered on **every** reconnect. Without this
    guard, a flaky network connection would trigger the same button press
    over and over — and a boost nobody pressed only becomes apparent once
    the room is too warm.
    """
    zone, device = _installation(session)
    set_binding(session, device, "single_plus", "setpoint_up")
    payload = json.dumps(
        {"action": "single_plus", "last_seen": "2026-08-31T08:00:00Z"}
    ).encode()

    for _ in range(3):
        process_message(
            session,
            f"zigbee2mqtt/{device.external_id}",
            payload,
            base="zigbee2mqtt",
            received_at=MONDAY_EIGHT,
        )

    assert resolved_setpoint(session, zone, MONDAY_EIGHT).temperature_c == Decimal("21.5")


def test_a_later_press_takes_effect_again(session: Session) -> None:
    """Counter-check for the guard above: it must not block every repeat.

    Someone pressing 'warmer' twice means two steps -- otherwise the guard
    would be worse than the problem.
    """
    zone, device = _installation(session)
    set_binding(session, device, "single_plus", "setpoint_up")

    for offset in (0, 1, 2):
        moment = MONDAY_EIGHT + timedelta(minutes=offset)
        process_message(
            session,
            f"zigbee2mqtt/{device.external_id}",
            json.dumps(
                {"action": "single_plus", "last_seen": moment.isoformat() + "Z"}
            ).encode(),
            base="zigbee2mqtt",
            received_at=moment,
        )

    assert resolved_setpoint(session, zone, MONDAY_EIGHT).temperature_c == Decimal("22.5")


def test_every_button_press_is_stored_as_a_measurement(session: Session) -> None:
    """It is the basis for setup -- and the answer to 'does anything even arrive?'."""
    _zone, device = _installation(session)
    process_message(
        session,
        f"zigbee2mqtt/{device.external_id}",
        json.dumps({"action": "double_center"}).encode(),
        base="zigbee2mqtt",
        received_at=MONDAY_EIGHT,
    )

    capability_id = session.scalar(
        select(DeviceCapability.id).where(DeviceCapability.code == "action")
    )
    values = session.scalars(
        select(Measurement.value_text).where(
            Measurement.device_id == device.id, Measurement.capability_id == capability_id
        )
    ).all()
    assert list(values) == ["double_center"]


def test_a_binding_can_be_deleted_again(session: Session) -> None:
    _zone, device = _installation(session)
    set_binding(session, device, "single_plus", "setpoint_up")
    set_binding(session, device, "single_plus", "boost", Decimal("1.0"))

    assert session.scalars(select(ControllerBinding)).one().step_k == Decimal("1.0")

    set_binding(session, device, "single_plus", None)
    assert session.scalars(select(ControllerBinding)).all() == []
    # And deleting a second time is not an error.
    set_binding(session, device, "single_plus", None)


def test_unusable_bindings_are_rejected(session: Session) -> None:
    _zone, device = _installation(session)
    with pytest.raises(ControllerError, match="gibt es nicht"):
        set_binding(session, device, "single_plus", "gibtsnicht")
    with pytest.raises(ControllerError, match="groesser als null"):
        set_binding(session, device, "single_plus", "setpoint_up", Decimal(0))
    with pytest.raises(ControllerError, match="Nachkommastelle"):
        set_binding(session, device, "single_plus", "setpoint_up", Decimal("0.25"))
