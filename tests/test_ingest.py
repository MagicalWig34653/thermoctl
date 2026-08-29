import json
import logging
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.hilfen import (
    anbindung,
    einstellungen_anlegen,
    geraet_anlegen,
    sensorstatus,
    zone_anlegen,
)
from thermoctl.db.models.device import Device, DeviceCapabilityLink
from thermoctl.db.models.lookup import DeviceCapability, SensorStatus
from thermoctl.db.models.messwert import DeviceHealth, Measurement
from thermoctl.db.models.zone import Zone
from thermoctl.db.models.zustand import ZoneState
from thermoctl.services.ingest import nachricht_verarbeiten, zonenzustand_fortschreiben

DATENPFAD = Path(__file__).parent / "daten" / "anlage-beispiele.json"
BASIS = "test-basis"
EMPFANGEN_AM = datetime(2026, 8, 29, 7, 0)


def _faehigkeit(session: Session, code: str) -> DeviceCapability:
    faehigkeit = DeviceCapability(code=code, label=code)
    session.add(faehigkeit)
    session.flush()
    return faehigkeit


def _beispielzustand() -> tuple[str, bytes]:
    daten = json.loads(DATENPFAD.read_text(encoding="utf-8"))
    name = next(name for name, zustand in daten["zustaende"].items() if "humidity" in zustand)
    return name, json.dumps(daten["zustaende"][name]).encode()


def _geraetenamen() -> list[str]:
    return json.loads(DATENPFAD.read_text(encoding="utf-8"))["geraete"]


def test_echte_nachricht_schreibt_historie_und_ein_lebenszeichen(session: Session) -> None:
    anbindung(session)
    for code in ("battery", "humidity", "link_quality", "temperature"):
        _faehigkeit(session, code)
    name, nutzlast = _beispielzustand()

    nachricht_verarbeiten(
        session, f"{BASIS}/{name}", nutzlast, basis=BASIS, empfangen_am=EMPFANGEN_AM
    )
    nachricht_verarbeiten(
        session, f"{BASIS}/{name}", nutzlast, basis=BASIS, empfangen_am=EMPFANGEN_AM
    )
    session.flush()

    assert session.query(Measurement).count() == 8
    geraet = session.scalar(select(Device).where(Device.external_id == name))
    assert geraet is not None
    assert geraet.last_seen_at == EMPFANGEN_AM
    gesund = session.get(DeviceHealth, geraet.id)
    assert gesund is not None
    assert gesund.payload_count == 2


def test_unbekanntes_geraet_wird_ohne_zone_angelegt(session: Session) -> None:
    anbindung(session)
    _faehigkeit(session, "temperature")
    name, _nutzlast = _beispielzustand()

    nachricht_verarbeiten(
        session,
        f"{BASIS}/{name}",
        b'{"temperature": 21}',
        basis=BASIS,
        empfangen_am=EMPFANGEN_AM,
    )
    session.flush()

    geraet = session.scalar(select(Device).where(Device.external_id == name))
    assert geraet is not None
    assert geraet.is_enabled is True
    assert not any(zone.temperature_source_device_id == geraet.id for zone in session.query(Zone))


