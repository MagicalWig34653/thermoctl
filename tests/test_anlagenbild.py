"""Das Anlagenbild -- welches Geraet wo etwas tut.

Der Wert dieser Ansicht liegt in den **Luecken**: Eine Zone ohne Messquelle regelt nichts,
eine ohne Aktor bewirkt nichts, und ein Geraet ohne Zone tut ueberhaupt nichts. Genau das
sieht man einer Geraeteliste nicht an.
"""

from sqlalchemy.orm import Session

from tests.hilfen import faehigkeit, geraet_anlegen, rolle, zone_anlegen
from thermoctl.db.models.device import DeviceCapabilityLink, ZoneDevice
from thermoctl.domain.anlagenbild import anlagenbild


def test_vollstaendig_verdrahtete_zone_hat_keine_maengel(session: Session) -> None:
    zone = zone_anlegen(session, "vollstaendig")
    sensor = geraet_anlegen(session, "thermometer")
    ventil = geraet_anlegen(session, "ventil")
    zone.temperature_source_device_id = sensor.id
    session.add(
        ZoneDevice(
            zone_id=zone.id, device_id=ventil.id, device_role_id=rolle(session, "actuator").id
        )
    )
    session.flush()

    bild = anlagenbild(session, [zone]).zonen[0]
    assert bild.messquelle is not None and bild.messquelle.name == sensor.display_name
    assert [a.name for a in bild.aktoren] == [ventil.display_name]
    assert bild.maengel == []


def test_zone_ohne_messquelle_meldet_den_mangel(session: Session) -> None:
    zone = zone_anlegen(session, "blind")
    ventil = geraet_anlegen(session, "ventil-blind")
    session.add(
        ZoneDevice(
            zone_id=zone.id, device_id=ventil.id, device_role_id=rolle(session, "actuator").id
        )
    )
    session.flush()
    bild = anlagenbild(session, [zone]).zonen[0]
    assert bild.messquelle is None
    assert any("Messquelle" in m for m in bild.maengel)


def test_zone_ohne_aktor_meldet_den_mangel(session: Session) -> None:
    zone = zone_anlegen(session, "ohnmaechtig")
    sensor = geraet_anlegen(session, "thermometer-ohnmaechtig")
    zone.temperature_source_device_id = sensor.id
    session.flush()
    bild = anlagenbild(session, [zone]).zonen[0]
    assert any("Aktor" in m for m in bild.maengel)


def test_fensterkontakte_stehen_bei_ihrer_zone(session: Session) -> None:
    zone = zone_anlegen(session, "mit-fenster")
    kontakt = geraet_anlegen(session, "fensterkontakt")
    session.add(
        ZoneDevice(
            zone_id=zone.id,
            device_id=kontakt.id,
            device_role_id=rolle(session, "window_contact").id,
        )
    )
    session.flush()
    bild = anlagenbild(session, [zone]).zonen[0]
    assert [k.name for k in bild.fensterkontakte] == [kontakt.display_name]
    assert bild.aktoren == []


def test_geraete_ohne_zone_werden_eigens_aufgefuehrt(session: Session) -> None:
    """Der haeufigste Grund, warum ein neu eingebundener Sensor 'nicht ankommt': Er
    meldet sich, aber keine Zone benutzt ihn."""
    zone = zone_anlegen(session, "eine-zone")
    benutzt = geraet_anlegen(session, "benutzt")
    unbenutzt = geraet_anlegen(session, "herrenlos")
    zone.temperature_source_device_id = benutzt.id
    session.flush()

    bild = anlagenbild(session, [zone])
    assert [g.name for g in bild.ohne_zone] == [unbenutzt.display_name]


def test_faehigkeiten_stehen_am_geraet(session: Session) -> None:
    """Sie beantworten, *warum* ein Geraet an dieser Stelle steht -- ein Ventil ohne
    Schaltausgang waere ein Aktor, der nichts kann."""
    zone = zone_anlegen(session, "faehig")
    sensor = geraet_anlegen(session, "faehiger-sensor")
    session.add(
        DeviceCapabilityLink(
            device_id=sensor.id, capability_id=faehigkeit(session, "temperature").id
        )
    )
    zone.temperature_source_device_id = sensor.id
    session.flush()
    bild = anlagenbild(session, [zone]).zonen[0]
    assert bild.messquelle is not None
    assert bild.messquelle.faehigkeiten
