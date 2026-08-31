"""Take Meross devices into the installation.

Until now a Meross socket could appear nowhere: `services/ingest.py` was the only place
that creates `Device` rows, and it reads the Zigbee2MQTT list alone. The switching
adapter existed, but no device it could have applied to -- reported as "the Meross
switches turn up nowhere", and that was right.

This is the second source. It deliberately follows the same rules as the first:

* **The identifier is the device's `uuid`, not its name.** Names change when somebody
  renames something in the Meross app; an assignment hanging on that would be gone
  afterwards. The display name is carried along on every pass.
* **Nothing is deleted.** A device the cloud does not name right now is usually offline,
  or the query failed. A zone would otherwise lose its actuator because the internet
  was away for a moment.
* **A failure disturbs nothing.** No account, no answer, an error of the cloud: it is
  logged, and the installation carries on with what it knows.
"""

import logging
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.config import Settings
from thermoctl.db.models.device import Device, DeviceCapabilityLink
from thermoctl.db.models.lookup import DeviceCapability, Integration
from thermoctl.integrations.meross import (
    JsonTransport,
    MerossDevice,
    MerossError,
    device_list,
    sign_in,
)

log = logging.getLogger(__name__)

INTEGRATION_CODE = "meross"
# A switching socket has exactly one capability that counts for this installation. The
# Meross list names no features the way Zigbee2MQTT does -- the device type `mss710`
# says everything that is needed: it switches.
SWITCH_CAPABILITY = "switch"


def save_devices(
    session: Session, devices: Sequence[MerossDevice], seen_at: datetime
) -> int:
    """Carries the reported devices forward. Returns how many were new."""
    integration = session.scalar(
        select(Integration).where(Integration.code == INTEGRATION_CODE)
    )
    if integration is None:  # pragma: no cover - row comes from the migration
        raise RuntimeError("Anbindung meross fehlt in der Nachschlagetabelle")
    capability = session.scalar(
        select(DeviceCapability).where(DeviceCapability.code == SWITCH_CAPABILITY)
    )

    new_devices = 0
    for found in devices:
        device = session.scalar(
            select(Device).where(
                Device.integration_id == integration.id, Device.external_id == found.uuid
            )
        )
        if device is None:
            device = Device(
                integration_id=integration.id,
                external_id=found.uuid,
                display_name=found.name,
                is_enabled=True,
                first_seen_at=seen_at,
            )
            session.add(device)
            new_devices += 1
        else:
            # The display name follows the Meross app: whoever renames there wants to
            # see it here too. The assignment hangs on the uuid and survives it.
            device.display_name = found.name
        device.model = found.model or None
        if found.online:
            device.last_seen_at = seen_at
        session.flush()

        if capability is not None:
            link = session.scalar(
                select(DeviceCapabilityLink).where(
                    DeviceCapabilityLink.device_id == device.id,
                    DeviceCapabilityLink.capability_id == capability.id,
                )
            )
            if link is None:
                session.add(
                    DeviceCapabilityLink(
                        device_id=device.id, capability_id=capability.id
                    )
                )
    return new_devices


async def refresh(
    session: Session, settings: Settings, transport: JsonTransport, now: datetime
) -> int:
    """Fetches the device list and carries it forward. `0` if there was nothing to fetch.

    Without credentials nothing happens, silently -- Meross is optional, and a warning
    on every pass would be nothing but noise for everyone who does not use it.
    """
    if settings.meross_email is None or settings.meross_password is None:
        return 0
    try:
        account = await sign_in(
            transport,
            settings.meross_api_base,
            settings.meross_email,
            settings.meross_password.get_secret_value(),
        )
        devices = await device_list(transport, settings.meross_api_base, account)
    except MerossError as exc:
        log.error("Meross-Geräteliste nicht abrufbar", extra={"grund": str(exc)})
        return 0
    except Exception:
        log.exception("Meross-Geräteliste nicht abrufbar")
        return 0

    new_devices = save_devices(session, devices, now)
    log.info(
        "Meross-Geräte abgeglichen",
        extra={"gefunden": len(devices), "neu": new_devices},
    )
    return new_devices
