import json
import logging
from datetime import datetime
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from thermoctl.db.models.device import Device, DeviceCapabilityLink, ZoneDevice
from thermoctl.db.models.lookup import DeviceCapability, DeviceRole, Integration, SensorStatus
from thermoctl.db.models.messwert import DeviceHealth, Measurement
from thermoctl.db.models.zone import Zone
from thermoctl.db.models.zustand import ZoneState
from thermoctl.domain.bediengeraet import aktion_ausfuehren
from thermoctl.domain.beobachtung import Beobachtung, beobachtungen_aus_nutzlast
from thermoctl.domain.geraeteklassen import (
    Geraetebeschreibung,
    beschreibungen_aus_bridge_liste,
)
from thermoctl.domain.stoerung import KEINE_QUELLE, OK, sensorzustand
from thermoctl.domain.zone_settings import regelparameter
from thermoctl.integrations.mqtt.zigbee2mqtt import Nachrichtenart, zuschneiden

log = logging.getLogger(__name__)


def _anbindung(session: Session) -> Integration:
    anbindung = session.scalar(select(Integration).where(Integration.code == "zigbee2mqtt"))
    if anbindung is None:  # pragma: no cover
        # Konsistenzpruefung gegen die Migration, die diese Zeile anlegt. Erreichbar nur
        # mit einem von Hand beschaedigten Schema — ein Test dafuer muesste die
        # Nachschlagetabelle leerraeumen und pruefte damit die Migration, nicht uns.
        raise RuntimeError("Anbindung zigbee2mqtt fehlt in der Nachschlagetabelle")
    return anbindung


def _geraet(session: Session, name: str, empfangen_am: datetime) -> Device:
    anbindung = _anbindung(session)
    geraet = session.scalar(
        select(Device).where(
            Device.integration_id == anbindung.id,
            Device.external_id == name,
        )
    )
    if geraet is None:
        geraet = Device(
            integration_id=anbindung.id,
            external_id=name,
            display_name=name,
            is_enabled=True,
            first_seen_at=empfangen_am,
        )
        session.add(geraet)
        session.flush()
    return geraet


def _geraeteliste_verarbeiten(session: Session, nutzlast: bytes, empfangen_am: datetime) -> None:
    try:
        beschreibungen = beschreibungen_aus_bridge_liste(nutzlast)
    except ValueError:
        log.warning("Zigbee2MQTT-Geraeteliste ist ungueltig")
        return

    faehigkeiten = {
        faehigkeit.code: faehigkeit for faehigkeit in session.scalars(select(DeviceCapability))
    }
    unbekannte: set[str] = set()
    for beschreibung in beschreibungen:
        _beschreibung_speichern(session, beschreibung, empfangen_am, faehigkeiten, unbekannte)
    for code in sorted(unbekannte):
        log.warning(
            "Geraetefaehigkeit fehlt in der Nachschlagetabelle",
            extra={"faehigkeitscode": code},
        )


def _beschreibung_speichern(
    session: Session,
    beschreibung: Geraetebeschreibung,
    empfangen_am: datetime,
    faehigkeiten: dict[str, DeviceCapability],
    unbekannte: set[str],
) -> None:
    geraet = _geraet(session, beschreibung.name, empfangen_am)
    geraet.display_name = beschreibung.name
    geraet.model = beschreibung.modell
    geraet.is_group = beschreibung.ist_gruppe
    if geraet.first_seen_at is None:
        # Nachtrag fuer Geraete, die nicht ueber den Ingest entstanden sind — ab
        # Teilprojekt 3 legt sie auch die Oberflaeche an, und dort gibt es noch keine
        # Sichtung. Beim Ingest selbst ist der Wert schon gesetzt (_geraet).
        geraet.first_seen_at = empfangen_am

    session.execute(delete(DeviceCapabilityLink).where(DeviceCapabilityLink.device_id == geraet.id))
    for code in beschreibung.faehigkeiten:
        faehigkeit = faehigkeiten.get(code)
        if faehigkeit is None:
            unbekannte.add(code)
            continue
        session.add(DeviceCapabilityLink(device_id=geraet.id, capability_id=faehigkeit.id))


