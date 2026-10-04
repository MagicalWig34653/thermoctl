"""The shadow run: assemble the situation per zone, decide, log.

Dry run (section 1 of the specification): this module switches nothing and publishes
nothing. It reads `zone_state` (already advanced by `ingest.zonenzustand_fortschreiben`),
calls `regelung.entscheiden()`, and writes the result as a `shadow_decision` row. These
exact rows later become the basis for comparison against the old system (subproject 4).
"""

import logging
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.db.models.device import Device, DeviceCapabilityLink, ZoneDevice
from thermoctl.db.models.lookup import DeviceCapability, DeviceRole, SensorStatus
from thermoctl.db.models.measurement import Measurement
from thermoctl.db.models.operations import Setting
from thermoctl.db.models.override import ZoneOverride
from thermoctl.db.models.sensor_failure import (
    ActuatorDecision,
    ActuatorEmergencyState,
    SensorFailureEpisode,
    SensorFailureSourceComparison,
    ZoneSensorFailureState,
)
from thermoctl.db.models.state import ShadowDecision, ZoneState
from thermoctl.db.models.vacation import Vacation
from thermoctl.db.models.zone import Zone, ZoneSetpoint
from thermoctl.domain import (
    emergency_actuator_plan,
    emergency_cycle,
    emergency_operation,
    sensor_failure_policy,
)
from thermoctl.domain import outdoor as outdoor_domain
from thermoctl.domain.control_loop import (
    REASON_CODE_BLOCKED_MINIMUM_DURATION,
    REASON_CODE_HEATING,
    REASON_CODE_OFF,
    REASON_CODE_VALVE_PROTECTION,
    Decision,
    Situation,
    decide,
)
from thermoctl.domain.fault import NO_SOURCE, OK, VERALTET
from thermoctl.domain.pi_control import (
    INTEGRATOR_RESET,
    RESET_REASON_ARMING,
    RESET_REASON_FROST,
    RESET_REASON_INVALID_STATE,
    RESET_REASON_SENSOR_FAILURE,
    RESET_REASON_VALVE_PROTECTION,
    RESET_REASON_WINDOW_OPEN,
    ActuatorProfile,
    ModulatorState,
    PiCycleInput,
    PiCycleOutput,
    PiState,
    pi_cycle,
    pi_eligible,
    reset_pi_state,
)
from thermoctl.domain.schedule import Setpoint, resolved_setpoint, running_vacation
from thermoctl.domain.solar_setback import HourlyForecast, sun_expected
from thermoctl.domain.solar_setback import apply as apply_solar_setback
from thermoctl.domain.temperature_source_health import (
    SourceHealth,
    ThermostatCandidate,
    WallProbeReading,
    evaluate_source_health,
)
from thermoctl.domain.zone_settings import ControlParameters, control_parameters
from thermoctl.services.emergency_prior import entry_prior_hint
from thermoctl.services.temperature_source_health import zone_candidates

log = logging.getLogger(__name__)

_FROST_DEFAULT = Decimal("16.0")

# Not a `pi_control` reset reason -- there is no stable short code for "the zone
# fails `pi_eligible()`" there (`PiEligibility.reason` is a human sentence for the
# interface, not a code; see the build report for why this was not added to
# `pi_control.py` for this task). The full sentence still reaches the shadow log's
# free-text `reason`, so nothing about *why* is lost -- only the structured column
# gets this one stable placeholder instead of `PiEligibility.reason` verbatim,
# which would not fit `controller_fallback_reason`'s 64 characters reliably.
PI_FALLBACK_INELIGIBLE = "pi_ungeeignet"

