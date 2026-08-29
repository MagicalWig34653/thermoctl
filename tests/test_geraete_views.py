import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy.orm import Session

from tests.hilfen import geraet_anlegen, geraetezustand_anlegen, rolle, zone_anlegen
from thermoctl.db.models.device import DeviceCapabilityLink, ZoneDevice
from thermoctl.db.models.lookup import DeviceCapability


def test_geraeteseite_braucht_device_read(client_als) -> None:
    assert client_als([("zone.read", None)]).get("/geraete").status_code == 403


def test_leere_geraeteseite_erklaert_den_normalfall(client_als) -> None:
    antwort = client_als([("device.read", None)]).get("/geraete")
    assert antwort.status_code == 200
    assert "MQTT nicht eingerichtet" in antwort.text
    assert "Gerätenachricht eingegangen" in antwort.text


def test_geraeteseite_zeigt_lebenszeichen_faehigkeit_und_zone(
    client_als, session: Session
) -> None:
    beispiele = json.loads(
        (Path(__file__).parent / "daten/anlage-beispiele.json").read_text(encoding="utf-8")
    )
    geraet = geraet_anlegen(session, beispiele["geraete"][1])
    geraet.display_name = "Prüfsensor"
    geraet.model = "Modell A"
    geraet.is_group = True
    zone = zone_anlegen(session, "pruefzone")
    zone.temperature_source_device_id = geraet.id
    zweite_zone = zone_anlegen(session, "zweite-pruefzone")
    session.add(
        ZoneDevice(
            zone_id=zweite_zone.id,
            device_id=geraet.id,
            device_role_id=rolle(session, "controller").id,
        )
    )
    faehigkeit = DeviceCapability(code="temperature", label="Temperaturmessung")
    session.add(faehigkeit)
    session.flush()
    session.add(DeviceCapabilityLink(device_id=geraet.id, capability_id=faehigkeit.id))
    zustand = geraetezustand_anlegen(session, geraet)
    zustand.battery_percent = Decimal("71.50")
    zustand.link_quality = 88
    zustand.availability = "online"
    session.flush()

    antwort = client_als([("device.read", None)]).get("/geraete")

    assert antwort.status_code == 200
    for erwartet in (
        "Prüfsensor",
        "Modell A",
        "Temperaturmessung",
        "Pruefzone",
        "Zweite-pruefzone",
        "Gruppe",
    ):
        assert erwartet in antwort.text
    assert "71.50 %" in antwort.text
    assert "online" in antwort.text


def test_startseite_zeigt_zonenzustand_ohne_nulltemperatur(client_als, session: Session) -> None:
    from thermoctl.db.models.lookup import SensorStatus
    from thermoctl.db.models.zustand import ZoneState

    zone_mit_wert = zone_anlegen(session, "mit-wert")
    zone_ohne_wert = zone_anlegen(session, "ohne-wert")
    zone_ohne_zustand = zone_anlegen(session, "ohne-zustand")
    status = SensorStatus(code="ok", label="In Ordnung")
    session.add(status)
    session.flush()
    zeitpunkt = datetime(2026, 8, 29, 8, 15)
    session.add_all(
        [
            ZoneState(
                zone_id=zone_mit_wert.id,
                temperature_c=Decimal("20.25"),
                measured_at=zeitpunkt,
                sensor_status_id=status.id,
                updated_at=zeitpunkt,
            ),
            ZoneState(
                zone_id=zone_ohne_wert.id,
                temperature_c=None,
                measured_at=None,
                sensor_status_id=status.id,
                updated_at=zeitpunkt,
            ),
        ]
    )
    session.flush()

    antwort = client_als([("zone.read", None)]).get("/")

    assert antwort.status_code == 200
    assert "20.25 °C" in antwort.text
    assert "kein Messwert" in antwort.text
    assert "Noch kein Zonenzustand vorhanden" in antwort.text
    assert zone_ohne_zustand.display_name in antwort.text
