import json
from datetime import datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.helpers import (
    create_device,
    create_zone,
    create_zone_state,
    operating_mode,
    role,
)
from thermoctl.auth.csrf import CSRF_HEADER, csrf_token
from thermoctl.auth.sessions import COOKIE_NAME
from thermoctl.config import get_settings
from thermoctl.db.models.device import DeviceProperty, DevicePropertyValue, ZoneDevice
from thermoctl.db.models.lookup import CHANNEL_KINDS, ChannelKind
from thermoctl.db.models.zone import ZoneSetpoint
from thermoctl.domain.controller_channels import ControllerChannelError, configure_channel
from thermoctl.domain.device_classes import properties_from_exposes
from thermoctl.services.publishing import PublicationState, _send_controller_channels

MONDAY_EIGHT = datetime(2026, 8, 31, 8, 0)


def _kinds(session: Session) -> None:
    for code, label in CHANNEL_KINDS:
        session.add(ChannelKind(code=code, label=label))
    session.flush()


def _property(
    session: Session, device_id: int, name: str = "external_temperature", *, readable: bool = False
) -> DeviceProperty:
    prop = DeviceProperty(
        device_id=device_id,
        name=name,
        value_type="numeric",
        unit="°C",
        min_value=Decimal("-100"),
        max_value=Decimal("100"),
        is_readable=readable,
        is_writable=True,
    )
    session.add(prop)
    session.flush()
    return prop


def _assign(session: Session, zone_id: int, device_id: int, role_code: str) -> None:
    session.add(
        ZoneDevice(
            zone_id=zone_id, device_id=device_id, device_role_id=role(session, role_code).id
        )
    )
    session.flush()


def test_exposes_returns_access_range_and_enum_values() -> None:
    properties = properties_from_exposes(
        [
            {
                "type": "climate",
                "features": [
                    {
                        "type": "numeric",
                        "property": "external_temperature",
                        "access": 2,
                        "unit": "°C",
                        "value_min": -100,
                        "value_max": 100,
                    },
                    {
                        "type": "enum",
                        "property": "system_mode",
                        "access": 3,
                        "values": ["off", "heat", "auto"],
                    },
                ],
            }
        ]
    )
    assert properties[0].is_readable is False and properties[0].is_writable is True
    assert (properties[0].min_value, properties[0].max_value) == (Decimal("-100"), Decimal("100"))
    assert properties[1].values == ("off", "heat", "auto")


def test_a_write_channel_on_an_actuator_is_rejected(session: Session) -> None:
    _kinds(session)
    zone = create_zone(session, "aktorzone")
    device = create_device(session, "ventil")
    _assign(session, zone.id, device.id, "actuator")
    _property(session, device.id)
    with pytest.raises(ControllerChannelError, match="nur auf Bediengeräten"):
        configure_channel(
            session, device, "external_temperature", "write", "fixed", fixed_number=Decimal("20")
        )


class Recorder:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str, bool]] = []

    async def publishing(
        self, topic: str, payload: str, *, switches: bool, retained: bool = False
    ) -> bool:
        self.messages.append((topic, payload, switches))
        return True


@pytest.mark.anyio
async def test_the_same_value_is_not_sent_twice(session: Session) -> None:
    _kinds(session)
    zone = create_zone(session, "wandzone")
    device = create_device(session, "wandregler")
    _assign(session, zone.id, device.id, "controller")
    _property(session, device.id)
    configure_channel(
        session, device, "external_temperature", "write", "fixed", fixed_number=Decimal("20")
    )
    state, recorder = PublicationState(), Recorder()
    await _send_controller_channels(
        session, recorder, state, "zigbee2mqtt", datetime(2026, 8, 30)
    )
    await _send_controller_channels(
        session, recorder, state, "zigbee2mqtt", datetime(2026, 8, 30)
    )
    assert recorder.messages == [
        (
            "zigbee2mqtt/wandregler/set",
            json.dumps({"external_temperature": 20.0}, separators=(",", ":")),
            False,
        )
    ]


def _csrf(client: TestClient) -> dict[str, str]:
    secret = client.cookies.get(COOKIE_NAME)
    assert secret is not None
    return {CSRF_HEADER: csrf_token(secret, get_settings().secret_key.get_secret_value())}


