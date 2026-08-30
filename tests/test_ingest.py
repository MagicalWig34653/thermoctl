import json
import logging
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.helpers import (
    create_device,
    create_settings,
    create_zone,
    integration,
    role,
    sensor_status_of,
)
from thermoctl.db.models.device import Device, DeviceCapabilityLink, ZoneDevice
from thermoctl.db.models.lookup import DeviceCapability, SensorStatus
from thermoctl.db.models.measurement import DeviceHealth, Measurement
from thermoctl.db.models.state import ZoneState
from thermoctl.db.models.zone import Zone
from thermoctl.services.ingest import advance_zone_state, process_message

DATENPFAD = Path(__file__).parent / "daten" / "anlage-beispiele.json"
BASIS = "test-basis"
EMPFANGEN_AM = datetime(2026, 8, 29, 7, 0)


def _capability(session: Session, code: str) -> DeviceCapability:
    capability = DeviceCapability(code=code, label=code)
    session.add(capability)
    session.flush()
    return capability


def _example_state() -> tuple[str, bytes]:
    data = json.loads(DATENPFAD.read_text(encoding="utf-8"))
    name = next(name for name, state in data["zustaende"].items() if "humidity" in state)
    return name, json.dumps(data["zustaende"][name]).encode()


def _device_names() -> list[str]:
    return json.loads(DATENPFAD.read_text(encoding="utf-8"))["geraete"]


def test_a_real_message_writes_history_and_a_sign_of_life(session: Session) -> None:
    integration(session)
    for code in ("battery", "humidity", "link_quality", "temperature"):
        _capability(session, code)
    name, payload = _example_state()

    process_message(
        session, f"{BASIS}/{name}", payload, base=BASIS, received_at=EMPFANGEN_AM
    )
    process_message(
        session, f"{BASIS}/{name}", payload, base=BASIS, received_at=EMPFANGEN_AM
    )
    session.flush()

    assert session.query(Measurement).count() == 8
    device = session.scalar(select(Device).where(Device.external_id == name))
    assert device is not None
    assert device.last_seen_at == EMPFANGEN_AM
    healthy = session.get(DeviceHealth, device.id)
    assert healthy is not None
    assert healthy.payload_count == 2


def test_an_unknown_device_is_created_without_a_zone(session: Session) -> None:
    integration(session)
    _capability(session, "temperature")
    name, _payload = _example_state()

    process_message(
        session,
        f"{BASIS}/{name}",
        b'{"temperature": 21}',
        base=BASIS,
        received_at=EMPFANGEN_AM,
    )
    session.flush()

    device = session.scalar(select(Device).where(Device.external_id == name))
    assert device is not None
    assert device.is_enabled is True
    assert not any(zone.temperature_source_device_id == device.id for zone in session.query(Zone))


