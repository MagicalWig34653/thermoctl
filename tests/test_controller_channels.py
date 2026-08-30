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


def test_exposes_liefert_zugriff_bereich_und_auswahlwerte() -> None:
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


def test_schreibkanal_auf_aktor_wird_abgewiesen(session: Session) -> None:
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
async def test_gleicher_wert_wird_nicht_zweimal_gesendet(session: Session) -> None:
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


def test_controller_seite_und_beide_formularendpunkte(client_als, session: Session) -> None:
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


def test_ein_geraet_das_auch_aktor_ist_bekommt_keinen_schreibkanal(session: Session) -> None:
    """Ein Thermostat kann in einer Zone Aktor sein und in einer anderen Bediengeraet.

    Es zeigt ja einen Sollwert an. Ein Schreibkanal auf sein `occupied_heating_setpoint`
    waere dann als blosse Anzeige angemeldet und bewegte trotzdem ein Ventil -- mit
    `switches=False` an beiden Riegeln des Trockenlaufs vorbei. Deshalb reicht es nicht,
    dass das Geraet *irgendwo* Bediengeraet ist: Aktor darf es nirgends sein.
    """
    _kinds(session)
    anzeigezone = create_zone(session, "anzeigezone")
    ventilzone = create_zone(session, "ventilzone")
    device = create_device(session, "thermostat-mit-anzeige")
    _assign(session, anzeigezone.id, device.id, "controller")
    _assign(session, ventilzone.id, device.id, "actuator")
    _property(session, device.id)

    with pytest.raises(ControllerChannelError, match="nirgends Aktor"):
        configure_channel(
            session, device, "external_temperature", "write", "fixed",
            fixed_number=Decimal("20"),
        )


def test_ohne_die_aktorrolle_geht_derselbe_kanal(session: Session) -> None:
    """Die Gegenprobe: Der Riegel darf nicht jedes Bediengeraet aussperren."""
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