def test_the_controller_page_and_both_form_endpoints(client_als, session: Session) -> None:
    _kinds(session)
    zone = create_zone(session, "webzone")
    device = create_device(session, "wandgeraet")
    _assign(session, zone.id, device.id, "controller")
    _property(session, device.id, readable=True)
    client = client_als([("device.read", zone.id), ("device.manage", zone.id)])
    response = client.get("/controllers")
    assert response.status_code == 200 and "external_temperature" in response.text
    response = client.post(
        "/controllers/channel",
        data={
            "device_id": device.id,
            "property_name": "external_temperature",
            "direction": "write",
            "kind": "fixed",
            "fixed_number": "21,5",
        },
        headers=_csrf(client),
        follow_redirects=False,
    )
    assert response.status_code == 303
    response = client.post(
        "/controllers/button",
        data={"device_id": device.id, "action_code": "single_plus", "command": ""},
        headers=_csrf(client),
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_a_device_that_is_also_an_actuator_gets_no_write_channel(session: Session) -> None:
    """A thermostat can be an actuator in one zone and a controller in another.

    After all, it does display a setpoint. A write channel on its
    `occupied_heating_setpoint` would then be registered as a mere display
    and would still move a valve -- with `switches=False` slipping past both
    of the dry run's bolts. That is why it is not enough for the device to be
    a controller *somewhere*: it must be an actuator nowhere.
    """
    _kinds(session)
    display_zone = create_zone(session, "anzeigezone")
    valve_zone = create_zone(session, "ventilzone")
    device = create_device(session, "thermostat-mit-anzeige")
    _assign(session, display_zone.id, device.id, "controller")
    _assign(session, valve_zone.id, device.id, "actuator")
    _property(session, device.id)

    with pytest.raises(ControllerChannelError, match="nirgends Aktor"):
        configure_channel(
            session, device, "external_temperature", "write", "fixed",
            fixed_number=Decimal("20"),
        )


def test_without_the_actuator_role_the_same_channel_works(session: Session) -> None:
    """The counter-check: the bolt must not lock out every controller."""
    _kinds(session)
    zone = create_zone(session, "reine-anzeigezone")
    device = create_device(session, "reines-bediengeraet")
    _assign(session, zone.id, device.id, "controller")
    _property(session, device.id)

    channel = configure_channel(
        session, device, "external_temperature", "write", "fixed",
        fixed_number=Decimal("20"),
    )
    assert channel.direction == "write"


def _controller(session: Session, name: str = "wall-unit"):
    """A device that is a controller in a zone and an actuator nowhere."""
    zone = create_zone(session, f"zone-for-{name}")
    device = create_device(session, name)
    _assign(session, zone.id, device.id, "controller")
    return zone, device


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("unknown_property", "bietet dieses Merkmal nicht an"),
        ("bad_direction", "Kanalrichtung ist ungültig"),
        ("read_on_write_only", "nicht lesbar"),
        ("kind_does_not_fit", "passt nicht zur Richtung"),
        ("zone_kind_without_zone", "eine Zone erforderlich"),
        ("sensor_kind_without_source", "ein Quellgerät erforderlich"),
        ("fixed_without_value", "feste Wert fehlt"),
    ],
)
def test_every_rule_for_a_channel_is_enforced(session: Session, case: str, expected: str) -> None:
    """Each of these rules exists because the alternative is a channel that does nothing.

    A channel pointing at a property the device does not have, or a kind that needs a zone
    without one, would be accepted, stored, and then silently skipped on every cycle --
    and whoever configured it would look for the fault at the device.
    """
    _kinds(session)
    _zone, device = _controller(session)
    _property(session, device.id)

    calls = {
        "unknown_property": dict(property_name="does_not_exist", direction="write",
                                 kind_code="fixed", fixed_number=Decimal("20")),
        "bad_direction": dict(property_name="external_temperature", direction="sideways",
                              kind_code="fixed", fixed_number=Decimal("20")),
        "read_on_write_only": dict(property_name="external_temperature", direction="read",
                                   kind_code="zone_setpoint"),
        "kind_does_not_fit": dict(property_name="external_temperature", direction="write",
                                  kind_code="operating_mode"),
        "zone_kind_without_zone": dict(property_name="external_temperature",
                                       direction="write", kind_code="zone_temperature"),
        "sensor_kind_without_source": dict(property_name="external_temperature",
                                           direction="write", kind_code="sensor_temperature"),
        "fixed_without_value": dict(property_name="external_temperature", direction="write",
                                    kind_code="fixed"),
    }[case]

    with pytest.raises(ControllerChannelError, match=expected):
        configure_channel(session, device, **calls)


