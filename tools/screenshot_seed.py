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
from thermoctl.db.models.vacation import Vacation
from thermoctl.db.models.zone import SetpointMode, ZoneSetpoint
from thermoctl.domain.absence import start_absence
from thermoctl.domain.controller import set_binding
from thermoctl.domain.controller_channels import configure_channel

ZONE_NAMES = ("Wohnzimmer", "Küche", "Bad", "Schlafzimmer", "Kinderzimmer", "Büro")
ZONE_SLUGS = ("wohnzimmer", "kueche", "bad", "schlafzimmer", "kinderzimmer", "buero")


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
            entry = create_device_command(session, zone, actuator, at=at)
            entry.command = "on" if decision.would_heat else "off"
            entry.payload = '{"state":"ON"}' if decision.would_heat else '{"state":"OFF"}'
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
