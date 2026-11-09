"""Von einer Störungsmeldung zu den Räumen, deren Schalter sie betrifft.

Der Raum-Schalter `zone.fault_notifications_enabled` entscheidet nur über die
**Zustellung** (Webhook). Dieses Modul löst für eine `FaultNotice` anhand ihres
Schlüssels auf, zu welchen Zonen sie gehört, und reicht deren Schalter an die reine
Entscheidung in `domain/fault_notice.py` weiter. Die Schlüssel-Formen sind die, die
die Erzeuger vergeben:

* `sensor:<zone id>` (Sensorstörung, festhängender Messwert) und
  `fenster:<zone id>` (Fenster-Alarm): die Zone steht im Schlüssel;
* `notbetrieb:<episode id>` (Notbetrieb-Meldung und -Entwarnung): die Zone steht an
  der Episode (`sensor_failure_episode.zone_id`);
* `schaltbefehl:<device id>` (Schaltbefehl gescheitert/geht wieder durch): am Gerät
  verankert; die Zonen sind die, denen das Gerät als Aktor zugeordnet ist.

Fail-open: ist die Zone nicht auffindbar (unbekannte Schlüsselform, gelöschte Zone
oder Episode ohne Zone, Gerät ohne Aktor-Zuordnung), bleibt die Liste leer und die
Meldung wird zugestellt -- eine Störung wird nie wegen einer Lücke hier verschluckt.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.db.models.device import ZoneDevice
from thermoctl.db.models.lookup import DeviceRole
from thermoctl.db.models.operations import Setting
from thermoctl.db.models.sensor_failure import SensorFailureEpisode
from thermoctl.db.models.zone import Zone
from thermoctl.domain.fault_notice import (
    EMERGENCY_NOTICE_KEY_PREFIX,
    FaultNotice,
    notice_enabled,
    notification_audit_action,
    zones_allow_notice,
)

_ZONE_KEY_PREFIXES = ("sensor:", "fenster:")
_COMMAND_KEY_PREFIX = "schaltbefehl:"


def _id_after(prefix: str, key: str) -> int | None:
    rest = key[len(prefix):]
    return int(rest) if rest.isdecimal() else None


def _flags(session: Session, zone_ids: list[int]) -> list[bool]:
    if not zone_ids:
        return []
    return list(
        session.scalars(
            select(Zone.fault_notifications_enabled).where(Zone.id.in_(zone_ids))
        )
    )


def notice_zone_flags(session: Session, notice: FaultNotice) -> list[bool]:
    """The `fault_notifications_enabled` values of the zones this notice belongs to
    (empty if none can be found -- see the module docstring)."""
    key = notice.key
    for prefix in _ZONE_KEY_PREFIXES:
        if key.startswith(prefix):
            zone_id = _id_after(prefix, key)
            return _flags(session, [] if zone_id is None else [zone_id])
    if key.startswith(EMERGENCY_NOTICE_KEY_PREFIX):
        episode_id = _id_after(EMERGENCY_NOTICE_KEY_PREFIX, key)
        episode = None if episode_id is None else session.get(SensorFailureEpisode, episode_id)
        if episode is None or episode.zone_id is None:
            return []
        return _flags(session, [episode.zone_id])
    if key.startswith(_COMMAND_KEY_PREFIX):
        device_id = _id_after(_COMMAND_KEY_PREFIX, key)
        if device_id is None:
            return []
        zone_ids = list(
            session.scalars(
                select(ZoneDevice.zone_id)
                .join(DeviceRole, DeviceRole.id == ZoneDevice.device_role_id)
                .where(ZoneDevice.device_id == device_id, DeviceRole.code == "actuator")
            )
        )
        return _flags(session, sorted(set(zone_ids)))
    return []


def notice_deliverable(
    session: Session, notice: FaultNotice, setting_row: Setting | None
) -> bool:
    """Whether the webhook path should deliver this notice: plant-wide switch of its
    kind **and** the zone switch(es). `setting_row=None` (setup not finished) is
    fail-open for the plant-wide part, as everywhere else."""
    flags = notice_zone_flags(session, notice)
    if setting_row is None:
        return zones_allow_notice(notice.kind, flags)
    return notice_enabled(notice.kind, setting_row, flags)


def notification_audit_action_for(
    session: Session, notice: FaultNotice, setting_row: Setting | None
) -> str:
    """The audit action (`sent`/`suppressed`) matching `notice_deliverable`."""
    return notification_audit_action(
        notice.kind, setting_row, notice_zone_flags(session, notice)
    )