def test_a_fixed_value_must_be_one_the_device_accepts(session: Session) -> None:
    """The bridge says what a property takes; a value outside that is a message the
    device will discard -- and the discarding happens where nobody sees it."""
    _kinds(session)
    _zone, device = _controller(session)
    prop = DeviceProperty(
        device_id=device.id, name="sensor", value_type="enum",
        is_readable=True, is_writable=True,
    )
    session.add(prop)
    session.flush()
    for i, value in enumerate(("internal", "external")):
        session.add(DevicePropertyValue(property_id=prop.id, value=value, sort_order=i))
    session.flush()

    with pytest.raises(ControllerChannelError, match="nicht erlaubt"):
        configure_channel(session, device, "sensor", "write", "fixed", fixed_text="draussen")

    # The counter-check: a value the bridge does list goes through.
    channel = configure_channel(session, device, "sensor", "write", "fixed",
                                fixed_text="external")
    assert channel.fixed_text == "external"


def test_a_fixed_number_must_be_inside_the_range(session: Session) -> None:
    _kinds(session)
    _zone, device = _controller(session)
    _property(session, device.id)  # -100 … 100 °C

    with pytest.raises(ControllerChannelError, match="außerhalb des Wertebereichs"):
        configure_channel(session, device, "external_temperature", "write", "fixed",
                          fixed_number=Decimal("250"))
    # Exactly on the boundary is still allowed -- the counter-check to the rule above.
    channel = configure_channel(session, device, "external_temperature", "write", "fixed",
                                fixed_number=Decimal("100"))
    assert channel.fixed_number == Decimal("100")


def test_configuring_the_same_property_twice_replaces_the_channel(session: Session) -> None:
    """One property, one channel. Otherwise two of them would write to the same place
    and the device would show whichever message arrived last."""
    from sqlalchemy import select

    from thermoctl.db.models.device import ControllerChannel

    _kinds(session)
    _zone, device = _controller(session)
    _property(session, device.id)
    source = create_device(session, "sensor-for-display")

    configure_channel(session, device, "external_temperature", "write", "fixed",
                      fixed_number=Decimal("20"))
    configure_channel(session, device, "external_temperature", "write", "sensor_temperature",
                      source_device_id=source.id)

    channel = session.scalars(select(ControllerChannel)).one()
    assert channel.source_device_id == source.id
    assert channel.fixed_number is None, "der alte feste Wert blieb stehen"


def _read_channel(session: Session, device, zone, prop_name: str, kind: str) -> None:
    prop = DeviceProperty(
        device_id=device.id, name=prop_name, value_type="numeric",
        is_readable=True, is_writable=False,
    )
    session.add(prop)
    session.flush()
    configure_channel(session, device, prop_name, "read", kind, zone_id=zone.id)


def test_a_setpoint_turned_on_the_device_becomes_the_setpoint_of_the_zone(
    session: Session,
) -> None:
    """Like the thermostat in Home Assistant: it changes the mode that currently applies.

    As an override the value would be gone after the next schedule point, and the room
    would cool down again without anyone touching it.
    """
    from tests.helpers import create_mode, create_settings, source
    from thermoctl.db.models.schedule import SchedulePoint
    from thermoctl.db.models.zone import ZoneSetpoint
    from thermoctl.domain.controller_channels import apply_read_channels
    from thermoctl.domain.schedule import resolved_setpoint

    create_settings(session).timezone = "UTC"
    source(session, "system")
    _kinds(session)
    zone, device = _controller(session, "dial")
    day = create_mode(session, "day")
    session.add_all([
        SchedulePoint(zone_id=zone.id, weekday=1, minute_of_day=0, setpoint_mode_id=day.id),
        ZoneSetpoint(zone_id=zone.id, setpoint_mode_id=day.id, temperature_c=Decimal("21.0")),
    ])
    session.flush()
    _read_channel(session, device, zone, "occupied_heating_setpoint", "zone_setpoint")

    apply_read_channels(session, device, {"occupied_heating_setpoint": 23}, MONDAY_EIGHT)

    assert resolved_setpoint(session, zone, MONDAY_EIGHT).temperature_c == Decimal("23")


