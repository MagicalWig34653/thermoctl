"""Invented, repeatable demonstration data; only for the isolated screenshot DB."""

from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from browser_tests.seed import (
    create_login_tenant_user,
    create_login_user,
    create_schedule_zone,
    create_temperature_device,
    seed_outdoor_measurement,
)
from tests.helpers import (
    capability,
    create_device,
    create_device_command,
    create_device_state,
    create_mode,
    create_passkey,
    create_shadow_decision,
    create_zone_state,
    role,
    source,
)
from thermoctl.auth.tokens import issue_token
from thermoctl.db.base import utcnow
from thermoctl.db.models.device import DeviceCapabilityLink, DeviceProperty, ZoneDevice
from thermoctl.db.models.identity import User
from thermoctl.db.models.lookup import PERMISSIONS
from thermoctl.db.models.operations import AuditEvent
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.sensor_failure import (
    ActuatorEmergencyState,
    SensorFailureEpisode,
    SensorFailureSourceComparison,
    ZoneSensorFailureState,
)
from thermoctl.db.models.vacation import Vacation
from thermoctl.db.models.zone import SetpointMode, ZoneSetpoint
from thermoctl.domain import emergency_operation
from thermoctl.domain.absence import start_absence
from thermoctl.domain.controller import set_binding
from thermoctl.domain.controller_channels import configure_channel
from tools.screenshot_views import ZONE_SLUGS

ZONE_NAMES = ("Wohnzimmer", "Küche", "Bad", "Schlafzimmer", "Kinderzimmer", "Büro")