def test_fehlende_faehigkeit_verwirft_nicht_die_uebrigen_werte(
    session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    anbindung(session)
    _faehigkeit(session, "temperature")
    name, _nutzlast = _beispielzustand()
    with caplog.at_level(logging.WARNING, logger="thermoctl.services.ingest"):
        nachricht_verarbeiten(
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
    anbindung(session)
    temperatur = _faehigkeit(session, "temperature")
    name, _nutzlast = _beispielzustand()
    liste = [
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

    nachricht_verarbeiten(
        session,
        f"{BASIS}/bridge/devices",
        json.dumps(liste).encode(),
        basis=BASIS,
        empfangen_am=EMPFANGEN_AM,
    )
    session.flush()

    geraet = session.scalar(select(Device).where(Device.external_id == name))
    assert geraet is not None
    assert geraet.model == "testmodell"
    assert session.scalars(
        select(DeviceCapabilityLink.capability_id).where(
            DeviceCapabilityLink.device_id == geraet.id
        )
    ).all() == [temperatur.id]


def test_kaputte_nutzlast_bleibt_ohne_datenbankzeile(session: Session) -> None:
    anbindung(session)
    nachricht_verarbeiten(
        session,
        f"{BASIS}/bridge/devices",
        b"{kaputt",
        basis=BASIS,
        empfangen_am=EMPFANGEN_AM,
    )
    nachricht_verarbeiten(
        session, f"{BASIS}/geraet", b"{kaputt", basis=BASIS, empfangen_am=EMPFANGEN_AM
    )
    assert session.query(Device).count() == 0


def test_erreichbarkeit_wird_am_einen_geraetezustand_fortgeschrieben(
    session: Session,
) -> None:
    anbindung(session)
    name = _geraetenamen()[0]
    nachricht_verarbeiten(
        session,
        f"{BASIS}/{name}/availability",
        b'{"state": "online"}',
        basis=BASIS,
        empfangen_am=EMPFANGEN_AM,
    )
    session.flush()

    geraet = session.scalar(select(Device).where(Device.external_id == name))
    assert geraet is not None
    gesund = session.get(DeviceHealth, geraet.id)
    assert gesund is not None
    assert gesund.availability == "online"


def test_zonenzustand_beruecksichtigt_quelle_alter_und_zonen_timeout(
    session: Session,
) -> None:
    einstellungen_anlegen(session)
    for code in ("ok", "veraltet", "keine_quelle"):
        sensorstatus(session, code)
    temperatur = _faehigkeit(session, "temperature")
    geraetenamen = _geraetenamen()
    frisch_geraet = geraet_anlegen(session, geraetenamen[0])
    alt_geraet = geraet_anlegen(session, geraetenamen[1])
    frisch = zone_anlegen(session, "frisch-zone")
    alt = zone_anlegen(session, "alt-zone")
    ohne = zone_anlegen(session, "ohne-zone")
    frisch.temperature_source_device_id = frisch_geraet.id
    alt.temperature_source_device_id = alt_geraet.id
    alt.sensor_timeout_seconds = 30
    for geraet, alter in ((frisch_geraet, 60), (alt_geraet, 31)):
        session.add(
            Measurement(
                device_id=geraet.id,
                capability_id=temperatur.id,
                value_numeric=Decimal("20.5"),
                measured_at=EMPFANGEN_AM - timedelta(seconds=alter),
                received_at=EMPFANGEN_AM,
            )
        )

    zonenzustand_fortschreiben(session, EMPFANGEN_AM)
    session.flush()
    codes = {status.id: status.code for status in session.query(SensorStatus)}
    zustaende = {z.zone_id: codes[z.sensor_status_id] for z in session.query(ZoneState)}
    assert zustaende == {frisch.id: "ok", alt.id: "veraltet", ohne.id: "keine_quelle"}


def test_kaputte_erreichbarkeit_bleibt_ohne_wirkung(session: Session) -> None:
    """Der dritte Nachrichtenweg braucht dieselbe Absicherung wie die beiden anderen.

    Zigbee2MQTT schickt beim Neustart der Bruecke schon einmal eine leere Nutzlast auf
    `.../availability`. Ein Ausnahmefehler dort haelt den Ingest aller anderen Geraete an.
    """
    for nutzlast in (b"", b"{kaputt", b"\xff\xfe", b'"nur ein Text"', b"{}"):
        nachricht_verarbeiten(
            session,
            "zigbee2mqtt/Ein Geraet/availability",
            nutzlast,
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
    liste = json.dumps(
        [
            {
                "friendly_name": "Ein Multisensor",
                "ieee_address": "0x0000000000000001",
                "type": "EndDevice",
                "definition": {"model": "M1", "vendor": "V", "exposes": []},
            }
        ]
    ).encode()
    anbindung(session, "zigbee2mqtt")
    frueher = datetime(2026, 8, 1, 8, 0, 0)
    spaeter = datetime(2026, 8, 29, 8, 0, 0)
    nachricht_verarbeiten(
        session, "zigbee2mqtt/bridge/devices", liste, basis="zigbee2mqtt", empfangen_am=frueher
    )
    nachricht_verarbeiten(
        session, "zigbee2mqtt/bridge/devices", liste, basis="zigbee2mqtt", empfangen_am=spaeter
    )
    session.flush()
    geraet = session.scalar(select(Device).where(Device.external_id == "Ein Multisensor"))
    assert geraet is not None
    assert geraet.first_seen_at == frueher


def test_nicht_verarbeitete_nachrichtenarten_bleiben_folgenlos(
    session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    """Bruecken- und Fremdnachrichten werden protokolliert, nicht verworfen und nicht
    verarbeitet — protokolliert, damit ein unerwartetes Topic beim Debuggen auffaellt."""
    vorher = len(list(session.scalars(select(Device))))
    with caplog.at_level(logging.INFO):
        nachricht_verarbeiten(
            session, "zigbee2mqtt/bridge/state", b'{"state": "online"}',
            basis="zigbee2mqtt", empfangen_am=datetime(2026, 8, 29, 12, 0, 0),
        )
        nachricht_verarbeiten(
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
    verbindung = anbindung(session, "zigbee2mqtt")
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
    liste = json.dumps(
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
    nachricht_verarbeiten(
        session, "zigbee2mqtt/bridge/devices", liste, basis="zigbee2mqtt", empfangen_am=gesehen
    )
    session.flush()
    geraet = session.scalar(select(Device).where(Device.external_id == "Von Hand angelegt"))
    assert geraet is not None and geraet.first_seen_at == gesehen