def test_a_mode_turned_on_the_device_becomes_the_operating_mode(session: Session) -> None:
    from tests.helpers import create_settings, operating_mode, source
    from thermoctl.domain.controller_channels import apply_read_channels

    create_settings(session)
    source(session, "system")
    operating_mode(session, "off")
    _kinds(session)
    zone, device = _controller(session, "switch")
    _read_channel(session, device, zone, "system_mode", "operating_mode")

    apply_read_channels(session, device, {"system_mode": "off"}, MONDAY_EIGHT)

    assert zone.operating_mode.code == "off"


def test_a_value_the_domain_rejects_is_logged_and_changes_nothing(
    session: Session, caplog
) -> None:
    """99 degrees from a wall dial are not a fault of the service.

    The domain limit applies, and the reason belongs in the log instead of vanishing --
    otherwise the only symptom is a dial that does nothing.
    """
    import logging

    from tests.helpers import create_settings, source
    from thermoctl.db.models.zone import ZoneSetpoint
    from thermoctl.domain.controller_channels import apply_read_channels
    from thermoctl.domain.schedule import resolved_setpoint

    settings = create_settings(session)
    source(session, "system")
    _kinds(session)
    zone, device = _controller(session, "wild-dial")
    session.add(ZoneSetpoint(
        zone_id=zone.id,
        setpoint_mode_id=settings.frost_protection_mode_id,
        temperature_c=Decimal("16.0"),
    ))
    session.flush()
    _read_channel(session, device, zone, "occupied_heating_setpoint", "zone_setpoint")
    before = resolved_setpoint(session, zone, MONDAY_EIGHT).temperature_c

    with caplog.at_level(logging.WARNING):
        apply_read_channels(session, device, {"occupied_heating_setpoint": 99}, MONDAY_EIGHT)

    assert resolved_setpoint(session, zone, MONDAY_EIGHT).temperature_c == before
    assert "abgewiesen" in caplog.text.lower()


def test_a_property_that_is_not_in_the_message_is_left_alone(session: Session) -> None:
    """The counter-check: a device sends more than the one value a channel watches."""
    from tests.helpers import create_settings, operating_mode, source
    from thermoctl.domain.controller_channels import apply_read_channels

    create_settings(session)
    source(session, "system")
    operating_mode(session, "off")
    _kinds(session)
    zone, device = _controller(session, "quiet-dial")
    _read_channel(session, device, zone, "system_mode", "operating_mode")
    before = zone.operating_mode.code

    apply_read_channels(session, device, {"battery": 90, "linkquality": 120}, MONDAY_EIGHT)

    assert zone.operating_mode.code == before


def test_the_bridge_list_becomes_device_properties(session: Session) -> None:
    """The whole point: the page works with any device Zigbee2MQTT knows.

    If the properties did not arrive from `bridge/devices`, someone would have to type
    them in per model -- and every device not on that list would stay unconfigurable.
    """
    from sqlalchemy import select

    from tests.helpers import integration
    from thermoctl.services.ingest import process_message

    integration(session, "zigbee2mqtt")
    device_list = [{
        "friendly_name": "wandthermostat",
        "ieee_address": "0x00158d0001",
        "definition": {"model": "TH-S04D", "vendor": "Aqara", "exposes": [
            {"type": "numeric", "property": "external_temperature", "access": 2,
             "unit": "°C", "value_min": -100, "value_max": 100},
            {"type": "enum", "property": "sensor", "access": 3,
             "values": ["internal", "external"]},
        ]},
    }]
    process_message(
        session, "zigbee2mqtt/bridge/devices", json.dumps(device_list).encode(),
        base="zigbee2mqtt", received_at=MONDAY_EIGHT,
    )

    properties = {
        p.name: p for p in session.scalars(select(DeviceProperty))
    }
    assert properties["external_temperature"].is_writable is True
    assert properties["external_temperature"].is_readable is False
    assert properties["sensor"].value_type == "enum"
    values = session.scalars(
        select(DevicePropertyValue.value)
        .where(DevicePropertyValue.property_id == properties["sensor"].id)
        .order_by(DevicePropertyValue.sort_order)
    ).all()
    assert list(values) == ["internal", "external"]


