"""Zonenstatus fuer die Startseiten: ein Aufbau fuer /ui-api."""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from thermoctl.db.models.lookup import SensorStatus
from thermoctl.db.models.operations import Setting
from thermoctl.db.models.override import ZoneOverride
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.state import ZoneState
from thermoctl.db.models.zone import SetpointMode, Zone, ZoneSetpoint
from thermoctl.domain.authz import has_permission
from thermoctl.domain.modes import MAXIMUM_TEMPERATURE_C, MINIMUM_TEMPERATURE_C
from thermoctl.domain.principal import Principal
from thermoctl.domain.schedule import (
    BulkSetpointContext,
    resolved_setpoint,
    running_vacation,
    week_segments,
)
from thermoctl.domain.time import local_time
from thermoctl.domain.zones import latest_decisions_by_zone
from thermoctl.services.emergency_state import zone_emergency_view
from thermoctl.web import warmth_fraction

MINUTES_PER_DAY = 1440


def day_track(
    modes: dict[int, SetpointMode],
    temperatures: dict[tuple[int, int], Decimal],
    points_per_zone: dict[int, list[SchedulePoint]],
    weekday: int,
) -> dict[int, list[dict[str, object]]]:
    """Today's schedule per zone as segments with share, time, and setpoint.

    The same decomposition as the week view (`wochenabschnitte`), just restricted to
    one day -- a second version of the same logic in the browser would be exactly what
    principle 6 forbids.

    Takes its data pre-fetched rather than querying: `zone_status_context` (the only
    caller) already loads all three -- modes, per-zone-and-mode temperatures, and
    schedule points -- once for the whole page, the same data `resolved_setpoint`
    needs per zone. Querying them again here would be the exact N+1 duplication this
    module exists to avoid.
    """
    if not points_per_zone:
        return {}
    names = {identifier: mode.name for identifier, mode in modes.items()}

    tracks: dict[int, list[dict[str, object]]] = {}
    for zone_id, points in points_per_zone.items():
        segments = [
            a for a in week_segments(points, names) if a.weekday == weekday
        ]
        tracks[zone_id] = [
            {
                "start": segment.start_minute,
                "width": (segment.end_minute - segment.start_minute)
                * 100
                / MINUTES_PER_DAY,
                "left": segment.start_minute * 100 / MINUTES_PER_DAY,
                "mode_name": segment.mode_name,
                "temperature": temperatures.get((zone_id, segment.mode_id)),
                "warmth": warmth_fraction(temperatures.get((zone_id, segment.mode_id))),
            }
            for segment in segments
        ]
    return tracks