# Same reasoning as `PI_FALLBACK_INELIGIBLE` above -- not a `pi_control` reset
# reason, because the concept it names ("this phase started under hysteresis and
# its minimum switch duration has not elapsed yet") belongs to the composition of
# `decide()` and PI in this module, not to PI's own pure state machine.
PI_FALLBACK_HYSTERESIS_MINIMUM = "haelt_hysterese_mindestdauer"


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
) -> tuple[bool, int | None, bool | None, str | None]:
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

    Fourth value, `phase_started_by`: the `effective_controller` of that same
    earliest row -- whichever controller's decision actually started the currently
    held phase. Added 2026-09-27 (second security review of the PI-minimum-duration
    fix) for `_pi_outcome`'s general invariant ("a phase started under hysteresis
    holds for the hysteresis minimum, regardless of which controller decides later
    cycles; a phase PI itself started is subject only to PI's own minimums"): reusing
    this exact row -- the same one `seit_s` already comes from -- means the invariant
    needs no new column and no migration. `None` exactly when there is no history at
    all yet (`seit_s` is `None` too in that case); rule 5 in `decide()` itself treats
    an unknown duration as nothing to enforce yet, and the invariant mirrors that.
    """
    rows = list(
        session.execute(
            select(
                ShadowDecision.would_heat,
                ShadowDecision.decided_at,
                ShadowDecision.effective_controller,
                ShadowDecision.outcome_code,
            )
            .where(ShadowDecision.zone_id == zone_id)
            .order_by(ShadowDecision.decided_at.desc(), ShadowDecision.id.desc())
        )
    )
    if not rows:
        return False, None, None, None

    current_state = rows[0].would_heat
    start = rows[0].decided_at
    phase_started_by = rows[0].effective_controller
    for state, moment, controller, outcome_code in rows:
        if state != current_state:
            break
        start = moment
        phase_started_by = controller
        if outcome_code == OUTCOME_CODE_NOTBETRIEB_RUECKKEHR:
            # Grundsatz 7: the recovery marker (`_seed_recovery_phase_marker`)
            # is the real relay state's only known anchor. Older rows with the
            # same value are the *simulated* timeline that ran alongside the
            # emergency cycle; counting them would credit a hold the real relay
            # never had (minimum on/off duration violated on return).
            break
    return current_state, int((now - start).total_seconds()), current_state, phase_started_by


# Blocker 1 (Hauptsession, nach Auftrag 7b, Grundsatz 7): outcome code for the
# synthetic `ShadowDecision` row `_seed_recovery_phase_marker` inserts the one
# cycle a sensor-failure episode ends -- no ordinary decision is ever written
# with this code, so it stays recognisable in the log as what it is.
OUTCOME_CODE_NOTBETRIEB_RUECKKEHR = "notbetrieb_rueckkehr_start"


def _seed_recovery_phase_marker(session: Session, zone: Zone, now: datetime) -> None:
    """Blocker 1 (Hauptsession-Vorgabe nach Auftrag 7b, Grundsatz 7,
    konservativ): die Mindestschaltdauer-Prüfung nach einer Rückkehr aus dem
    Notbetrieb darf den tatsächlich zuletzt gesendeten Relaiszustand nicht
    verletzen.

    `_previous_state()` liest ausschließlich `shadow_decision` -- die während
    `notbetrieb`/`rueckkehrpruefung` unverändert simuliert weiterlief
    (Auftrag 7a: die Zonenentscheidung bleibt exakt `decide()`s eigene
    Antwort), während das reale Relais in genau dieser Zeit vom
    Notbetriebstakt geschaltet wurde (Auftrag 7b, `services/publishing.py`)
    -- zwei unabhängige Zeitlinien. Ohne Korrektur würde der erste reguläre
    Zyklus nach der Rückkehr `_previous_state()`s simulierte (und unter
    Umständen falsche) Vorstellung von "schon lange gehalten" oder "gerade
    erst umgeschaltet" übernehmen, nicht die reale.

    Löst das konservativ (Grundsatz 7: im Zweifel vorsichtig), nicht durch
    exakte Rekonstruktion der realen Haltezeit: schreibt eine zusätzliche
    `shadow_decision`-Zeile mit `decided_at=now` und `would_heat` = dem real
    zuletzt erfolgreich gesendeten Zustand
    (`ActuatorEmergencyState.last_successful_command_state`, die scharfen
    Felder -- zu diesem Zeitpunkt im Zyklus noch nicht durch den Publisher
    zurückgesetzt, siehe `services/publishing.py::_send_emergency_actuators`,
    das erst nach `shadow_run.cycle()` läuft). Eingefügt *vor* dem
    natürlichen `decide()`-Ergebnis desselben Zyklus (das denselben
    `decided_at`, aber eine höhere `id` bekommt und deshalb als
    `_latest_decision()`s Antwort unverändert gewinnt -- diese Zeile
    beeinflusst nur, was `_previous_state()` beim *nächsten* Aufruf als
    Historie vorfindet): `held_for_s` beginnt dadurch konservativ bei 0 ab
    `now`, nie mit mehr Anrechnung als real erreicht -- die Mindestdauer wird
    dadurch nie zu früh als erfüllt behandelt, höchstens strenger als nötig.

    Nur für Zonen mit genau einem Schaltausgang mit bekanntem scharfem
    Zustand: die reale Anlage hat je Zone höchstens einen
    (`lokal/plaene/0.11.0-geraetevertrag.md` (d)/(e)); bei keinem oder bei
    mehreren Schaltausgängen schreibt diese Funktion bewusst nichts -- ein
    einzelner `would_heat`-Wert für die Zone könnte nicht erfunden werden,
    ohne für einen der Aktoren zu raten (Grundsatz 1), und die bisherige
    (konservativ zu lesende, aber nicht erfundene) Simulation bleibt dann
    einfach stehen.
    """
    switches = [
        zone_device
        for zone_device, _device, kind in zone_actuator_assignments(session, zone)
        if kind == emergency_actuator_plan.KIND_SWITCH
    ]
    if len(switches) != 1:
        return
    row = session.get(ActuatorEmergencyState, switches[0].id)
    if row is None or row.last_successful_command_state is None:
        return
    session.add(
        ShadowDecision(
            zone_id=zone.id,
            decided_at=now,
            setpoint_reason="",
            would_heat=row.last_successful_command_state,
            outcome_code=OUTCOME_CODE_NOTBETRIEB_RUECKKEHR,
            reason=(
                "Rückkehr aus dem Notbetrieb: realer Schaltzustand "
                f"({'ein' if row.last_successful_command_state else 'aus'}) übernommen "
                "-- Grundlage für die Mindestschaltdauer."
            ),
            requested_controller="hysteresis",
            effective_controller="hysteresis",
        )
    )
    session.flush()


def _window_situation(
    session: Session, zone: Zone, state: ZoneState | None, now: datetime
) -> tuple[bool, int | None]:
    """Window state and duration since the last closing, from history."""
    if state is None or state.window_open is not False:
        return bool(state and state.window_open), None

    contact = session.scalar(select(DeviceCapability).where(DeviceCapability.code == "contact"))
    role = session.scalar(select(DeviceRole).where(DeviceRole.code == "window_contact"))
    if contact is None or role is None:
        return False, None
    devices_ids = list(
        session.scalars(
            select(ZoneDevice.device_id).where(
                ZoneDevice.zone_id == zone.id,
                ZoneDevice.device_role_id == role.id,
            )
        )
    )
    last_closed: datetime | None = None
    for device_id in devices_ids:
        previous_value: str | None = None
        for value, measured_at in session.execute(
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
                    last_closed or measured_at,
                    measured_at,
                )
            previous_value = value
    if last_closed is None:
        return False, None
    return False, max(0, int((now - last_closed).total_seconds()))


def _on_off_actuators_only(session: Session, zone: Zone) -> bool:
    """True exactly when `Situation.on_off_actuators_only` should be — the zone has at
    least one actuator and none of them is `ZoneDevice.self_regulating`.

    Deliberately its own tiny query rather than reusing `_pi_actuator_profiles()`:
    that one also resolves each device's capabilities for PI's own, unrelated
    eligibility question, and calling it here would run that extra work on every
    cycle for every zone, PI enabled or not, just to read the one flag this needs.
    """
    actuator_role = session.scalar(select(DeviceRole).where(DeviceRole.code == "actuator"))
    if actuator_role is None:
        return False
    flags = list(
        session.scalars(
            select(ZoneDevice.self_regulating).where(
                ZoneDevice.zone_id == zone.id,
                ZoneDevice.device_role_id == actuator_role.id,
            )
        )
    )
    return bool(flags) and not any(flags)


def _with_solar_setback(
    setpoint: Setpoint,
    frost_c: Decimal,
    zone: Zone,
    parameter: ControlParameters,
    settings: Setting,
    forecast: list[HourlyForecast] | None,
    now: datetime,
) -> tuple[Decimal, str]:
    """The setpoint and its reasoning, corrected for an expected solar gain.

    Correction happens **here**, before `Situation` is built -- not inside
    `regelung.entscheiden()`, which stays exactly as unaware of solar setback as it
    was before this feature existed (see `domain.solar_setback` for why). `forecast`
    being `None` (feature off, no location configured, or the source unreachable --
    `integrations.forecast.ForecastCache` already collapses all three to the same
    thing) and a zone with `solar_gain_factor == 0` both fall straight through
    `solar_setback.apply()` to "no correction", so this function never needs to tell
    those cases apart itself.
    """
    setpoint_c: Decimal = setpoint.temperature_c
    setpoint_reason: str = setpoint.reason
    if forecast is None:
        return setpoint_c, setpoint_reason
    expects_sun = sun_expected(forecast, now, settings.solar_setback_lookahead_hours)
    result = apply_solar_setback(
        setpoint_c,
        frost_c,
        factor=zone.solar_gain_factor,
        max_reduction_k=parameter.solar_setback_max_k,
        expects_sun=expects_sun,
    )
    if result is None:
        return setpoint_c, setpoint_reason
    return (
        result.setpoint_c,
        f"{setpoint_reason} Sonnenabsenkung: -{result.reduction_k} K wegen erwarteter "
        f"Sonneneinstrahlung in den nächsten {settings.solar_setback_lookahead_hours} Stunden.",
    )


def _effective_override(session: Session, zone: Zone, now: datetime) -> ZoneOverride | None:
    """The override currently in force, if any -- same query `resolved_setpoint()`
    uses, but keeping the row itself: `_process_zone` needs the plain boolean for
    `Situation.override_active` (as before), and the PI wiring below needs the
    override's own id for its setpoint-context key (section 2 of the PI
    specification), which a boolean cannot give it.
    """
    return session.scalars(
        select(ZoneOverride)
        .where(
            ZoneOverride.zone_id == zone.id,
            ZoneOverride.cancelled_at.is_(None),
            ZoneOverride.starts_at <= now,
            (ZoneOverride.ends_at.is_(None) | (ZoneOverride.ends_at > now)),
        )
        .order_by(ZoneOverride.created_at.desc(), ZoneOverride.id.desc())
    ).first()


# --------------------------------------------------------------------------- #
# Notbetrieb (Auftrag 7a of `lokal/plaene/0.11.0-notbetrieb.md`): assembles the
# already-built, tested-in-isolation domain modules
# (`domain.temperature_source_health`, `domain.emergency_operation`,
# `domain.emergency_cycle`, `domain.emergency_actuator_plan`) into the shadow
# cycle. Shadow-only, by this task's explicit scope: every `ActuatorDecision`/
# `ActuatorEmergencyState` write below is `simulated=True` -- Auftrag 7b is the
# only place that turns a `handover`/`switch_on`/`switch_off` action into an
# actual command, TRV write suppression, or dedup-cache reset.
#
# Engaged only for a zone that is `sensor_failure_enabled` (on by default since
# 0.11.0, migration `d4a81c6e5b29`), *or* one that has a
# still-open `ZoneSensorFailureState` episode from before it was switched off --
# the latter matters so an episode opened while the zone was enabled is closed
# properly (`emergency_operation.advance`'s own `REASON_DEAKTIVIERT` branch),
# not abandoned mid-episode the moment the flag flips. Every other zone -- the
# zones an operator switched it off for -- never reaches
# `sensor_failure_policy.effective_policy()`, `zone_candidates()`, or any new
# table at all: bitgenau today's behaviour, the plan's explicit regression
# requirement (Auftrag 7a scope, item 1).
# --------------------------------------------------------------------------- #


def derive_switch_phase_started_at(
    phase: str, phase_deadline_at: datetime, on_seconds: int, off_seconds: int
) -> datetime:
    """Reconstructs `emergency_cycle.CycleState.phase_started_at` from what
    `ActuatorEmergencyState` actually persists (`simulated_phase_deadline_at`,
    `simulated_on_seconds`, `simulated_off_seconds`) -- not a guess:
    `emergency_cycle.advance()` always sets `phase_deadline = phase_started_at +
    timedelta(seconds=on_seconds if phase==PHASE_ON else off_seconds)`, so the
    inverse is exact, not an approximation.
    """
    duration = on_seconds if phase == emergency_cycle.PHASE_ON else off_seconds
    return phase_deadline_at - timedelta(seconds=duration)


def zone_actuator_assignments(
    session: Session, zone: Zone
) -> list[tuple[ZoneDevice, Device, str]]:
    """Every actuator assignment of `zone`, classified by plan 1.1 entry 1's rule:
    the strategy follows the device's own capability, not a per-assignment
    field. `switch` wins over `thermostat` when a device has both (`Mischgerät
    wird zentral als Switch behandelt`, `domain.switch_commands.py:99`/`:132`) --
    no such device exists in the plant today
    (`lokal/plaene/0.11.0-geraetevertrag.md` (d)), but the check costs nothing
    and never has to guess if one appears. An actuator with neither capability
    has no entry in plan 1.4's vorrangtabelle at all and is silently skipped,
    not treated as an error.
    """
    actuator_role = session.scalar(select(DeviceRole).where(DeviceRole.code == "actuator"))
    if actuator_role is None:
        return []
    switch = session.scalar(select(DeviceCapability).where(DeviceCapability.code == "switch"))
    thermostat = session.scalar(
        select(DeviceCapability).where(DeviceCapability.code == "thermostat")
    )
    rows = session.execute(
        select(ZoneDevice, Device)
        .join(Device, Device.id == ZoneDevice.device_id)
        .where(ZoneDevice.zone_id == zone.id, ZoneDevice.device_role_id == actuator_role.id)
        .order_by(ZoneDevice.sort_order, ZoneDevice.id)
    ).all()
    result: list[tuple[ZoneDevice, Device, str]] = []
    for zone_device, device in rows:
        capability_ids = set(
            session.scalars(
                select(DeviceCapabilityLink.capability_id).where(
                    DeviceCapabilityLink.device_id == device.id
                )
            )
        )
        if switch is not None and switch.id in capability_ids:
            result.append((zone_device, device, emergency_actuator_plan.KIND_SWITCH))
        elif thermostat is not None and thermostat.id in capability_ids:
            result.append((zone_device, device, emergency_actuator_plan.KIND_THERMOSTAT))
    return result


def _load_emergency_state(
    db_state: ZoneSensorFailureState | None,
) -> emergency_operation.ZoneEmergencyState | None:
    if db_state is None:
        return None
    # `tracked_kind` has no column of its own -- it is fully determined by
    # `active_source_device_id`: `emergency_operation._device_id_for()` only
    # ever returns `None` for kind `KIND_WANDFUEHLER`, never for
    # `KIND_ERSATZQUELLE` (a tracked replacement always names a real device).
    # Redundant information, not a missing column.
    tracked_kind = (
        emergency_operation.KIND_WANDFUEHLER
        if db_state.active_source_device_id is None
        else emergency_operation.KIND_ERSATZQUELLE
    )
    return emergency_operation.ZoneEmergencyState(
        stage=db_state.stage,
        tracked_kind=tracked_kind,
        tracked_device_id=db_state.active_source_device_id,
        source_measured_at=db_state.source_measured_at,
        recovery_started_at=db_state.recovery_started_at,
        last_counted_measurement_at=db_state.last_counted_measurement_at,
        recovery_sample_count=db_state.recovery_sample_count,
        episode_open=db_state.episode_id is not None,
        handover_due_signalled=db_state.handover_due_signalled,
    )


def _persist_episode(
    session: Session,
    zone: Zone,
    db_state: ZoneSensorFailureState | None,
    stage_output: emergency_operation.StageOutput,
    policy: sensor_failure_policy.EffectivePolicy,
    sensor_timeout_seconds: int,
    now: datetime,
) -> int | None:
    """Creates, continues or closes `sensor_failure_episode` for this cycle --
    "eine Zeile je Störung" (plan 2.1), driven entirely by
    `stage_output.events`/`stage_output.state.episode_open`, never re-derived
    from the stage string itself.
    """
    events = stage_output.events
    if events.episode_started:
        source_device = (
            session.get(Device, zone.temperature_source_device_id)
            if zone.temperature_source_device_id is not None
            else None
        )
        row = SensorFailureEpisode(
            zone_id=zone.id,
            zone_name=zone.display_name,
            started_at=now,
            trigger_kind=events.trigger_kind or emergency_operation.TRIGGER_ALLE_QUELLEN,
            profile_version=policy.profile.version,
            fixed_on_seconds=policy.profile.values.fixed_on_seconds,
            fixed_off_seconds=policy.profile.values.fixed_off_seconds,
            recovery_seconds=policy.profile.values.recovery_seconds,
            recovery_samples=policy.profile.values.recovery_samples,
            warm_restart_hysteresis_k=policy.profile.values.warm_restart_hysteresis_k,
            emergency_setpoint_c=policy.emergency_setpoint_c,
            sensor_timeout_seconds=sensor_timeout_seconds,
            source_device_id=zone.temperature_source_device_id,
            source_device_name=(
                source_device.display_name if source_device is not None else None
            ),
            # `notification_state` stays at its column default (`offen`)
            # deliberately -- Auftrag 8 owns the webhook and only needs to pick
            # this row up, not decide how it started (plan Auftrag 7a, item 4).
        )
        session.add(row)
        session.flush()
        return row.id

    if stage_output.state.episode_open:
        return db_state.episode_id if db_state is not None else None

    if events.episode_ended and db_state is not None and db_state.episode_id is not None:
        episode = session.get(SensorFailureEpisode, db_state.episode_id)
        if episode is not None:
            episode.ended_at = now
            # Auftrag 8b: the one bit the notification dispatch (`app.py::
            # _emergency_notices`) needs to tell a real recovery from a mere
            # "sensorausfall_deaktiviert" -- see `ended_reason_code`'s column
            # docstring.
            episode.ended_reason_code = events.reason_code
    return None


def _persist_zone_state(
    session: Session,
    zone: Zone,
    db_state: ZoneSensorFailureState | None,
    stage_output: emergency_operation.StageOutput,
    episode_id: int | None,
    now: datetime,
) -> None:
    row = db_state
    if row is None:
        row = ZoneSensorFailureState(zone_id=zone.id)
        session.add(row)
    new_state = stage_output.state
    # Mirrors `SensorFailureEpisode.started_at` of the currently linked episode
    # for cheap display without a join (Auftrag 8) -- set once on entry, carried
    # unchanged while the same episode stays open, cleared the moment it closes.
    row.failure_started_at = (
        now
        if stage_output.events.episode_started
        else (row.failure_started_at if new_state.episode_open else None)
    )
    row.episode_id = episode_id
    row.stage = new_state.stage
    row.active_source_device_id = new_state.tracked_device_id
    row.source_measured_at = new_state.source_measured_at
    row.recovery_started_at = new_state.recovery_started_at
    row.last_counted_measurement_at = new_state.last_counted_measurement_at
    row.recovery_sample_count = new_state.recovery_sample_count
    row.handover_due_signalled = new_state.handover_due_signalled
    session.flush()


@dataclass(frozen=True)
class _SensorFailureOutcome:
    stage_output: emergency_operation.StageOutput
    health: SourceHealth
    effective_temperature_c: Decimal | None
    effective_device_name: str | None
    episode_id: int | None
    policy: sensor_failure_policy.EffectivePolicy


def _record_source_comparisons(
    session: Session,
    zone: Zone,
    wall_probe: WallProbeReading,
    raw_candidates: list[ThermostatCandidate],
    health: SourceHealth,
    stage: str,
) -> None:
    """Vergleichsprotokoll Ersatzquelle <-> Wandfühler (Plan Abschnitt 6, R2;
    freigegeben im Kreuzreview von 88bc87a). One row per candidate per *new*
    measurement instant, never per cycle: a candidate whose reading has not
    moved since the last cycle produces no second row (checked against the
    table itself -- the same "already have a row for this exact
    (device, measured_at)" question, asked here of the table rather than of
    a persisted counter). Written only when there is something to compare
    (wall probe *and* candidate both have a value -- not necessarily fresh
    or independent of an echo, this is a comparison log, not a usability
    gate) or while the zone is actually relying on the candidate as its
    active replacement (`ersatzquelle`); writing unconditionally on every
    cycle would grow this table without bound long after a candidate has
    stopped reporting anything new.
    """
    if not raw_candidates:
        return
    assessment_by_device = {a.device_id: a for a in health.candidates}
    both_have_a_value = wall_probe.temperature_c is not None
    for candidate in raw_candidates:
        if candidate.measured_at is None:
            continue
        if not (
            (both_have_a_value and candidate.local_temperature_c is not None)
            or stage == emergency_operation.STAGE_ERSATZQUELLE
        ):
            continue
        already_logged = session.scalar(
            select(SensorFailureSourceComparison.id).where(
                SensorFailureSourceComparison.device_id == candidate.device_id,
                SensorFailureSourceComparison.measured_at == candidate.measured_at,
            )
        )
        if already_logged is not None:
            continue
        assessment = assessment_by_device.get(candidate.device_id)
        session.add(
            SensorFailureSourceComparison(
                zone_id=zone.id,
                zone_name=zone.display_name,
                device_id=candidate.device_id,
                device_name=candidate.device_name,
                measured_at=candidate.measured_at,
                wall_probe_c=wall_probe.temperature_c,
                raw_c=candidate.local_temperature_c,
                corrected_c=assessment.corrected_temperature_c if assessment is not None else None,
                echo=assessment.echo if assessment is not None else False,
                usable=assessment.usable if assessment is not None else False,
            )
        )
    session.flush()


def _apply_sensor_failure(
    session: Session,
    zone: Zone,
    state: ZoneState | None,
    settings: Setting,
    parameter: ControlParameters,
    db_state: ZoneSensorFailureState | None,
    now: datetime,
) -> _SensorFailureOutcome:
    """One cycle's quellenbewertung + Zustandsautomat for one
    `sensor_failure_enabled` zone -- plan Auftrag 7a, item 1."""
    policy = sensor_failure_policy.effective_policy(session, zone)
    raw_candidates = zone_candidates(session, zone, now)
    wall_probe = WallProbeReading(
        state.temperature_c if state is not None else None,
        state.measured_at if state is not None else None,
    )
    health = evaluate_source_health(
        wall_probe, raw_candidates, now=now, timeout_s=parameter.sensor_timeout_seconds
    )
    assessment_by_device = {a.device_id: a for a in health.candidates}
    measured_at_by_device = {c.device_id: c.measured_at for c in raw_candidates}
    usable_candidates = [a for a in health.candidates if a.usable]
    if usable_candidates:
        # Same tie-break rule `evaluate_source_health` itself documents
        # (coldest corrected reading, then `device_id`) -- recomputed here,
        # independent of the wall probe's own status, because
        # `emergency_operation.StageInput.replacement` needs "is there a usable
        # candidate right now" on *every* cycle, not only the ones where the
        # wall probe has already failed (`evaluate_source_health` only names a
        # `selected_device_id` once `wall_usable` is `False`).
        chosen = min(usable_candidates, key=lambda a: (a.corrected_temperature_c, a.device_id))
        replacement_reading = emergency_operation.SourceReading(
            usable=True,
            measured_at=measured_at_by_device.get(chosen.device_id),
            device_id=chosen.device_id,
            device_name=chosen.device_name,
        )
    else:
        replacement_reading = emergency_operation.SourceReading(usable=False, measured_at=None)

    prior = _load_emergency_state(db_state)
    stage_output = emergency_operation.advance(
        prior,
        emergency_operation.StageInput(
            now=now,
            enabled=zone.sensor_failure_enabled,
            wall_probe=emergency_operation.SourceReading(
                usable=health.wall_probe_usable, measured_at=wall_probe.measured_at
            ),
            replacement=replacement_reading,
            recovery_seconds=policy.profile.values.recovery_seconds,
            recovery_samples=policy.profile.values.recovery_samples,
        ),
    )

    episode_id = _persist_episode(
        session, zone, db_state, stage_output, policy, parameter.sensor_timeout_seconds, now
    )
    _persist_zone_state(session, zone, db_state, stage_output, episode_id, now)

    effective_temperature_c: Decimal | None = None
    effective_device_name: str | None = None
    if (
        stage_output.state.stage == emergency_operation.STAGE_ERSATZQUELLE
        and replacement_reading.device_id is not None
    ):
        # Whenever the machine lands in `ERSATZQUELLE` this cycle, the
        # `replacement` reading just fed into `StageInput` above must have been
        # `usable=True` (`_ersatzquelle`/`_enter_from_normal` only choose this
        # stage on exactly that condition) -- so it, not the state machine's own
        # (recovery-bookkeeping-only) `tracked_device_id`, is this cycle's
        # active replacement.
        chosen_assessment = assessment_by_device[replacement_reading.device_id]
        effective_temperature_c = chosen_assessment.corrected_temperature_c
        effective_device_name = chosen_assessment.device_name

    _record_source_comparisons(
        session, zone, wall_probe, raw_candidates, health, stage_output.state.stage
    )

    return _SensorFailureOutcome(
        stage_output=stage_output,
        health=health,
        effective_temperature_c=effective_temperature_c,
        effective_device_name=effective_device_name,
        episode_id=episode_id,
        policy=policy,
    )


def _apply_emergency_actuators(
    session: Session,
    zone: Zone,
    outcome: _SensorFailureOutcome,
    settings: Setting,
    parameter: ControlParameters,
    now: datetime,
) -> None:
    """Rang 3/4 of plan 1.4 for every actuator assignment of `zone` --
    persistence only (`actuator_decision`/`actuator_emergency_state`,
    `simulated=True`). Deliberately returns nothing and never touches the
    zone's own `ShadowDecision`/`Decision`: Auftrag 7a is shadow-only by
    scope, and `ShadowDecision.would_heat` is exactly what the existing
    publisher reads and sends the moment an installation runs scharf (see the
    caller's own comment, Hauptsession review of 88bc87a). Only ever called
    while `outcome.stage_output.state.stage` is `NOTBETRIEB`/
    `RUECKKEHRPRUEFUNG`, both of which guarantee `outcome.episode_id is not
    None` (episode_open is true in both stages).
    """
    assert outcome.episode_id is not None
    policy = outcome.policy
    profile = policy.profile

    outdoor = outdoor_domain.outdoor_reading(session, settings, now)
    outdoor_sample = emergency_cycle.OutdoorSample(
        value_c=outdoor.temperature_c, measured_at=outdoor.measured_at, usable=outdoor.status == OK
    )
    cycle_profile = emergency_cycle.CycleProfile(
        fixed_on_seconds=profile.values.fixed_on_seconds,
        fixed_off_seconds=profile.values.fixed_off_seconds,
        warm_restart_hysteresis_k=profile.values.warm_restart_hysteresis_k,
        curve_points=tuple(
            emergency_cycle.CurvePoint(p.outdoor_c, p.on_seconds, p.off_seconds)
            for p in profile.values.curve_points
        ),
    )
    # A control loop can go silent past a phase's own deadline for up to this
    # long before a persisted cycle state is distrusted and restarted
    # (`emergency_cycle`'s own "lange Prozesspause" handling) -- five shadow
    # intervals, floored at 300s, the same order of magnitude
    # `services/publishing.py`'s own staleness reasoning uses elsewhere; not a
    # hard-coded constant (Grundsatz 1), derived from the plant's own
    # configured cycle length.
    stale_state_seconds = max(settings.shadow_interval_seconds * 5, 300)

    for zone_device, device, kind in zone_actuator_assignments(session, zone):
        existing_row = session.get(ActuatorEmergencyState, zone_device.id)
        fresh_episode = existing_row is None or existing_row.episode_id != outcome.episode_id
        row = existing_row if existing_row is not None else ActuatorEmergencyState(
            zone_device_id=zone_device.id
        )
        if existing_row is None:
            session.add(row)

        if kind == emergency_actuator_plan.KIND_THERMOSTAT:
            already_attempted = (
                not fresh_episode and row.simulated_handover_attempted_at is not None
            )
            if fresh_episode:
                row.simulated_handover_attempted_at = None
                row.simulated_last_command_state = None
                row.simulated_last_command_at = None
                row.simulated_phase = None
                row.simulated_phase_deadline_at = None
                row.simulated_on_seconds = None
                row.simulated_off_seconds = None
                row.simulated_cycle_source = None
                row.simulated_warm_locked = None
            thermostat_decision = emergency_actuator_plan.plan_thermostat(
                already_attempted=already_attempted,
                now=now,
                device_name=device.display_name,
                emergency_setpoint_c=policy.emergency_setpoint_c,
            )
            if thermostat_decision.handover_attempted_at is not None:
                row.simulated_handover_attempted_at = thermostat_decision.handover_attempted_at
                row.simulated_last_command_state = True
                row.simulated_last_command_at = now
            row.episode_id = outcome.episode_id
            row.last_evaluated_at = now
            row.profile_version = profile.version
            session.add(
                ActuatorDecision(
                    episode_id=outcome.episode_id,
                    zone_device_id=zone_device.id,
                    zone_name=zone.display_name,
                    device_name=device.display_name,
                    decided_at=now,
                    action=thermostat_decision.action,
                    reason_code=thermostat_decision.reason_code,
                    reason=thermostat_decision.reason,
                    phase=None,
                    phase_deadline_at=None,
                    simulated=True,
                    cycle_source=None,
                    on_seconds=None,
                    off_seconds=None,
                    outdoor_c=None,
                    profile_version=profile.version,
                )
            )
        elif kind == emergency_actuator_plan.KIND_SWITCH:
            prior_cycle_state = None
            if (
                not fresh_episode
                and row.simulated_phase is not None
                and row.simulated_phase_deadline_at is not None
                and row.simulated_on_seconds is not None
                and row.simulated_off_seconds is not None
                and row.simulated_cycle_source is not None
                and row.simulated_warm_locked is not None
            ):
                # Taktquelle und Wiederanlaufsperre kommen jetzt aus der
                # Persistenz, nicht mehr live neu geschätzt (Kreuzreview von
                # 88bc87a: ohne das griff die Sperre nie über einen Zyklus
                # hinweg, weil `CycleState` jeden Zyklus frisch aus der DB
                # rekonstruiert wird). `derive_switch_phase_started_at` bleibt
                # der exakte -- nicht angenäherte -- Umkehrweg für
                # `phase_started_at`, das keine eigene Spalte hat.
                prior_cycle_state = emergency_cycle.CycleState(
                    phase=row.simulated_phase,
                    phase_started_at=derive_switch_phase_started_at(
                        row.simulated_phase,
                        row.simulated_phase_deadline_at,
                        row.simulated_on_seconds,
                        row.simulated_off_seconds,
                    ),
                    phase_deadline=row.simulated_phase_deadline_at,
                    source=row.simulated_cycle_source,
                    on_seconds=row.simulated_on_seconds,
                    off_seconds=row.simulated_off_seconds,
                    warm_locked=row.simulated_warm_locked,
                )
            switch_decision = emergency_actuator_plan.plan_switch(
                prior_cycle_state,
                emergency_cycle.CycleInput(
                    now=now,
                    profile=cycle_profile,
                    outdoor=outdoor_sample,
                    min_on_seconds=parameter.min_on_seconds,
                    min_off_seconds=parameter.min_off_seconds,
                    stale_state_seconds=stale_state_seconds,
                    # Only consulted on first entry (`prior_cycle_state is None`):
                    # the real last relay state from the command log (concept 3.4).
                    prior=(
                        entry_prior_hint(
                            session,
                            device.id,
                            now=now,
                            min_on_seconds=parameter.min_on_seconds,
                        )
                        if prior_cycle_state is None
                        else None
                    ),
                ),
            )
            new_cycle_state = switch_decision.cycle.state
            row.simulated_phase = new_cycle_state.phase
            row.simulated_phase_deadline_at = new_cycle_state.phase_deadline
            row.simulated_on_seconds = new_cycle_state.on_seconds
            row.simulated_off_seconds = new_cycle_state.off_seconds
            row.simulated_cycle_source = new_cycle_state.source
            row.simulated_warm_locked = new_cycle_state.warm_locked
            row.episode_id = outcome.episode_id
            row.last_evaluated_at = now
            row.profile_version = profile.version
            session.add(
                ActuatorDecision(
                    episode_id=outcome.episode_id,
                    zone_device_id=zone_device.id,
                    zone_name=zone.display_name,
                    device_name=device.display_name,
                    decided_at=now,
                    action=switch_decision.action,
                    reason_code=switch_decision.reason_code,
                    reason=switch_decision.reason,
                    phase=new_cycle_state.phase,
                    phase_deadline_at=new_cycle_state.phase_deadline,
                    simulated=True,
                    cycle_source=new_cycle_state.source,
                    on_seconds=new_cycle_state.on_seconds,
                    off_seconds=new_cycle_state.off_seconds,
                    outdoor_c=outdoor_sample.value_c,
                    profile_version=profile.version,
                )
            )

    session.flush()


# --------------------------------------------------------------------------- #
# PI wiring (steps 4 and 5 of the build order in section 11 of the PI
# specification). `thermoctl.domain.pi_control` supplies the pure arithmetic and
# window modulator (step 3, already built and mutation-tested); everything below
# is the orchestration that decides, once per zone and cycle, whether to call it
# at all, loads and persists `ZoneState`'s PI columns, and turns the result into
# `ShadowDecision`'s structured PI diagnostics.
#
# `decide()` itself is untouched -- `Situation`/`Decision` do not gain a PI notion
# of their own, and the 2.376-line state-table test keeps proving that for
# `pi_enabled=False` (every zone until someone flips the latch directly in the
# database; there is deliberately no operating path for it yet, see
# `tests/test_pi_schema.py`). PI replaces only what section 6, rule 6 of
# `control_loop.decide()` would otherwise decide -- rules 1, 3, 4 and 7 keep
# exactly the precedence they already have, because their conditions are read
# from `Situation`/`decision.reason_code` here, never recomputed. Rule 3's own
# frost-overrides-window exception (2026-09-06) does not change this: it still
# regulates against the frost setpoint on its own tight, hysteresis-only leash,
# never against whatever PI would otherwise pursue -- `_pi_gate_reason`'s
# `window_governs` keeps every cycle rule 3 actually applies to (window open, on a
# zone rule 3 is not exempted from) out of PI's territory, exactly as an ordinary
# window-open "off" already was.
# --------------------------------------------------------------------------- #


def _pi_actuator_profiles(session: Session, zone: Zone) -> list[ActuatorProfile]:
    """Every device carrying the zone's `actuator` role -- self-regulating or not.

    `domain.switch_commands.switch_commands()`/`thermostat_commands()` already
    filter self-regulating devices out of their own results; `pi_eligible()` needs to
    see them anyway, because its verdict is device-accurate rather than zone-wide
    (the "Feststehender Zuschnitt" section of the PI specification, as amended): a
    self-regulating valve never receives PI's `heating` decision at all, so
    `pi_eligible()` has to know a profile is self-regulating in order to *skip* it,
    not reject the zone for it -- while a non-self-regulating thermostat-capable
    actuator still must reject the zone, since `thermostat_commands()` would turn
    PI's decision into a setpoint jump. Handing over only the already-narrowed
    switch actuators would hide exactly the distinction `pi_eligible()` needs to
    draw.
    """
    actuator_role = session.scalar(select(DeviceRole).where(DeviceRole.code == "actuator"))
    if actuator_role is None:
        return []
    switch = session.scalar(select(DeviceCapability).where(DeviceCapability.code == "switch"))
    thermostat = session.scalar(
        select(DeviceCapability).where(DeviceCapability.code == "thermostat")
    )
    rows = session.execute(
        select(ZoneDevice.device_id, ZoneDevice.self_regulating).where(
            ZoneDevice.zone_id == zone.id,
            ZoneDevice.device_role_id == actuator_role.id,
        )
    )
    profiles: list[ActuatorProfile] = []
    for device_id, self_regulating in rows:
        capability_ids = set(
            session.scalars(
                select(DeviceCapabilityLink.capability_id).where(
                    DeviceCapabilityLink.device_id == device_id
                )
            )
        )
        profiles.append(
            ActuatorProfile(
                self_regulating=bool(self_regulating),
                has_switch_capability=switch is not None and switch.id in capability_ids,
                has_thermostat_capability=(
                    thermostat is not None and thermostat.id in capability_ids
                ),
            )
        )
    return profiles


def _pi_setpoint_context_key(
    setpoint: Setpoint, override: ZoneOverride | None, vacation: Vacation | None
) -> str:
    """A stable key for "which setpoint context is in effect" (section 2 of the PI
    specification): its origin and identity, never the free-text reason -- the
    specification explicitly rules out comparing `setpoint.reason`.

    Only ever called once `_pi_gate_reason()` has already ruled out this cycle's
    setpoint being the frost-protection one (operating mode 'off', or the
    frost-protection mode itself, both go through `RESET_REASON_FROST` first, in
    `_pi_outcome`) -- so unlike `resolved_setpoint()`'s own precedence, this
    function never needs an 'off' or "no schedule at all" branch of its own: by the
    time it runs, `setpoint.mode_id` is always set (`resolved_setpoint()` never
    returns `None` there except for a fixed-temperature override or a running
    vacation, each handled by its own branch below). It still adds the override's
    own id as an extra axis on top of `setpoint.mode_id`: that alone cannot tell an
    override apart from a schedule point naming the same mode, and section 2
    explicitly requires a reset on both the start and the end of an override even
    then. Boost needs no separate case -- the specification is explicit that boost
    is technically an override (`ZoneOverride`), so it already goes through the
    `override` branch.

    The vacation branch exists for the same reason as the override one: a vacation
    also resolves to a fixed temperature with `mode_id=None` (see
    `domain.schedule._vacation_setpoint`), and without its own key here the
    `assert` below would fire the first time a vacation ran on a PI-enabled zone
    with no override active. Its own id, not a fixed string, for the same reason
    the override branch uses `override.id` and not merely `"override"`: two
    successive vacations must reset the integral between them exactly as two
    successive overrides do.
    """
    if override is not None:
        return f"override:{override.id}"
    if vacation is not None:
        return f"urlaub:{vacation.id}"
    assert setpoint.mode_id is not None  # see docstring: ruled out by the caller's gate
    return f"zeitplan:{setpoint.mode_id}"


def _pi_gate_reason(
    reason_code: str,
    *,
    sensor_failed: bool,
    window_governs: bool,
    resume_delay_active: bool,
    frost_effective: bool,
    sensor_failure_emergency_active: bool = False,
) -> str | None:
    """Which of section 4's PI-resetting precedence rules governs this cycle, if
    any. `None` means none of them do -- exactly "wo die gewöhnliche Regelung
    heizen würde" (rule 6's territory, including the plain "stay off" case rule 6
    falls through to when rule 7 does not apply either): PI computes a real
    candidate for this cycle.

    `sensor_failed`, `window_governs`, `resume_delay_active`, and `frost_effective`
    are all computed by the caller directly from `Situation`/context, deliberately
    *not* from `decision.reason_code` -- see the paragraph below for why that
    matters for `sensor_failed` in particular. Only `REASON_CODE_VALVE_PROTECTION`
    is still read from `decision.reason_code`, since it is produced by exactly one
    branch of `decide()` (rule 7, only once rule 6 has already deferred) with
    nothing else that could pre-empt it the way rule 5 can pre-empt the others
    (see below).

    `sensor_failed` used to be read from `decision.reason_code` too (`in
    (REASON_CODE_NO_SOURCE, REASON_CODE_FROST_SENSOR_FAILURE)`), on the assumption
    that "keine Quelle" and a stale reading each produce exactly one, unambiguous
    code -- true for `REASON_CODE_NO_SOURCE` (rule 1 returns before anything else
    can run), but **not** for a stale ("veraltet") reading: `decide()` only assigns
    `REASON_CODE_FROST_SENSOR_FAILURE` inside rule 6, and rule 5 (the ordinary
    minimum-switch-duration hold, `situation.parameter.min_on_seconds`/
    `min_off_seconds`) sits *before* rule 6 and returns early whenever the current
    state has not been held long enough -- with `REASON_CODE_BLOCKED_MINIMUM_DURATION`,
    not the sensor code. On such a cycle this function used to see an ordinary,
    `_PERMITTED` code and let PI compute a real candidate against the zone's normal
    setpoint and the stale measurement -- exactly the situation rule 1 exists to
    prevent (found 2026-09-27, security review of the fix above: reproduced with a
    zone held at 20.9 °C against a 21 °C setpoint, a 300 s hysteresis minimum and a
    60 s PI minimum -- `would_heat` flips to `True` on PI's own candidate two
    minutes after the sensor went stale, with `effective_controller` staying
    `"pi"` the whole time). `sensor_failed` (`Situation.sensor_status in
    (NO_SOURCE, VERALTET) or Situation.measured_c is None`) cannot be masked this
    way: it reads the same field rule 1 itself reads, independent of which rule in
    `decide()` happened to return first this cycle.

    `window_governs` -- `Situation.window_open and not Situation.on_off_actuators_only`,
    i.e. rule 3's own condition for actually applying (added 2026-09-06 alongside the
    frost-overrides-window exception and the EIN/AUS exemption) -- covers *every*
    outcome rule 3 can now produce for such a cycle: the ordinary window-open "off"
    (`REASON_CODE_WINDOW_OPEN`), the new frost-overrides-window heat
    (`REASON_CODE_FROST_OVERRIDES_WINDOW`), and a minimum-switch-duration hold that
    happened to interrupt the frost exception (`REASON_CODE_BLOCKED_MINIMUM_DURATION`
    -- indistinguishable here from an unrelated one, but PI must not chase a target
    the base decision itself was blocked from reaching). None of these are PI's
    territory: the whole point of rule 3's frost exception is to regulate against the
    frost setpoint on a tight, hysteresis-only leash, not against whatever PI would
    otherwise pursue for the zone's actual schedule. An EIN/AUS-only zone is the
    opposite case -- rule 3 never applies to it at all, window open or not, so PI
    must keep running for it exactly as if there were no window.

    `REASON_CODE_OFF` is returned both by rule 4 (the window resume delay) and by
    rule 6's ordinary "off" branch -- only the former is one of section 4's rules,
    so `resume_delay_active` (computed the same way `Situation.window_closed_for_s`
    already is) tells them apart. And a setpoint resolved to the frost-protection
    mode is not a separate branch in `decide()` at all -- it simply feeds a
    different setpoint into the very same rule 6 -- so `frost_effective` is computed
    independently by the caller from `resolved_setpoint()`'s own result.

    Order matters only for which single reason gets attributed when more than one
    would apply -- the effective boolean is `decide()`'s regardless -- and mirrors
    `decide()`'s actual rule order (1, 3, 4, ..., 7) wherever that is well-defined.
    Valve protection is checked from `decide()`'s own code (authoritative: it is
    only ever returned once rule 6 has already deferred) *before* the independently
    computed `frost_effective`, so a cycle where protection actually wins is
    attributed to protection, not to the frost setpoint that never got to decide
    anything that cycle. `sensor_failed` is checked first, ahead of everything
    else -- rule 1 has the same, highest precedence in `decide()` itself.

    `sensor_failure_emergency_active` -- Auftrag 7a of
    `lokal/plaene/0.11.0-notbetrieb.md`, plan 1.3/1.4 Rang 9: `True` on any
    cycle `domain.emergency_operation.advance()` reports a stage other than
    `normal` for this zone (`ersatzquelle`, `notbetrieb`,
    `rueckkehrpruefung`). Checked first, ahead of `sensor_failed` itself,
    because it must also gate `ERSATZQUELLE`: there `_process_zone` has
    already fed a corrected replacement reading into `Situation` with
    `sensor_status="ok"`, so the ordinary `sensor_failed` computation
    (`Situation.sensor_status in (NO_SOURCE, VERALTET)`) would not catch it on
    its own -- plan 1.3's own requirement ("PI wird bei aktiver Ersatzquelle
    ausgesetzt") needs its own, independent signal, exactly the way
    `window_governs`/`resume_delay_active`/`frost_effective` each already
    have their own instead of being inferred from `decision.reason_code`.
    """
    if sensor_failed or sensor_failure_emergency_active:
        return RESET_REASON_SENSOR_FAILURE
    if window_governs:
        return RESET_REASON_WINDOW_OPEN
    if resume_delay_active:
        return RESET_REASON_WINDOW_OPEN
    if reason_code == REASON_CODE_VALVE_PROTECTION:
        return RESET_REASON_VALVE_PROTECTION
    if frost_effective:
        return RESET_REASON_FROST
    return None


def _aware(value: datetime | None) -> datetime | None:
    """UTC-aware, for `pi_control`'s arithmetic -- `window_start_for()` refuses a
    naive `datetime` on purpose (section 3 fixes the window to UTC quarter-hours,
    not local time, so a summer-time change never moves the boundary). Everywhere
    else in this application a naive `datetime` already means UTC implicitly (see
    e.g. `services/publishing.py::_as_text`) -- including every `DateTime` column
    `ZoneState` stores PI's own state in, and the `now` this module is called
    with. This is the one boundary where that implicit convention needs to become
    explicit; `_naive()` below is its exact inverse, used everywhere a value goes
    back into a column.
    """
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def _naive(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is None:
        return value
    return value.replace(tzinfo=None)


def _load_pi_state(state: ZoneState) -> PiState:
    """Reconstructs the pure `PiState` from `ZoneState`'s durable PI columns.

    `ModulatorState.held_for_s` has no column of its own -- `pi_last_switch_at`
    (when the current on/off run started) plus `pi_last_evaluated_at` (this state's
    own "as of" timestamp) already determine it exactly, since the modulator's own
    bookkeeping accumulates real elapsed time cycle by cycle (`_write_pi_state`
    below is the inverse: it is what keeps `pi_last_switch_at` meaning exactly
    that). `None` for either column -- never run, or freshly reset -- means
    "unknown", matching `NEUTRAL_MODULATOR_STATE.held_for_s`.
    """
    held_for_s: int | None = None
    if state.pi_last_switch_at is not None and state.pi_last_evaluated_at is not None:
        held_for_s = int(
            (state.pi_last_evaluated_at - state.pi_last_switch_at).total_seconds()
        )
    modulator = ModulatorState(
        on=bool(state.pi_last_switch_heating),
        held_for_s=held_for_s,
        remainder_s=state.pi_time_balance_seconds,
        window_start=_aware(state.pi_window_started_at),
        frozen_duty=state.pi_window_duty,
    )
    return PiState(
        integral=state.pi_integral,
        last_evaluated_at=_aware(state.pi_last_evaluated_at),
        setpoint_context_key=state.pi_setpoint_context_key,
        modulator=modulator,
        awaiting_boundary_until=_aware(state.pi_awaiting_boundary_until),
        last_reset_reason=state.pi_last_reset_reason,
    )


def _write_pi_state(
    row: ZoneState, old_state: PiState, output: PiCycleOutput, now: datetime
) -> None:
    """Persists one regular `pi_cycle()` result -- the inverse of `_load_pi_state`.

    `pi_last_switch_at` only moves when the current on/off run genuinely restarted
    this cycle: either the modulator itself flipped (`output.switched`), or its
    starting point was reset from under it by a setpoint-context change this same
    cycle (`old_state`'s context key differs from the new one -- section 2's
    "Beginn und Ende einer Übersteuerung", handled inside `pi_cycle()` itself, see
    its docstring) even though the *chosen* on/off value happens not to have
    changed, or there was no previous run to speak of at all. Any other cycle
    leaves it untouched, so `_load_pi_state`'s derivation above keeps accumulating
    the true elapsed time of the *same* run.
    """
    new_state = output.state
    row.pi_integral = new_state.integral
    row.pi_last_evaluated_at = _naive(new_state.last_evaluated_at)
    row.pi_setpoint_context_key = new_state.setpoint_context_key
    row.pi_window_started_at = _naive(new_state.modulator.window_start)
    row.pi_window_duty = new_state.modulator.frozen_duty
    row.pi_time_balance_seconds = new_state.modulator.remainder_s
    if new_state.modulator.held_for_s is None:
        row.pi_last_switch_at = None
        row.pi_last_switch_heating = None
    else:
        row.pi_last_switch_heating = new_state.modulator.on
        context_reset_this_cycle = (
            old_state.setpoint_context_key is not None
            and old_state.setpoint_context_key != new_state.setpoint_context_key
        )
        if output.switched or context_reset_this_cycle or row.pi_last_switch_at is None:
            row.pi_last_switch_at = _naive(now)
    row.pi_awaiting_boundary_until = _naive(new_state.awaiting_boundary_until)
    row.pi_last_reset_reason = new_state.last_reset_reason


def _write_reset_state(row: ZoneState, reset_state: PiState) -> None:
    """Persists an out-of-band reset (section 4's precedence table, or the safe
    arming/invalid-state wait) -- unlike `_write_pi_state`, always unconditionally
    clears the switch-timing columns, matching `reset_pi_state()`'s own neutral
    modulator.

    `pi_awaiting_boundary_until` is the one exception: a section-4 gate reset
    (window/frost/sensor/valve -- every call here except the safe-start wait
    itself) always calls `reset_pi_state()` without `await_next_boundary`, so
    `reset_state.awaiting_boundary_until` is `None` on those calls -- but writing
    that `None` through unconditionally used to silently erase an *already
    pending* safe-start wait from an earlier cycle, not merely leave it alone.
    Found in the second security review, 2026-09-27: a temporary gate (a stale
    sensor, an open window, ...) firing even once during the wait let PI resume
    the moment the gate cleared, without ever finishing the wait it was still
    in the middle of. Keep whichever of the existing and the newly written
    value is later; a `None` from this call never *shortens* an existing wait,
    it only fails to *set* one where none was pending.
    """
    row.pi_integral = reset_state.integral
    row.pi_last_evaluated_at = _naive(reset_state.last_evaluated_at)
    row.pi_setpoint_context_key = reset_state.setpoint_context_key
    row.pi_window_started_at = _naive(reset_state.modulator.window_start)
    row.pi_window_duty = reset_state.modulator.frozen_duty
    row.pi_time_balance_seconds = reset_state.modulator.remainder_s
    row.pi_last_switch_at = None
    row.pi_last_switch_heating = None
    now = _aware(reset_state.last_evaluated_at)
    existing_wait = _aware(row.pi_awaiting_boundary_until)
    if existing_wait is not None and now is not None and existing_wait <= now:
        existing_wait = None  # already elapsed -- no longer "pending"
    new_wait = reset_state.awaiting_boundary_until
    if existing_wait is None:
        row.pi_awaiting_boundary_until = _naive(new_wait)
    elif new_wait is None or existing_wait >= new_wait:
        row.pi_awaiting_boundary_until = _naive(existing_wait)
    else:
        # Reachable, and not just hypothetically: an upgrade can leave a `now`-
        # format wait behind that this codebase's own two calls would never
        # produce between themselves (the boundary-only formula alone cannot
        # advance an existing pending wait -- see the two branches above), but
        # a value written by an *earlier* version of this function can still be
        # sitting in the column when a second `needs_safe_start` fires under
        # the current code. Found by review, 2026-09-27: heating starts at
        # 12:13, PI is enabled at 12:14 under the version of this fix that
        # still extended the wait to the hysteresis deadline itself (300s after
        # 12:13, so 12:18) -- superseded by the general invariant, but that
        # 12:18 is what a zone upgraded mid-wait still has stored. Arming the
        # installation at 12:16, before that stored wait elapses, fires
        # `needs_safe_start` again (`RESET_REASON_ARMING`) and computes a fresh
        # boundary-only wait of 12:30 -- later than the still-pending 12:18, so
        # it correctly wins here instead of being discarded in favour of the
        # earlier, now-stale value.
        row.pi_awaiting_boundary_until = _naive(new_wait)
    row.pi_last_reset_reason = reset_state.last_reset_reason


def _neutralize_pi_state(row: ZoneState) -> None:
    """The full wipe section 5 requires when PI is off or ineligible for this zone
    -- deliberately not `_write_reset_state()` with `reset_pi_state()`'s output,
    which still records a timestamp and a reason: "neutralisiert" means a later
    activation starts exactly as clean as a zone that has never run PI, including
    `pi_last_control_armed`, so the safe arming wait (section 4's closing
    paragraph) applies again in full the next time this zone is enabled.
    """
    row.pi_integral = Decimal("0")
    row.pi_last_evaluated_at = None
    row.pi_setpoint_context_key = None
    row.pi_last_control_armed = None
    row.pi_window_started_at = None
    row.pi_window_duty = None
    row.pi_time_balance_seconds = Decimal("0")
    row.pi_last_switch_at = None
    row.pi_last_switch_heating = None
    row.pi_awaiting_boundary_until = None
    row.pi_last_reset_reason = None


def _neutral_pi_fields() -> dict[str, object]:
    return {
        "requested_controller": "hysteresis",
        "effective_controller": "hysteresis",
        "controller_fallback_reason": None,
        "pi_error_k": None,
        "pi_proportional_term": None,
        "pi_integral_before": None,
        "pi_integral_after": None,
        "pi_raw_duty": None,
        "pi_frozen_duty": None,
        "pi_window_started_at": None,
        "pi_time_balance_before_seconds": None,
        "pi_time_balance_after_seconds": None,
        "pi_state_runtime_seconds": None,
        "pi_integrator_action": None,
        "pi_min_duration_decision": None,
        "pi_reset_reason": None,
        "pi_candidate_would_heat": None,
    }


def _hysteresis_phase_still_holding(
    situation: Situation, phase_started_by: str | None
) -> bool:
    """The general invariant (project owner, second security review, 2026-09-27):
    a phase (on or off) that started under hysteresis control is held for at
    least the ordinary hysteresis minimum switch duration
    (`situation.parameter.min_on_seconds`/`min_off_seconds`, the same values
    rule 5 in `decide()` itself checks) -- regardless of when, or through which
    path, PI later takes over. A phase PI itself started is subject only to
    PI's own, softer minimums (`pi_min_on_seconds`/`pi_min_off_seconds`, entirely
    separate bookkeeping inside `pi_cycle()`/`window_modulate()`) and never
    checked here.

    Replaces a narrower, first version of this fix that only checked the
    equivalent condition once, at the `needs_safe_start` transition itself --
    found insufficient by this second review, two ways:

    1. A one-time check at the moment PI is enabled cannot react to a
       hysteresis-controlled phase that only *starts* afterwards. PI enabled at
       12:01 found nothing held yet, so the resulting wait ended at the next
       window boundary (12:15) with nothing left to extend it -- a phase that
       then starts under hysteresis at 12:13 was never protected at all, and PI
       switched it off after only 120s once 12:15 arrived.
    2. The one-time wait was stored as `ZoneState.pi_awaiting_boundary_until`,
       which every *other* gate reset (window/frost/sensor/valve) overwrote to
       `None` the moment it fired even once during the wait -- clearing a
       still-pending wait, not just superseding it (see `_write_reset_state`'s
       own fix for the general form of this). A temporary sensor outage during
       the wait let PI resume immediately once the outage cleared, with its own
       short minimums, on a phase hysteresis had started and was still holding.

    Both are closed by checking this fresh, from history, on *every* cycle PI
    would otherwise decide -- not stored anywhere, so there is nothing to
    silently go stale or get erased.

    `phase_started_by` is `_previous_state()`'s fourth value: the
    `effective_controller` of the earliest `shadow_decision` row in the
    currently held run -- the exact same row `held_for_s` itself already comes
    from, so this needs no new column and no migration. `None` means no history
    at all yet (a zone's very first cycle, where `held_for_s` is `None` too);
    `decide()`'s own rule 5 already treats an unknown duration as nothing to
    enforce yet, and this mirrors that instead of conservatively blocking on it.
    """
    if situation.held_for_s is None or phase_started_by != "hysteresis":
        return False
    minimum_duration = (
        situation.parameter.min_on_seconds
        if situation.heating_now
        else situation.parameter.min_off_seconds
    )
    return situation.held_for_s < minimum_duration


def _pi_outcome(
    session: Session,
    zone: Zone,
    state: ZoneState | None,
    situation: Situation,
    decision: Decision,
    parameter: ControlParameters,
    settings: Setting,
    setpoint: Setpoint,
    override: ZoneOverride | None,
    vacation: Vacation | None,
    phase_started_by: str | None,
    now: datetime,
    *,
    sensor_failure_emergency_active: bool = False,
) -> tuple[bool, str | None, dict[str, object]]:
    """Everything PI contributes to one zone's cycle.

    `sensor_failure_emergency_active` -- see `_pi_gate_reason`'s own docstring
    for why this needs its own flag, independent of `situation.sensor_status`
    (Auftrag 7a, plan 1.3/1.4 Rang 9).

    Returns `(effective_heating, reason_suffix, shadow_decision_fields)`:
    `effective_heating` is `decision.heating` (the ordinary hysteresis decision)
    whenever PI is off, ineligible, temporarily unavailable, or a precedence rule
    from section 4 overrides it -- PI never invents a decision in any of those
    cases, it only explains, in `shadow_decision_fields`, why not. Only when PI is
    enabled, eligible, and none of section 4's rules apply does its own candidate
    become the effective decision -- this is the single point where step 5 connects
    `Decision.heating` to what `services/publishing.py` reads back out as
    `would_heat`; step 4 alone (calling this, but ignoring `effective_heating` and
    keeping `decision.heating`) is the "Parallelwert ohne Wirkung".
    """
    # `pi_control` requires a timezone-aware `now` (`window_start_for()`'s own
    # guard); everywhere else, including every caller of this module, a naive
    # `datetime` already means UTC implicitly. See `_aware()`'s docstring.
    now = now if now.tzinfo is not None else now.replace(tzinfo=UTC)

    fields = _neutral_pi_fields()
    fields["requested_controller"] = "pi" if parameter.pi_enabled else "hysteresis"

    if not parameter.pi_enabled:
        if state is not None:
            _neutralize_pi_state(state)
        return decision.heating, None, fields

    if state is None:
        # Nothing to load or persist PI state into. `decide()` has already fallen
        # back to `REASON_CODE_NO_SOURCE` for this cycle -- no `zone_state` row
        # means no measurement either -- so there is nothing meaningful PI could
        # add regardless.
        return decision.heating, None, fields

    eligibility = pi_eligible(
        _pi_actuator_profiles(session, zone),
        control_cycle_seconds=settings.shadow_interval_seconds,
        pi_min_on_seconds=parameter.pi_min_on_seconds,
        pi_min_off_seconds=parameter.pi_min_off_seconds,
    )
    if not eligibility.eligible:
        _neutralize_pi_state(state)
        fields["controller_fallback_reason"] = PI_FALLBACK_INELIGIBLE
        return decision.heating, f"PI-Rückfall: {eligibility.reason}", fields

    armed = bool(settings.control_armed)
    previous_armed = state.pi_last_control_armed
    state.pi_last_control_armed = armed
    needs_safe_start = previous_armed is None or (previous_armed is False and armed)
    if needs_safe_start:
        # Purely about a *safe* PI start after arming/an invalid state -- giving
        # `pi_dt()` a clean, known-recent `last_evaluated_at` instead of computing
        # over a potentially huge or meaningless gap. Deliberately unaware of the
        # hysteresis minimum duration of whatever is currently held: that is now
        # the general invariant checked below, on every cycle, not just this one
        # transition (second security review, 2026-09-27 -- see
        # `_hysteresis_phase_still_holding`'s own docstring for why a one-time
        # check here was not enough).
        reason = RESET_REASON_INVALID_STATE if previous_armed is None else RESET_REASON_ARMING
        _write_reset_state(state, reset_pi_state(reason, now=now, await_next_boundary=True))
        fields["controller_fallback_reason"] = reason
        fields["pi_reset_reason"] = reason
        fields["pi_integrator_action"] = INTEGRATOR_RESET
        return (
            decision.heating,
            f"PI-Rückfall: {reason}, wartet auf die nächste Fenstergrenze.",
            fields,
        )

    # Mirrors rule 4's own condition exactly, `on_off_actuators_only` exemption
    # (2026-09-06) included -- without it, an EIN/AUS-only zone whose window
    # recently closed would still have PI reset here even though `decide()` itself
    # never even reaches rule 4 for such a zone (found while adding this exemption:
    # `_pi_gate_reason` was computing this independently of `decide()`'s actual
    # rule-4 outcome and had not been taught the same skip).
    resume_delay_active = (
        not situation.on_off_actuators_only
        and situation.window_closed_for_s is not None
        and situation.window_closed_for_s < situation.parameter.window_resume_delay_seconds
    )
    frost_effective = (
        zone.operating_mode.code == "off"
        or setpoint.mode_id == settings.frost_protection_mode_id
    )
    # Read directly off `Situation`, not off `decision.reason_code` -- rule 5 (the
    # ordinary minimum-switch-duration hold) can return `REASON_CODE_BLOCKED_
    # MINIMUM_DURATION` before rule 6 ever gets to assign
    # `REASON_CODE_FROST_SENSOR_FAILURE` for a stale reading, masking it from a
    # reason-code-based check the same way rule 4's resume delay and rule 3's
    # window handling would be masked without their own independent booleans
    # above (see `_pi_gate_reason`'s docstring for the security finding this
    # closes, 2026-09-27). `situation.sensor_status` is exactly what rule 1 itself
    # reads, so this cannot be pre-empted by any other rule.
    sensor_failed = (
        situation.sensor_status in (NO_SOURCE, VERALTET) or situation.measured_c is None
    )
    gate = _pi_gate_reason(
        decision.reason_code,
        sensor_failed=sensor_failed,
        window_governs=situation.window_open and not situation.on_off_actuators_only,
        resume_delay_active=resume_delay_active,
        frost_effective=frost_effective,
        sensor_failure_emergency_active=sensor_failure_emergency_active,
    )
    if gate is not None:
        _write_reset_state(state, reset_pi_state(gate, now=now))
        fields["pi_reset_reason"] = gate
        fields["pi_integrator_action"] = INTEGRATOR_RESET
        return decision.heating, None, fields

    # General invariant (project owner, second security review, 2026-09-27):
    # a phase started under hysteresis holds for the hysteresis minimum switch
    # duration regardless of which controller decides later cycles; only a
    # phase PI itself started is subject to PI's own, softer minimums. Checked
    # fresh every cycle from history (Grundsatz 5 -- the reason names exactly
    # this, not the inapplicable hysteresis rule and not PI's own minimum).
    if _hysteresis_phase_still_holding(situation, phase_started_by):
        _write_reset_state(
            state, reset_pi_state(PI_FALLBACK_HYSTERESIS_MINIMUM, now=now)
        )
        fields["pi_reset_reason"] = PI_FALLBACK_HYSTERESIS_MINIMUM
        fields["pi_integrator_action"] = INTEGRATOR_RESET
        return (
            decision.heating,
            "PI hält: die aktuelle Phase hat unter Hysterese begonnen und deren "
            "Mindestdauer läuft noch.",
            fields,
        )

    # No section-4 rule applies: rule 6's territory (or rule 7's plain "stay off"
    # fallthrough, which is behaviourally the same "stay off" rule 6 itself would
    # give -- see `_pi_gate_reason`'s docstring) -- PI computes a real candidate.
    assert situation.measured_c is not None  # the sensor gate above already excludes this
    pi_state = _load_pi_state(state)
    context_key = _pi_setpoint_context_key(setpoint, override, vacation)
    calibrated_c = situation.measured_c + parameter.temperature_offset_k
    error_k = situation.setpoint_c - calibrated_c
    fields["pi_error_k"] = error_k
    fields["pi_proportional_term"] = parameter.pi_gain_per_k * error_k

    output = pi_cycle(
        pi_state,
        PiCycleInput(
            now=now,
            error_k=error_k,
            setpoint_context_key=context_key,
            expected_cycle_seconds=settings.shadow_interval_seconds,
            gain_per_k=parameter.pi_gain_per_k,
            integral_time_minutes=Decimal(parameter.pi_integral_time_minutes),
            pi_min_on_seconds=parameter.pi_min_on_seconds,
            pi_min_off_seconds=parameter.pi_min_off_seconds,
        ),
    )
    _write_pi_state(state, pi_state, output, now)

    fields["pi_integral_before"] = pi_state.integral
    fields["pi_integral_after"] = output.state.integral
    fields["pi_raw_duty"] = output.duty_raw
    fields["pi_frozen_duty"] = output.state.modulator.frozen_duty
    fields["pi_window_started_at"] = _naive(output.state.modulator.window_start)
    fields["pi_time_balance_before_seconds"] = pi_state.modulator.remainder_s
    fields["pi_time_balance_after_seconds"] = output.state.modulator.remainder_s
    fields["pi_state_runtime_seconds"] = (
        Decimal(output.state.modulator.held_for_s)
        if output.state.modulator.held_for_s is not None
        else None
    )
    fields["pi_integrator_action"] = output.integrator_action
    fields["pi_reset_reason"] = output.state.last_reset_reason
    fields["pi_candidate_would_heat"] = output.heating

    if not output.pi_available:
        # A time gap, or still waiting out an earlier safe-start boundary --
        # `decide()`'s ordinary hysteresis answer already is the correct one for
        # this one cycle (section 4's closing paragraph).
        fields["controller_fallback_reason"] = output.reason_code
        return decision.heating, None, fields

    fields["pi_min_duration_decision"] = output.reason_code
    fields["effective_controller"] = "pi"
    assert output.heating is not None  # guaranteed whenever pi_available is True
    assert output.duty_raw is not None  # likewise: set whenever PI is available
    reason_suffix = _pi_reason_text(
        error_k, output.duty_raw, output.reason_code, output.heating
    )
    return output.heating, reason_suffix, fields


def _pi_reason_text(
    error_k: Decimal, duty_raw: Decimal, reason_code: str | None, heating: bool
) -> str:
    """Wording of the PI part of a decision reason (display only, no rule logic).

    `error_k` is the control deviation (setpoint minus calibrated actual value), so a
    positive value means the room is colder than wanted. The duty cycle is shown as a
    percentage rounded to one decimal; both numbers use a decimal comma. A tiny
    negative deviation that rounds to zero is shown as "0,00", not "-0,00".
    """
    deviation = f"{error_k:.2f}"
    if Decimal(deviation) == 0:
        deviation = "0.00"
    duty_percent = f"{duty_raw * 100:.1f}"
    return (
        f"PI-Regelung: Abweichung {deviation.replace('.', ',')} K, "
        f"Tastgrad {duty_percent.replace('.', ',')} %, "
        f"{reason_code} -> {'Heizen' if heating else 'Aus'}."
    )


def _advance_valve_protection(
    session: Session, zone: Zone, state: ZoneState | None, now: datetime
) -> tuple[bool, bool]:
    """Advances the valve protection bookkeeping in `state` and reports its due-ness.

    Returns `(protection_due, protection_was_active)`. Two writes happen here, in the
    same order as before this was split out of `_process_zone`: closing an expired
    protection run, and the one-time bridge that condenses pre-existing shadow history
    into `last_regular_heat_at` for installations upgraded with history already in
    place. Both only ever touch `state`, so pulling them out changes nothing about when
    they run relative to the rest of the cycle.

    `protection_was_active` is deliberately the value of `protection_started is not
    None` from *before* this function's own write closes an expired run -- not the
    `protection_active` used internally to decide whether to close it. A cycle that
    closes an expired run must still report the zone as having been under protection:
    otherwise the previous on-state (which came from protection) would look like an
    ordinary hold to the hysteresis rule.

    That guard lasts exactly one cycle, and on its own it is not enough. If a zone's
    minimum *on* duration outlives its protection run -- both are per-zone settings,
    so `min_on_seconds=1200` together with a ten-minute run is a legal configuration
    -- the closing cycle answers `gesperrt_mindestdauer` and keeps the valve open,
    the marker is gone by the next cycle, and the protection on-state is then read as
    regular heating from there on. That is a known defect of the control rules, not
    of this bookkeeping -- found and fixed 2026-09-02 in
    `thermoctl.domain.control_loop.decide()`, which now exempts a held on-state from
    `min_on_seconds` whenever it traces back to a protection run.
    """
    interval = timedelta(days=zone.valve_protection_interval_days)
    run_duration = timedelta(minutes=zone.valve_protection_duration_minutes)
    protection_started = state.valve_protection_started_at if state is not None else None
    protection_active = (
        protection_started is not None and now < protection_started + run_duration
    )
    if state is not None and protection_started is not None and not protection_active:
        state.valve_protection_started_at = None
        # This timestamp closes a simulated shadow run, not a physical valve run.
        # Keeping it preserves the intended cadence in the comparison log; without
        # it, the still-due rule would restart in the same cycle and then forever
        # displace ordinary decisions. Actuator wiring must not treat this marker as
        # proof of movement; it will need its own confirmed-execution semantics.
        state.last_valve_protection_at = now
    if state is not None and not state.regular_heat_history_compacted:
        # One-time bridge for installations upgraded with existing shadow history.
        # Afterwards the condensed marker is authoritative, so protection scheduling
        # never has to reconstruct this operating state from the growing detailed log.
        state.last_regular_heat_at = session.scalar(
            select(ShadowDecision.decided_at)
            .where(
                ShadowDecision.zone_id == zone.id,
                ShadowDecision.would_heat.is_(True),
                ShadowDecision.outcome_code != REASON_CODE_VALVE_PROTECTION,
            )
            .order_by(ShadowDecision.decided_at.desc(), ShadowDecision.id.desc())
                .limit(1)
        )
        state.regular_heat_history_compacted = True
    last_movement = max(
        (moment for moment in (
            zone.created_at,
            state.last_regular_heat_at if state is not None else None,
            state.last_valve_protection_at if state is not None else None,
        ) if moment is not None),
    )
    protection_due = now >= last_movement + interval
    return protection_due, protection_started is not None


def _apply_decision_to_state(
    state: ZoneState | None, decision: Decision, now: datetime
) -> None:
    """Writes the consequence of a decision back into `state`'s valve protection markers.

    Split out of `_process_zone` because it is a separate concern from *making* the
    decision: `decide()` above only looks at the situation as it stood at the start of
    the cycle, this writes down what that decision means for the next one.
    """
    if state is None:
        return
    if decision.reason_code == REASON_CODE_VALVE_PROTECTION:
        if state.valve_protection_started_at is None:
            state.valve_protection_started_at = now
    elif (
        decision.heating
        and decision.reason_code != REASON_CODE_BLOCKED_MINIMUM_DURATION
    ):
        # Normal control has taken ownership of the on-state. Keeping the
        # protection marker would make the next hysteresis cycle treat that
        # regular state as temporary protection and switch it off too early.
        state.valve_protection_started_at = None
        # Persist simulated regular heating as constant-size operating state,
        # separately from the unbounded detailed shadow log. The marker records a
        # regular heating decision, not a command or physical movement.
        state.last_regular_heat_at = now


def _process_zone(
    session: Session,
    zone: Zone,
    now: datetime,
    forecast: list[HourlyForecast] | None = None,
) -> ShadowDecision:
    settings = session.get(Setting, 1)
    assert settings is not None, "setting-Zeile fehlt — Einrichtung unvollständig"

    state = session.get(ZoneState, zone.id)
    if state is None:
        measured_c = None
        sensor_status = NO_SOURCE
    else:
        measured_c = state.temperature_c
        sensor_status_row = session.get(SensorStatus, state.sensor_status_id)
        assert sensor_status_row is not None, "sensor_status-Zeile fehlt zur Referenz"
        sensor_status = sensor_status_row.code
    window_open, window_closed_for_s = _window_situation(session, zone, state, now)
    # Read straight off the already-persisted state, not recomputed: `state`
    # itself is `advance_zone_state`'s own result for this exact cycle, which set
    # `window_open` and `window_open_by_temperature` together
    # (`services/ingest.py`). `bool(state and ...)` matches `_window_situation`'s
    # own `bool(state and state.window_open)` immediately above for a missing row.
    window_open_by_temperature = bool(state and state.window_open_by_temperature)

    setpoint = resolved_setpoint(session, zone, now)
    frost_c = _frost_setpoint(session, zone, settings)
    parameter = control_parameters(session, zone)

    # Notbetrieb (Auftrag 7a): engaged for a zone that currently has it
    # switched on (the default for every new zone and, since migration
    # `d4a81c6e5b29`, for every pre-existing one), or one
    # with a still-open episode from before it was opted out again -- see the
    # module-level comment above `_apply_sensor_failure` for why the second
    # half of this condition matters. Every other zone never touches any of
    # this (bitgenau today's behaviour, the plan's explicit regression
    # requirement).
    #
    # Deliberately computed *before* `_previous_state()` below (Blocker 1,
    # Hauptsession-Vorgabe nach Auftrag 7b, Grundsatz 7): the one cycle an
    # episode ends (`events.episode_ended`), `_seed_recovery_phase_marker`
    # must get the chance to seed a protective `ShadowDecision` row *before*
    # `_previous_state()` reads this zone's history for this exact cycle --
    # otherwise the Mindestschaltdauer-Prüfung (`decide()` Regel 5) would see
    # only the simulated hysteresis history that ran, unaware of Notbetrieb,
    # throughout the whole episode, not the real relay state the
    # Notbetriebstakt actually left it in. See `_seed_recovery_phase_marker`'s
    # own docstring for the full reasoning.
    sf_db_state = session.get(ZoneSensorFailureState, zone.id)
    sensor_failure_outcome: _SensorFailureOutcome | None = None
    sf_stage: str | None = None
    if zone.sensor_failure_enabled or (
        sf_db_state is not None and sf_db_state.episode_id is not None
    ):
        sensor_failure_outcome = _apply_sensor_failure(
            session, zone, state, settings, parameter, sf_db_state, now
        )
        sf_stage = sensor_failure_outcome.stage_output.state.stage
        if sf_stage == emergency_operation.STAGE_ERSATZQUELLE:
            # Plan 1.4 Rang 2: the corrected replacement reading becomes the
            # zone's istwert for the ordinary `decide()` path below, sensor
            # status `ok` -- `_pi_gate_reason` is still told separately that an
            # emergency stage is active (see its own docstring), so PI does not
            # mistake this for a healthy, ordinary cycle.
            measured_c = sensor_failure_outcome.effective_temperature_c
            sensor_status = OK
        if sensor_failure_outcome.stage_output.events.episode_ended:
            _seed_recovery_phase_marker(session, zone, now)

    heating_now, held_for_s, previous_would_heat, phase_started_by = _previous_state(
        session, zone.id, now
    )
    setpoint_c, setpoint_reason = _with_solar_setback(
        setpoint, frost_c, zone, parameter, settings, forecast, now
    )

    override = _effective_override(session, zone, now)
    override_active = override is not None
    # Deliberately not folded into `override_active`/`Situation`: the project owner's
    # explicit requirement is that window handling, sensor-failure fallback, minimum
    # switch durations and valve protection all keep applying during a vacation
    # exactly as they do outside one -- `decide()` itself must not learn a vacation
    # exists. This value only feeds the PI setpoint-context key below, a concern
    # `decide()` never sees.
    vacation = running_vacation(session, now)
    protection_due, protection_was_active = _advance_valve_protection(session, zone, state, now)
    on_off_actuators_only = _on_off_actuators_only(session, zone)

    situation = Situation(
        measured_c=measured_c,
        setpoint_c=setpoint_c,
        setpoint_reason=setpoint_reason,
        frost_c=frost_c,
        operating_mode=zone.operating_mode.code,
        heating_now=heating_now,
        held_for_s=held_for_s,
        window_open=window_open,
        window_closed_for_s=window_closed_for_s,
        window_open_by_temperature=window_open_by_temperature,
        sensor_status=sensor_status,
        parameter=parameter,
        override_active=override_active,
        valve_protection_due=protection_due,
        # Also true in the first cycle at/after the deadline: the previous on-state
        # still came from protection and must not turn into an endless hysteresis hold.
        valve_protection_active=protection_was_active,
        on_off_actuators_only=on_off_actuators_only,
    )
    decision = decide(situation)
    if sensor_failure_outcome is not None and sf_stage == emergency_operation.STAGE_ERSATZQUELLE:
        # Plan Auftrag 7a, item 2: "eigener Hinweis im Entscheidungsgrund".
        # `decide()` itself never learns the source is a replacement -- this is
        # purely textual, `decision.heating`/`reason_code` stay exactly what
        # ordinary hysteresis against the corrected value produced.
        decision = replace(
            decision,
            reason=(
                f"{decision.reason} Ersatzquelle aktiv: "
                f"{sensor_failure_outcome.effective_device_name}."
            ),
        )

    # PI (steps 4 and 5 of the PI specification's build order, section 11): a
    # parallel candidate that -- once the zone is enabled for it, eligible, and no
    # precedence rule from section 4 overrides it -- becomes the effective
    # decision below. `decision` itself, and everything computed from `situation`
    # above, stays exactly the ordinary hysteresis path; nothing here feeds back
    # into `decide()`.
    sensor_failure_emergency_active = sf_stage is not None and sf_stage != (
        emergency_operation.STAGE_NORMAL
    )
    effective_heating, pi_reason_suffix, pi_fields = _pi_outcome(
        session,
        zone,
        state,
        situation,
        decision,
        parameter,
        settings,
        setpoint,
        override,
        vacation,
        phase_started_by,
        now,
        sensor_failure_emergency_active=sensor_failure_emergency_active,
    )
    # `decide()`'s rule 5 always answers the pure-hysteresis question, against
    # `situation.parameter.min_on_seconds`/`min_off_seconds` -- correct for that
    # question, but `REASON_CODE_BLOCKED_MINIMUM_DURATION` is exactly the code
    # `_pi_gate_reason()` treats as *not* blocking PI (its own, shorter minimums
    # govern instead -- see `TestPiGateReasonClassifiesEveryReasonCode` in
    # `tests/test_shadow_run_pi.py`). Once PI actually is this cycle's effective
    # controller, keeping `decision.reason_code`/`decision.reason` as the base --
    # as the general branch below does for every other code -- reports the wrong
    # minimum duration (the hysteresis one, not PI's) and, whenever PI's own
    # candidate actually differs from `decision.heating`, a reason sentence that
    # literally claims "bleibt unverändert" for a state that just changed.
    # Grundsatz 5 asks for an accurate reason, not merely an accurate `heating`
    # value -- found 2026-09-27 from the project owner's report that the 300s
    # hysteresis minimum still appeared to govern a PI-controlled zone. Rebuilt
    # entirely from PI's own outcome instead of patching decide()'s text: PI's
    # reason (`pi_reason_suffix`, always set whenever `pi_fields` names PI as the
    # effective controller) already names the applicable minimum-duration
    # decision (`pi_min_duration_decision`) and the resulting direction, with
    # nothing left over from the inapplicable hysteresis rule to contradict it.
    pi_overrides_hysteresis_block = (
        decision.reason_code == REASON_CODE_BLOCKED_MINIMUM_DURATION
        and pi_fields.get("effective_controller") == "pi"
    )
    if pi_overrides_hysteresis_block:
        assert pi_reason_suffix is not None  # always set once PI is effective
        effective_decision = replace(
            decision,
            heating=effective_heating,
            reason_code=REASON_CODE_HEATING if effective_heating else REASON_CODE_OFF,
            reason=pi_reason_suffix.strip(),
        )
    else:
        effective_decision = (
            decision
            if effective_heating == decision.heating and pi_reason_suffix is None
            else replace(
                decision,
                heating=effective_heating,
                reason=(
                    decision.reason
                    if pi_reason_suffix is None
                    else f"{decision.reason} {pi_reason_suffix}"
                ),
            )
        )

    if sensor_failure_outcome is not None and sf_stage in (
        emergency_operation.STAGE_NOTBETRIEB,
        emergency_operation.STAGE_RUECKKEHRPRUEFUNG,
    ):
        # Plan 1.4 Rang 3/4 -- persists the per-actuator handover/Takt plan
        # (`actuator_decision`/`actuator_emergency_state`, `simulated=True`)
        # for Auftrag 7b to read later, but **must not** touch
        # `effective_decision`/the zone's `ShadowDecision` row (Hauptsession
        # review of 88bc87a, security-relevant): `ShadowDecision.would_heat`
        # is exactly what the existing publisher
        # (`services/publishing.py::_latest_decision`/`_send_actuator_
        # switches`) reads and sends the moment an installation runs scharf,
        # regardless of Notbetrieb -- Auftrag 7a is shadow-only by scope, so
        # the zone-level decision a zone in Notbetrieb reports stays exactly
        # what `decide()` (necessarily `keine_quelle`/off here, both sources
        # having already failed) and PI (neutralised by
        # `sensor_failure_emergency_active` above regardless) would already
        # give without any of this module existing. Auftrag 7b is the one
        # place that may reroute the publisher to read the actuator plan
        # instead.
        _apply_emergency_actuators(session, zone, sensor_failure_outcome, settings, parameter, now)

    _apply_decision_to_state(state, effective_decision, now)

    row = ShadowDecision(
        decided_at=now,
        zone_id=zone.id,
        temperature_c=measured_c,
        setpoint_c=setpoint_c,
        setpoint_reason=setpoint_reason,
        would_heat=effective_decision.heating,
        previous_would_heat=previous_would_heat,
        # `effective_decision.reason_code`, not the raw `decision.reason_code`:
        # identical to it in every other branch (`replace()` above only touches
        # `reason_code` in the PI-overrides-hysteresis-block branch), but the
        # persisted `outcome_code` must name what actually governed this cycle,
        # not decide()'s pure-hysteresis answer whenever PI overrode it.
        outcome_code=effective_decision.reason_code,
        reason=effective_decision.reason,
        **pi_fields,
    )
    session.add(row)
    session.flush()
    return row


def cycle(
    session: Session,
    now: datetime,
    forecast: list[HourlyForecast] | None = None,
) -> list[ShadowDecision]:
    """One shadow cycle over all zones — writes, but switches nothing.

    A zone whose processing fails does not hold up the others: each zone runs in its
    own savepoint, whose rollback on an exception only undoes its own incomplete
    changes — not the zones already processed successfully within the same call.

    `forecast` is fetched once, outside this function (`integrations.forecast`), and
    handed to every zone unchanged -- there is exactly one installation-wide location,
    so one fetch per cycle already covers every zone.
    """
    results: list[ShadowDecision] = []
    for zone in session.scalars(select(Zone).order_by(Zone.id)):
        try:
            with session.begin_nested():
                row = _process_zone(session, zone, now, forecast)
        except Exception:
            log.exception(
                "Schattenzyklus für eine Zone gescheitert — übrige Zonen laufen weiter",
                extra={"zone_id": zone.id},
            )
            continue
        results.append(row)
    return results
