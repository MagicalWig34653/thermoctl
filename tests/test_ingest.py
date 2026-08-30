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
    rolle,
    sensorstatus,
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
    daten = json.loads(DATENPFAD.read_text(encoding="utf-8"))
    name = next(name for name, state in daten["zustaende"].items() if "humidity" in state)
    return name, json.dumps(daten["zustaende"][name]).encode()


def _device_names() -> list[str]:
    return json.loads(DATENPFAD.read_text(encoding="utf-8"))["geraete"]


def test_echte_nachricht_schreibt_historie_und_ein_lebenszeichen(session: Session) -> None:
    integration(session)
    for code in ("battery", "humidity", "link_quality", "temperature"):
        _capability(session, code)
    name, payload = _example_state()

    process_message(
        session, f"{BASIS}/{name}", payload, basis=BASIS, empfangen_am=EMPFANGEN_AM
    )
    process_message(
        session, f"{BASIS}/{name}", payload, basis=BASIS, empfangen_am=EMPFANGEN_AM
    )
    session.flush()

    assert session.query(Measurement).count() == 8
    device = session.scalar(select(Device).where(Device.external_id == name))
    assert device is not None
    assert device.last_seen_at == EMPFANGEN_AM
    gesund = session.get(DeviceHealth, device.id)
    assert gesund is not None
    assert gesund.payload_count == 2


def test_unbekanntes_geraet_wird_ohne_zone_angelegt(session: Session) -> None:
    integration(session)
    _capability(session, "temperature")
    name, _payload = _example_state()

    process_message(
        session,
        f"{BASIS}/{name}",
        b'{"temperature": 21}',
        basis=BASIS,
        empfangen_am=EMPFANGEN_AM,
    )
    session.flush()

    device = session.scalar(select(Device).where(Device.external_id == name))
    assert device is not None
    assert device.is_enabled is True
    assert not any(zone.temperature_source_device_id == device.id for zone in session.query(Zone))


def test_fehlende_faehigkeit_verwirft_nicht_die_uebrigen_werte(
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
            basis=BASIS,
            empfangen_am=EMPFANGEN_AM,
        )
    session.flush()

    assert [m.value_numeric for m in session.query(Measurement)] == [Decimal("20.500")]
    assert "Messwertfaehigkeit fehlt" in caplog.text


