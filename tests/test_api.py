import json
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from tests.hilfen import (
    benutzer_mit_rechten,
    betriebsart,
    geraet_anlegen,
    geraetezustand_anlegen,
    quelle,
    rolle,
    sensorstatus,
)
from thermoctl.auth.tokens import token_ausstellen
from thermoctl.db.models.device import DeviceCapabilityLink, ZoneDevice
from thermoctl.db.models.lookup import DeviceCapability
from thermoctl.db.models.zone import Zone
from thermoctl.db.models.zustand import ZoneState


@pytest.fixture
def token_fuer(session: Session) -> Callable[[list[tuple[str, str | None]]], dict[str, str]]:
    art = betriebsart(session)
    bad = Zone(id=1, name="bad", display_name="Bad", operating_mode_id=art.id)
    andere = Zone(id=2, name="andere", display_name="Andere", operating_mode_id=art.id)
    session.add_all([bad, andere])
    session.flush()
    quelle(session, "api")

    zaehler = 0

    def _token_fuer(rechte: list[tuple[str, str | None]]) -> dict[str, str]:
        nonlocal zaehler
        zaehler += 1
        aufgeloest = [(code, bad.id if zone == "bad" else None) for code, zone in rechte]
        besitzer = benutzer_mit_rechten(session, f"api-{zaehler}", aufgeloest)
        _token, klartext = token_ausstellen(
            session, besitzer, f"test-{zaehler}", aufgeloest, None
        )
        return {"Authorization": f"Bearer {klartext}"}

    return _token_fuer


def test_ohne_token_kein_zugriff(client) -> None:
    assert client.get("/api/v1/zones").status_code == 401


def test_ungueltiges_token_wird_abgewiesen(client) -> None:
    antwort = client.get("/api/v1/zones", headers={"Authorization": "Bearer tctl_x_y"})
    assert antwort.status_code == 401


def test_token_sieht_nur_erlaubte_zonen(client, token_fuer) -> None:
    """visible_zones muss auch hier wirken — sonst leckt die API, was die UI verbirgt."""
    kopf = token_fuer([("zone.read", "bad")])
    namen = [z["name"] for z in client.get("/api/v1/zones", headers=kopf).json()]
    assert namen == ["bad"]


def test_zugriff_auf_fremde_zone_ergibt_404(client, token_fuer) -> None:
    """404 und nicht 403: ein 403 verraet, dass die Zone existiert."""
    kopf = token_fuer([("zone.read", "bad")])
    assert client.get("/api/v1/zones/2", headers=kopf).status_code == 404


def test_geraeteliste_braucht_device_read(client, token_fuer) -> None:
    kopf = token_fuer([("zone.read", "bad")])
    assert client.get("/api/v1/devices", headers=kopf).status_code == 403


def test_geraeteliste_liefert_lebenszeichen(client, token_fuer, session: Session) -> None:
    beispiele = json.loads(
        (Path(__file__).parent / "daten/anlage-beispiele.json").read_text(encoding="utf-8")
    )
    geraet = geraet_anlegen(session, beispiele["geraete"][2])
    session.get(Zone, 1).temperature_source_device_id = geraet.id
    session.add(
        ZoneDevice(
            zone_id=2,
            device_id=geraet.id,
            device_role_id=rolle(session, "controller").id,
        )
    )
    faehigkeit = DeviceCapability(code="temperature", label="Temperaturmessung")
    session.add(faehigkeit)
    session.flush()
    session.add(DeviceCapabilityLink(device_id=geraet.id, capability_id=faehigkeit.id))
    zustand = geraetezustand_anlegen(session, geraet)
    zustand.availability = "online"
    session.flush()
    kopf = token_fuer([("device.read", None)])

    antwort = client.get("/api/v1/devices", headers=kopf)

    assert antwort.status_code == 200
    assert antwort.json()[0]["external_id"] == beispiele["geraete"][2]
    assert antwort.json()[0]["availability"] == "online"
    assert antwort.json()[0]["capabilities"] == ["temperature"]
    assert antwort.json()[0]["zones"] == ["andere", "bad"]


def test_zonenzustand_ist_nur_fuer_sichtbare_zone_lesbar(
    client, token_fuer, session: Session
) -> None:
    zeitpunkt = datetime(2026, 8, 29, 8, 0)
    session.add_all(
        [
            ZoneState(
                zone_id=1,
                temperature_c=Decimal("19.75"),
                measured_at=zeitpunkt,
                sensor_status_id=sensorstatus(session).id,
                updated_at=zeitpunkt,
            ),
            ZoneState(
                zone_id=2,
                temperature_c=Decimal("21.00"),
                measured_at=zeitpunkt,
                sensor_status_id=sensorstatus(session).id,
                updated_at=zeitpunkt,
            ),
        ]
    )
    session.flush()
    kopf = token_fuer([("zone.read", "bad")])

    antwort = client.get("/api/v1/zones/1/state", headers=kopf)

    assert antwort.status_code == 200
    assert antwort.json()["temperature_c"] == "19.75"
    assert antwort.json()["sensor_status"] == "ok"
    assert client.get("/api/v1/zones/2/state", headers=kopf).status_code == 404


