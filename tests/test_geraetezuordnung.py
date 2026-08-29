from datetime import datetime
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.hilfen import (
    geraet_anlegen,
    modus_anlegen,
    quelle,
    rolle,
    zone_anlegen,
)
from thermoctl.auth.csrf import CSRF_HEADER, csrf_token
from thermoctl.auth.sessions import COOKIE_NAME
from thermoctl.config import get_settings
from thermoctl.db.models.device import ZoneDevice
from thermoctl.db.models.lookup import DeviceCapability
from thermoctl.db.models.messwert import Measurement
from thermoctl.db.models.operations import AuditEvent
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.zone import ZoneSetpoint
from thermoctl.domain.geraetezuordnung import geraet_tauschen


def _csrf(client: TestClient) -> dict[str, str]:
    sitzung = client.cookies.get(COOKIE_NAME)
    assert sitzung is not None
    return {
        CSRF_HEADER: csrf_token(
            sitzung, get_settings().secret_key.get_secret_value()
        )
    }


def _zuordnen(session: Session, zone_id: int, geraet_id: int, rollencode: str) -> None:
    session.add(
        ZoneDevice(
            zone_id=zone_id,
            device_id=geraet_id,
            device_role_id=rolle(session, rollencode).id,
        )
    )
    session.flush()


def test_tausch_erhaelt_zonenkonfiguration_und_uebernimmt_alle_rollen(
    session: Session,
) -> None:
    quelle(session)
    zone = zone_anlegen(session, "wohnzimmer")
    zone.hysteresis_k = Decimal("0.45")
    zone.min_on_seconds = 420
    zone.sensor_timeout_seconds = 900
    modus = modus_anlegen(session, "tag")
    session.add_all(
        [
            ZoneSetpoint(
                zone_id=zone.id,
                setpoint_mode_id=modus.id,
                temperature_c=Decimal("21.5"),
            ),
            SchedulePoint(
                zone_id=zone.id,
                weekday=1,
                minute_of_day=360,
                setpoint_mode_id=modus.id,
            ),
        ]
    )
    altes = geraet_anlegen(session, "alt")
    neues = geraet_anlegen(session, "neu")
    _zuordnen(session, zone.id, altes.id, "actuator")
    _zuordnen(session, zone.id, altes.id, "controller")
    session.flush()
    vorher_sollwerte = list(
        session.execute(
            select(
                ZoneSetpoint.zone_id,
                ZoneSetpoint.setpoint_mode_id,
                ZoneSetpoint.temperature_c,
            ).where(ZoneSetpoint.zone_id == zone.id)
        )
    )
    vorher_zeitplan = list(
        session.execute(
            select(
                SchedulePoint.zone_id,
                SchedulePoint.weekday,
                SchedulePoint.minute_of_day,
                SchedulePoint.setpoint_mode_id,
            ).where(SchedulePoint.zone_id == zone.id)
        )
    )
    vorher_parameter = (
        zone.hysteresis_k,
        zone.min_on_seconds,
        zone.min_off_seconds,
        zone.sensor_timeout_seconds,
        zone.temperature_offset_k,
        zone.window_resume_delay_seconds,
    )

    geraet_tauschen(session, zone, altes, neues, akteur_id=None)

    assert list(
        session.execute(
            select(
                ZoneSetpoint.zone_id,
                ZoneSetpoint.setpoint_mode_id,
                ZoneSetpoint.temperature_c,
            ).where(ZoneSetpoint.zone_id == zone.id)
        )
    ) == vorher_sollwerte
    assert list(
        session.execute(
            select(
                SchedulePoint.zone_id,
                SchedulePoint.weekday,
                SchedulePoint.minute_of_day,
                SchedulePoint.setpoint_mode_id,
            ).where(SchedulePoint.zone_id == zone.id)
        )
    ) == vorher_zeitplan
    assert (
        zone.hysteresis_k,
        zone.min_on_seconds,
        zone.min_off_seconds,
        zone.sensor_timeout_seconds,
        zone.temperature_offset_k,
        zone.window_resume_delay_seconds,
    ) == vorher_parameter
    neue_rollen = set(
        session.scalars(
            select(ZoneDevice.device_role_id).where(
                ZoneDevice.zone_id == zone.id, ZoneDevice.device_id == neues.id
            )
        )
    )
    assert neue_rollen == {rolle(session, "actuator").id, rolle(session, "controller").id}
    assert session.scalar(
        select(ZoneDevice).where(
            ZoneDevice.zone_id == zone.id, ZoneDevice.device_id == altes.id
        )
    ) is None


def test_tausch_laesst_messhistorie_beim_alten_geraet(session: Session) -> None:
    quelle(session)
    zone = zone_anlegen(session, "bad")
    altes = geraet_anlegen(session, "sensor-alt")
    neues = geraet_anlegen(session, "sensor-neu")
    zone.temperature_source_device_id = altes.id
    faehigkeit = DeviceCapability(code="temperature", label="Temperatur")
    session.add(faehigkeit)
    session.flush()
    messwert = Measurement(
        device_id=altes.id,
        capability_id=faehigkeit.id,
        value_numeric=Decimal("19.750"),
        measured_at=datetime(2026, 8, 29, 10, 0),
        received_at=datetime(2026, 8, 29, 10, 0),
    )
    session.add(messwert)
    session.flush()

    geraet_tauschen(session, zone, altes, neues, akteur_id=None)

    assert zone.temperature_source_device_id == neues.id
    assert session.get(Measurement, messwert.id).device_id == altes.id
    assert session.scalar(
        select(Measurement).where(Measurement.device_id == neues.id)
    ) is None