def _zustand_verarbeiten(
    session: Session, name: str, nutzlast: bytes, empfangen_am: datetime
) -> None:
    beobachtungen = beobachtungen_aus_nutzlast(nutzlast, empfangen_am)
    if not beobachtungen:
        return
    geraet = _geraet(session, name, empfangen_am)
    # Vor dem Einfuegen gelesen: Danach waere der eigene neue Messwert der juengste,
    # und der Vergleich unten verglichen die Nachricht mit sich selbst.
    zuletzt_gedrueckt = _zuletzt_gedrueckt(session, geraet.id)
    faehigkeiten = {
        faehigkeit.code: faehigkeit for faehigkeit in session.scalars(select(DeviceCapability))
    }
    unbekannte: set[str] = set()
    for beobachtung in beobachtungen:
        faehigkeit = faehigkeiten.get(beobachtung.faehigkeit)
        if faehigkeit is None:
            unbekannte.add(beobachtung.faehigkeit)
            continue
        session.add(
            Measurement(
                device_id=geraet.id,
                capability_id=faehigkeit.id,
                value_numeric=beobachtung.zahl,
                value_text=beobachtung.text,
                measured_at=beobachtung.gemessen_am,
                received_at=empfangen_am,
            )
        )
    for code in sorted(unbekannte):
        log.warning(
            "Messwertfaehigkeit fehlt in der Nachschlagetabelle",
            extra={"faehigkeitscode": code},
        )

    gesund = session.get(DeviceHealth, geraet.id)
    if gesund is None:
        gesund = DeviceHealth(
            device_id=geraet.id,
            last_payload_at=empfangen_am,
            payload_count=0,
        )
        session.add(gesund)
    gesund.last_payload_at = empfangen_am
    gesund.payload_count += 1
    gesund.link_quality = _ganzzahl(beobachtungen, "link_quality", gesund.link_quality)
    gesund.battery_percent = _dezimalzahl(beobachtungen, "battery", gesund.battery_percent)
    geraet.last_seen_at = empfangen_am
    _tastendruck_ausfuehren(session, geraet, beobachtungen, zuletzt_gedrueckt, empfangen_am)


def _zuletzt_gedrueckt(session: Session, geraet_id: int) -> datetime | None:
    """Wann dieses Geraet zuletzt eine Taste gemeldet hat -- vor dieser Nachricht.

    Der Schutz gegen doppelte Ausfuehrung: Zigbee2MQTT sendet Zustandsnachrichten
    normalerweise ohne retain-Flag, aber eine behaltene Nachricht wird bei **jeder**
    Neuverbindung erneut zugestellt. Ohne diesen Vergleich loeste ein Wackelkontakt in
    der Netzverbindung jedes Mal denselben Tastendruck erneut aus -- und ein Boost, den
    niemand gedrueckt hat, faellt erst auf, wenn es im Raum zu warm ist.
    """
    faehigkeit_id = session.scalar(
        select(DeviceCapability.id).where(DeviceCapability.code == "action")
    )
    if faehigkeit_id is None:
        return None
    return session.scalar(
        select(Measurement.measured_at)
        .where(
            Measurement.device_id == geraet_id,
            Measurement.capability_id == faehigkeit_id,
        )
        .order_by(Measurement.measured_at.desc(), Measurement.id.desc())
        .limit(1)
    )


def _tastendruck_ausfuehren(
    session: Session,
    geraet: Device,
    beobachtungen: list[Beobachtung],
    zuletzt: datetime | None,
    empfangen_am: datetime,
) -> None:
    """Fuehrt aus, was ein Tastendruck an einem Bediengeraet belegt hat."""
    druck = next((b for b in beobachtungen if b.faehigkeit == "action" and b.text), None)
    if druck is None:
        return
    if zuletzt is not None and druck.gemessen_am <= zuletzt:
        log.debug(
            "Tastendruck bereits verarbeitet, wird uebergangen",
            extra={"geraet": geraet.display_name, "aktion": druck.text},
        )
        return
    assert druck.text is not None
    aktion_ausfuehren(session, geraet, druck.text, empfangen_am)


def _dezimalzahl(
    beobachtungen: list[Beobachtung], code: str, bisher: Decimal | None
) -> Decimal | None:
    return next(
        (b.zahl for b in beobachtungen if b.faehigkeit == code and b.zahl is not None),
        bisher,
    )


def _ganzzahl(beobachtungen: list[Beobachtung], code: str, bisher: int | None) -> int | None:
    wert = _dezimalzahl(beobachtungen, code, None)
    return int(wert) if wert is not None else bisher


def _erreichbarkeit_verarbeiten(
    session: Session, name: str, nutzlast: bytes, empfangen_am: datetime
) -> None:
    try:
        daten = json.loads(nutzlast)
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
    geraet = _geraet(session, name, empfangen_am)
    gesund = session.get(DeviceHealth, geraet.id)
    if gesund is None:
        gesund = DeviceHealth(
            device_id=geraet.id,
            last_payload_at=empfangen_am,
            payload_count=0,
        )
        session.add(gesund)
    gesund.availability = daten["state"]


