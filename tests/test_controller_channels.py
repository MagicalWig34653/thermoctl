import json
from datetime import datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.helpers import create_device, create_zone, rolle
from thermoctl.auth.csrf import CSRF_HEADER, csrf_token
from thermoctl.auth.sessions import COOKIE_NAME
from thermoctl.config import get_settings
from thermoctl.db.models.device import DeviceProperty, ZoneDevice
from thermoctl.db.models.lookup import CHANNEL_KINDS, ChannelKind
from thermoctl.domain.controller_channels import ControllerChannelError, configure_channel
from thermoctl.domain.device_classes import properties_from_exposes
from thermoctl.services.publishing import PublicationState, _controller_channels_senden


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


def _assign(session: Session, zone_id: int, device_id: int, role: str) -> None:
    session.add(
        ZoneDevice(zone_id=zone_id, device_id=device_id, device_role_id=rolle(session, role).id)
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
        self, topic: str, payload: str, *, switches: bool, behalten: bool = False
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
    await _controller_channels_senden(
        session, recorder, state, "zigbee2mqtt", datetime(2026, 8, 30)
    )
    await _controller_channels_senden(
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