def test_geraeteliste_aktualisiert_geraet_und_setzt_bekannte_faehigkeiten(
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
        basis=BASIS,
        empfangen_am=EMPFANGEN_AM,
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


def test_kaputte_nutzlast_bleibt_ohne_datenbankzeile(session: Session) -> None:
    integration(session)
    process_message(
        session,
        f"{BASIS}/bridge/devices",
        b"{kaputt",
        basis=BASIS,
        empfangen_am=EMPFANGEN_AM,
    )
    process_message(
        session, f"{BASIS}/geraet", b"{kaputt", basis=BASIS, empfangen_am=EMPFANGEN_AM
    )
    assert session.query(Device).count() == 0


def test_erreichbarkeit_wird_am_einen_geraetezustand_fortgeschrieben(
    session: Session,
) -> None:
    integration(session)
    name = _device_names()[0]
    process_message(
        session,
        f"{BASIS}/{name}/availability",
        b'{"state": "online"}',
        basis=BASIS,
        empfangen_am=EMPFANGEN_AM,
    )
    session.flush()

    device = session.scalar(select(Device).where(Device.external_id == name))
    assert device is not None
    gesund = session.get(DeviceHealth, device.id)
    assert gesund is not None
    assert gesund.availability == "online"


def test_zonenzustand_beruecksichtigt_quelle_alter_und_zonen_timeout(
    session: Session,
) -> None:
    create_settings(session)
    for code in ("ok", "veraltet", "keine_quelle"):
        sensorstatus(session, code)
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
    zustaende = {z.zone_id: codes[z.sensor_status_id] for z in session.query(ZoneState)}
    assert zustaende == {frisch.id: "ok", alt.id: "veraltet", ohne.id: "keine_quelle"}


@pytest.mark.parametrize(("contact_value", "expected"), [("true", False), ("false", True)])
def test_zonenzustand_kehrt_zigbee_kontaktwert_einmalig_um(
    session: Session, contact_value: str, expected: bool
) -> None:
    create_settings(session)
    sensorstatus(session, "keine_quelle")
    contact = _capability(session, "contact")
    zone = create_zone(session, "kontakt-zone")
    device = create_device(session, _device_names()[0])
    session.add(
        ZoneDevice(
            zone_id=zone.id,
            device_id=device.id,
            device_role_id=rolle(session, "window_contact").id,
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


def test_zonenzustand_ist_offen_sobald_einer_von_zwei_kontakten_offen_ist(
    session: Session,
) -> None:
    create_settings(session)
    sensorstatus(session, "keine_quelle")
    contact = _capability(session, "contact")
    zone = create_zone(session, "zwei-kontakte-zone")
    window_role = rolle(session, "window_contact")
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


def test_fehlender_oder_veralteter_fensterkontakt_bleibt_unbekannt(
    session: Session,
) -> None:
    create_settings(session)
    sensorstatus(session, "keine_quelle")
    contact = _capability(session, "contact")
    ohne = create_zone(session, "ohne-kontakt-zone")
    alt = create_zone(session, "alter-kontakt-zone")
    alt.sensor_timeout_seconds = 30
    device = create_device(session, _device_names()[0])
    session.add(
        ZoneDevice(
            zone_id=alt.id,
            device_id=device.id,
            device_role_id=rolle(session, "window_contact").id,
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


def test_kaputte_erreichbarkeit_bleibt_ohne_wirkung(session: Session) -> None:
    """Der dritte Nachrichtenweg braucht dieselbe Absicherung wie die beiden anderen.

    Zigbee2MQTT schickt beim Neustart der Bruecke schon einmal eine leere Nutzlast auf
    `.../availability`. Ein Ausnahmefehler dort haelt den Ingest aller anderen Geraete an.
    """
    for payload in (b"", b"{kaputt", b"\xff\xfe", b'"nur ein Text"', b"{}"):
        process_message(
            session,
            "zigbee2mqtt/Ein Geraet/availability",
            payload,
            basis="zigbee2mqtt",
            empfangen_am=datetime(2026, 8, 29, 12, 0, 0),
        )
    session.flush()
    zustaende = list(session.scalars(select(DeviceHealth)))
    assert all(z.availability is None for z in zustaende), (
        "Eine unverwertbare Erreichbarkeitsnachricht darf keinen Zustand setzen."
    )


def test_erste_sichtung_bleibt_bei_der_zweiten_geraeteliste_stehen(session: Session) -> None:
    """`first_seen_at` ist die erste Sichtung, nicht die letzte.

    Zigbee2MQTT sendet die Geraeteliste bei jeder Verbindung erneut. Wuerde sie den Wert
    ueberschreiben, waere er nach jedem Neustart der Bruecke von heute — und die Frage
    'seit wann kennen wir dieses Geraet?' unbeantwortbar.
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
    spaeter = datetime(2026, 8, 29, 8, 0, 0)
    process_message(
        session, "zigbee2mqtt/bridge/devices", items, basis="zigbee2mqtt", empfangen_am=frueher
    )
    process_message(
        session, "zigbee2mqtt/bridge/devices", items, basis="zigbee2mqtt", empfangen_am=spaeter
    )
    session.flush()
    device = session.scalar(select(Device).where(Device.external_id == "Ein Multisensor"))
    assert device is not None
    assert device.first_seen_at == frueher


def test_nicht_verarbeitete_nachrichtenarten_bleiben_folgenlos(
    session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    """Bruecken- und Fremdnachrichten werden protokolliert, nicht verworfen und nicht
    verarbeitet — protokolliert, damit ein unerwartetes Topic beim Debuggen auffaellt."""
    vorher = len(list(session.scalars(select(Device))))
    with caplog.at_level(logging.INFO):
        process_message(
            session, "zigbee2mqtt/bridge/state", b'{"state": "online"}',
            basis="zigbee2mqtt", empfangen_am=datetime(2026, 8, 29, 12, 0, 0),
        )
        process_message(
            session, "ganz/woanders/her", b"{}",
            basis="zigbee2mqtt", empfangen_am=datetime(2026, 8, 29, 12, 0, 0),
        )
    session.flush()
    assert len(list(session.scalars(select(Device)))) == vorher
    assert "nicht verarbeitet" in caplog.text


def test_erste_sichtung_wird_fuer_ein_von_hand_angelegtes_geraet_nachgetragen(
    session: Session,
) -> None:
    """Ab Teilprojekt 3 legt auch die Oberflaeche Geraete an — dort ohne Sichtung.

    Laeuft die erste Nachricht ein, soll der Zeitpunkt nachgetragen werden, statt leer zu
    bleiben. Sonst steht in der Uebersicht dauerhaft 'noch nie'.
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
        session, "zigbee2mqtt/bridge/devices", items, basis="zigbee2mqtt", empfangen_am=gesehen
    )
    session.flush()
    device = session.scalar(select(Device).where(Device.external_id == "Von Hand angelegt"))
    assert device is not None and device.first_seen_at == gesehen