def test_tausch_in_einer_zone_laesst_zweite_zone_unberuehrt(session: Session) -> None:
    quelle(session)
    zone_a = zone_anlegen(session, "a")
    zone_b = zone_anlegen(session, "b")
    altes = geraet_anlegen(session, "alt-gemeinsam")
    neues = geraet_anlegen(session, "neu-a")
    _zuordnen(session, zone_a.id, altes.id, "window_contact")
    _zuordnen(session, zone_b.id, altes.id, "window_contact")

    geraet_tauschen(session, zone_a, altes, neues, akteur_id=None)

    assert session.scalar(
        select(ZoneDevice).where(
            ZoneDevice.zone_id == zone_b.id, ZoneDevice.device_id == altes.id
        )
    ) is not None
    assert session.scalar(
        select(ZoneDevice).where(
            ZoneDevice.zone_id == zone_b.id, ZoneDevice.device_id == neues.id
        )
    ) is None


def test_tausch_schreibt_audit(session: Session) -> None:
    quelle(session)
    zone = zone_anlegen(session, "audit-zone")
    altes = geraet_anlegen(session, "audit-alt")
    neues = geraet_anlegen(session, "audit-neu")
    _zuordnen(session, zone.id, altes.id, "actuator")

    geraet_tauschen(session, zone, altes, neues, akteur_id=None)

    eintrag = session.scalar(
        select(AuditEvent).where(
            AuditEvent.object_type == "zone_device", AuditEvent.action == "replace"
        )
    )
    assert eintrag is not None
    assert "audit-alt" in eintrag.summary
    assert "audit-neu" in eintrag.summary


def test_seite_zeigt_zuordnungen_und_messquelle(client_als, session: Session) -> None:
    zone = zone_anlegen(session, "anzeige")
    geraet = geraet_anlegen(session, "thermostat")
    zone.temperature_source_device_id = geraet.id
    _zuordnen(session, zone.id, geraet.id, "controller")

    antwort = client_als([("device.read", zone.id)]).get(
        f"/zonen/{zone.id}/geraete"
    )

    assert antwort.status_code == 200
    assert "thermostat" in antwort.text
    assert "controller" in antwort.text
    assert "Gerät tauschen" not in antwort.text


def test_doppelte_rolle_zeigt_verstaendliche_meldung(client_als, session: Session) -> None:
    quelle(session)
    zone = zone_anlegen(session, "doppelt")
    geraet = geraet_anlegen(session, "kontakt")
    geraeterolle = rolle(session, "window_contact")
    _zuordnen(session, zone.id, geraet.id, "window_contact")
    client = client_als([("device.manage", zone.id)])

    antwort = client.post(
        f"/zonen/{zone.id}/geraete/zuordnen",
        data={"device_id": geraet.id, "role_id": geraeterolle.id},
        headers=_csrf(client),
    )

    assert antwort.status_code == 200
    assert "in dieser Rolle bereits zugeordnet" in antwort.text


def test_aendernde_wege_und_rechte(client_als, session: Session) -> None:
    quelle(session)
    eigene = zone_anlegen(session, "eigene")
    fremde = zone_anlegen(session, "fremde")
    altes = geraet_anlegen(session, "weg")
    neues = geraet_anlegen(session, "hin")
    client = client_als([("device.manage", eigene.id)])
    kopf = _csrf(client)

    zuordnen = client.post(
        f"/zonen/{eigene.id}/geraete/zuordnen",
        data={"device_id": altes.id, "role_id": rolle(session, "actuator").id},
        headers=kopf,
        follow_redirects=False,
    )
    assert zuordnen.status_code == 303
    zuordnung = session.scalar(
        select(ZoneDevice).where(ZoneDevice.zone_id == eigene.id)
    )
    assert zuordnung is not None

    messquelle = client.post(
        f"/zonen/{eigene.id}/geraete/messquelle",
        data={"device_id": altes.id},
        headers=kopf,
        follow_redirects=False,
    )
    assert messquelle.status_code == 303
    assert eigene.temperature_source_device_id == altes.id

    tausch = client.post(
        f"/zonen/{eigene.id}/geraete/tauschen",
        data={"old_device_id": altes.id, "new_device_id": neues.id},
        headers=kopf,
        follow_redirects=False,
    )
    assert tausch.status_code == 303
    neue_zuordnung = session.scalar(
        select(ZoneDevice).where(
            ZoneDevice.zone_id == eigene.id, ZoneDevice.device_id == neues.id
        )
    )
    assert neue_zuordnung is not None

    loesen = client.post(
        f"/zonen/{eigene.id}/geraete/{neue_zuordnung.id}/loesen",
        headers=kopf,
        follow_redirects=False,
    )
    assert loesen.status_code == 303
    assert session.get(ZoneDevice, neue_zuordnung.id) is None

    assert client.get(f"/zonen/{fremde.id}/geraete").status_code == 404
    assert client.post(
        f"/zonen/{fremde.id}/geraete/messquelle",
        data={"device_id": neues.id},
        headers=kopf,
    ).status_code == 404
