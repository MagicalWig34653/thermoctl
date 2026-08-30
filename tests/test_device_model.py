import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tests.helpers import create_device, create_zone, integration, rolle
from thermoctl.db.models.device import Device, ZoneDevice


def test_adresse_ist_je_anbindung_eindeutig(session: Session) -> None:
    z2m = integration(session, "zigbee2mqtt")
    session.add(Device(integration_id=z2m.id, external_id="sensor_wz", display_name="A"))
    session.flush()
    session.add(Device(integration_id=z2m.id, external_id="sensor_wz", display_name="B"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_dieselbe_adresse_in_zwei_anbindungen_ist_erlaubt(session: Session) -> None:
    for code in ("zigbee2mqtt", "meross"):
        session.add(
            Device(
                integration_id=integration(session, code).id,
                external_id="schalter",
                display_name=code,
            )
        )
    session.flush()


def test_zone_hat_beliebig_viele_aktoren(session: Session) -> None:
    zone = create_zone(session, "wohnzimmer")
    actuator = rolle(session, "actuator")
    for name in ("aktor_1", "aktor_2"):
        session.add(
            ZoneDevice(
                zone_id=zone.id,
                device_id=create_device(session, name).id,
                device_role_id=actuator.id,
            )
        )
    session.flush()
    assert session.query(ZoneDevice).filter_by(zone_id=zone.id).count() == 2


def test_ein_geraet_kann_zwei_rollen_haben(session: Session) -> None:
    """Ein Aqara W100 misst und bedient zugleich."""
    zone = create_zone(session, "bad")
    w100 = create_device(session, "w100_bad")
    for code in ("controller", "actuator"):
        session.add(
            ZoneDevice(zone_id=zone.id, device_id=w100.id, device_role_id=rolle(session, code).id)
        )
    session.flush()
    assert session.query(ZoneDevice).filter_by(device_id=w100.id).count() == 2


def test_dieselbe_rolle_zweimal_am_selben_geraet_ist_ausgeschlossen(session: Session) -> None:
    zone = create_zone(session, "kueche")
    device = create_device(session, "aktor_kueche")
    actuator = rolle(session, "actuator")
    session.add(ZoneDevice(zone_id=zone.id, device_id=device.id, device_role_id=actuator.id))
    session.flush()
    session.add(ZoneDevice(zone_id=zone.id, device_id=device.id, device_role_id=actuator.id))
    with pytest.raises(IntegrityError):
        session.flush()


def test_zone_hat_genau_eine_messquelle(session: Session) -> None:
    """Die Kardinalitaet steckt in der Spalte, nicht in einer Anwendungsregel."""
    zone = create_zone(session, "flur")
    zone.temperature_source_device_id = create_device(session, "sensor_flur").id
    session.flush()
    assert zone.temperature_source_device_id is not None


def test_geraetetausch_laesst_die_zone_unberuehrt(session: Session) -> None:
    zone = create_zone(session, "buero")
    alt = create_device(session, "aktor_alt")
    neu = create_device(session, "aktor_neu")
    assignment = ZoneDevice(
        zone_id=zone.id, device_id=alt.id, device_role_id=rolle(session, "actuator").id
    )
    session.add(assignment)
    session.flush()
    assignment.device_id = neu.id
    session.flush()
    assert zone.display_name == "Buero"