def test_a_missing_capability_does_not_discard_the_other_values(
    session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    integration(session)
    _capability(session, "temperature")
    name, _payload = _example_state()
    with caplog.at_level(logging.WARNING, logger="thermoctl.services.ingest"):
        process_message(
            session,
            f"{BASIS}/{name}",
            b'{"temperature": 20.5, "battery": 75}',
            base=BASIS,
            received_at=EMPFANGEN_AM,
        )
    session.flush()

    assert [m.value_numeric for m in session.query(Measurement)] == [Decimal("20.500")]
    assert "Messwertfaehigkeit fehlt" in caplog.text


def test_the_device_list_updates_the_device_and_sets_known_capabilities(
    session: Session,
) -> None:
    integration(session)
    temperature = _capability(session, "temperature")
    name, _payload = _example_state()
    items = [
        {
            "friendly_name": name,
            "definition": {
                "model": "testmodell",
                "exposes": [
                    {"type": "numeric", "property": "temperature"},
                    {"type": "numeric", "property": "humidity"},
                ],
            },
        }
    ]

    process_message(
        session,
        f"{BASIS}/bridge/devices",
        json.dumps(items).encode(),
        base=BASIS,
        received_at=EMPFANGEN_AM,
    )
    session.flush()

    device = session.scalar(select(Device).where(Device.external_id == name))
    assert device is not None
    assert device.model == "testmodell"
    assert session.scalars(
        select(DeviceCapabilityLink.capability_id).where(
            DeviceCapabilityLink.device_id == device.id
        )
    ).all() == [temperature.id]


def test_a_broken_payload_leaves_no_database_row(session: Session) -> None:
    integration(session)
    process_message(
        session,
        f"{BASIS}/bridge/devices",
        b"{kaputt",
        base=BASIS,
        received_at=EMPFANGEN_AM,
    )
    process_message(
        session, f"{BASIS}/geraet", b"{kaputt", base=BASIS, received_at=EMPFANGEN_AM
    )
    assert session.query(Device).count() == 0


def test_availability_is_carried_forward_on_the_one_device_state(
    session: Session,
) -> None:
    integration(session)
    name = _device_names()[0]
    process_message(
        session,
        f"{BASIS}/{name}/availability",
        b'{"state": "online"}',
        base=BASIS,
        received_at=EMPFANGEN_AM,
    )
    session.flush()

    device = session.scalar(select(Device).where(Device.external_id == name))
    assert device is not None
    healthy = session.get(DeviceHealth, device.id)
    assert healthy is not None
    assert healthy.availability == "online"


def test_zone_state_accounts_for_source_age_and_the_zone_timeout(
    session: Session,
) -> None:
    create_settings(session)
    for code in ("ok", "veraltet", "keine_quelle"):
        sensor_status_of(session, code)
    temperature = _capability(session, "temperature")
    device_names = _device_names()
    fresh_device = create_device(session, device_names[0])
    old_device = create_device(session, device_names[1])
    frisch = create_zone(session, "frisch-zone")
    alt = create_zone(session, "alt-zone")
    ohne = create_zone(session, "ohne-zone")
    frisch.temperature_source_device_id = fresh_device.id
    alt.temperature_source_device_id = old_device.id
    alt.sensor_timeout_seconds = 30
    for device, age in ((fresh_device, 60), (old_device, 31)):
        session.add(
            Measurement(
                device_id=device.id,
                capability_id=temperature.id,
                value_numeric=Decimal("20.5"),
                measured_at=EMPFANGEN_AM - timedelta(seconds=age),
                received_at=EMPFANGEN_AM,
            )
        )

    advance_zone_state(session, EMPFANGEN_AM)
    session.flush()
    codes = {status.id: status.code for status in session.query(SensorStatus)}
    states = {z.zone_id: codes[z.sensor_status_id] for z in session.query(ZoneState)}
    assert states == {frisch.id: "ok", alt.id: "veraltet", ohne.id: "keine_quelle"}


@pytest.mark.parametrize(("contact_value", "expected"), [("true", False), ("false", True)])
def test_zone_state_inverts_the_zigbee_contact_value_exactly_once(
    session: Session, contact_value: str, expected: bool
) -> None:
    create_settings(session)
    sensor_status_of(session, "keine_quelle")
    contact = _capability(session, "contact")
    zone = create_zone(session, "kontakt-zone")
    device = create_device(session, _device_names()[0])
    session.add(
        ZoneDevice(
            zone_id=zone.id,
            device_id=device.id,
            device_role_id=role(session, "window_contact").id,
        )
    )
    session.add(
        Measurement(
            device_id=device.id,
            capability_id=contact.id,
            value_text=contact_value,
            measured_at=EMPFANGEN_AM,
            received_at=EMPFANGEN_AM,
        )
    )

    advance_zone_state(session, EMPFANGEN_AM)

    state = session.get(ZoneState, zone.id)
    assert state is not None and state.window_open is expected


def test_the_zone_counts_as_open_as_soon_as_one_of_two_contacts_is_open(
    session: Session,
) -> None:
    create_settings(session)
    sensor_status_of(session, "keine_quelle")
    contact = _capability(session, "contact")
    zone = create_zone(session, "zwei-kontakte-zone")
    window_role = role(session, "window_contact")
    for name, value in zip(_device_names()[:2], ("true", "false"), strict=True):
        device = create_device(session, name)
        session.add(
            ZoneDevice(
                zone_id=zone.id,
                device_id=device.id,
                device_role_id=window_role.id,
            )
        )
        session.add(
            Measurement(
                device_id=device.id,
                capability_id=contact.id,
                value_text=value,
                measured_at=EMPFANGEN_AM,
                received_at=EMPFANGEN_AM,
            )
        )

    advance_zone_state(session, EMPFANGEN_AM)

    state = session.get(ZoneState, zone.id)
    assert state is not None and state.window_open is True


def test_a_missing_or_stale_window_contact_stays_unknown(
    session: Session,
) -> None:
    create_settings(session)
    sensor_status_of(session, "keine_quelle")
    contact = _capability(session, "contact")
    ohne = create_zone(session, "ohne-kontakt-zone")
    alt = create_zone(session, "alter-kontakt-zone")
    alt.sensor_timeout_seconds = 30
    device = create_device(session, _device_names()[0])
    session.add(
        ZoneDevice(
            zone_id=alt.id,
            device_id=device.id,
            device_role_id=role(session, "window_contact").id,
        )
    )
    session.add(
        Measurement(
            device_id=device.id,
            capability_id=contact.id,
            value_text="false",
            measured_at=EMPFANGEN_AM - timedelta(seconds=31),
            received_at=EMPFANGEN_AM,
        )
    )

    advance_zone_state(session, EMPFANGEN_AM)

    without_state = session.get(ZoneState, ohne.id)
    old_state = session.get(ZoneState, alt.id)
    assert without_state is not None and without_state.window_open is None
    assert old_state is not None and old_state.window_open is None


def test_a_broken_availability_message_has_no_effect(session: Session) -> None:
    """The third message path needs the same protection as the other two.

    Zigbee2MQTT is known to send an empty payload to `.../availability` when the
    bridge restarts. An exception there would halt ingest for every other device.
    """
    for payload in (b"", b"{kaputt", b"\xff\xfe", b'"nur ein Text"', b"{}"):
        process_message(
            session,
            "zigbee2mqtt/Ein Geraet/availability",
            payload,
            base="zigbee2mqtt",
            received_at=datetime(2026, 8, 29, 12, 0, 0),
        )
    session.flush()
    states = list(session.scalars(select(DeviceHealth)))
    assert all(z.availability is None for z in states), (
        "An unusable availability message must not set a state."
    )


def test_the_first_sighting_survives_a_second_device_list(session: Session) -> None:
    """`first_seen_at` is the first sighting, not the last.

    Zigbee2MQTT resends the device list on every connection. If it overwrote the
    value, it would say today after every bridge restart -- making the question
    'since when have we known this device?' unanswerable.
    """
    items = json.dumps(
        [
            {
                "friendly_name": "Ein Multisensor",
                "ieee_address": "0x0000000000000001",
                "type": "EndDevice",
                "definition": {"model": "M1", "vendor": "V", "exposes": []},
            }
        ]
    ).encode()
    integration(session, "zigbee2mqtt")
    frueher = datetime(2026, 8, 1, 8, 0, 0)
    later = datetime(2026, 8, 29, 8, 0, 0)
    process_message(
        session, "zigbee2mqtt/bridge/devices", items, base="zigbee2mqtt", received_at=frueher
    )
    process_message(
        session, "zigbee2mqtt/bridge/devices", items, base="zigbee2mqtt", received_at=later
    )
    session.flush()
    device = session.scalar(select(Device).where(Device.external_id == "Ein Multisensor"))
    assert device is not None
    assert device.first_seen_at == frueher


def test_message_kinds_that_are_not_processed_have_no_consequences(
    session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    """Bridge and foreign messages are logged, not silently dropped and not
    processed -- logged so an unexpected topic stands out while debugging."""
    before = len(list(session.scalars(select(Device))))
    with caplog.at_level(logging.INFO):
        process_message(
            session, "zigbee2mqtt/bridge/state", b'{"state": "online"}',
            base="zigbee2mqtt", received_at=datetime(2026, 8, 29, 12, 0, 0),
        )
        process_message(
            session, "ganz/woanders/her", b"{}",
            base="zigbee2mqtt", received_at=datetime(2026, 8, 29, 12, 0, 0),
        )
    session.flush()
    assert len(list(session.scalars(select(Device)))) == before
    assert "nicht verarbeitet" in caplog.text


def test_the_first_sighting_is_filled_in_for_a_hand_created_device(
    session: Session,
) -> None:
    """Starting with subproject 3, the interface also creates devices -- without a
    sighting there.

    When the first message comes in, the timestamp should be filled in retroactively
    instead of staying empty. Otherwise the overview would permanently show 'never'.
    """
    verbindung = integration(session, "zigbee2mqtt")
    session.add(
        Device(
            integration_id=verbindung.id,
            external_id="Von Hand angelegt",
            display_name="Von Hand angelegt",
            is_enabled=True,
            first_seen_at=None,
        )
    )
    session.flush()
    items = json.dumps(
        [
            {
                "friendly_name": "Von Hand angelegt",
                "ieee_address": "0x0000000000000002",
                "type": "EndDevice",
                "definition": {"model": "M2", "vendor": "V", "exposes": []},
            }
        ]
    ).encode()
    gesehen = datetime(2026, 8, 29, 9, 30, 0)
    process_message(
        session, "zigbee2mqtt/bridge/devices", items, base="zigbee2mqtt", received_at=gesehen
    )
    session.flush()
    device = session.scalar(select(Device).where(Device.external_id == "Von Hand angelegt"))
    assert device is not None and device.first_seen_at == gesehen
