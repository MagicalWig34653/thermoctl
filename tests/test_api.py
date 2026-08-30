import json
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from tests.helpers import (
    create_device,
    create_device_state,
    operating_mode,
    rolle,
    sensorstatus,
    source,
    user_with_permissions,
)
from thermoctl.auth.tokens import token_ausstellen
from thermoctl.db.models.device import DeviceCapabilityLink, ZoneDevice
from thermoctl.db.models.lookup import DeviceCapability
from thermoctl.db.models.state import ZoneState
from thermoctl.db.models.zone import Zone


@pytest.fixture
def token_fuer(session: Session) -> Callable[[list[tuple[str, str | None]]], dict[str, str]]:
    kind = operating_mode(session)
    bad = Zone(id=1, name="bad", display_name="Bad", operating_mode_id=kind.id)
    andere = Zone(id=2, name="andere", display_name="Andere", operating_mode_id=kind.id)
    session.add_all([bad, andere])
    session.flush()
    source(session, "api")

    counter = 0

    def _token_fuer(permissions: list[tuple[str, str | None]]) -> dict[str, str]:
        nonlocal counter
        counter += 1
        aufgeloest = [(code, bad.id if zone == "bad" else None) for code, zone in permissions]
        besitzer = user_with_permissions(session, f"api-{counter}", aufgeloest)
        _token, plaintext = token_ausstellen(
            session, besitzer, f"test-{counter}", aufgeloest, None
        )
        return {"Authorization": f"Bearer {plaintext}"}

    return _token_fuer


def test_ohne_token_kein_zugriff(client) -> None:
    assert client.get("/api/v1/zones").status_code == 401


def test_ungueltiges_token_wird_abgewiesen(client) -> None:
    response = client.get("/api/v1/zones", headers={"Authorization": "Bearer tctl_x_y"})
    assert response.status_code == 401


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
    device = create_device(session, beispiele["geraete"][2])
    session.get(Zone, 1).temperature_source_device_id = device.id
    session.add(
        ZoneDevice(
            zone_id=2,
            device_id=device.id,
            device_role_id=rolle(session, "controller").id,
        )
    )
    capability = DeviceCapability(code="temperature", label="Temperaturmessung")
    session.add(capability)
    session.flush()
    session.add(DeviceCapabilityLink(device_id=device.id, capability_id=capability.id))
    state = create_device_state(session, device)
    state.availability = "online"
    session.flush()
    kopf = token_fuer([("device.read", None)])

    response = client.get("/api/v1/devices", headers=kopf)

    assert response.status_code == 200
    assert response.json()[0]["external_id"] == beispiele["geraete"][2]
    assert response.json()[0]["availability"] == "online"
    assert response.json()[0]["capabilities"] == ["temperature"]
    assert response.json()[0]["zones"] == ["andere", "bad"]


def test_zonenzustand_ist_nur_fuer_sichtbare_zone_lesbar(
    client, token_fuer, session: Session
) -> None:
    moment = datetime(2026, 8, 29, 8, 0)
    session.add_all(
        [
            ZoneState(
                zone_id=1,
                temperature_c=Decimal("19.75"),
                measured_at=moment,
                sensor_status_id=sensorstatus(session).id,
                updated_at=moment,
            ),
            ZoneState(
                zone_id=2,
                temperature_c=Decimal("21.00"),
                measured_at=moment,
                sensor_status_id=sensorstatus(session).id,
                updated_at=moment,
            ),
        ]
    )
    session.flush()
    kopf = token_fuer([("zone.read", "bad")])

    response = client.get("/api/v1/zones/1/state", headers=kopf)

    assert response.status_code == 200
    assert response.json()["temperature_c"] == "19.75"
    assert response.json()["sensor_status"] == "ok"
    assert client.get("/api/v1/zones/2/state", headers=kopf).status_code == 404


def test_sichtbare_zone_ohne_zustand_ergibt_404(client, token_fuer) -> None:
    kopf = token_fuer([("zone.read", "bad")])
    assert client.get("/api/v1/zones/1/state", headers=kopf).status_code == 404


def test_uebersteuern_ohne_recht_wird_abgewiesen(client, token_fuer) -> None:
    kopf = token_fuer([("zone.read", "bad")])
    response = client.post("/api/v1/zones/1/override", headers=kopf,
                          json={"temperature_c": "22.0", "duration_minutes": 30})
    assert response.status_code == 403


def test_uebersteuern_mit_recht_legt_eintrag_an(client, token_fuer, session) -> None:
    from thermoctl.db.models.override import ZoneOverride

    kopf = token_fuer([("zone.read", "bad"), ("override.create", "bad")])
    response = client.post("/api/v1/zones/1/override", headers=kopf,
                          json={"temperature_c": "22.0", "duration_minutes": 30})
    assert response.status_code == 201
    entry = session.query(ZoneOverride).one()
    assert entry.ends_at is not None  # Dauer wird beim Anlegen ausgerechnet
    assert entry.created_by_token_id is not None