def zone_status_context(
    session: Session,
    principal: Principal,
    zones: list[Zone],
    now: datetime,
    settings: Setting | None,
) -> dict[str, object]:
    """Was beide Startseiten -- Anlage und Wohnung -- gleichermaßen brauchen:
    Zustand, aufgelöster Sollwert, laufende Übersteuerung, letzte Entscheidung,
    Bearbeitungsrechte und der Tagesverlauf je sichtbarer Zone.

    Eine Rechnung statt zweier: Grundsatz 6 verbietet eine zweite Fassung derselben
    Logik im Browser -- dieselbe Regel gilt hier zwischen den beiden Serveransichten.
    Die Anlagensicht (`start()` unten) ergänzt danach ihre eigenen, zusätzlichen
    Daten (Riegel, Brücke, Verbund, Urlaub, Außentemperatur); die Mieteransicht
    bekommt diese Zusätze gar nicht erst in ihren Kontext.
    """
    zone_ids = [zone.id for zone in zones]
    now_utc = now
    settings_row = settings
    local_now = local_time(now_utc, settings_row.timezone if settings_row is not None else None)
    states = {
        zone_id: (state, sensor_status_of)
        for zone_id, state, sensor_status_of in session.execute(
            select(ZoneState.zone_id, ZoneState, SensorStatus)
            .join(SensorStatus, SensorStatus.id == ZoneState.sensor_status_id)
            .where(ZoneState.zone_id.in_(zone_ids))
        )
    }
    # Bündelung statt N+1 (siehe fix/uebersicht-tempo): eine Anfrage je Tabelle über
    # alle sichtbaren Zonen, statt `resolved_setpoint` seine gut ein Dutzend eigenen
    # Abfragen je Zone selbst stellen zu lassen -- auf MariaDB hinter einem
    # Reverse Proxy kostet jede davon eine echte Netzrunde. `BulkSetpointContext`
    # trägt genau dieselbe Vorrangkette wie zuvor, nur mit vorab geladenen Daten
    # statt eigener Abfragen je Zone (Grundsatz 6: eine einzige Fassung der Logik).
    #
    # `overrides` (Anzeige des Übersteuerungs-Banners) und `overrides_by_zone` (was
    # `resolved_setpoint` als laufende Übersteuerung ansieht) sind bewusst dieselbe
    # Abfrage: vorher hatte die Anzeige-Abfrage keine `id`-Tiebreak-Sortierung, die
    # zonenweise Auflösung über `_running_override` aber schon -- bei zwei
    # Übersteuerungen innerhalb derselben MariaDB-Sekunde (Sekundenpräzision) konnten
    # Banner und tatsächliche Entscheidung damit unbemerkt auseinanderlaufen. Jetzt
    # sind es garantiert dieselbe Zeile.
    overrides: dict[int, ZoneOverride] = {}
    for entry in session.scalars(
        select(ZoneOverride)
        .where(
            ZoneOverride.zone_id.in_(zone_ids),
            ZoneOverride.cancelled_at.is_(None),
            ZoneOverride.starts_at <= now_utc,
            or_(ZoneOverride.ends_at.is_(None), ZoneOverride.ends_at > now_utc),
        )
        .order_by(ZoneOverride.created_at.desc(), ZoneOverride.id.desc())
    ):
        overrides.setdefault(entry.zone_id, entry)
    decisions = latest_decisions_by_zone(session, zone_ids)

    modes_by_id = {m.id: m for m in session.scalars(select(SetpointMode))}
    temperatures: dict[tuple[int, int], Decimal] = {
        (zone_id, mode_id): temperature
        for zone_id, mode_id, temperature in session.execute(
            select(
                ZoneSetpoint.zone_id,
                ZoneSetpoint.setpoint_mode_id,
                ZoneSetpoint.temperature_c,
            ).where(ZoneSetpoint.zone_id.in_(zone_ids))
        )
    }
    points_per_zone: dict[int, list[SchedulePoint]] = {zone_id: [] for zone_id in zone_ids}
    for point in session.scalars(
        select(SchedulePoint).where(SchedulePoint.zone_id.in_(zone_ids))
    ):
        points_per_zone[point.zone_id].append(point)

    # Only built when there is at least one zone to resolve -- `zone_ids` empty
    # means the `setpoints` dict comprehension below never calls `resolved_setpoint`
    # at all, so `ctx` is never read. Guarding it here (rather than asserting
    # `settings_row` unconditionally) matters for exactly the case the original,
    # per-zone `resolved_setpoint` call never hit either: a principal with zero
    # visible zones, on an instance whose `setting` row does not exist yet.
    ctx: BulkSetpointContext | None = None
    if zone_ids:
        assert settings_row is not None, "setting-Zeile fehlt — Einrichtung unvollstaendig"
        frost_code = session.scalar(
            select(SetpointMode.code).where(
                SetpointMode.id == settings_row.frost_protection_mode_id
            )
        )
        ctx = BulkSetpointContext(
            settings=settings_row,
            frost_code=frost_code,
            temperatures=temperatures,
            modes_by_id=modes_by_id,
            overrides_by_zone=overrides,
            vacation=running_vacation(session, now_utc),
            points_by_zone=points_per_zone,
        )

    # Auftrag 8b: one banner per zone, shared verbatim with the control page and
    # REST/MCP (`services/emergency_state.zone_emergency_view`, Grundsatz 6).
    # `None` for the overwhelming majority of zones that have never seen a
    # sensor failure -- `zone_banner` itself returns `None` for `STAGE_NORMAL`.
    emergency_banners = {
        zone.id: zone_emergency_view(session, zone, now_utc, settings_row).banner
        for zone in zones
    }

    return {
        "states": states,
        "emergency_banners": emergency_banners,
        "setpoints": {
            zone.id: resolved_setpoint(session, zone, now_utc, ctx) for zone in zones
        },
        "overrides": overrides,
        "decisions": decisions,
        "may_override": {
            zone.id
            for zone in zones
            if has_permission(principal, "override.create", zone.id)
        },
        "may_cancel": {
            zone.id
            for zone in zones
            if has_permission(principal, "override.cancel", zone.id)
        },
        "may_edit_setpoint": {
            zone.id
            for zone in zones
            if has_permission(principal, "setpoint.write", zone.id)
        },
        "minimum_temperature": MINIMUM_TEMPERATURE_C,
        "maximum_temperature": MAXIMUM_TEMPERATURE_C,
        "mode_names": {identifier: mode.name for identifier, mode in modes_by_id.items()},
        "day_tracks": day_track(
            modes_by_id, temperatures, points_per_zone, local_now.isoweekday()
        ),
        "now_fraction": (local_now.hour * 60 + local_now.minute) * 100 / MINUTES_PER_DAY,
        "timezone": settings_row.timezone if settings_row is not None else "UTC",
        "poll_interval_seconds": (
            settings_row.shadow_interval_seconds if settings_row is not None else 60
        ),
    }