def nachricht_verarbeiten(
    session: Session,
    topic: str,
    nutzlast: bytes,
    *,
    basis: str,
    empfangen_am: datetime,
) -> None:
    """Schreibt eine empfangene Zigbee2MQTT-Nachricht in die Datenbank."""
    zuschnitt = zuschneiden(topic, basis)
    if zuschnitt.art == Nachrichtenart.GERAETELISTE:
        _geraeteliste_verarbeiten(session, nutzlast, empfangen_am)
    elif zuschnitt.art == Nachrichtenart.GERAETEZUSTAND:
        assert zuschnitt.geraetename is not None
        _zustand_verarbeiten(session, zuschnitt.geraetename, nutzlast, empfangen_am)
    elif zuschnitt.art == Nachrichtenart.ERREICHBARKEIT:
        assert zuschnitt.geraetename is not None
        _erreichbarkeit_verarbeiten(session, zuschnitt.geraetename, nutzlast, empfangen_am)
    else:
        log.info(
            "Zigbee2MQTT-Nachricht wird nicht verarbeitet",
            extra={"nachrichtenart": zuschnitt.art.value, "topic": topic},
        )


def zonenzustand_fortschreiben(session: Session, jetzt: datetime) -> None:
    """Leitet den aktuellen Zustand aller Zonen aus ihrer Temperaturquelle ab."""
    temperatur = session.scalar(
        select(DeviceCapability).where(DeviceCapability.code == "temperature")
    )
    kontakt = session.scalar(select(DeviceCapability).where(DeviceCapability.code == "contact"))
    fensterrolle = session.scalar(
        select(DeviceRole).where(DeviceRole.code == "window_contact")
    )
    status_ids = {status.code: status.id for status in session.scalars(select(SensorStatus))}
    for zone in session.scalars(select(Zone)):
        messwert = None
        if zone.temperature_source_device_id is not None and temperatur is not None:
            messwert = session.scalar(
                select(Measurement)
                .where(
                    Measurement.device_id == zone.temperature_source_device_id,
                    Measurement.capability_id == temperatur.id,
                )
                .order_by(Measurement.measured_at.desc(), Measurement.id.desc())
                .limit(1)
            )
        code = (
            KEINE_QUELLE
            if zone.temperature_source_device_id is None
            else sensorzustand(
                messwert.measured_at if messwert is not None else None,
                jetzt,
                regelparameter(session, zone).sensor_timeout_seconds,
            )
        )
        status_id = status_ids.get(code)
        if status_id is None:  # pragma: no cover
            # Wie oben: Konsistenzpruefung gegen die Migration, nicht gegen Eingaben.
            raise RuntimeError(f"Sensorstatus {code} fehlt in der Nachschlagetabelle")
        zustand = session.get(ZoneState, zone.id)
        if zustand is None:
            zustand = ZoneState(
                zone_id=zone.id,
                sensor_status_id=status_id,
                updated_at=jetzt,
            )
            session.add(zustand)
        zustand.temperature_c = messwert.value_numeric if messwert is not None else None
        zustand.measured_at = messwert.measured_at if messwert is not None else None
        zustand.sensor_status_id = status_id
        zustand.window_open = _fenster_offen(
            session,
            zone,
            kontakt,
            fensterrolle,
            jetzt,
            regelparameter(session, zone).sensor_timeout_seconds,
        )
        zustand.updated_at = jetzt


def _fenster_offen(
    session: Session,
    zone: Zone,
    kontakt: DeviceCapability | None,
    fensterrolle: DeviceRole | None,
    jetzt: datetime,
    timeout_s: int,
) -> bool | None:
    if kontakt is None or fensterrolle is None:
        return None
    geraete_ids = list(
        session.scalars(
            select(ZoneDevice.device_id).where(
                ZoneDevice.zone_id == zone.id,
                ZoneDevice.device_role_id == fensterrolle.id,
            )
        )
    )
    if not geraete_ids:
        # Unbekannt wird von der Regelung wie geschlossen behandelt. Sonst koennte eine
        # Anlage ohne Fensterkontakte grundsaetzlich nie heizen.
        return None

    unbekannt = False
    for geraet_id in geraete_ids:
        messwert = session.scalar(
            select(Measurement)
            .where(
                Measurement.device_id == geraet_id,
                Measurement.capability_id == kontakt.id,
            )
            .order_by(Measurement.measured_at.desc(), Measurement.id.desc())
            .limit(1)
        )
        if (
            messwert is None
            or sensorzustand(messwert.measured_at, jetzt, timeout_s) != OK
            or messwert.value_text not in {"true", "false"}
        ):
            unbekannt = True
            continue
        # Zigbee2MQTT meldet `contact=true` fuer geschlossen und `false` fuer offen.
        # Die Umkehr bleibt bewusst hier, damit sie nicht in jedem Verbraucher erneut
        # und moeglicherweise widerspruechlich vorgenommen wird.
        if messwert.value_text == "false":
            return True
    return None if unbekannt else False
