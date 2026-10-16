"""Schließt Testlücken der abgelösten Alt-Oberfläche für Betrieb, Schnittstellen, Auswertungen.

Gegen `/ui-api/operation`, `/ui-api/interfaces`, `/ui-api/relay-wear`, `/ui-api/history` und
`/ui-api/heating-time`.
"""

import json
from collections.abc import Callable
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.helpers import (
    capability,
    create_device,
    create_device_command,
    create_settings,
    create_zone,
    role,
)
from thermoctl.config import Settings
from thermoctl.db.base import utcnow
from thermoctl.db.models.device import DeviceCapabilityLink, ZoneDevice
from thermoctl.db.models.measurement import Measurement
from thermoctl.db.models.sensor_failure import (
    ActuatorEmergencyState,
    SensorFailureEpisode,
    SensorFailureSourceComparison,
    ZoneSensorFailureState,
)
from thermoctl.db.models.state import ShadowDecision
from thermoctl.db.models.zone import Zone
from thermoctl.domain import emergency_operation
from thermoctl.integrations.mqtt.publication import alle_topics, command_topics, states_topics
from thermoctl.web import ui_api_operations

Als = Callable[[list[tuple[str, int | None]]], TestClient]

LESEN: list[tuple[str, int | None]] = [("zone.read", None)]


def _now_minutes() -> Any:
    return utcnow().replace(second=0, microsecond=0)


# -- Betriebsseite mit echten Zeilen -----------------------------------------------------------


def _switch_actuator(session: Session, zone: Zone, name: str) -> ZoneDevice:
    device = create_device(session, name)
    session.add(
        DeviceCapabilityLink(device_id=device.id, capability_id=capability(session, "switch").id)
    )
    row = ZoneDevice(
        zone_id=zone.id,
        device_id=device.id,
        device_role_id=role(session, "actuator").id,
        thermostat_mode="controlled",
    )
    session.add(row)
    session.flush()
    return row


def _notbetrieb(session: Session, zone: Zone) -> tuple[ZoneDevice, SensorFailureEpisode]:
    now = utcnow()
    assignment = _switch_actuator(session, zone, "fussbodenschalter")
    episode = SensorFailureEpisode(
        zone_id=zone.id,
        zone_name=zone.display_name,
        started_at=now - timedelta(minutes=30),
        trigger_kind=emergency_operation.TRIGGER_ALLE_QUELLEN,
        profile_version=1,
        fixed_on_seconds=600,
        fixed_off_seconds=1200,
        recovery_seconds=60,
        recovery_samples=2,
        warm_restart_hysteresis_k=Decimal("1"),
        emergency_setpoint_c=Decimal("20"),
        sensor_timeout_seconds=1800,
    )
    session.add(episode)
    session.flush()
    session.add(
        ZoneSensorFailureState(
            zone_id=zone.id,
            episode_id=episode.id,
            stage=emergency_operation.STAGE_NOTBETRIEB,
            failure_started_at=now - timedelta(minutes=30),
            recovery_sample_count=1,
        )
    )
    session.add(
        ActuatorEmergencyState(
            zone_device_id=assignment.id,
            episode_id=episode.id,
            armed_episode_id=episode.id,
            phase="ein",
            phase_deadline_at=now + timedelta(minutes=10),
            on_seconds=600,
            off_seconds=1200,
            cycle_source="festtakt",
            warm_locked=False,
            simulated_phase="ein",
            simulated_phase_deadline_at=now + timedelta(minutes=10),
            simulated_on_seconds=600,
            simulated_off_seconds=1200,
            simulated_cycle_source="festtakt",
            simulated_warm_locked=False,
            profile_version=1,
        )
    )
    probe = create_device(session, "wandfuehler")
    for index, (wall, raw) in enumerate([("20.00", "21.00"), ("19.00", "20.00")]):
        session.add(
            SensorFailureSourceComparison(
                zone_id=zone.id,
                zone_name=zone.display_name,
                device_id=probe.id,
                device_name="wandfuehler",
                measured_at=now - timedelta(minutes=index + 1),
                wall_probe_c=Decimal(wall),
                raw_c=Decimal(raw),
                corrected_c=Decimal(raw),
                echo=False,
                usable=True,
            )
        )
    session.flush()
    return assignment, episode


