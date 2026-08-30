"""Das Anlagenbild -- welches Geraet wo etwas tut.

Der Wert dieser Ansicht liegt in den **Luecken**: Eine Zone ohne Messquelle regelt nichts,
eine ohne Aktor bewirkt nichts, und ein Geraet ohne Zone tut ueberhaupt nichts. Genau das
sieht man einer Geraeteliste nicht an.
"""

from sqlalchemy.orm import Session

from tests.helpers import capability, create_device, create_zone, rolle
from thermoctl.db.models.device import DeviceCapabilityLink, ZoneDevice
from thermoctl.domain.plant_diagram import plant_diagram


def test_vollstaendig_verdrahtete_zone_hat_keine_maengel(session: Session) -> None:
    zone = create_zone(session, "vollstaendig")
    sensor = create_device(session, "thermometer")
    ventil = create_device(session, "ventil")
    zone.temperature_source_device_id = sensor.id
    session.add(
        ZoneDevice(
            zone_id=zone.id, device_id=ventil.id, device_role_id=rolle(session, "actuator").id
        )
    )
    session.flush()

    picture = plant_diagram(session, [zone]).zones[0]
    assert picture.temperature_source is not None
    assert picture.temperature_source.name == sensor.display_name
    assert [a.name for a in picture.actuators] == [ventil.display_name]
    assert picture.maengel == []


def test_zone_ohne_messquelle_meldet_den_mangel(session: Session) -> None:
    zone = create_zone(session, "blind")
    ventil = create_device(session, "ventil-blind")
    session.add(
        ZoneDevice(
            zone_id=zone.id, device_id=ventil.id, device_role_id=rolle(session, "actuator").id
        )
    )
    session.flush()
    picture = plant_diagram(session, [zone]).zones[0]
    assert picture.temperature_source is None
    assert any("Messquelle" in m for m in picture.maengel)


def test_zone_ohne_aktor_meldet_den_mangel(session: Session) -> None:
    zone = create_zone(session, "ohnmaechtig")
    sensor = create_device(session, "thermometer-ohnmaechtig")
    zone.temperature_source_device_id = sensor.id
    session.flush()
    picture = plant_diagram(session, [zone]).zones[0]
    assert any("Aktor" in m for m in picture.maengel)


def test_fensterkontakte_stehen_bei_ihrer_zone(session: Session) -> None:
    zone = create_zone(session, "mit-fenster")
    contact = create_device(session, "fensterkontakt")
    session.add(
        ZoneDevice(
            zone_id=zone.id,
            device_id=contact.id,
            device_role_id=rolle(session, "window_contact").id,
        )
    )
    session.flush()
    picture = plant_diagram(session, [zone]).zones[0]
    assert [k.name for k in picture.window_contacts] == [contact.display_name]
    assert picture.actuators == []


def test_geraete_ohne_zone_werden_eigens_aufgefuehrt(session: Session) -> None:
    """Der haeufigste Grund, warum ein neu eingebundener Sensor 'nicht ankommt': Er
    meldet sich, aber keine Zone benutzt ihn."""
    zone = create_zone(session, "eine-zone")
    benutzt = create_device(session, "benutzt")
    unbenutzt = create_device(session, "herrenlos")
    zone.temperature_source_device_id = benutzt.id
    session.flush()

    picture = plant_diagram(session, [zone])
    assert [g.name for g in picture.without_zone] == [unbenutzt.display_name]


def test_faehigkeiten_stehen_am_geraet(session: Session) -> None:
    """Sie beantworten, *warum* ein Geraet an dieser Stelle steht -- ein Ventil ohne
    Schaltausgang waere ein Aktor, der nichts kann."""
    zone = create_zone(session, "faehig")
    sensor = create_device(session, "faehiger-sensor")
    session.add(
        DeviceCapabilityLink(
            device_id=sensor.id, capability_id=capability(session, "temperature").id
        )
    )
    zone.temperature_source_device_id = sensor.id
    session.flush()
    picture = plant_diagram(session, [zone]).zones[0]
    assert picture.temperature_source is not None
    assert picture.temperature_source.capabilities


def test_eine_alte_fehlzuordnung_wird_als_mangel_gemeldet(session: Session) -> None:
    """Die Pruefung bei der Zuordnung verhindert neue solche Faelle. Die alten stehen
    schon in der Datenbank und wuerden sonst nie auffallen -- das Anlagenbild zeigte
    einen vollstaendigen Weg, und geschaltet haette trotzdem nie etwas."""
    zone = create_zone(session, "altlast")
    sensor = create_device(session, "sensor-als-aktor")
    session.add(
        DeviceCapabilityLink(
            device_id=sensor.id, capability_id=capability(session, "temperature").id
        )
    )
    # Absichtlich an der Domaenenfunktion vorbei: So sieht ein Bestand aus, der vor der
    # Pruefung entstanden ist.
    session.add(
        ZoneDevice(
            zone_id=zone.id,
            device_id=sensor.id,
            device_role_id=rolle(session, "actuator").id,
        )
    )
    session.flush()

    picture = plant_diagram(session, [zone]).zones[0]
    assert picture.actuators[0].ungeeignet is not None
    assert any("Schaltausgang" in m or "keinen Schaltausgang" in m for m in picture.maengel)


def test_ein_herrenloses_ventil_gilt_nicht_als_ungeeignet(session: Session) -> None:
    """Gegenprobe zur Unterscheidung "Messquelle" gegen "keine Anforderung": An ein
    Geraet ohne Zone stellt niemand eine Anforderung. Ohne sie waere jedes nicht
    zugeordnete Ventil als "misst keine Temperatur" markiert."""
    create_zone(session, "leerzone")
    ventil = create_device(session, "herrenloses-ventil")
    session.add(
        DeviceCapabilityLink(
            device_id=ventil.id, capability_id=capability(session, "switch").id
        )
    )
    session.flush()

    picture = plant_diagram(session, [])
    frei = next(g for g in picture.without_zone if g.name == ventil.display_name)
    assert frei.ungeeignet is None
