import json
import logging
from datetime import datetime
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from thermoctl.db.models.device import Device, DeviceCapabilityLink, ZoneDevice
from thermoctl.db.models.lookup import DeviceCapability, DeviceRole, Integration, SensorStatus
from thermoctl.db.models.measurement import DeviceHealth, Measurement
from thermoctl.db.models.state import ZoneState
from thermoctl.db.models.zone import Zone
from thermoctl.domain.controller import execute_aktion
from thermoctl.domain.device_classes import (
    DeviceDescription,
    descriptions_from_bridge_list,
)
from thermoctl.domain.fault import NO_SOURCE, OK, sensor_state
from thermoctl.domain.reading import Reading, readings_from_payload
from thermoctl.domain.zone_settings import control_parameters
from thermoctl.integrations.mqtt.zigbee2mqtt import MessageKind, zuschneiden

log = logging.getLogger(__name__)


def _integration(session: Session) -> Integration:
    integration = session.scalar(select(Integration).where(Integration.code == "zigbee2mqtt"))
    if integration is None:  # pragma: no cover
        # Konsistenzpruefung gegen die Migration, die diese Zeile anlegt. Erreichbar nur
        # mit einem von Hand beschaedigten Schema — ein Test dafuer muesste die
        # Nachschlagetabelle leerraeumen und pruefte damit die Migration, nicht uns.
        raise RuntimeError("Anbindung zigbee2mqtt fehlt in der Nachschlagetabelle")
    return integration


def _device(session: Session, name: str, empfangen_am: datetime) -> Device:
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
            first_seen_at=empfangen_am,
        )
        session.add(device)
        session.flush()
    return device


def _process_device_list(session: Session, payload: bytes, empfangen_am: datetime) -> None:
    try:
        beschreibungen = descriptions_from_bridge_list(payload)
    except ValueError:
        log.warning("Zigbee2MQTT-Geraeteliste ist ungueltig")
        return

    capabilities = {
        capability.code: capability for capability in session.scalars(select(DeviceCapability))
    }
    unbekannte: set[str] = set()
    for beschreibung in beschreibungen:
        _save_description(session, beschreibung, empfangen_am, capabilities, unbekannte)
    for code in sorted(unbekannte):
        log.warning(
            "Geraetefaehigkeit fehlt in der Nachschlagetabelle",
            extra={"faehigkeitscode": code},
        )


def _save_description(
    session: Session,
    beschreibung: DeviceDescription,
    empfangen_am: datetime,
    capabilities: dict[str, DeviceCapability],
    unbekannte: set[str],
) -> None:
    device = _device(session, beschreibung.name, empfangen_am)
    device.display_name = beschreibung.name
    device.model = beschreibung.modell
    device.is_group = beschreibung.ist_group
    if device.first_seen_at is None:
        # Nachtrag fuer Geraete, die nicht ueber den Ingest entstanden sind — ab
        # Teilprojekt 3 legt sie auch die Oberflaeche an, und dort gibt es noch keine
        # Sichtung. Beim Ingest selbst ist der Wert schon gesetzt (_geraet).
        device.first_seen_at = empfangen_am

    session.execute(delete(DeviceCapabilityLink).where(DeviceCapabilityLink.device_id == device.id))
    for code in beschreibung.capabilities:
        capability = capabilities.get(code)
        if capability is None:
            unbekannte.add(code)
            continue
        session.add(DeviceCapabilityLink(device_id=device.id, capability_id=capability.id))


def _process_state(
    session: Session, name: str, payload: bytes, empfangen_am: datetime
) -> None:
    readings = readings_from_payload(payload, empfangen_am)
    if not readings:
        return
    device = _device(session, name, empfangen_am)
    # Vor dem Einfuegen gelesen: Danach waere der eigene neue Messwert der juengste,
    # und der Vergleich unten verglichen die Nachricht mit sich selbst.
    last_pressed = _last_pressed(session, device.id)
    capabilities = {
        capability.code: capability for capability in session.scalars(select(DeviceCapability))
    }
    unbekannte: set[str] = set()
    for reading in readings:
        capability = capabilities.get(reading.capability)
        if capability is None:
            unbekannte.add(reading.capability)
            continue
        session.add(
            Measurement(
                device_id=device.id,
                capability_id=capability.id,
                value_numeric=reading.zahl,
                value_text=reading.text,
                measured_at=reading.gemessen_am,
                received_at=empfangen_am,
            )
        )
    for code in sorted(unbekannte):
        log.warning(
            "Messwertfaehigkeit fehlt in der Nachschlagetabelle",
            extra={"faehigkeitscode": code},
        )

    gesund = session.get(DeviceHealth, device.id)
    if gesund is None:
        gesund = DeviceHealth(
            device_id=device.id,
            last_payload_at=empfangen_am,
            payload_count=0,
        )
        session.add(gesund)
    gesund.last_payload_at = empfangen_am
    gesund.payload_count += 1
    gesund.link_quality = _ganzzahl(readings, "link_quality", gesund.link_quality)
    gesund.battery_percent = _dezimalzahl(readings, "battery", gesund.battery_percent)
    device.last_seen_at = empfangen_am
    _execute_button_press(session, device, readings, last_pressed, empfangen_am)


