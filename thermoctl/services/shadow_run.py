"""The shadow run: assemble the situation per zone, decide, log.

Dry run (section 1 of the specification): this module switches nothing and publishes
nothing. It reads `zone_state` (already advanced by `ingest.zonenzustand_fortschreiben`),
calls `regelung.entscheiden()`, and writes the result as a `shadow_decision` row. These
exact rows later become the basis for comparison against the old system (subproject 4).
"""

import logging
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.db.models.device import ZoneDevice
from thermoctl.db.models.lookup import DeviceCapability, DeviceRole, SensorStatus
from thermoctl.db.models.measurement import Measurement
from thermoctl.db.models.operations import Setting
from thermoctl.db.models.state import ShadowDecision, ZoneState
from thermoctl.db.models.zone import Zone, ZoneSetpoint
from thermoctl.domain.control_loop import Lage, entscheiden
from thermoctl.domain.fault import NO_SOURCE
from thermoctl.domain.schedule import resolved_setpoint
from thermoctl.domain.zone_settings import control_parameters

log = logging.getLogger(__name__)

_FROST_DEFAULT = Decimal("16.0")


def _frost_setpoint(session: Session, zone: Zone, settings: Setting) -> Decimal:
    """The zone's frost protection setpoint for the configured frost protection mode.

    The same fallback as in `aufgeloester_sollwert()`: if the zone has no own value
    for this mode, an unremarkable default value applies instead of an error — a
    missing row in `zone_setpoint` must not bring control to a halt.
    """
    value = session.scalar(
        select(ZoneSetpoint.temperature_c).where(
            ZoneSetpoint.zone_id == zone.id,
            ZoneSetpoint.setpoint_mode_id == settings.frost_protection_mode_id,
        )
    )
    return value if value is not None else _FROST_DEFAULT


def _previous_state(
    session: Session, zone_id: int, now: datetime
) -> tuple[bool, int | None, bool | None]:
    """`heizt_gerade` and `seit_s` from the chain of this zone's own past decisions.

    In shadow run nothing actually switches, so there's no real valve state to read
    off whether and since when it's currently heating. The only truth available is
    therefore its own decision history: `would_heat` of the latest row counts as the
    current state, and `seit_s` is the time back to the oldest row that still carries
    the same value. This is exactly what later makes the log comparable to the old
    system (section 6 of the specification) — and is the reason the minimum switch
    duration (rule 5 in `regelung.entscheiden`) has any effect at all in shadow run:
    without this derivation, `seit_s` would be `None` on every cycle.

    Also returns the raw `previous_would_heat` for the new row: `None` if there's no
    history at all yet, otherwise the most recently decided value.
    """
    zeilen = list(
        session.execute(
            select(ShadowDecision.would_heat, ShadowDecision.decided_at)
            .where(ShadowDecision.zone_id == zone_id)
            .order_by(ShadowDecision.decided_at.desc(), ShadowDecision.id.desc())
        )
    )
    if not zeilen:
        return False, None, None

    aktuell = zeilen[0].would_heat
    beginn = zeilen[0].decided_at
    for state, moment in zeilen:
        if state != aktuell:
            break
        beginn = moment
    return aktuell, int((now - beginn).total_seconds()), aktuell


def _window_situation(
    session: Session, zone: Zone, state: ZoneState | None, now: datetime
) -> tuple[bool, int | None]:
    """Window state and duration since the last closing, from history."""
    if state is None or state.window_open is not False:
        return bool(state and state.window_open), None

    contact = session.scalar(select(DeviceCapability).where(DeviceCapability.code == "contact"))
    rolle = session.scalar(select(DeviceRole).where(DeviceRole.code == "window_contact"))
    if contact is None or rolle is None:
        return False, None
    devices_ids = list(
        session.scalars(
            select(ZoneDevice.device_id).where(
                ZoneDevice.zone_id == zone.id,
                ZoneDevice.device_role_id == rolle.id,
            )
        )
    )
    last_closed: datetime | None = None
    for device_id in devices_ids:
        previous_value: str | None = None
        for value, gemessen_am in session.execute(
            select(Measurement.value_text, Measurement.measured_at)
            .where(
                Measurement.device_id == device_id,
                Measurement.capability_id == contact.id,
                Measurement.value_text.in_(("true", "false")),
            )
            .order_by(Measurement.measured_at, Measurement.id)
        ):
            if value == "true" and previous_value == "false":
                last_closed = max(
                    last_closed or gemessen_am,
                    gemessen_am,
                )
            previous_value = value
    if last_closed is None:
        return False, None
    return False, max(0, int((now - last_closed).total_seconds()))


def _process_zone(session: Session, zone: Zone, now: datetime) -> ShadowDecision:
    settings = session.get(Setting, 1)
    assert settings is not None, "setting-Zeile fehlt — Einrichtung unvollstaendig"

    state = session.get(ZoneState, zone.id)
    if state is None:
        ist_c = None
        sensor_status = NO_SOURCE
    else:
        ist_c = state.temperature_c
        sensor_status_row = session.get(SensorStatus, state.sensor_status_id)
        assert sensor_status_row is not None, "sensor_status-Zeile fehlt zur Referenz"
        sensor_status = sensor_status_row.code
    window_open, window_closed_for_s = _window_situation(session, zone, state, now)

    setpoint = resolved_setpoint(session, zone, now)
    frost_c = _frost_setpoint(session, zone, settings)
    parameter = control_parameters(session, zone)
    heizt_gerade, seit_s, previous_would_heat = _previous_state(session, zone.id, now)

    lage = Lage(
        ist_c=ist_c,
        soll_c=setpoint.temperature_c,
        soll_grund=setpoint.grund,
        frost_c=frost_c,
        operating_mode=zone.operating_mode.code,
        heizt_gerade=heizt_gerade,
        seit_s=seit_s,
        window_open=window_open,
        window_closed_for_s=window_closed_for_s,
        sensor_status=sensor_status,
        parameter=parameter,
    )
    entscheidung = entscheiden(lage)

    zeile = ShadowDecision(
        decided_at=now,
        zone_id=zone.id,
        temperature_c=ist_c,
        setpoint_c=setpoint.temperature_c,
        setpoint_reason=setpoint.grund,
        would_heat=entscheidung.heizen,
        previous_would_heat=previous_would_heat,
        outcome_code=entscheidung.grund_code,
        reason=entscheidung.grund,
    )
    session.add(zeile)
    session.flush()
    return zeile


def cycle(session: Session, now: datetime) -> list[ShadowDecision]:
    """One shadow cycle over all zones — writes, but switches nothing.

    A zone whose processing fails does not hold up the others: each zone runs in its
    own savepoint, whose rollback on an exception only undoes its own incomplete
    changes — not the zones already processed successfully within the same call.
    """
    results: list[ShadowDecision] = []
    for zone in session.scalars(select(Zone).order_by(Zone.id)):
        try:
            with session.begin_nested():
                zeile = _process_zone(session, zone, now)
        except Exception:
            log.exception(
                "Schattenzyklus fuer eine Zone gescheitert — uebrige Zonen laufen weiter",
                extra={"zone_id": zone.id},
            )
            continue
        results.append(zeile)
    return results