def test_the_operation_page_walks_real_emergency_rows_without_an_outdoor_source(
    client_als: Als, session: Session
) -> None:
    create_settings(session)
    zone = create_zone(session, "notzimmer")
    assignment, _episode = _notbetrieb(session, zone)

    (room,) = client_als(LESEN).get("/ui-api/operation").json()["rooms"]

    emergency = room["emergency"]
    assert emergency["stage"] == emergency_operation.STAGE_NOTBETRIEB
    assert emergency["stage_label"]
    assert emergency["failure_started_at"] is not None
    assert Decimal(emergency["emergency_setpoint_c"]) == Decimal("20")
    assert emergency["sensor_timeout_seconds"] == 1800
    assert emergency["recovery_sample_count"] == 1 and emergency["recovery_samples"] == 2
    # Keine Außenquelle eingerichtet: Klartext statt Code, und keine erfundene Zahl.
    assert emergency["outdoor_status_text"] == "keine Außenquelle eingerichtet"
    assert emergency["outdoor_temperature_c"] is None
    (actuator,) = emergency["actuators"]
    assert actuator["device_id"] == assignment.device_id
    assert actuator["kind"] == "switch" and actuator["phase"] == "ein"
    assert (actuator["on_seconds"], actuator["off_seconds"]) == (600, 1200)
    assert actuator["cycle_source"] == "festtakt"
    assert actuator["handover_status_text"] == "nicht versucht"
    (comparison,) = emergency["comparisons"]
    assert comparison["device_name"] == "wandfuehler" and comparison["sample_count"] == 2
    assert Decimal(comparison["mean_deviation_k"]) == Decimal("1")
    assert comparison["suggested_offset_text"] == "vorgeschlagener Ausgleichswert ≈ +1.00 K"


def test_the_operation_page_names_an_outdoor_source_by_state_and_value(
    client_als: Als, session: Session
) -> None:
    row = create_settings(session)
    zone = create_zone(session, "aussenzimmer")
    _notbetrieb(session, zone)
    outdoor = create_device(session, "aussenfuehler")
    session.add(
        DeviceCapabilityLink(
            device_id=outdoor.id, capability_id=capability(session, "temperature").id
        )
    )
    row.outdoor_temperature_source_device_id = outdoor.id
    row.default_sensor_timeout_seconds = 1800
    temperature = capability(session, "temperature")
    session.add(
        Measurement(
            device_id=outdoor.id,
            capability_id=temperature.id,
            value_numeric=Decimal("3.2"),
            measured_at=utcnow() - timedelta(minutes=5),
            received_at=utcnow() - timedelta(minutes=5),
        )
    )
    session.flush()
    client = client_als(LESEN)

    fresh = client.get("/ui-api/operation").json()["rooms"][0]["emergency"]
    assert fresh["outdoor_status_text"] == "in Ordnung"
    assert Decimal(fresh["outdoor_temperature_c"]) == Decimal("3.2")

    row.default_sensor_timeout_seconds = 60  # fünf Minuten alt ist jetzt zu alt
    session.flush()
    stale = client.get("/ui-api/operation").json()["rooms"][0]["emergency"]
    assert stale["outdoor_status_text"] == "veraltet (Quelle meldet nicht mehr)"


# -- Homebridge-Themen gegen publication.py -----------------------------------------------------