def _last_pressed(session: Session, device_id: int) -> datetime | None:
    """Wann dieses Geraet zuletzt eine Taste gemeldet hat -- vor dieser Nachricht.

    Der Schutz gegen doppelte Ausfuehrung: Zigbee2MQTT sendet Zustandsnachrichten
    normalerweise ohne retain-Flag, aber eine behaltene Nachricht wird bei **jeder**
    Neuverbindung erneut zugestellt. Ohne diesen Vergleich loeste ein Wackelkontakt in
    der Netzverbindung jedes Mal denselben Tastendruck erneut aus -- und ein Boost, den
    niemand gedrueckt hat, faellt erst auf, wenn es im Raum zu warm ist.
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
    empfangen_am: datetime,
) -> None:
    """Fuehrt aus, was ein Tastendruck an einem Bediengeraet belegt hat."""
    druck = next((b for b in readings if b.capability == "action" and b.text), None)
    if druck is None:
        return
    if last_seen is not None and druck.gemessen_am <= last_seen:
        log.debug(
            "Tastendruck bereits verarbeitet, wird uebergangen",
            extra={"geraet": device.display_name, "aktion": druck.text},
        )
        return
    assert druck.text is not None
    execute_aktion(session, device, druck.text, empfangen_am)


def _dezimalzahl(
    readings: list[Reading], code: str, bisher: Decimal | None
) -> Decimal | None:
    return next(
        (b.zahl for b in readings if b.capability == code and b.zahl is not None),
        bisher,
    )


def _ganzzahl(readings: list[Reading], code: str, bisher: int | None) -> int | None:
    value = _dezimalzahl(readings, code, None)
    return int(value) if value is not None else bisher


def _process_availability(
    session: Session, name: str, payload: bytes, empfangen_am: datetime
) -> None:
    try:
        daten = json.loads(payload)
    # Klammern, obwohl Python 3.14 sie hier nicht mehr verlangt (PEP 758): Ohne sie sieht
    # die Zeile genau aus wie die Python-2-Form, die etwas anderes bedeutete — dort band
    # der zweite Name die Ausnahme, statt eine zweite Klasse zu fangen. Wer das einmal
    # falsch liest, sucht den Fehler an der falschen Stelle.
    except (json.JSONDecodeError, UnicodeDecodeError):
        log.warning("Zigbee2MQTT-Erreichbarkeit ist kein gueltiges JSON")
        return
    if not isinstance(daten, dict) or not isinstance(daten.get("state"), str):
        log.warning("Zigbee2MQTT-Erreichbarkeit enthaelt keinen Zustand")
        return
    device = _device(session, name, empfangen_am)
    gesund = session.get(DeviceHealth, device.id)
    if gesund is None:
        gesund = DeviceHealth(
            device_id=device.id,
            last_payload_at=empfangen_am,
            payload_count=0,
        )
        session.add(gesund)
    gesund.availability = daten["state"]


def process_message(
    session: Session,
    topic: str,
    payload: bytes,
    *,
    basis: str,
    empfangen_am: datetime,
) -> None:
    """Schreibt eine empfangene Zigbee2MQTT-Nachricht in die Datenbank."""
    zuschnitt = zuschneiden(topic, basis)
    if zuschnitt.kind == MessageKind.DEVICE_LIST:
        _process_device_list(session, payload, empfangen_am)
    elif zuschnitt.kind == MessageKind.DEVICE_STATE:
        assert zuschnitt.device_name is not None
        _process_state(session, zuschnitt.device_name, payload, empfangen_am)
    elif zuschnitt.kind == MessageKind.AVAILABILITY:
        assert zuschnitt.device_name is not None
        _process_availability(session, zuschnitt.device_name, payload, empfangen_am)
    else:
        log.info(
            "Zigbee2MQTT-Nachricht wird nicht verarbeitet",
            extra={"nachrichtenart": zuschnitt.kind.value, "topic": topic},
        )


def advance_zone_state(session: Session, now: datetime) -> None:
    """Leitet den aktuellen Zustand aller Zonen aus ihrer Temperaturquelle ab."""
    temperature = session.scalar(
        select(DeviceCapability).where(DeviceCapability.code == "temperature")
    )
    contact = session.scalar(select(DeviceCapability).where(DeviceCapability.code == "contact"))
    window_role = session.scalar(
        select(DeviceRole).where(DeviceRole.code == "window_contact")
    )
    status_ids = {status.code: status.id for status in session.scalars(select(SensorStatus))}
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
            # Wie oben: Konsistenzpruefung gegen die Migration, nicht gegen Eingaben.
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
        state.window_open = _window_open(
            session,
            zone,
            contact,
            window_role,
            now,
            control_parameters(session, zone).sensor_timeout_seconds,
        )
        state.updated_at = now


def _window_open(
    session: Session,
    zone: Zone,
    contact: DeviceCapability | None,
    window_role: DeviceRole | None,
    now: datetime,
    timeout_s: int,
) -> bool | None:
    if contact is None or window_role is None:
        return None
    devices_ids = list(
        session.scalars(
            select(ZoneDevice.device_id).where(
                ZoneDevice.zone_id == zone.id,
                ZoneDevice.device_role_id == window_role.id,
            )
        )
    )
    if not devices_ids:
        # Unbekannt wird von der Regelung wie geschlossen behandelt. Sonst koennte eine
        # Anlage ohne Fensterkontakte grundsaetzlich nie heizen.
        return None

    unbekannt = False
    for device_id in devices_ids:
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
            unbekannt = True
            continue
        # Zigbee2MQTT meldet `contact=true` fuer geschlossen und `false` fuer offen.
        # Die Umkehr bleibt bewusst hier, damit sie nicht in jedem Verbraucher erneut
        # und moeglicherweise widerspruechlich vorgenommen wird.
        if measurement.value_text == "false":
            return True
    return None if unbekannt else False