def test_a_second_bridge_list_replaces_the_properties(session: Session) -> None:
    """A device can be re-paired or its firmware updated; then it exposes something else.

    Without replacing, the old properties would linger and the page would offer settings
    the device no longer has.
    """
    from sqlalchemy import select

    from tests.helpers import integration
    from thermoctl.services.ingest import process_message

    integration(session, "zigbee2mqtt")

    def liste(property: str) -> bytes:
        return json.dumps([{
            "friendly_name": "wechselgeraet", "ieee_address": "0x00158d0002",
            "definition": {"model": "X", "vendor": "Y", "exposes": [
                {"type": "numeric", "property": property, "access": 2},
            ]},
        }]).encode()

    process_message(session, "zigbee2mqtt/bridge/devices", liste("altes_merkmal"),
                    base="zigbee2mqtt", received_at=MONDAY_EIGHT)
    process_message(session, "zigbee2mqtt/bridge/devices", liste("neues_merkmal"),
                    base="zigbee2mqtt", received_at=MONDAY_EIGHT)

    names = list(session.scalars(select(DeviceProperty.name)))
    assert names == ["neues_merkmal"]


def test_the_last_value_of_a_property_is_kept_for_the_page(session: Session) -> None:
    """"Does the device talk to me at all?" is the first question on that page.

    It is answered by the last value of every readable property -- and the same
    timestamp is what keeps a retained message from being applied twice.
    """
    from sqlalchemy import select

    from thermoctl.services.ingest import process_message

    device = create_device(session, "melder")
    session.add(DeviceProperty(
        device_id=device.id, name="local_temperature", value_type="numeric",
        is_readable=True, is_writable=False,
    ))
    session.add(DeviceProperty(
        device_id=device.id, name="sensor", value_type="enum",
        is_readable=True, is_writable=True,
    ))
    session.flush()

    process_message(
        session, f"zigbee2mqtt/{device.external_id}",
        json.dumps({"local_temperature": 21.5, "sensor": "external"}).encode(),
        base="zigbee2mqtt", received_at=MONDAY_EIGHT,
    )

    werte = {
        p.name: (p.last_value_number, p.last_value_text, p.last_value_at)
        for p in session.scalars(select(DeviceProperty))
    }
    assert werte["local_temperature"][0] == Decimal("21.5")
    assert werte["sensor"][1] == "external"
    assert werte["local_temperature"][2] == MONDAY_EIGHT


def test_a_rejected_channel_shows_the_reason_instead_of_a_blank_page(
    client_als, session: Session
) -> None:
    """A rejected setting is not a fault of the service, but it must not vanish either.

    Without the reason on the page the only symptom is a form that keeps resetting, and
    the fault is looked for at the device.
    """
    from tests.helpers import source

    source(session)
    _kinds(session)
    zone, device = _controller(session, "meldegeraet")
    _property(session, device.id)
    client = client_als([("device.read", zone.id), ("device.manage", zone.id)])
    client.get("/controllers")

    response = client.post(
        "/controllers/channel",
        headers=_csrf(client),
        data={"device_id": str(device.id), "property_name": "external_temperature",
              "direction": "write", "kind": "fixed", "fixed_number": "250"},
    )

    assert response.status_code == 400
    assert "außerhalb des Wertebereichs" in response.text


def test_a_button_binding_without_an_action_is_refused(client_als, session: Session) -> None:
    from tests.helpers import source

    source(session)
    _kinds(session)
    zone, device = _controller(session, "tastengeraet")
    client = client_als([("device.read", zone.id), ("device.manage", zone.id)])
    client.get("/controllers")

    response = client.post(
        "/controllers/button",
        headers=_csrf(client),
        data={"device_id": str(device.id), "action_code": "", "command": "boost"},
    )
    assert response.status_code == 400


def test_a_bad_step_width_shows_the_reason(client_als, session: Session) -> None:
    """The same limit as everywhere: a setpoint carries one decimal, so a step does too."""
    from tests.helpers import source
    from thermoctl.db.models.lookup import CONTROLLER_COMMANDS, ControllerCommand

    source(session)
    _kinds(session)
    for code, label in CONTROLLER_COMMANDS:
        session.add(ControllerCommand(code=code, label=label))
    session.flush()
    zone, device = _controller(session, "schrittgeraet")
    client = client_als([("device.read", zone.id), ("device.manage", zone.id)])
    client.get("/controllers")

    response = client.post(
        "/controllers/button",
        headers=_csrf(client),
        data={"device_id": str(device.id), "action_code": "single_plus",
              "command": "setpoint_up", "step_k": "0,25"},
    )
    assert response.status_code == 400
    assert "Nachkommastelle" in response.text