def seed_demo(session: Session, password: str) -> dict[str, str | int]:
    """Return IDs for paths, never depend on migration-assigned primary keys."""
    now = utcnow().replace(microsecond=0)
    admin = session.scalar(select(User))
    assert admin is not None
    admin.display_name = "Demo-Verwaltung"
    modes = {m.code: m for m in session.scalars(select(SetpointMode))}
    custom = create_mode(session, "demo-komfort", "Komfort")
    paths: dict[str, str | int] = {"mode_id": custom.id}
    zones = []
    for i, (name, slug) in enumerate(zip(ZONE_NAMES, ZONE_SLUGS, strict=True)):
        zone = create_schedule_zone(session, name, day_temperature=Decimal(20 + i % 3))
        zones.append(zone)
        paths[f"zone_{slug}"] = zone.id
        sensor = create_temperature_device(session, f"0x00124b00dead{i:04x}")
        sensor.display_name = f"Raumfühler {name}"
        sensor.model = "Demo-Sensor T1"
        sensor.last_seen_at = now
        zone.temperature_source_device_id = sensor.id
        health = create_device_state(session, sensor)
        health.last_payload_at = now
        health.battery_percent = Decimal(92 - i * 7)
        health.link_quality = 180 - i * 10
        health.availability = "online"
        health.payload_count = 433
        for hour in range(73):
            seed_outdoor_measurement(
                session,
                sensor,
                value_c=Decimal(195 + i * 3 + (hour % 12)) / 10,
                measured_at=now - timedelta(hours=72 - hour),
            )
        state = create_zone_state(session, zone)
        state.temperature_c = Decimal(203 + i * 2) / 10
        state.measured_at = state.updated_at = now
        state.window_open = False
        actuator = create_device(
            session, f"demo-aktor-{i + 1}", "meross" if i == 1 else "zigbee2mqtt"
        )
        actuator.display_name = f"{'Heizkörperventil' if i == 2 else 'Heizkreis'} {name}"
        actuator.model = "Demo-Ventil V1" if i == 2 else "Demo-Schalter S1"
        session.add(
            DeviceCapabilityLink(
                device_id=actuator.id,
                capability_id=capability(session, "thermostat" if i == 2 else "switch").id,
            )
        )
        session.add(
            ZoneDevice(
                zone_id=zone.id,
                device_id=actuator.id,
                device_role_id=role(session, "actuator").id,
                self_regulating=i == 2,
            )
        )
        for day in range(2, 8):
            for minute, code in ((360, "tag"), (1320, "nacht")):
                session.add(
                    SchedulePoint(
                        zone_id=zone.id,
                        weekday=day,
                        minute_of_day=minute,
                        setpoint_mode_id=modes[code].id,
                    )
                )
        for code in ("frost",):
            if code in modes:
                session.add(
                    ZoneSetpoint(
                        zone_id=zone.id, setpoint_mode_id=modes[code].id, temperature_c=Decimal(8)
                    )
                )
        session.add(
            ZoneSetpoint(zone_id=zone.id, setpoint_mode_id=custom.id, temperature_c=Decimal(22))
        )
        for hour in range(72):
            at = now - timedelta(hours=72 - hour)
            decision = create_shadow_decision(session, zone)
            decision.decided_at = at
            decision.temperature_c = Decimal(195 + i * 3 + hour % 12) / 10
            decision.setpoint_c = Decimal(21)
            decision.would_heat = hour % 2 == 0
            decision.previous_would_heat = not decision.would_heat
            decision.outcome_code = "ein" if decision.would_heat else "aus"
            decision.reason = (
                "Temperatur unter Sollwert" if decision.would_heat else "Sollwert erreicht"
            )
        # Six commands keep the log readable; the 72 hourly decisions per zone
        # remain intact for heating charts and relay statistics.
        outcome = ("executed", "suppressed", "failed")[i % 3]
        entry = create_device_command(
            session, zone, actuator, at=now - timedelta(minutes=5 + i * 7),
            outcome_code=outcome, source_code=("system", "web", "api")[i % 3],
        )
        if i != 2:
            entry.command = "on" if i % 2 == 0 else "off"
            entry.payload = '{"state":"ON"}' if i % 2 == 0 else '{"state":"OFF"}'
        entry.reason = ("Zeitplan", "Manuelle Änderung", "Sollwert übertragen")[i % 3]
        if outcome == "failed":
            entry.error = "Gerät nicht erreichbar"
        point = session.scalar(select(SchedulePoint).where(SchedulePoint.zone_id == zone.id))
        assert point is not None
        paths[f"point_{slug}"] = point.id
        session.add(
            AuditEvent(
                occurred_at=now - timedelta(minutes=5 + i),
                source_id=source(session).id,
                actor_user_id=admin.id,
                action="update",
                object_type="zone",
                object_id=str(zone.id),
                summary=f"Wochenplan für {name} angepasst",
                detail="Tag ab 06:00, Nacht ab 22:00",
            )
        )
    # Auftrag 8b: one zone demonstrating the Notbetrieb display -- "Wohnzimmer"
    # (`zones[0]`), reused as the kiosk `kiosk_detail_zone` target so the same
    # scenario shows on start/tenant/kiosk/control without a second zone.
    notbetrieb_zone = zones[0]
    notbetrieb_actuator_assignment = session.scalar(
        select(ZoneDevice).where(
            ZoneDevice.zone_id == notbetrieb_zone.id,
            ZoneDevice.device_role_id == role(session, "actuator").id,
        )
    )
    assert notbetrieb_actuator_assignment is not None
    notbetrieb_episode = SensorFailureEpisode(
        zone_id=notbetrieb_zone.id,
        zone_name=notbetrieb_zone.display_name,
        started_at=now - timedelta(hours=2),
        trigger_kind=emergency_operation.TRIGGER_ALLE_QUELLEN,
        profile_version=1,
        notification_state="gemeldet",
        fixed_on_seconds=600,
        fixed_off_seconds=1200,
        recovery_seconds=120,
        recovery_samples=2,
        warm_restart_hysteresis_k=Decimal("1.0"),
        emergency_setpoint_c=Decimal("20"),
        sensor_timeout_seconds=1800,
    )
    session.add(notbetrieb_episode)
    session.flush()
    session.add(
        ZoneSensorFailureState(
            zone_id=notbetrieb_zone.id,
            episode_id=notbetrieb_episode.id,
            stage=emergency_operation.STAGE_NOTBETRIEB,
            failure_started_at=now - timedelta(hours=2),
            handover_due_signalled=True,
        )
    )
    session.add(
        ActuatorEmergencyState(
            zone_device_id=notbetrieb_actuator_assignment.id,
            episode_id=notbetrieb_episode.id,
            armed_episode_id=notbetrieb_episode.id,
            phase="ein",
            phase_deadline_at=now + timedelta(minutes=8),
            on_seconds=600,
            off_seconds=1800,
            cycle_source="festtakt",
            warm_locked=False,
            simulated_phase="ein",
            simulated_phase_deadline_at=now + timedelta(minutes=8),
            simulated_on_seconds=600,
            simulated_off_seconds=1800,
            simulated_cycle_source="festtakt",
            simulated_warm_locked=False,
            profile_version=1,
        )
    )
    # A few days of Ersatzquelle<->Wandfühler comparison history -- shows the
    # calibration hint on the Betriebsseite without needing a second zone.
    for day in range(1, 5):
        session.add(
            SensorFailureSourceComparison(
                zone_id=notbetrieb_zone.id,
                zone_name=notbetrieb_zone.display_name,
                device_id=notbetrieb_actuator_assignment.device_id,
                device_name="Heizkreis Wohnzimmer",
                measured_at=now - timedelta(days=day),
                wall_probe_c=Decimal("20.0"),
                raw_c=Decimal(str(19.0 + day * 0.1)),
                corrected_c=Decimal(str(19.0 + day * 0.1)),
                echo=False,
                usable=True,
            )
        )

    controller = create_device(session, "0x00124b00deadbeef")
    controller.display_name = "Wandregler Wohnzimmer"
    controller.model = "Demo-Regler R1"
    session.add(
        ZoneDevice(
            zone_id=zones[0].id,
            device_id=controller.id,
            device_role_id=role(session, "controller").id,
        )
    )
    session.add(
        DeviceProperty(
            device_id=controller.id,
            name="occupied_heating_setpoint",
            value_type="numeric",
            unit="°C",
            is_readable=True,
            is_writable=True,
            min_value=Decimal(5),
            max_value=Decimal(30),
            last_value_number=Decimal(21),
            last_value_at=now,
        )
    )
    session.flush()
    configure_channel(
        session,
        controller,
        "occupied_heating_setpoint",
        "read",
        "zone_setpoint",
        zone_id=zones[0].id,
    )
    set_binding(session, controller, "demo_plus", "setpoint_up", Decimal("0.5"))
    set_binding(session, controller, "demo_boost", "boost", None)
    tenant = create_login_tenant_user(
        session,
        "demo-mieter",
        password,
        [(code, z.id) for code, _, scoped in PERMISSIONS if scoped for z in zones[:4]],
    )
    tenant.display_name = "Demo-Wohnung"
    create_login_user(session, "demo-hausdienst", password, [("zone.read", None)])
    start_absence(
        session, zones[:4], Decimal(17), now + timedelta(days=3), now=now, user_id=tenant.id
    )
    session.add(
        Vacation(
            starts_at=now + timedelta(days=7),
            ends_at=now + timedelta(days=14),
            setback_temperature_c=Decimal(16),
            source_id=source(session).id,
            created_by_user_id=admin.id,
        )
    )
    for user in (admin, tenant):
        create_passkey(session, user, f"demo-{user.id}").label = "Demo-Sicherheitsschlüssel"
    issue_token(session, admin, "Demo-Auswertung", [("zone.read", None)], None)
    _, plaintext = issue_token(
        session,
        admin,
        "Demo-Wandtablet",
        [("zone.read", None), ("setpoint.write", None)],
        None,
        is_kiosk=True,
    )
    paths["plaintext"] = plaintext
    session.commit()
    return paths