def _env(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "_env_file": None,
        "database_url": "sqlite://",
        "secret_key": "s" * 32,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def test_all_six_homebridge_topics_are_the_ones_publication_py_builds(
    client_als: Als, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    create_settings(session)
    first = create_zone(session, "eins")
    second = create_zone(session, "zwei")
    prefix = "haus/heizung"
    monkeypatch.setattr(ui_api_operations, "get_settings", lambda: _env(mqtt_prefix=prefix))
    client = client_als([("setting.manage", None), ("zone.read", None)])

    body = client.get("/ui-api/interfaces").json()

    configs = {z["zone_id"]: json.loads(z["config_json"]) for z in body["homebridge_zones"]}
    assert set(configs) == {first.id, second.id}
    for zone in (first, second):
        state = states_topics(zone.id, prefix)
        command = command_topics(zone.id, prefix)
        shown = {name: entry["topic"] for name, entry in configs[zone.id]["topics"].items()}
        assert shown == {
            "getCurrentTemperature": state.current_temperature,
            "getTargetTemperature": state.setpoint,
            "setTargetTemperature": command.setpoint,
            "getCurrentHeatingCoolingState": state.would_heat,
            "getTargetHeatingCoolingState": state.operating_mode,
            "setTargetHeatingCoolingState": command.operating_mode,
        }
        # Jedes angezeigte Thema gehört zum Vertrag der echten Veröffentlichung.
        assert set(shown.values()) <= set(alle_topics(zone.id, prefix))
        assert all(topic.startswith(f"{prefix}/zones/{zone.id}/") for topic in shown.values())
        # Lesen läuft über state/, Schreiben über command/ -- nie vertauscht.
        for name, topic in shown.items():
            assert ("/command/" in topic) == name.startswith("set"), name


# -- Relaisverschleiß ----------------------------------------------------------------------------


def _toggle(session: Session, zone: Zone, name: str, switches: int) -> None:
    """`switches` bestätigte Wechsel (plus ein Ausgangszustand, der keiner ist)."""
    device = create_device(session, name)
    now = _now_minutes()
    for index in range(switches + 1):
        create_device_command(
            session,
            zone,
            device,
            at=now - timedelta(minutes=(switches + 1 - index)),
            command="switch",
            payload=json.dumps({"state": "ON" if index % 2 == 0 else "OFF"}),
        )


def _annual(entry: dict[str, Any]) -> int:
    days = len(entry["days"])
    return (entry["operations_total"] * 365 + days // 2) // days


def test_relay_wear_orders_by_projection_and_grades_the_levels(
    client_als: Als, session: Session
) -> None:
    settings = create_settings(session)
    settings.assumed_relay_lifetime_operations = 1000
    zimmer_a = create_zone(session, "Alpha")
    zimmer_z = create_zone(session, "Zeta")
    _toggle(session, zimmer_z, "wenig-z", 2)
    _toggle(session, zimmer_a, "wenig-a", 2)
    _toggle(session, zimmer_a, "mittel", 12)
    _toggle(session, zimmer_a, "viel", 24)
    session.flush()
    client = client_als([("audit.read", None), ("zone.read", None)])

    body = client.get("/ui-api/relay-wear", params={"period": "7"}).json()

    assert body["assumed_lifetime_operations"] == 1000
    devices = body["devices"]
    # Höchste Hochrechnung zuerst; bei Gleichstand erst Raumname, dann Gerätename.
    assert [(d["zone_name"], d["device_name"]) for d in devices] == [
        ("Alpha", "viel"),
        ("Alpha", "mittel"),
        ("Alpha", "wenig-a"),
        ("Zeta", "wenig-z"),
    ]
    assert [d["operations_total"] for d in devices] == [24, 12, 2, 2]
    assert [d["wear_level"] for d in devices] == ["danger", "warning", "normal", "normal"]
    projections = [d["annual_projection"] for d in devices]
    assert projections == [_annual(d) for d in devices]
    assert projections == sorted(projections, reverse=True)
    assert projections[0] >= 1000 and 500 <= projections[1] < 1000 and projections[2] < 500
    for entry in devices:
        assert entry["lifetime_percent_per_year"] == pytest.approx(
            entry["annual_projection"] * 100 / 1000
        )
        assert entry["lifetime_years"] == pytest.approx(1000 / entry["annual_projection"])


def test_relay_wear_reflects_a_changed_lifetime_assumption(
    client_als: Als, session: Session
) -> None:
    settings = create_settings(session)
    zone = create_zone(session, "Einzel")
    _toggle(session, zone, "relais", 6)
    session.flush()
    client = client_als([("audit.read", None), ("zone.read", None)])

    default = client.get("/ui-api/relay-wear").json()
    assert default["assumed_lifetime_operations"] == 500_000
    (before,) = default["devices"]
    projection = before["annual_projection"]
    assert projection > 0
    assert before["lifetime_percent_per_year"] == pytest.approx(projection * 100 / 500_000)

    settings.assumed_relay_lifetime_operations = 100_000
    session.flush()
    changed = client.get("/ui-api/relay-wear").json()

    assert changed["assumed_lifetime_operations"] == 100_000
    (after,) = changed["devices"]
    assert after["annual_projection"] == projection
    assert after["lifetime_percent_per_year"] == pytest.approx(projection * 100 / 100_000)
    assert after["lifetime_years"] == pytest.approx(100_000 / projection)
    assert after["lifetime_percent_per_year"] == pytest.approx(
        before["lifetime_percent_per_year"] * 5
    )


# -- Verlauf und Heizzeit mit zwei Räumen --------------------


def _decision(zone: Zone, at: Any, **extra: Any) -> ShadowDecision:
    return ShadowDecision(
        decided_at=at,
        zone_id=zone.id,
        setpoint_reason="Zeitplan",
        outcome_code="aus",
        reason="Test",
        **extra,
    )


def test_history_and_heating_time_carry_only_the_values_of_the_asked_zone(
    client_als: Als, session: Session
) -> None:
    create_settings(session)
    cool = create_zone(session, "kuehl")
    warm = create_zone(session, "warm")
    end = _now_minutes() - timedelta(hours=1)
    start = end - timedelta(hours=1)
    for minute in range(10):
        at = start + timedelta(minutes=minute)
        session.add(
            _decision(
                cool,
                at,
                would_heat=False,
                temperature_c=Decimal("18.0"),
                setpoint_c=Decimal("19.0"),
                scheduled_setpoint_c=Decimal("19.0"),
            )
        )
        session.add(
            _decision(
                warm,
                at,
                would_heat=True,
                temperature_c=Decimal("25.0"),
                setpoint_c=Decimal("26.0"),
                scheduled_setpoint_c=Decimal("27.0"),
                solar_setback_k=Decimal("1.0"),
            )
        )
    session.flush()
    client = client_als(LESEN)

    def history(zone: Zone) -> dict[str, Any]:
        params = {"zone_id": zone.id, "from": start.isoformat(), "to": end.isoformat()}
        response = client.get("/ui-api/history", params=params)
        assert response.status_code == 200, response.text
        body: dict[str, Any] = response.json()
        return body

    cool_body, warm_body = history(cool), history(warm)

    assert cool_body["rows"] == 10 and warm_body["rows"] == 10  # nicht 20
    cool_buckets = [b for b in cool_body["buckets"] if b["rows"]]
    warm_buckets = [b for b in warm_body["buckets"] if b["rows"]]
    assert len(cool_buckets) == len(warm_buckets) == 10
    assert {b["temperature_c"] for b in cool_buckets} == {"18.0"}
    assert {b["temperature_c"] for b in warm_buckets} == {"25.0"}
    assert {b["heat_percent"] for b in cool_buckets} == {0}
    assert {b["heat_percent"] for b in warm_buckets} == {100}
    assert {b["effective_min_c"] for b in cool_buckets} == {"19.0"}
    assert {b["scheduled_max_c"] for b in warm_buckets} == {"27.0"}
    assert {b["solar_setback_k"] for b in cool_buckets} == {"0.0"}  # keine Absenkung
    assert {b["solar_setback_k"] for b in warm_buckets} == {"1.0"}

    heating = client.get("/ui-api/heating-time", params={"period": "7"}).json()
    seconds = {z["zone_id"]: z["seconds_total"] for z in heating["zones"]}
    assert seconds[cool.id] == 0
    assert seconds[warm.id] == 540  # 9 Abstände zu 60 s dieses Raums, nicht die beider Räume
    only_cool = client.get("/ui-api/heating-time", params={"zone_id": cool.id}).json()
    assert [z["zone_id"] for z in only_cool["zones"]] == [cool.id]
    assert only_cool["zones"][0]["seconds_total"] == 0
    only_warm = client.get("/ui-api/heating-time", params={"zone_id": warm.id}).json()
    assert [z["zone_id"] for z in only_warm["zones"]] == [warm.id]
    assert only_warm["zones"][0]["seconds_total"] == 540