def test_a_controller_in_a_foreign_zone_is_not_found(client_als, session: Session) -> None:
    """Not 'forbidden' but 'does not exist' -- otherwise the answer reveals which
    devices are configured elsewhere. The rest of the project handles it the same way."""
    from tests.helpers import source

    source(session)
    _kinds(session)
    own = create_zone(session, "eigene-zone")
    foreign_zone, foreign_device = _controller(session, "fremdes-geraet")
    del foreign_zone
    client = client_als([("device.read", own.id), ("device.manage", own.id)])
    client.get("/controllers")

    response = client.post(
        "/controllers/button",
        headers=_csrf(client),
        data={"device_id": str(foreign_device.id), "action_code": "single_plus",
              "command": "boost"},
    )
    assert response.status_code == 404


def _temperature_measurement(
    session: Session, device_id: int, value: str, when: datetime
) -> None:
    from sqlalchemy import select

    from thermoctl.db.models.lookup import DeviceCapability
    from thermoctl.db.models.measurement import Measurement

    capability = session.scalar(
        select(DeviceCapability).where(DeviceCapability.code == "temperature")
    )
    if capability is None:
        capability = DeviceCapability(code="temperature", label="Temperatur")
        session.add(capability)
        session.flush()
    session.add(
        Measurement(
            device_id=device_id,
            capability_id=capability.id,
            value_numeric=Decimal(value),
            measured_at=when,
            received_at=when,
        )
    )
    session.flush()


@pytest.mark.anyio
async def test_a_sensor_channel_sends_the_most_recent_measurement(session: Session) -> None:
    """This is what a W100 shows on its display: the temperature of a chosen sensor.

    The *most recent* one, and that is the point of the second measurement here -- a
    display that shows an older reading than the one the control loop decides on would
    make the plant look as if it were regulating to a value nobody set.
    """
    _kinds(session)
    zone = create_zone(session, "wohnzimmer")
    controller = create_device(session, "wandregler")
    sensor = create_device(session, "fuehler")
    _assign(session, zone.id, controller.id, "controller")
    _property(session, controller.id)
    _temperature_measurement(session, sensor.id, "19.5", datetime(2026, 8, 30, 8, 0))
    _temperature_measurement(session, sensor.id, "21.5", datetime(2026, 8, 30, 9, 0))
    configure_channel(
        session, controller, "external_temperature", "write", "sensor_temperature",
        source_device_id=sensor.id,
    )

    state, recorder = PublicationState(), Recorder()
    await _send_controller_channels(
        session, recorder, state, "zigbee2mqtt", datetime(2026, 8, 30, 10, 0)
    )

    assert recorder.messages == [
        (
            "zigbee2mqtt/wandregler/set",
            json.dumps({"external_temperature": 21.5}, separators=(",", ":")),
            False,
        )
    ]


@pytest.mark.anyio
async def test_a_sensor_channel_without_any_measurement_sends_nothing(
    session: Session,
) -> None:
    """Better no value on the display than a made-up one.

    A sensor that has never reported has no temperature, and there is no sensible
    substitute -- zero would read as freezing, the setpoint as if it were measured.
    """
    _kinds(session)
    zone = create_zone(session, "leerzone")
    controller = create_device(session, "regler-ohne-wert")
    sensor = create_device(session, "stummer-fuehler")
    _assign(session, zone.id, controller.id, "controller")
    _property(session, controller.id)
    configure_channel(
        session, controller, "external_temperature", "write", "sensor_temperature",
        source_device_id=sensor.id,
    )

    state, recorder = PublicationState(), Recorder()
    await _send_controller_channels(
        session, recorder, state, "zigbee2mqtt", datetime(2026, 8, 30, 10, 0)
    )
    assert recorder.messages == []


