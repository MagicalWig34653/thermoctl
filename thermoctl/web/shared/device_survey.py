"""Geraeteueberblick samt Befunden -- Quelle fuer /ui-api."""
from collections import defaultdict
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.db.base import utcnow
from thermoctl.db.models.device import Device, DeviceCapabilityLink, ZoneDevice
from thermoctl.db.models.lookup import DeviceCapability, Integration
from thermoctl.db.models.measurement import DeviceHealth
from thermoctl.db.models.operations import Setting
from thermoctl.db.models.zone import Zone
from thermoctl.domain.device_survey import (
    MEROSS_SILENT_AFTER_SECONDS,
    WITHOUT_CHIP,
    DeviceSurvey,
    findings,
)
from thermoctl.services.meross_discovery import INTEGRATION_CODE as MEROSS_INTEGRATION_CODE

# The threshold after which the device page considers a device silent when setup
# hasn't created defaults yet. The page is reachable at exactly that point, and
# without this fallback it would have no threshold at all -- a device would never be
# silent, no matter how long it stayed quiet.
SILENT_WITHOUT_DEFAULTS_SECONDS = 900


def build_device_survey(session: Session) -> tuple[list[DeviceSurvey], Setting | None]:
    """Der Geräteüberblick samt Befunden, dringendste zuerst -- Quelle für HTML und `/ui-api`."""
    rows = session.execute(
        select(Device, Integration, DeviceHealth)
        .join(Integration, Integration.id == Device.integration_id)
        .outerjoin(DeviceHealth, DeviceHealth.device_id == Device.id)
        .order_by(Device.display_name, Device.id)
    ).all()
    capabilities: defaultdict[int, list[str]] = defaultdict(list)
    still: defaultdict[int, int] = defaultdict(int)
    for device_id, code, label in session.execute(
        select(DeviceCapabilityLink.device_id, DeviceCapability.code, DeviceCapability.label)
        .join(DeviceCapability, DeviceCapability.id == DeviceCapabilityLink.capability_id)
        .order_by(DeviceCapability.label)
    ):
        if code in WITHOUT_CHIP:
            still[device_id] += 1
        else:
            capabilities[device_id].append(label)

    zones: defaultdict[int, set[str]] = defaultdict(set)
    for device_id, display_name in session.execute(
        select(ZoneDevice.device_id, Zone.display_name).join(Zone, Zone.id == ZoneDevice.zone_id)
    ):
        zones[device_id].add(display_name)
    for device_id, display_name in session.execute(
        select(Zone.temperature_source_device_id, Zone.display_name).where(
            Zone.temperature_source_device_id.is_not(None)
        )
    ):
        if device_id is not None:
            zones[device_id].add(display_name)

    defaults = session.get(Setting, 1)
    silent_after = (
        defaults.default_sensor_timeout_seconds
        if defaults is not None
        else SILENT_WITHOUT_DEFAULTS_SECONDS
    )
    now = utcnow()
    survey = []
    for device, integration, state in rows:
        # A Meross socket never sends us anything on its own -- `device_health` stays
        # empty for it forever, because `services/ingest.py` only ever writes there
        # from an incoming Zigbee2MQTT message. Its only sign of life is
        # `device.last_seen_at`, written by the hourly cloud reconciliation
        # (`services/meross_discovery.py::save_devices`) whenever the cloud still
        # calls it online. Reading `state.last_payload_at` for it, as this used to,
        # meant every Meross socket showed "hat sich noch nie gemeldet" forever, no
        # matter how often the reconciliation found it -- a wrong claim, not just an
        # unhelpful one: the device *was* reporting in, just not over MQTT.
        is_meross = integration.code == MEROSS_INTEGRATION_CODE
        last_heard = (
            device.last_seen_at
            if is_meross
            else (state.last_payload_at if state else None)
        )
        last_heard_kind: Literal["gemeldet", "abgeglichen"] = (
            "abgeglichen" if is_meross else "gemeldet"
        )
        # The sensor timeout fits a device that is expected to speak up on its own
        # schedule. A Meross socket only ever gets fresher once an hour, at the
        # reconciliation's pace -- judging it by the sensor timeout would flag every
        # single one as silent between reconciliations. See
        # `MEROSS_SILENT_AFTER_SECONDS` for the reasoning behind the number used
        # instead.
        silent_after_for_device = MEROSS_SILENT_AFTER_SECONDS if is_meross else silent_after
        survey.append(
            DeviceSurvey(
                device_id=device.id,
                name=device.display_name,
                model=device.model,
                integration=integration.label,
                ist_group=device.is_group,
                capabilities=capabilities[device.id],
                quiet_capabilities=still[device.id],
                zones=sorted(zones[device.id]),
                last_heard=last_heard,
                battery=state.battery_percent if state else None,
                radio_quality=state.link_quality if state else None,
                findings=findings(
                    active=device.is_enabled,
                    last_heard=last_heard,
                    availability=state.availability if state else None,
                    battery=state.battery_percent if state else None,
                    radio_quality=state.link_quality if state else None,
                    silent_after_seconds=silent_after_for_device,
                    now=now,
                    last_heard_kind=last_heard_kind,
                ),
            )
        )
    # Notable ones on top: the question someone arrives with is almost always "is
    # something wrong?" -- and the answer shouldn't be buried under twenty healthy
    # devices.
    survey.sort(key=lambda g: (g.severity, g.name))
    return survey, defaults
