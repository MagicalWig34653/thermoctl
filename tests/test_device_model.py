import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tests.hilfen import anbindung, geraet_anlegen, rolle, zone_anlegen
from thermoctl.db.models.device import Device, ZoneDevice


def test_adresse_ist_je_anbindung_eindeutig(session: Session) -> None:
    z2m = anbindung(session, "zigbee2mqtt")
    session.add(Device(integration_id=z2m.id, external_id="sensor_wz", display_name="A"))
    session.flush()
    session.add(Device(integration_id=z2m.id, external_id="sensor_wz", display_name="B"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_dieselbe_adresse_in_zwei_anbindungen_ist_erlaubt(session: Session) -> None:
    for code in ("zigbee2mqtt", "meross"):
        session.add(
            Device(
                integration_id=anbindung(session, code).id,
                external_id="schalter",
                display_name=code,
            )
        )
    session.flush()


def test_zone_hat_beliebig_viele_aktoren(session: Session) -> None:
    zone = zone_anlegen(session, "wohnzimmer")
    aktor = rolle(session, "actuator")
    for name in ("aktor_1", "aktor_2"):
        session.add(
            ZoneDevice(
                zone_id=zone.id, device_id=geraet_anlegen(session, name).id, device_role_id=aktor.id
            )
        )
    session.flush()
    assert session.query(ZoneDevice).filter_by(zone_id=zone.id).count() == 2


def test_ein_geraet_kann_zwei_rollen_haben(session: Session) -> None:
    """Ein Aqara W100 misst und bedient zugleich."""
    zone = zone_anlegen(session, "bad")
    w100 = geraet_anlegen(session, "w100_bad")
    for code in ("controller", "actuator"):
        session.add(
            ZoneDevice(zone_id=zone.id, device_id=w100.id, device_role_id=rolle(session, code).id)
        )
    session.flush()
    assert session.query(ZoneDevice).filter_by(device_id=w100.id).count() == 2


def test_dieselbe_rolle_zweimal_am_selben_geraet_ist_ausgeschlossen(session: Session) -> None:
    zone = zone_anlegen(session, "kueche")
    geraet = geraet_anlegen(session, "aktor_kueche")
    aktor = rolle(session, "actuator")
    session.add(ZoneDevice(zone_id=zone.id, device_id=geraet.id, device_role_id=aktor.id))
    session.flush()
    session.add(ZoneDevice(zone_id=zone.id, device_id=geraet.id, device_role_id=aktor.id))
    with pytest.raises(IntegrityError):
        session.flush()


def test_zone_hat_genau_eine_messquelle(session: Session) -> None:
    """Die Kardinalitaet steckt in der Spalte, nicht in einer Anwendungsregel."""
    zone = zone_anlegen(session, "flur")
    zone.temperature_source_device_id = geraet_anlegen(session, "sensor_flur").id
    session.flush()
    assert zone.temperature_source_device_id is not None


def test_geraetetausch_laesst_die_zone_unberuehrt(session: Session) -> None:
    zone = zone_anlegen(session, "buero")
    alt = geraet_anlegen(session, "aktor_alt")
    neu = geraet_anlegen(session, "aktor_neu")
    zuordnung = ZoneDevice(
        zone_id=zone.id, device_id=alt.id, device_role_id=rolle(session, "actuator").id
    )
    session.add(zuordnung)
    session.flush()
    zuordnung.device_id = neu.id
    session.flush()
    assert zone.display_name == "Buero"