def test_sichtbare_zone_ohne_zustand_ergibt_404(client, token_fuer) -> None:
    kopf = token_fuer([("zone.read", "bad")])
    assert client.get("/api/v1/zones/1/state", headers=kopf).status_code == 404


def test_uebersteuern_ohne_recht_wird_abgewiesen(client, token_fuer) -> None:
    kopf = token_fuer([("zone.read", "bad")])
    antwort = client.post("/api/v1/zones/1/override", headers=kopf,
                          json={"temperature_c": "22.0", "dauer_minuten": 30})
    assert antwort.status_code == 403


def test_uebersteuern_mit_recht_legt_eintrag_an(client, token_fuer, session) -> None:
    from thermoctl.db.models.override import ZoneOverride

    kopf = token_fuer([("zone.read", "bad"), ("override.create", "bad")])
    antwort = client.post("/api/v1/zones/1/override", headers=kopf,
                          json={"temperature_c": "22.0", "dauer_minuten": 30})
    assert antwort.status_code == 201
    eintrag = session.query(ZoneOverride).one()
    assert eintrag.ends_at is not None  # Dauer wird beim Anlegen ausgerechnet
    assert eintrag.created_by_token_id is not None


def test_api_braucht_kein_csrf_token(client, token_fuer) -> None:
    """Token-Anfragen schicken kein Cookie und sind damit nicht CSRF-gefaehrdet."""
    kopf = token_fuer([("zone.read", "bad"), ("override.create", "bad")])
    antwort = client.post("/api/v1/zones/1/override", headers=kopf,
                          json={"temperature_c": "22.0", "dauer_minuten": 30})
    assert antwort.status_code == 201


def test_token_hash_erscheint_in_keiner_antwort(client, token_fuer) -> None:
    kopf = token_fuer([("zone.read", "bad"), ("token.self", None)])
    assert "token_hash" not in client.get("/api/v1/me", headers=kopf).text


def test_me_ohne_recht_wird_abgewiesen(client, token_fuer) -> None:
    """token.self fehlt hier bewusst -- auch das eigene Token einsehen ist ein Recht."""
    kopf = token_fuer([("zone.read", "bad")])
    antwort = client.get("/api/v1/me", headers=kopf)
    assert antwort.status_code == 403


def test_uebersteuern_bis_naechste_schaltung_ohne_zeitplan(client, token_fuer) -> None:
    """Ohne Schaltpunkte in der Zone bleibt die Uebersteuerung unbefristet."""
    kopf = token_fuer([("zone.read", "bad"), ("override.create", "bad")])
    antwort = client.post(
        "/api/v1/zones/1/override", headers=kopf,
        json={"temperature_c": "22.0", "bis_naechste_schaltung": True},
    )
    assert antwort.status_code == 201
    assert antwort.json()["ends_at"] is None


def test_uebersteuern_bis_naechste_schaltung_mit_zeitplan(client, token_fuer, session) -> None:
    from thermoctl.db.models.operations import Setting
    from thermoctl.db.models.schedule import SchedulePoint
    from thermoctl.db.models.zone import SetpointMode

    modus = SetpointMode(code="tag", name="Tag")
    session.add(modus)
    session.flush()
    session.add(SchedulePoint(zone_id=1, weekday=1, minute_of_day=0, setpoint_mode_id=modus.id))
    session.add(Setting(id=1, timezone="Europe/Berlin", frost_protection_mode_id=modus.id))
    session.flush()

    kopf = token_fuer([("zone.read", "bad"), ("override.create", "bad")])
    antwort = client.post(
        "/api/v1/zones/1/override", headers=kopf,
        json={"temperature_c": "22.0", "bis_naechste_schaltung": True},
    )
    assert antwort.status_code == 201
    assert antwort.json()["ends_at"] is not None


def test_uebersteuerung_loeschen_ohne_recht_wird_abgewiesen(client, token_fuer) -> None:
    kopf = token_fuer([("zone.read", "bad")])
    antwort = client.delete("/api/v1/zones/1/override", headers=kopf)
    assert antwort.status_code == 403


def test_uebersteuerung_loeschen_beendet_die_aktive(client, token_fuer, session) -> None:
    from thermoctl.db.models.override import ZoneOverride

    kopf = token_fuer([("zone.read", "bad"), ("override.create", "bad"),
                       ("override.cancel", "bad")])
    client.post("/api/v1/zones/1/override", headers=kopf,
               json={"temperature_c": "22.0", "dauer_minuten": 30})
    antwort = client.delete("/api/v1/zones/1/override", headers=kopf)
    assert antwort.status_code == 204
    eintrag = session.query(ZoneOverride).one()
    assert eintrag.cancelled_at is not None