@pytest.mark.anyio
async def test_a_zone_temperature_channel_sends_the_zone_state(session: Session) -> None:
    """Not the same as a sensor channel: the zone value is the one the control loop
    actually used, including the calibration offset applied to the raw reading."""
    _kinds(session)
    zone = create_zone(session, "zonentemperatur")
    controller = create_device(session, "zonenregler")
    _assign(session, zone.id, controller.id, "controller")
    _property(session, controller.id)
    zone_state = create_zone_state(session, zone)
    zone_state.temperature_c = Decimal("22.5")
    zone_state.measured_at = datetime(2026, 8, 30, 9, 0)
    session.flush()
    configure_channel(
        session, controller, "external_temperature", "write", "zone_temperature",
        zone_id=zone.id,
    )

    state, recorder = PublicationState(), Recorder()
    await _send_controller_channels(
        session, recorder, state, "zigbee2mqtt", datetime(2026, 8, 30, 10, 0)
    )
    assert recorder.messages == [
        (
            "zigbee2mqtt/zonenregler/set",
            json.dumps({"external_temperature": 22.5}, separators=(",", ":")),
            False,
        )
    ]


@pytest.mark.anyio
async def test_a_setpoint_channel_sends_the_setpoint_in_effect(session: Session) -> None:
    """The value a thermostat on the wall should show as its target.

    Deliberately the resolved setpoint, not the schedule's raw entry: an override or a
    boost changes what the plant is actually aiming for, and a display showing the
    schedule instead would contradict the plant it belongs to.
    """
    from tests.helpers import create_mode, create_settings, source

    _kinds(session)
    create_settings(session)
    source(session, "web")
    zone = create_zone(session, "sollwertzone")
    mode = create_mode(session, "tag")
    zone.operating_mode = operating_mode(session, "auto")
    session.add(
        ZoneSetpoint(zone_id=zone.id, setpoint_mode_id=mode.id, temperature_c=Decimal("21.0"))
    )
    controller = create_device(session, "sollwertregler")
    _assign(session, zone.id, controller.id, "controller")
    _property(session, controller.id, "occupied_heating_setpoint")
    session.flush()
    configure_channel(
        session, controller, "occupied_heating_setpoint", "write", "zone_setpoint",
        zone_id=zone.id,
    )

    state, recorder = PublicationState(), Recorder()
    await _send_controller_channels(
        session, recorder, state, "zigbee2mqtt", datetime(2026, 8, 30, 10, 0)
    )

    assert len(recorder.messages) == 1
    topic, payload, switches = recorder.messages[0]
    assert topic == "zigbee2mqtt/sollwertregler/set"
    assert json.loads(payload)["occupied_heating_setpoint"] > 0
    assert switches is False


@pytest.mark.anyio
async def test_a_device_that_became_an_actuator_is_no_longer_written_to(
    session: Session,
) -> None:
    """The second bolt, checked here where it actually bites.

    A channel is only accepted for a device that is a controller and **nowhere** an
    actuator. But a role can change afterwards: the same thermostat can be hung into
    another zone as an actuator later. From that moment its `occupied_heating_setpoint`
    is no longer a display but a valve -- and a message carrying `switches=False` would
    move it right past both dry-run bolts. So the publication cycle asks the same
    question again before every send.
    """
    _kinds(session)
    zone = create_zone(session, "anzeigezone")
    other = create_zone(session, "ventilzone")
    device = create_device(session, "erst-anzeige-dann-ventil")
    _assign(session, zone.id, device.id, "controller")
    _property(session, device.id)
    configure_channel(
        session, device, "external_temperature", "write", "fixed", fixed_number=Decimal("20")
    )

    # Only now does it also become an actuator -- the channel already exists.
    _assign(session, other.id, device.id, "actuator")

    state, recorder = PublicationState(), Recorder()
    await _send_controller_channels(
        session, recorder, state, "zigbee2mqtt", datetime(2026, 8, 30, 10, 0)
    )
    assert recorder.messages == []


def test_the_controllers_page_shows_a_configured_channel(
    angemeldeter_client: TestClient, session: Session
) -> None:
    """An existing channel has to appear on the page it was set up on.

    The page builds its table from the channels; with none stored the loop never ran,
    so nobody had checked that a saved channel is actually shown again afterwards --
    the one thing that tells a user their setting was kept.
    """
    _kinds(session)
    zone = create_zone(session, "anzeigezone-seite")
    device = create_device(session, "seitenregler")
    _assign(session, zone.id, device.id, "controller")
    _property(session, device.id)
    configure_channel(
        session, device, "external_temperature", "write", "fixed", fixed_number=Decimal("20")
    )
    session.flush()

    response = angemeldeter_client.get("/controllers")
    assert response.status_code == 200
    assert "external_temperature" in response.text