def test_api_braucht_kein_csrf_token(client, token_fuer) -> None:
    """Token-Anfragen schicken kein Cookie und sind damit nicht CSRF-gefaehrdet."""
    kopf = token_fuer([("zone.read", "bad"), ("override.create", "bad")])
    response = client.post("/api/v1/zones/1/override", headers=kopf,
                          json={"temperature_c": "22.0", "duration_minutes": 30})
    assert response.status_code == 201


def test_token_hash_erscheint_in_keiner_antwort(client, token_fuer) -> None:
    kopf = token_fuer([("zone.read", "bad"), ("token.self", None)])
    assert "token_hash" not in client.get("/api/v1/me", headers=kopf).text


def test_me_ohne_recht_wird_abgewiesen(client, token_fuer) -> None:
    """token.self fehlt hier bewusst -- auch das eigene Token einsehen ist ein Recht."""
    kopf = token_fuer([("zone.read", "bad")])
    response = client.get("/api/v1/me", headers=kopf)
    assert response.status_code == 403


def test_uebersteuern_bis_naechste_schaltung_ohne_zeitplan(client, token_fuer) -> None:
    """Ohne Schaltpunkte in der Zone bleibt die Uebersteuerung unbefristet."""
    kopf = token_fuer([("zone.read", "bad"), ("override.create", "bad")])
    response = client.post(
        "/api/v1/zones/1/override", headers=kopf,
        json={"temperature_c": "22.0", "until_next_switch": True},
    )
    assert response.status_code == 201
    assert response.json()["ends_at"] is None


def test_uebersteuern_bis_naechste_schaltung_mit_zeitplan(client, token_fuer, session) -> None:
    from thermoctl.db.models.operations import Setting
    from thermoctl.db.models.schedule import SchedulePoint
    from thermoctl.db.models.zone import SetpointMode

    mode = SetpointMode(code="tag", name="Tag")
    session.add(mode)
    session.flush()
    session.add(SchedulePoint(zone_id=1, weekday=1, minute_of_day=0, setpoint_mode_id=mode.id))
    session.add(Setting(id=1, timezone="Europe/Berlin", frost_protection_mode_id=mode.id))
    session.flush()

    kopf = token_fuer([("zone.read", "bad"), ("override.create", "bad")])
    response = client.post(
        "/api/v1/zones/1/override", headers=kopf,
        json={"temperature_c": "22.0", "until_next_switch": True},
    )
    assert response.status_code == 201
    assert response.json()["ends_at"] is not None


def test_uebersteuerung_loeschen_ohne_recht_wird_abgewiesen(client, token_fuer) -> None:
    kopf = token_fuer([("zone.read", "bad")])
    response = client.delete("/api/v1/zones/1/override", headers=kopf)
    assert response.status_code == 403


def test_uebersteuerung_loeschen_beendet_die_aktive(client, token_fuer, session) -> None:
    from thermoctl.db.models.override import ZoneOverride

    kopf = token_fuer([("zone.read", "bad"), ("override.create", "bad"),
                       ("override.cancel", "bad")])
    client.post("/api/v1/zones/1/override", headers=kopf,
               json={"temperature_c": "22.0", "duration_minutes": 30})
    response = client.delete("/api/v1/zones/1/override", headers=kopf)
    assert response.status_code == 204
    entry = session.query(ZoneOverride).one()
    assert entry.cancelled_at is not None


def _zone_with_plan(session: Session) -> None:
    """Zone 1 bekommt Tag ab 00:00 und Nacht ab 22:00, beide mit Sollwert."""
    from thermoctl.db.models.operations import Setting
    from thermoctl.db.models.schedule import SchedulePoint
    from thermoctl.db.models.zone import SetpointMode, ZoneSetpoint

    day = SetpointMode(code="tag", name="Tag")
    night = SetpointMode(code="nacht", name="Nacht")
    session.add_all([day, night])
    session.flush()
    session.add_all(
        [
            Setting(id=1, timezone="UTC", frost_protection_mode_id=day.id),
            SchedulePoint(zone_id=1, weekday=1, minute_of_day=0, setpoint_mode_id=day.id),
            SchedulePoint(zone_id=1, weekday=1, minute_of_day=1320, setpoint_mode_id=night.id),
            ZoneSetpoint(zone_id=1, setpoint_mode_id=day.id, temperature_c=Decimal("21.0")),
            ZoneSetpoint(zone_id=1, setpoint_mode_id=night.id, temperature_c=Decimal("18.0")),
        ]
    )
    session.flush()


def test_boost_braucht_das_recht_zu_uebersteuern(client, token_fuer) -> None:
    """Es *ist* eine Uebersteuerung -- nur eine, deren Wert der Zeitplan bestimmt."""
    kopf = token_fuer([("zone.read", "bad")])
    assert client.post("/api/v1/zones/1/boost", headers=kopf).status_code == 403


def test_boost_zieht_die_naechste_schaltung_vor(client, token_fuer, session) -> None:
    from thermoctl.db.models.override import ZoneOverride

    _zone_with_plan(session)
    kopf = token_fuer([("zone.read", "bad"), ("override.create", "bad")])

    response = client.post("/api/v1/zones/1/boost", headers=kopf)

    assert response.status_code == 201
    daten = response.json()
    # Der Modus steht mit drin: "18,0 °C bis 22:00" sagt nicht, warum.
    assert daten["mode_code"] in ("tag", "nacht")
    assert daten["gilt_bis"] is not None
    entry = session.query(ZoneOverride).one()
    # Sie endet an der Schaltung, die sie vorzieht -- nicht irgendwann.
    assert entry.ends_at is not None
    assert entry.ends_at.isoformat() == daten["gilt_bis"]


def test_boost_ohne_zeitplan_sagt_warum(client, token_fuer, session) -> None:
    """Gegenprobe: Ohne Plan gibt es nichts vorzuziehen, und das ist kein Serverfehler."""
    from thermoctl.db.models.operations import Setting
    from thermoctl.db.models.zone import SetpointMode

    mode = SetpointMode(code="frost", name="Frost")
    session.add(mode)
    session.flush()
    session.add(Setting(id=1, timezone="UTC", frost_protection_mode_id=mode.id))
    session.flush()

    kopf = token_fuer([("zone.read", "bad"), ("override.create", "bad")])
    response = client.post("/api/v1/zones/1/boost", headers=kopf)

    assert response.status_code == 409
    assert "Zeitplan" in response.json()["detail"]


def _defaults(session: Session) -> None:
    """Die globalen Vorgaben, von denen jede Zone erbt."""
    from thermoctl.db.models.operations import Setting
    from thermoctl.db.models.zone import SetpointMode

    mode = SetpointMode(code="frost", name="Frost")
    session.add(mode)
    session.flush()
    session.add(Setting(id=1, timezone="UTC", frost_protection_mode_id=mode.id))
    session.flush()


def test_ein_einzelner_parameter_laesst_die_uebrigen_geerbt(client, token_fuer, session) -> None:
    """Wer nur die Hysterese aendern will, soll nicht alle sechs mitschicken muessen.

    Er schriebe dabei jeden geerbten Wert als Zonenabweichung fest -- und eine spaetere
    Aenderung des globalen Standards ginge an dieser Zone vorbei.
    """
    _defaults(session)
    kopf = token_fuer([("zone.read", "bad"), ("zone.manage", "bad")])

    response = client.put(
        "/api/v1/zones/1/parameters/hysteresis_k", headers=kopf, json={"value": "0.40"}
    )

    assert response.status_code == 200
    assert response.json()["hysteresis_k"] == "0.40"
    zone = session.get(Zone, 1)
    assert zone is not None
    assert zone.hysteresis_k == Decimal("0.40")
    assert zone.min_on_seconds is None, "ein geerbter Wert wurde festgeschrieben"


def test_ein_unbekannter_parametername_nennt_die_gueltigen(client, token_fuer, session) -> None:
    _defaults(session)
    kopf = token_fuer([("zone.read", "bad"), ("zone.manage", "bad")])
    response = client.put(
        "/api/v1/zones/1/parameters/farbe", headers=kopf, json={"value": "1"}
    )
    assert response.status_code == 404
    assert "hysteresis_k" in response.json()["detail"]


def test_ein_parameter_ausserhalb_der_grenzen_wird_abgewiesen(client, token_fuer, session) -> None:
    """Die Grenzen stehen in der Domaene und gelten fuer jeden Weg gleich."""
    _defaults(session)
    kopf = token_fuer([("zone.read", "bad"), ("zone.manage", "bad")])
    response = client.put(
        "/api/v1/zones/1/parameters/hysteresis_k", headers=kopf, json={"value": "99"}
    )
    assert response.status_code == 422
    zone = session.get(Zone, 1)
    assert zone is not None
    assert zone.hysteresis_k is None


def test_ein_einzelner_parameter_braucht_zone_manage(client, token_fuer, session) -> None:
    _defaults(session)
    kopf = token_fuer([("zone.read", "bad")])
    response = client.put(
        "/api/v1/zones/1/parameters/hysteresis_k", headers=kopf, json={"value": "0.4"}
    )
    assert response.status_code == 403
