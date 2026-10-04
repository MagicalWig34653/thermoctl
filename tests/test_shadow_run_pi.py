"""Wiring the PI controller (`thermoctl.domain.pi_control`) into the shadow run.

The next two build steps after `thermoctl/domain/pi_control.py` itself:
`services/shadow_run.py` loads a zone's `zone_state` PI columns, calls the pure
functions from `domain/pi_control.py`, and -- once a zone is enabled for PI,
eligible, and none of the seven precedence rules override it -- the PI
candidate becomes `ShadowDecision.would_heat`, the same field
`services/publishing.py` reads back out unchanged. `domain.control_loop.decide()`
itself is never touched; `tests/test_control_loop_state_table.py` stays the
exhaustive proof of that for `pi_enabled=False`.

There is still no operating path that can set `zone.pi_enabled` (that is a
separate, later task -- see `tests/test_pi_schema.py`), so every test here sets it
directly on the ORM object, exactly like that file already does.
"""

import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.helpers import (
    capability,
    create_mode,
    create_settings,
    integration,
    operating_mode,
    role,
    sensor_status_of,
)
from tests.test_control_loop import _lage, _parameter
from thermoctl.db.models.device import Device, DeviceCapabilityLink, ZoneDevice
from thermoctl.db.models.measurement import Measurement
from thermoctl.db.models.operations import Setting
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.state import ShadowDecision, ZoneState
from thermoctl.db.models.zone import Zone, ZoneSetpoint
from thermoctl.domain import control_loop
from thermoctl.domain.control_loop import (
    REASON_CODE_HEATING,
    REASON_CODE_OFF,
    REASON_CODE_WINDOW_OPEN,
)
from thermoctl.domain.pi_control import (
    INTEGRATOR_HOLD,
    INTEGRATOR_RESET,
    MODULATOR_REASON_HELD,
    RESET_REASON_ARMING,
    RESET_REASON_CONTEXT_CHANGE,
    RESET_REASON_FROST,
    RESET_REASON_INVALID_STATE,
    RESET_REASON_SENSOR_FAILURE,
    RESET_REASON_VALVE_PROTECTION,
    RESET_REASON_WINDOW_OPEN,
    WINDOW_SECONDS,
    window_start_for,
)
from thermoctl.services import shadow_run
from thermoctl.services.shadow_run import PI_FALLBACK_HYSTERESIS_MINIMUM, PI_FALLBACK_INELIGIBLE

NOW = datetime(2026, 9, 7, 12, 0)  # a Monday -- matches weekday 1 in the schedule below
NOW_AWARE = NOW.replace(tzinfo=UTC)  # for calling `window_start_for()` directly in assertions
SETPOINT_C = Decimal("21.0")
COLD_C = Decimal("18.0")  # 3K below setpoint, well outside a 0.3K hysteresis band
WARM_C = Decimal("21.5")  # inside the band, but not below it


def _ensure_settings(session: Session) -> Setting:
    """`create_settings()` inserts the single `setting` row (id=1) -- every zone
    in one test shares it, so only the first `_pi_zone()` call in a test may
    create it."""
    existing = session.get(Setting, 1)
    if existing is not None:
        return existing
    return create_settings(session)


def _assign_actuator(
    session: Session,
    zone: Zone,
    *,
    self_regulating: bool = False,
    capability_code: str = "switch",
    suffix: str = "",
) -> Device:
    device = Device(
        integration_id=integration(session).id,
        external_id=f"{zone.name}-relais{suffix}",
        display_name=f"{zone.name}-relais{suffix}",
    )
    session.add(device)
    session.flush()
    session.add(
        DeviceCapabilityLink(
            device_id=device.id, capability_id=capability(session, capability_code).id
        )
    )
    session.add(
        ZoneDevice(
            zone_id=zone.id,
            device_id=device.id,
            device_role_id=role(session, "actuator").id,
            self_regulating=self_regulating,
        )
    )
    session.flush()
    return device


def _pi_zone(
    session: Session,
    name: str,
    *,
    measured_c: Decimal = COLD_C,
    with_actuator: bool = True,
    pi_enabled: bool = True,
    pi_min_on: int = 60,
    pi_min_off: int = 60,
    already_running: bool = True,
    with_state: bool = True,
) -> Zone:
    """A zone with a schedule, a fresh measurement, and -- unless told otherwise --
    a single ordinary switch actuator (PI-eligible on its own).

    `already_running=True` seeds `pi_last_control_armed` as if the zone had
    already been through PI's safe-start wait in some earlier cycle -- most tests
    care about PI's *regular* behaviour, not that one-time boundary wait, which
    has its own dedicated tests below.
    """
    _ensure_settings(session)
    zone = Zone(
        name=name, display_name=name.capitalize(), operating_mode_id=operating_mode(session).id,
        sensor_failure_enabled=False,  # test baseline: emergency operation off
    )
    session.add(zone)
    session.flush()
    # Ausdruecklich gesetzt, nicht dem Spaltenvorgabewert ueberlassen: `created_at`
    # geht in die Faelligkeit des Ventilschutzes ein (`shadow_run.py`, `last_movement`),
    # und der Vorgabewert ist die *echte* Uhr -- waehrend dieser Test mit dem festen
    # `NOW` rechnet. Beides zusammen macht die Faelligkeit vom Kalendertag abhaengig,
    # an dem die Suite laeuft: Der Ventilschutz-Test war am 05.09. gruen und am 06.09.
    # rot, ohne dass sich eine Zeile Code geaendert haette.
    zone.created_at = NOW - timedelta(days=30)
    mode = create_mode(session, f"heizen-{name}")
    session.add(ZoneSetpoint(zone_id=zone.id, setpoint_mode_id=mode.id, temperature_c=SETPOINT_C))
    session.add(
        SchedulePoint(zone_id=zone.id, weekday=1, minute_of_day=0, setpoint_mode_id=mode.id)
    )
    zone.pi_enabled = pi_enabled
    zone.pi_gain_per_k = Decimal("0.25")
    zone.pi_integral_time_minutes = 180
    zone.pi_min_on_seconds = pi_min_on
    zone.pi_min_off_seconds = pi_min_off
    if with_actuator:
        _assign_actuator(session, zone)
    if not with_state:
        session.flush()
        return zone
    state = ZoneState(
        zone_id=zone.id,
        temperature_c=measured_c,
        measured_at=NOW,
        sensor_status_id=sensor_status_of(session).id,
        window_open=False,
        updated_at=NOW,
    )
    if already_running:
        state.pi_last_control_armed = False
        # A real evaluation 60s ago -- so the very first cycle at `NOW` already has
        # a valid `dt` (`pi_control.pi_dt()` demands a previous evaluation; without
        # one, the first cycle of any brand-new PI zone is itself a lockout, its
        # own dedicated case in `TestSafeStart`, not what most tests here mean to
        # exercise).
        state.pi_last_evaluated_at = NOW - timedelta(seconds=60)
    session.add(state)
    session.flush()
    return zone


def _row_for(rows: list[ShadowDecision], zone: Zone) -> ShadowDecision:
    return next(r for r in rows if r.zone_id == zone.id)


class TestPiGateReasonClassifiesEveryReasonCode:
    """`_pi_gate_reason()` is *permissive by default*: any `decide()` reason code it
    does not recognise falls through its `if`/`elif` chain to `None`, which means
    "no precedence rule applies, PI decides". That is the right answer for the
    codes this function already knows about (see the table below) -- but it is the
    wrong direction for a code nobody has looked at yet: a code added to
    `control_loop.py` in some later change and never taught to `_pi_gate_reason()`
    would silently let PI decide in a situation nobody has actually reasoned about.

    This test does not call `_pi_gate_reason()` and trust its `None` answer as
    proof of anything -- an unclassified code produces exactly the same `None` a
    deliberately-permitted one does, so that would prove nothing. Instead it
    verifies, once, that *this file's own* classification below accounts for every
    single `REASON_CODE_*` constant `control_loop.py` currently defines, then
    exercises `_pi_gate_reason()` against each of those to check the mapping still
    holds. Add a new code to `control_loop.py` without touching either dict here,
    and the completeness check below fails first -- forcing a conscious decision
    ("does this block PI or not, and why") instead of inheriting one silently.
    """

    # Every code `decide()` can return that must reset PI (section 4 of the PI
    # specification), and the `pi_control` reset reason `_pi_gate_reason()` must
    # answer with for it. `REASON_CODE_OFF` is deliberately absent here: it is
    # ambiguous on its own (rule 4's resume delay *and* rule 6's ordinary "off"
    # share it) and is disambiguated by the caller-computed `resume_delay_active`
    # flag, exercised separately in `TestPrecedenceRulesBeatPi`.
    _BLOCKING = {
        control_loop.REASON_CODE_WINDOW_OPEN: RESET_REASON_WINDOW_OPEN,
        control_loop.REASON_CODE_NO_SOURCE: RESET_REASON_SENSOR_FAILURE,
        control_loop.REASON_CODE_FROST_SENSOR_FAILURE: RESET_REASON_SENSOR_FAILURE,
        control_loop.REASON_CODE_VALVE_PROTECTION: RESET_REASON_VALVE_PROTECTION,
        # Added 2026-09-06 alongside rule 3's frost-overrides-window exception: this
        # code is unique to that exception actually engaging while it governs (an
        # EIN/AUS-only zone never produces it, since rule 3 never applies to one) --
        # so it is covered the same way the ordinary window-open "off" is, via
        # `window_governs`, not via its own separate branch in `_pi_gate_reason`.
        control_loop.REASON_CODE_FROST_OVERRIDES_WINDOW: RESET_REASON_WINDOW_OPEN,
    }

    # Every code that must *not* block PI, with the reason each is deliberately
    # permitted:
    _PERMITTED = {
        # Rule 6's own "heat" answer -- exactly the territory PI is meant to
        # replace.
        control_loop.REASON_CODE_HEATING: "PI ersetzt genau diese Entscheidung.",
        # Ambiguous on its own (see `_BLOCKING`'s comment); the plain "stay off"
        # case it also covers is ordinary rule-6 territory.
        control_loop.REASON_CODE_OFF: (
            "mehrdeutig -- der Wiederanlauf-Fall wird über resume_delay_active "
            "erkannt, nicht über den Code."
        ),
        # Rule 6/7's "state holds" answer -- still ordinary territory, not one of
        # section 4's precedence rules.
        control_loop.REASON_CODE_UNCHANGED: "gewöhnliche Hysterese-Fortschreibung.",
        # The explicit, deliberate exception from the PI specification's section 2:
        # PI has its own, shorter minimum durations and its own tastgrad-vorrang
        # exception; the ordinary minimum-duration rule must not additionally hold
        # PI back.
        control_loop.REASON_CODE_BLOCKED_MINIMUM_DURATION: (
            "PI hat eigene, kürzere Mindestdauern (Spezifikation Abschnitt 2/3)."
        ),
    }

    def test_every_known_reason_code_is_classified(self) -> None:
        known = {
            value
            for name, value in vars(control_loop).items()
            if name.startswith("REASON_CODE_") and isinstance(value, str)
        }
        classified = set(self._BLOCKING) | set(self._PERMITTED)

        unclassified = known - classified
        assert not unclassified, (
            f"control_loop.py kennt Ergebniscode(s) {sorted(unclassified)}, die "
            "in diesem Test weder als blockierend noch als bewusst erlaubt "
            "eingeordnet sind. _pi_gate_reason() ist erlaubend per Vorgabe -- ein "
            "unklassifizierter Code lässt PI stillschweigend entscheiden. Ordne "
            "ihn hier in _BLOCKING oder _PERMITTED ein, mit Begründung."
        )
        stale = classified - known
        assert not stale, (
            f"Klassifizierte Code(s) {sorted(stale)} existieren nicht mehr in "
            "control_loop.py -- die Klassifikation hier ist veraltet."
        )

    def test_blocking_codes_gate_pi_on_their_own(self) -> None:
        # `resume_delay_active`/`frost_effective` are not exercised as `True` here
        # together with an unrelated blocking code: in real `decide()` output they
        # are mutually exclusive with most of `_BLOCKING` by construction (rule 4's
        # resume delay and rule 1's sensor-failure/rule 7's valve-protection codes
        # cannot co-occur -- whichever rule actually won is the only one whose
        # condition is true this cycle). `TestPrecedenceRulesBeatPi` below exercises
        # `resume_delay_active` and `frost_effective` themselves, end to end.
        # `window_governs` (added 2026-09-06 alongside rule 3's frost-overrides-window
        # exception and the EIN/AUS exemption) replaces the old bare
        # `reason_code == REASON_CODE_WINDOW_OPEN` check -- true here exactly for the
        # one code it is meant to gate, mirroring how `_process_zone` derives it from
        # `Situation.window_open and not Situation.on_off_actuators_only`.
        #
        # `sensor_failed` (added 2026-09-27, security finding on the reason/outcome
        # fix above): mirrors `window_governs` in the same way -- true here exactly
        # for the two codes rule 1's sensor check is meant to gate, and *not* read
        # from `reason_code` at all inside `_pi_gate_reason()` itself, because rule 5
        # can return `REASON_CODE_BLOCKED_MINIMUM_DURATION` before rule 6 ever
        # assigns `REASON_CODE_FROST_SENSOR_FAILURE` (see `_pi_gate_reason`'s own
        # docstring). `TestPrecedenceRulesBeatPi` exercises that masking scenario
        # end to end; this test only proves the mapping in isolation.
        window_governed_codes = (
            control_loop.REASON_CODE_WINDOW_OPEN,
            control_loop.REASON_CODE_FROST_OVERRIDES_WINDOW,
        )
        sensor_failure_codes = (
            control_loop.REASON_CODE_NO_SOURCE,
            control_loop.REASON_CODE_FROST_SENSOR_FAILURE,
        )
        for code, expected_reason in self._BLOCKING.items():
            assert (
                shadow_run._pi_gate_reason(
                    code,
                    sensor_failed=code in sensor_failure_codes,
                    window_governs=code in window_governed_codes,
                    resume_delay_active=False,
                    frost_effective=False,
                )
                == expected_reason
            )

    def test_permitted_codes_let_pi_decide_absent_the_other_gates(self) -> None:
        for code in self._PERMITTED:
            assert (
                shadow_run._pi_gate_reason(
                    code, sensor_failed=False, window_governs=False,
                    resume_delay_active=False, frost_effective=False,
                )
                is None
            )

    def test_window_governing_gates_pi_even_for_an_otherwise_permitted_code(self) -> None:
        """`window_governs=True` must win regardless of `reason_code` -- it covers
        every outcome rule 3's frost exception can now produce for a cycle it
        actually applies to, including a minimum-switch-duration hold
        (`REASON_CODE_BLOCKED_MINIMUM_DURATION`) that happens to interrupt it, which
        on its own is one of `_PERMITTED`'s codes."""
        assert (
            shadow_run._pi_gate_reason(
                control_loop.REASON_CODE_BLOCKED_MINIMUM_DURATION,
                sensor_failed=False,
                window_governs=True,
                resume_delay_active=False,
                frost_effective=False,
            )
            == RESET_REASON_WINDOW_OPEN
        )

    def test_sensor_failed_gates_pi_even_for_an_otherwise_permitted_code(self) -> None:
        """Mirrors the window test above, for `sensor_failed` -- the exact
        combination the security finding reproduced end to end
        (`REASON_CODE_BLOCKED_MINIMUM_DURATION` masking a stale reading)."""
        assert (
            shadow_run._pi_gate_reason(
                control_loop.REASON_CODE_BLOCKED_MINIMUM_DURATION,
                sensor_failed=True,
                window_governs=False,
                resume_delay_active=False,
                frost_effective=False,
            )
            == RESET_REASON_SENSOR_FAILURE
        )


class TestZoneWithoutPiIsUnaffected:
    """The single most important test in this file (per the task): a zone that
    could run PI (an eligible switch actuator is assigned) but has
    `pi_enabled=False` must decide *exactly* as it would with no PI code in the
    picture at all -- bitwise, not just "close enough"."""

    def test_an_eligible_but_disabled_zone_matches_one_with_no_actuator_at_all(
        self, session: Session
    ) -> None:
        without_actuator = _pi_zone(
            session, "ohne-aktor", with_actuator=False, pi_enabled=False
        )
        with_actuator = _pi_zone(
            session, "mit-aktor-aber-aus", with_actuator=True, pi_enabled=False
        )

        rows = shadow_run.cycle(session, NOW)
        a = _row_for(rows, without_actuator)
        b = _row_for(rows, with_actuator)

        assert (a.would_heat, a.outcome_code, a.setpoint_c) == (
            b.would_heat, b.outcome_code, b.setpoint_c
        )
        # The reason text itself differs only in the two fixtures' own schedule
        # mode names ("Heizen-ohne-aktor" vs "Heizen-mit-aktor-aber-aus") -- a test
        # artifact, not something PI touches; the part `decide()` actually wrote is
        # identical.
        assert a.reason.split(" (")[0] == b.reason.split(" (")[0]
        assert a.would_heat is True  # sanity: COLD_C is below setpoint, hysteresis heats
        for row in (a, b):
            assert row.requested_controller == "hysteresis"
            assert row.effective_controller == "hysteresis"
            assert row.controller_fallback_reason is None
            assert row.pi_candidate_would_heat is None
            assert row.pi_integrator_action is None
            assert row.pi_error_k is None

    def test_a_disabled_zone_heats_and_stops_exactly_like_hysteresis_over_several_cycles(
        self, session: Session
    ) -> None:
        zone = _pi_zone(session, "hysterese-normal", measured_c=COLD_C, pi_enabled=False)
        now = NOW
        first = _row_for(shadow_run.cycle(session, now), zone)
        assert first.would_heat is True
        assert first.outcome_code == REASON_CODE_HEATING

        state = session.get(ZoneState, zone.id)
        assert state is not None
        state.temperature_c = WARM_C + Decimal("1.0")  # comfortably above setpoint + h
        # Past the default 300s minimum switch-off duration -- otherwise rule 5
        # (unaffected by PI either way) would keep this zone on regardless.
        now += timedelta(seconds=301)
        second = _row_for(shadow_run.cycle(session, now), zone)
        assert second.would_heat is False
        assert second.outcome_code == REASON_CODE_OFF


class TestNoZoneState:
    def test_a_pi_enabled_zone_with_no_zone_state_row_falls_back_without_error(
        self, session: Session
    ) -> None:
        zone = _pi_zone(session, "kein-zustand", with_state=False)
        row = _row_for(shadow_run.cycle(session, NOW), zone)

        assert row.would_heat is False  # decide()'s own REASON_CODE_NO_SOURCE answer
        assert row.requested_controller == "pi"
        assert row.effective_controller == "hysteresis"
        assert row.pi_candidate_would_heat is None


class TestIneligibilityFallsBackVisibly:
    def test_a_self_regulating_valve_alone_makes_the_zone_ineligible(
        self, session: Session
    ) -> None:
        """A self-regulating valve is never counted as the required ordinary switch
        actuator (`pi_eligible()` skips it) -- so a zone with nothing else assigned
        stays ineligible for lack of a switch, not because the valve itself is
        disqualifying (see `TestMixedZoneWithASelfRegulatingValve` below for the
        case where a switch is also present)."""
        zone = _pi_zone(session, "selbstregelnd", with_actuator=False)
        _assign_actuator(session, zone, self_regulating=True)

        rows = shadow_run.cycle(session, NOW)
        row = _row_for(rows, zone)

        assert row.requested_controller == "pi"
        assert row.effective_controller == "hysteresis"
        assert row.controller_fallback_reason == PI_FALLBACK_INELIGIBLE
        assert "PI-Rückfall" in row.reason
        assert row.would_heat is True  # falls back to the hysteresis answer, unharmed
        assert row.pi_candidate_would_heat is None

        state = session.get(ZoneState, zone.id)
        assert state is not None
        assert state.pi_integral == Decimal("0")
        assert state.pi_last_reset_reason is None  # a full wipe, not a tracked reset

    def test_a_thermostat_capable_device_makes_the_zone_ineligible(
        self, session: Session
    ) -> None:
        """Not self-regulating, but carrying `thermostat` -- `thermostat_commands()`
        would turn PI's `heating` into a setpoint jump, so the zone stays ineligible
        regardless of what else is assigned to it."""
        zone = _pi_zone(session, "thermostatfähig", with_actuator=False)
        _assign_actuator(session, zone, capability_code="thermostat")

        row = _row_for(shadow_run.cycle(session, NOW), zone)
        assert row.controller_fallback_reason == PI_FALLBACK_INELIGIBLE

    def test_no_ordinary_actuator_at_all_makes_the_zone_ineligible(
        self, session: Session
    ) -> None:
        zone = _pi_zone(session, "kein-aktor", with_actuator=False)
        row = _row_for(shadow_run.cycle(session, NOW), zone)
        assert row.controller_fallback_reason == PI_FALLBACK_INELIGIBLE


class TestMixedZoneWithASelfRegulatingValve:
    """The case that changed the rule: the project owner's own room, one
    self-regulating radiator thermostat next to one Meross switch. Only the switch
    is meant to hear PI's decision -- see `pi_eligible()`'s docstring
    (`thermoctl/domain/pi_control.py`) for why a self-regulating valve is safe to
    ignore here: `switch_commands()`/`thermostat_commands()`
    (`thermoctl/domain/switch_commands.py`) both filter it out of their own queries
    via `ZoneDevice.self_regulating.is_(False)`, so it is structurally unreachable
    from `publishing.py`'s `switch_commands` dispatch and only ever receives its own
    setpoint through `domain/self_regulating.py`.
    """

    def test_a_self_regulating_thermostat_next_to_a_switch_lets_pi_decide(
        self, session: Session
    ) -> None:
        zone = _pi_zone(session, "heizkörper-und-meross", with_actuator=False)
        # The self-regulating radiator thermostat -- excluded from both command
        # queries by `self_regulating=True`, so it must not block PI eligibility.
        _assign_actuator(
            session,
            zone,
            self_regulating=True,
            capability_code="thermostat",
            suffix="-heizkörper",
        )
        # The Meross switch -- the one actuator PI's decision can actually reach.
        switch = _assign_actuator(session, zone, capability_code="switch", suffix="-meross")

        rows = shadow_run.cycle(session, NOW)
        row = _row_for(rows, zone)

        # The zone is eligible: PI decides, hysteresis is not the fallback.
        assert row.requested_controller == "pi"
        assert row.effective_controller == "pi"
        assert row.controller_fallback_reason is None
        assert row.pi_candidate_would_heat is not None

        # The command layer sends the on/off result to the switch alone -- the
        # self-regulating valve is excluded by construction (`self_regulating.is_(False)`
        # in both `switch_commands()` and `thermostat_commands()`), never by anything
        # PI computes.
        from thermoctl.domain.switch_commands import switch_commands, thermostat_commands

        actual_zone = session.get(Zone, zone.id)
        assert actual_zone is not None
        switch_only = switch_commands(session, actual_zone)
        assert [command.device.id for command in switch_only] == [switch.id]
        assert thermostat_commands(session, actual_zone) == []

    def test_a_too_short_pi_minimum_against_the_control_cycle_is_ineligible(
        self, session: Session
    ) -> None:
        # setting.shadow_interval_seconds defaults to 60 -- the modelled control
        # cycle can never be shorter than a PI minimum below it (section 3).
        zone = _pi_zone(session, "zu-kurz", pi_min_on=60, pi_min_off=60)
        state = session.get(ZoneState, zone.id)
        assert state is not None
        settings = session.get(Setting, 1)
        assert settings is not None
        settings.shadow_interval_seconds = 90
        session.flush()

        row = _row_for(shadow_run.cycle(session, NOW), zone)
        assert row.controller_fallback_reason == PI_FALLBACK_INELIGIBLE


class TestSafeStart:
    """Section 4's closing paragraph: "Dasselbe sichere Anlaufen gilt nach
    fehlendem oder beschädigtem PI-Zustand" -- a zone's very first PI cycle has
    exactly that (no prior `pi_last_control_armed`), and the transition from dry
    run to armed gets the same treatment (`RESET_REASON_ARMING`)."""

    def test_the_very_first_pi_cycle_waits_for_the_next_window_boundary(
        self, session: Session
    ) -> None:
        zone = _pi_zone(session, "erstlauf", already_running=False)
        row = _row_for(shadow_run.cycle(session, NOW), zone)

        assert row.controller_fallback_reason == RESET_REASON_INVALID_STATE
        assert row.pi_reset_reason == RESET_REASON_INVALID_STATE
        assert row.pi_integrator_action == INTEGRATOR_RESET
        assert row.would_heat is True  # the ordinary hysteresis answer, unharmed
        assert row.effective_controller == "hysteresis"

        state = session.get(ZoneState, zone.id)
        assert state is not None
        # `zone_state` columns are naive (implicitly UTC, like everywhere else in
        # this application) -- `window_start_for()` itself demands an aware value.
        expected = window_start_for(NOW_AWARE) + timedelta(seconds=WINDOW_SECONDS)
        assert state.pi_awaiting_boundary_until == expected.replace(tzinfo=None)

    def test_arming_from_dry_run_also_waits_for_the_next_boundary(
        self, session: Session
    ) -> None:
        zone = _pi_zone(session, "scharfschaltung")
        state = session.get(ZoneState, zone.id)
        assert state is not None
        state.pi_last_control_armed = False  # a zone that has run PI in dry run before
        session.flush()

        settings = session.get(Setting, 1)
        assert settings is not None
        settings.control_armed = True
        session.flush()

        row = _row_for(shadow_run.cycle(session, NOW), zone)
        assert row.controller_fallback_reason == RESET_REASON_ARMING
        assert row.would_heat is True

    def test_pi_becomes_available_again_at_the_next_boundary(self, session: Session) -> None:
        zone = _pi_zone(session, "wartezeit-vorbei", already_running=False, measured_c=COLD_C)
        now = NOW
        shadow_run.cycle(session, now)  # triggers the safe-start wait

        boundary = (window_start_for(NOW_AWARE) + timedelta(seconds=WINDOW_SECONDS)).replace(
            tzinfo=None
        )
        while now < boundary:
            now += timedelta(seconds=60)
            row = _row_for(shadow_run.cycle(session, now), zone)
        assert row.effective_controller == "pi"
        assert row.pi_candidate_would_heat is not None


class TestPrecedenceRulesBeatPi:
    """One test per row of section 4's table: each precedence rule still wins,
    and the integrator is reset (or held, for the PI minimum-duration row) exactly
    as the table prescribes -- proven over a real multi-cycle sequence, not a
    single call, because windup only shows up over time."""

    def test_window_open_resets_every_cycle_for_two_hours(self, session: Session) -> None:
        """A *mixed* zone (owner's decision, 2026-09-06): `_pi_zone()`'s own plain
        switch actuator would, on its own, now make this an EIN/AUS-zone, which an
        open window no longer switches off at all -- see
        `TestOnOffOnlyZoneIgnoresTheWindow` below for exactly that case. Adding a
        second, self-regulating actuator (the same fixture
        `TestMixedZoneWithASelfRegulatingValve` uses) keeps this zone in the *more
        cautious*, unaffected category, so this test still proves what it always
        did: an open window resets PI every cycle."""
        zone = _pi_zone(session, "fenster-offen", measured_c=COLD_C)
        _assign_actuator(
            session, zone, self_regulating=True, capability_code="thermostat",
            suffix="-heizkörper",
        )
        state = session.get(ZoneState, zone.id)
        assert state is not None
        state.window_open = True
        session.flush()

        now = NOW
        for _ in range(120):  # 2h at 60s cycles
            row = _row_for(shadow_run.cycle(session, now), zone)
            assert row.would_heat is False
            assert row.outcome_code == REASON_CODE_WINDOW_OPEN
            assert row.pi_reset_reason == RESET_REASON_WINDOW_OPEN
            assert row.pi_integrator_action == INTEGRATOR_RESET
            now += timedelta(seconds=60)

        state = session.get(ZoneState, zone.id)
        assert state is not None
        assert state.pi_integral == Decimal("0")
        assert state.pi_time_balance_seconds == Decimal("0")

        # And once it closes, PI starts from a genuinely fresh window -- no debt
        # from the two hours the window was open.
        state.window_open = False
        session.flush()
        now += timedelta(seconds=60)
        row = _row_for(shadow_run.cycle(session, now), zone)
        assert row.effective_controller == "pi"
        assert row.pi_integral_before == Decimal("0")

    def test_the_window_resume_delay_also_resets_pi_not_just_the_open_window(
        self, session: Session
    ) -> None:
        """A mixed zone, for the same reason as the test above: an EIN/AUS-only
        zone skips rule 4 (the resume delay) entirely and is covered separately in
        `TestOnOffOnlyZoneIgnoresTheWindow`."""
        zone = _pi_zone(session, "nachlauf", measured_c=COLD_C)
        _assign_actuator(
            session, zone, self_regulating=True, capability_code="thermostat",
            suffix="-heizkörper",
        )
        state = session.get(ZoneState, zone.id)
        assert state is not None
        # A window that has just closed -- `_window_situation` only derives this
        # from device history, so the test seeds `zone_state.window_open = False`
        # (already the default here) and instead proves the resume delay's own
        # effect directly through the resolved `Situation`, the same way
        # `tests/test_shadow_run.py` already covers the resume delay itself: by
        # observing the zone's own decision history. A single cold cycle followed
        # immediately by another is not, on its own, inside a resume delay -- the
        # window default of `window_resume_delay_seconds` is 0 unless configured,
        # so instead this test configures a real delay and a device history.
        from thermoctl.db.models.device import Device as DeviceModel
        from thermoctl.db.models.measurement import Measurement

        contact_capability = capability(session, "contact")
        window_role = role(session, "window_contact")
        sensor = DeviceModel(
            integration_id=integration(session).id,
            external_id=f"{zone.name}-fenster",
            display_name=f"{zone.name}-fenster",
        )
        session.add(sensor)
        session.flush()
        session.add(
            ZoneDevice(zone_id=zone.id, device_id=sensor.id, device_role_id=window_role.id)
        )
        session.add(
            Measurement(
                device_id=sensor.id,
                capability_id=contact_capability.id,
                value_text="false",
                measured_at=NOW - timedelta(minutes=30),
                received_at=NOW - timedelta(minutes=30),
            )
        )
        session.add(
            Measurement(
                device_id=sensor.id,
                capability_id=contact_capability.id,
                value_text="true",  # closed again 30s ago
                measured_at=NOW - timedelta(seconds=30),
                received_at=NOW - timedelta(seconds=30),
            )
        )
        zone.window_resume_delay_seconds = 300
        # `state.window_open` stays `False` (set by `_pi_zone`) -- `_window_situation`
        # only walks the device history when it is exactly `False` ("known closed"),
        # to compute how long ago that was.
        session.flush()

        row = _row_for(shadow_run.cycle(session, NOW), zone)
        assert row.would_heat is False
        assert row.pi_reset_reason == RESET_REASON_WINDOW_OPEN

    def test_frost_protection_mode_resets_every_cycle_it_is_effective(
        self, session: Session
    ) -> None:
        zone = _pi_zone(session, "frostschutz", measured_c=Decimal("10.0"))
        # Switch operating mode to 'off' -- `resolved_setpoint()` then always
        # answers with the frost-protection setpoint, section 4's "Frostschutz als
        # Betriebsart" case. Assigning the relationship object itself (not just
        # the raw `operating_mode_id` column) keeps `zone.operating_mode.code`
        # correct without an explicit re-fetch/expire.
        zone.operating_mode = operating_mode(session, "off")
        session.flush()

        now = NOW
        for _ in range(30):
            row = _row_for(shadow_run.cycle(session, now), zone)
            assert row.pi_reset_reason == RESET_REASON_FROST
            assert row.pi_integrator_action == INTEGRATOR_RESET
            now += timedelta(seconds=60)

        state = session.get(ZoneState, zone.id)
        assert state is not None
        assert state.pi_integral == Decimal("0")

    def test_sensor_failure_resets_and_stale_readings_do_not_resume_into_pi(
        self, session: Session
    ) -> None:
        zone = _pi_zone(session, "sensorausfall", measured_c=COLD_C)
        state = session.get(ZoneState, zone.id)
        assert state is not None
        state.temperature_c = None
        state.sensor_status_id = sensor_status_of(session, "keine_quelle").id
        session.flush()

        row = _row_for(shadow_run.cycle(session, NOW), zone)
        assert row.would_heat is False
        assert row.pi_reset_reason == RESET_REASON_SENSOR_FAILURE
        assert row.pi_integrator_action == INTEGRATOR_RESET

    def test_a_stale_sensor_gates_pi_even_when_a_minimum_duration_block_masks_it(
        self, session: Session
    ) -> None:
        """Security finding from the cross-review of commit 45ecf1a (2026-09-27):
        `_pi_gate_reason` used to recognise a stale ("veraltet") sensor only via
        `decision.reason_code == REASON_CODE_FROST_SENSOR_FAILURE` -- but
        `decide()` only assigns that code inside rule 6, and rule 5 (the ordinary
        minimum-switch-duration hold) sits *before* rule 6 and returns
        `REASON_CODE_BLOCKED_MINIMUM_DURATION` first whenever the current state
        has not been held long enough. On such a cycle PI kept computing a real
        candidate against the zone's normal setpoint and the stale measurement --
        exactly what rule 1 exists to prevent.

        Reproduced with the exact scenario from the review: setpoint 21 °C,
        measured 20.9 °C (inside the hysteresis band -- "aus" from the start,
        so nothing but rule 5 ever has a reason to block a switch), a 300 s
        hysteresis minimum, a 60 s PI minimum. The sensor goes stale after the
        first cycle; before the fix, `would_heat` flipped to `True` on PI's own
        candidate at t=120s with `effective_controller` still `"pi"`.

        Proven against a twin, PI-disabled zone under an identical timeline
        rather than a hand-picked expected value: the fix's own safety
        requirement *is* "behaves exactly like the hysteresis-only zone", so that
        is exactly what this test compares against, cycle by cycle.
        """
        pi_zone = _pi_zone(
            session, "pi-sensor-veraltet", measured_c=Decimal("20.9"), pi_min_on=60, pi_min_off=60
        )
        hysteresis_zone = _pi_zone(
            session, "hysterese-sensor-veraltet", measured_c=Decimal("20.9"), pi_enabled=False
        )

        now = NOW
        for i in range(4):
            if i == 1:
                # Sensor goes stale right after the first cycle (t=0s) for both
                # zones, *before* the cycle at t=60s below -- so both already
                # share an identical "aus" history to hold from when it does.
                for z in (pi_zone, hysteresis_zone):
                    state = session.get(ZoneState, z.id)
                    assert state is not None
                    state.sensor_status_id = sensor_status_of(session, "veraltet").id
                session.flush()

            pi_row = _row_for(shadow_run.cycle(session, now), pi_zone)
            control_row = _row_for(shadow_run.cycle(session, now), hysteresis_zone)
            now += timedelta(seconds=60)

            assert pi_row.would_heat == control_row.would_heat, (
                f"cycle {i}: PI zone diverged from the hysteresis-only twin while "
                "the sensor was stale"
            )
            if i >= 1:
                # The actual bug: this stayed "pi" before the fix, at exactly the
                # cycle (t=120s, i=2) the review reproduced `would_heat=True` on.
                assert pi_row.effective_controller == "hysteresis"
                assert pi_row.pi_reset_reason == RESET_REASON_SENSOR_FAILURE
                assert pi_row.pi_integrator_action == INTEGRATOR_RESET

    def test_window_governed_frost_override_gates_pi_even_when_a_minimum_duration_block_masks_it(
        self, session: Session
    ) -> None:
        """Same class of finding as the stale-sensor test above, checked for the
        other three caller-computed gates named in the review
        (`window_governs`/`resume_delay_active`/`frost_effective`): unlike the
        sensor check before this fix, none of them ever read `decision.reason_code`
        -- they were already computed straight from `Situation`/context, so rule 5
        masking `decide()`'s own reason code cannot hide them. This is the one
        combination that actually exercises the masking risk end to end: an open
        window whose frost exception is engaged (`REASON_CODE_FROST_OVERRIDES_WINDOW`,
        rule 3's fallthrough) still runs rule 5 afterwards, which can return
        `REASON_CODE_BLOCKED_MINIMUM_DURATION` before rule 6 ever assigns the
        window-frost code. `frost_effective` (a plain operating-mode/off-mode
        check) has no such fallthrough to begin with -- rule 5 running first
        cannot mask it, and the existing 30-cycle
        `test_frost_protection_mode_resets_every_cycle_it_is_effective` above
        already asserts `RESET_REASON_FROST` on every one of those cycles,
        including the early ones where rule 5 would otherwise fire. A missing
        measurement (`REASON_CODE_NO_SOURCE`) cannot be masked by rule 5 at all --
        rule 1 returns before rule 5 (or anything else) ever runs.
        """
        zone = _pi_zone(session, "fenster-frost-block", measured_c=Decimal("15.0"))
        # Mixed zone, same reason as `test_window_open_resets_every_cycle_for_two_hours`
        # above: a plain switch-only zone is `on_off_actuators_only`, which exempts
        # it from rule 3 (and this test) entirely.
        _assign_actuator(
            session, zone, self_regulating=True, capability_code="thermostat", suffix="-heizkörper",
        )
        state = session.get(ZoneState, zone.id)
        assert state is not None
        state.window_open = True
        session.flush()

        now = NOW
        for i in range(5):  # t=0..240s, well inside the 300s hysteresis minimum
            row = _row_for(shadow_run.cycle(session, now), zone)
            assert row.effective_controller == "hysteresis"
            assert row.pi_reset_reason == RESET_REASON_WINDOW_OPEN
            assert row.pi_integrator_action == INTEGRATOR_RESET
            if i >= 1:
                # The actual masking risk: `decide()` itself answers
                # `gesperrt_mindestdauer` here (held_for_s < 300s), not the
                # window-frost code -- proving `pi_reset_reason` above does not
                # merely coincide with an unmasked `decide()` answer.
                assert row.outcome_code == control_loop.REASON_CODE_BLOCKED_MINIMUM_DURATION
            now += timedelta(seconds=60)

    def test_a_valve_protection_run_holds_pi_at_zero_for_its_whole_duration(
        self, session: Session
    ) -> None:
        zone = _pi_zone(session, "ventilschutz", measured_c=WARM_C)
        zone.valve_protection_enabled = True
        zone.valve_protection_interval_days = 1
        zone.valve_protection_duration_minutes = 10
        session.flush()

        now = NOW
        rows = []
        for _ in range(10):
            row = _row_for(shadow_run.cycle(session, now), zone)
            rows.append(row)
            now += timedelta(minutes=1)

        assert all(row.would_heat for row in rows)
        assert all(row.pi_reset_reason == RESET_REASON_VALVE_PROTECTION for row in rows)
        assert all(row.pi_integrator_action == INTEGRATOR_RESET for row in rows)

        state = session.get(ZoneState, zone.id)
        assert state is not None
        assert state.pi_integral == Decimal("0")

        # After the run: PI resumes without the old (zero) integral being treated
        # as anything but a fresh start -- and without the protection's on-state
        # being mistaken for regular heating history. `pi_reset_reason` itself
        # legitimately keeps naming the valve-protection run for a while longer --
        # it is "the last reset's reason", and nothing resets again on this cycle
        # (no setpoint-context change) -- so this checks the two things section 4
        # actually promises instead: PI is deciding again, from a fresh integral.
        row = _row_for(shadow_run.cycle(session, now), zone)
        assert row.effective_controller == "pi"
        assert row.pi_integral_before == Decimal("0")

    def test_the_pi_minimum_duration_holds_the_integrator_pi_still_governs(
        self, session: Session
    ) -> None:
        # Long PI minimums (the maximum the schema allows) make the modulator hold
        # its state for several regular cycles -- exactly the row of section 4's
        # table that is *not* a fallback to hysteresis: PI is still the effective
        # controller, its own minimum just blocks an early switch.
        # A small positive error -- 0 < u < 1 -- so the modulator actually has a
        # duty cycle to spread out (a duty of exactly 0 or 1 is absolute, section
        # 3, and never triggers a minimum-duration hold at all).
        zone = _pi_zone(
            session,
            "pi-mindestdauer",
            measured_c=Decimal("20.9"),
            pi_min_on=300,
            pi_min_off=300,
        )
        now = NOW
        saw_a_hold = False
        for _ in range(6):
            row = _row_for(shadow_run.cycle(session, now), zone)
            assert row.effective_controller == "pi"
            if row.pi_integrator_action == INTEGRATOR_HOLD:
                saw_a_hold = True
                assert row.pi_min_duration_decision == MODULATOR_REASON_HELD
                assert row.pi_integral_before == row.pi_integral_after
            now += timedelta(seconds=60)
        assert saw_a_hold

    def test_starting_and_ending_an_override_resets_the_integral_but_pi_keeps_deciding(
        self, session: Session
    ) -> None:
        zone = _pi_zone(session, "übersteuerung", measured_c=WARM_C)
        now = NOW
        shadow_run.cycle(session, now)  # establish some integral first

        now += timedelta(seconds=60)
        from tests.helpers import source
        from thermoctl.db.models.override import ZoneOverride

        override = ZoneOverride(
            zone_id=zone.id,
            temperature_c=Decimal("24.0"),
            starts_at=now,
            ends_at=now + timedelta(minutes=5),
            source_id=source(session).id,
        )
        session.add(override)
        session.flush()

        started = _row_for(shadow_run.cycle(session, now), zone)
        assert started.effective_controller == "pi"  # not a fallback -- PI keeps deciding
        assert started.pi_reset_reason == RESET_REASON_CONTEXT_CHANGE

        # Step in realistic ~60s cycles past the override's end -- a single large
        # jump would itself trip `pi_dt()`'s own time-gap guard (more than twice
        # the expected cycle) and mask the context-change reset this test means to
        # observe with an unrelated one.
        ended = started
        for _ in range(7):
            now += timedelta(seconds=60)
            ended = _row_for(shadow_run.cycle(session, now), zone)
        assert ended.effective_controller == "pi"
        assert ended.pi_reset_reason == RESET_REASON_CONTEXT_CHANGE

    def test_starting_and_ending_a_vacation_resets_the_integral_but_pi_keeps_deciding(
        self, session: Session
    ) -> None:
        """Same reasoning as the override case above: a vacation resolves to a fixed
        temperature with `mode_id=None` too (`domain.schedule._vacation_setpoint`),
        so without its own branch in `_pi_setpoint_context_key` this would trip the
        function's `assert` instead of resetting cleanly."""
        from thermoctl.domain.schedule import cancel_vacation, create_vacation

        zone = _pi_zone(session, "urlaub-pi", measured_c=WARM_C)
        now = NOW
        shadow_run.cycle(session, now)  # establish some integral first

        now += timedelta(seconds=60)
        vacation = create_vacation(
            session,
            start_date=now.date(),
            end_date=now.date() + timedelta(days=5),
            setback_temperature_c=Decimal("15.0"),
            timezone_name="Europe/Berlin",
            now=now,
        )
        assert vacation.starts_at <= now < vacation.ends_at

        started = _row_for(shadow_run.cycle(session, now), zone)
        assert started.effective_controller == "pi"  # not a fallback -- PI keeps deciding
        assert started.pi_reset_reason == RESET_REASON_CONTEXT_CHANGE

        now += timedelta(seconds=60)
        cancel_vacation(session, now=now)
        ended = _row_for(shadow_run.cycle(session, now), zone)
        assert ended.effective_controller == "pi"
        assert ended.pi_reset_reason == RESET_REASON_CONTEXT_CHANGE


class TestOnOffOnlyZoneIgnoresTheWindow:
    """The owner's second 2026-09-06 decision, in its PI interplay: PI's own
    eligibility (`pi_eligible()`) already requires at least one *ordinary*
    (non-self-regulating) switch actuator and excludes any self-regulating one --
    which is exactly `Situation.on_off_actuators_only`'s own condition. So every
    PI-eligible zone built from `_pi_zone()`'s default, single plain-switch
    actuator *is* an EIN/AUS-only zone: an open window no longer reaches rule 3 or
    4 for it at all, and `_pi_gate_reason`'s `window_governs` is therefore always
    `False` for it too -- PI keeps deciding, targeting the ordinary setpoint,
    exactly as if there were no window."""

    def test_pi_keeps_deciding_through_an_open_window(self, session: Session) -> None:
        zone = _pi_zone(session, "einaus-fenster-offen", measured_c=COLD_C)
        state = session.get(ZoneState, zone.id)
        assert state is not None
        state.window_open = True
        session.flush()

        row = _row_for(shadow_run.cycle(session, NOW), zone)
        assert row.outcome_code != REASON_CODE_WINDOW_OPEN
        assert row.effective_controller == "pi"
        assert row.pi_reset_reason != RESET_REASON_WINDOW_OPEN
        assert row.pi_candidate_would_heat is not None

    def test_pi_is_unaffected_by_the_resume_delay_too(self, session: Session) -> None:
        """Same device-history setup as
        `TestPrecedenceRulesBeatPi::test_the_window_resume_delay_also_resets_pi_not_just_the_open_window`
        (a window closed just 30s ago, with a 300s configured delay) -- there, on a
        mixed zone, this resets PI; here, on the default EIN/AUS-only zone, rule 4
        never applies at all, so PI is unaffected."""
        zone = _pi_zone(session, "einaus-nachlauf", measured_c=COLD_C)
        from thermoctl.db.models.device import Device as DeviceModel
        from thermoctl.db.models.measurement import Measurement

        contact_capability = capability(session, "contact")
        window_role = role(session, "window_contact")
        sensor = DeviceModel(
            integration_id=integration(session).id,
            external_id=f"{zone.name}-fenster",
            display_name=f"{zone.name}-fenster",
        )
        session.add(sensor)
        session.flush()
        session.add(
            ZoneDevice(zone_id=zone.id, device_id=sensor.id, device_role_id=window_role.id)
        )
        session.add(
            Measurement(
                device_id=sensor.id,
                capability_id=contact_capability.id,
                value_text="false",
                measured_at=NOW - timedelta(minutes=30),
                received_at=NOW - timedelta(minutes=30),
            )
        )
        session.add(
            Measurement(
                device_id=sensor.id,
                capability_id=contact_capability.id,
                value_text="true",  # closed again 30s ago
                measured_at=NOW - timedelta(seconds=30),
                received_at=NOW - timedelta(seconds=30),
            )
        )
        zone.window_resume_delay_seconds = 300
        session.flush()

        row = _row_for(shadow_run.cycle(session, NOW), zone)
        assert row.effective_controller == "pi"
        assert row.pi_reset_reason != RESET_REASON_WINDOW_OPEN


class TestDisablingNeutralizes:
    def test_turning_pi_off_neutralizes_the_stored_state_in_the_same_cycle(
        self, session: Session
    ) -> None:
        zone = _pi_zone(session, "abschalten", measured_c=WARM_C)
        now = NOW
        for _ in range(3):
            shadow_run.cycle(session, now)
            now += timedelta(seconds=60)

        state = session.get(ZoneState, zone.id)
        assert state is not None
        assert state.pi_integral != Decimal("0") or state.pi_last_control_armed is not None

        zone.pi_enabled = False
        session.flush()
        row = _row_for(shadow_run.cycle(session, now), zone)

        assert row.requested_controller == "hysteresis"
        assert row.pi_candidate_would_heat is None
        state = session.get(ZoneState, zone.id)
        assert state is not None
        assert (
            state.pi_integral,
            state.pi_last_evaluated_at,
            state.pi_setpoint_context_key,
            state.pi_last_control_armed,
            state.pi_window_started_at,
            state.pi_window_duty,
            state.pi_time_balance_seconds,
            state.pi_last_switch_at,
            state.pi_last_switch_heating,
            state.pi_awaiting_boundary_until,
            state.pi_last_reset_reason,
        ) == (Decimal("0"), None, None, None, None, None, Decimal("0"), None, None, None, None)

        # Re-enabling starts clean, not from the old run's values -- proven by the
        # safe-start wait firing again exactly as it did on a first-ever cycle.
        zone.pi_enabled = True
        session.flush()
        now += timedelta(seconds=60)
        again = _row_for(shadow_run.cycle(session, now), zone)
        assert again.controller_fallback_reason == RESET_REASON_INVALID_STATE


class TestHysteresisMinimumDurationDoesNotGovernPi:
    """The project owner's report (2026-09-27): "Bei der PI-Regelung greift aktuell
    noch die 300s Mindest-Ein- und -Ausschaltdauer, nicht die PI-exklusiven Werte."

    `decide()`'s rule 5 always answers the pure-hysteresis question, against
    `min_on_seconds`/`min_off_seconds` (300s here) -- deliberately controller-agnostic
    (see its own module docstring and `_process_zone`'s comment above `decide()`).
    `_pi_gate_reason()` already treats `REASON_CODE_BLOCKED_MINIMUM_DURATION` as one
    of the codes that must *not* block PI (`TestPiGateReasonClassifiesEveryReasonCode`
    above) -- so `would_heat` itself already follows PI's own, shorter minimums
    (`pi_min_on_seconds`/`pi_min_off_seconds`, 60s here), confirmed below by a switch
    at 90s, well inside the 300s hysteresis minimum.

    The actual defect was one level up: `ShadowDecision.outcome_code`/`.reason` kept
    reporting `decide()`'s own, inapplicable answer -- "Mindestdauer 300s ... bleibt
    unverändert" -- even on the very cycle `would_heat` changed. That is exactly what
    the owner's report describes: reading the shadow log gives the impression the
    300s rule still governs, even though the effective decision does not come from
    it. Fixed in `services/shadow_run.py::_process_zone`: once PI is the effective
    controller, the persisted `outcome_code`/`reason` are rebuilt entirely from PI's
    own outcome instead of decide()'s hysteresis-based one. Before the fix, this
    class's tests failed at the marked assertions (`outcome_code` was
    `gesperrt_mindestdauer` and `reason` contained "bleibt unverändert" while
    `would_heat` had just flipped).
    """

    def test_a_pi_switch_inside_the_hysteresis_minimum_reports_pi_as_the_reason(
        self, session: Session
    ) -> None:
        # A small, non-saturating error (0 < u < 1) so the modulator has a duty
        # cycle to spread across the window instead of the absolute u=0/u=1 case
        # (section 3), which would switch regardless of any minimum duration and
        # prove nothing about which minimum actually governed here.
        zone = _pi_zone(
            session,
            "pi-ueberstimmt-hysterese",
            measured_c=Decimal("20.9"),
            pi_min_on=60,
            pi_min_off=60,
        )
        now = NOW
        rows: list[ShadowDecision] = []
        for _ in range(7):  # 0, 30, ..., 180s
            rows.append(_row_for(shadow_run.cycle(session, now), zone))
            now += timedelta(seconds=30)

        by_offset = {i * 30: row for i, row in enumerate(rows)}

        # The switch-on at 90s: well inside the 300s hysteresis minimum (the state
        # had held "Aus" for only 90s), but past PI's own 60s minimum -- `would_heat`
        # already proves PI's own value governs the actual outcome.
        switched_on = by_offset[90]
        assert switched_on.would_heat is True
        assert by_offset[60].would_heat is False  # the state it switched away from

        # The bug: `outcome_code` used to stay `gesperrt_mindestdauer` (decide()'s
        # own, inapplicable rule-5 answer) on exactly this cycle, and `reason` used
        # to say the heating request "bleibt unverändert" despite the flip above.
        assert switched_on.outcome_code == REASON_CODE_HEATING  # was: gesperrt_mindestdauer
        assert "unverändert" not in switched_on.reason
        assert switched_on.effective_controller == "pi"
        # The corrected reason must name PI, not the hysteresis minimum duration it
        # replaced -- no dangling "300s" from decide()'s own sentence.
        assert "PI-Regelung" in switched_on.reason
        assert "300s" not in switched_on.reason

        # The switch-off at 180s, symmetric case (heating -> off, again inside the
        # 300s hysteresis minimum: the "Heizen" state had held for only 90s).
        switched_off = by_offset[180]
        assert switched_off.would_heat is False
        assert by_offset[150].would_heat is True
        assert switched_off.outcome_code == REASON_CODE_OFF  # was: gesperrt_mindestdauer
        assert "unverändert" not in switched_off.reason
        assert "300s" not in switched_off.reason

    def test_an_unchanged_pi_hold_still_names_pi_not_the_hysteresis_minimum(
        self, session: Session
    ) -> None:
        """Even when PI's own candidate happens to *agree* with decide()'s blocked
        answer (both say "stay off"), the reason must name PI's own, applicable
        minimum -- not the 300s hysteresis one that never actually governed."""
        zone = _pi_zone(
            session,
            "pi-haelt-selbst",
            measured_c=Decimal("20.9"),
            pi_min_on=60,
            pi_min_off=60,
        )
        now = NOW
        shadow_run.cycle(session, now)  # cycle 0: establishes the initial state
        now += timedelta(seconds=30)
        held = _row_for(shadow_run.cycle(session, now), zone)  # cycle 1: 30s in

        assert held.would_heat is False
        assert held.outcome_code == REASON_CODE_OFF  # was: gesperrt_mindestdauer
        assert held.effective_controller == "pi"
        assert held.pi_min_duration_decision == MODULATOR_REASON_HELD
        assert "300s" not in held.reason
        assert "Tastgrad" in held.reason  # PI's own reasoning, not decide()'s

    def test_a_hysteresis_only_zone_is_unaffected(self, session: Session) -> None:
        """Regression guard: a zone with PI disabled (or ineligible) must keep
        reporting `decide()`'s own `gesperrt_mindestdauer` verbatim -- the fix only
        touches the cycle where PI actually is the effective controller."""
        zone = _pi_zone(
            session, "nur-hysterese", measured_c=Decimal("20.9"), pi_enabled=False
        )
        now = NOW
        shadow_run.cycle(session, now)
        now += timedelta(seconds=30)
        row = _row_for(shadow_run.cycle(session, now), zone)

        assert row.requested_controller == "hysteresis"
        assert row.effective_controller == "hysteresis"
        # Deterministic, not incidental: `held_for_s` is 30s at this second
        # cycle (< the 300s hysteresis minimum), so rule 5 in `decide()` always
        # fires here regardless of what rule 6 would otherwise have decided --
        # unconditional per the task's review (a conditional assertion here
        # would silently stop proving anything the moment that stopped holding).
        assert row.outcome_code == "gesperrt_mindestdauer"
        assert "Mindestdauer 300s" in row.reason
        assert "unverändert" in row.reason


class TestNonBlockedReasonCodesKeepDecideOwnReasoning:
    """Mutation boundary for `pi_overrides_hysteresis_block`'s first term
    (`decision.reason_code == REASON_CODE_BLOCKED_MINIMUM_DURATION`,
    `services/shadow_run.py`): a `<=`/`>=` in place of `==` survived a full
    cosmic-ray run against this file (791 mutants, 462 killed / 329 survived / 0
    incompetent) because no existing test exercised a PI-governed cycle whose
    `decide()` reason code is *not* the minimum-duration block but still sorts on
    one side of it alphabetically.

    `REASON_CODE_OFF` ("aus") sorts *before* "gesperrt_mindestdauer";
    `REASON_CODE_HEATING` ("heizen") sorts *after* it. Together they bracket the
    exact-match code from both directions, so one test per side kills both a `<=`
    and a `>=` mutant of that comparison -- verified by hand (see the class this
    docstring belongs to's tests, applied as single mutants and reverted; not
    re-run as a fresh full cosmic-ray pass per the task's instruction not to start
    a new complete run).
    """

    def test_a_plain_pi_governed_heat_keeps_decide_s_own_reason(
        self, session: Session
    ) -> None:
        # Fresh zone, first cycle: no minimum-duration question at all yet
        # (`held_for_s` is `None`). `decide()` itself answers `REASON_CODE_HEATING`
        # ("heizen", sorts *after* "gesperrt_mindestdauer") -- kills a `>=` mutant
        # of the guard, which would wrongly divert this cycle into the
        # PI-overrides-hysteresis-block branch and rewrite the reason/outcome code
        # (to `REASON_CODE_OFF` here, since the modulator's own tie-break at a
        # brand-new window boundary -- remainder exactly 0 -- favours "aus"
        # regardless of duty; `would_heat` itself is therefore not asserted here,
        # only `outcome_code`/`reason`, which the mutant corrupts either way).
        zone = _pi_zone(session, "pi-normal-heizen", measured_c=COLD_C)
        row = _row_for(shadow_run.cycle(session, NOW), zone)

        assert row.effective_controller == "pi"
        assert row.outcome_code == control_loop.REASON_CODE_HEATING  # not rewritten
        # decide()'s own sentence ("Ist ... °C unter Soll ...") must still be the
        # base, with PI's own reasoning appended -- not replaced outright.
        assert "Ist" in row.reason
        assert "PI-Regelung" in row.reason

    def test_a_plain_pi_governed_switch_off_keeps_decide_s_own_reason(
        self, session: Session
    ) -> None:
        # `REASON_CODE_OFF` ("aus") sorts *before* "gesperrt_mindestdauer" -- kills
        # a `<=` mutant of the same guard. Needs a genuine rule-6 "switch off" (i.e.
        # `regular_heating_now` must actually have been `True`), not a
        # minimum-duration block, so the hysteresis minimums are set to 1s here
        # (irrelevant to the boundary itself -- with the ordinary 300s default,
        # the warm-up cycle below would be `gesperrt_mindestdauer` and prove
        # nothing about this particular boundary; that case is already covered by
        # `TestHysteresisMinimumDurationDoesNotGovernPi` above).
        settings = create_settings(session, min_ein=1)
        settings.default_min_off_seconds = 1
        session.flush()
        zone = _pi_zone(
            session, "pi-normal-aus", measured_c=COLD_C, pi_min_on=60, pi_min_off=60
        )

        # The modulator's own tie-break favours "aus" on the very first cycle of a
        # fresh window (remainder starts at exactly 0, see the test above) --
        # several cold cycles are needed before `would_heat` actually turns `True`
        # and rule 6 has a genuine "Heizen" state to switch off from.
        now = NOW
        row = None
        for _ in range(6):
            row = _row_for(shadow_run.cycle(session, now), zone)
            now += timedelta(seconds=60)
            if row.would_heat:
                break
        assert row is not None
        assert row.would_heat is True  # otherwise the warm-up below proves nothing

        state = session.get(ZoneState, zone.id)
        assert state is not None
        state.temperature_c = Decimal("23.0")  # well above setpoint + hysteresis
        session.flush()
        row = _row_for(shadow_run.cycle(session, now), zone)

        assert row.effective_controller == "pi"
        assert row.outcome_code == control_loop.REASON_CODE_OFF  # not rewritten
        assert "Ist" in row.reason
        assert "PI-Regelung" in row.reason


class TestHysteresisPhaseStillHolding:
    """Direct unit tests for `_hysteresis_phase_still_holding()`, the pure helper
    the general invariant (`TestGeneralHysteresisMinimumInvariant` below) is built
    on. Two things an end-to-end `_process_zone()` scenario cannot reliably pin
    down on its own:

    - A `min_on_seconds == min_off_seconds` situation (the common case in the
      rest of this file) cannot distinguish a `heating_now`/`not heating_now`
      swap in which of the two this function reads -- both give the same
      number either way. Exercised here with deliberately different values.
    - Which string, exactly, counts as "started by hysteresis" -- only the
      literal `"hysteresis"` must hold the phase; `"pi"`, `None`, and any other
      value must not.
    """

    def test_false_without_any_held_history(self) -> None:
        situation = _lage(held_for_s=None)
        assert not shadow_run._hysteresis_phase_still_holding(situation, "hysteresis")

    def test_false_once_the_minimum_has_elapsed(self) -> None:
        situation = _lage(
            heating_now=True, held_for_s=300, parameter=_parameter(min_on_seconds=300)
        )
        assert not shadow_run._hysteresis_phase_still_holding(situation, "hysteresis")

    def test_true_below_min_on_seconds_while_heating(self) -> None:
        situation = _lage(
            heating_now=True,
            held_for_s=10,
            parameter=_parameter(min_on_seconds=300, min_off_seconds=30),
        )
        assert shadow_run._hysteresis_phase_still_holding(situation, "hysteresis")

    def test_true_below_min_off_seconds_while_off(self) -> None:
        situation = _lage(
            heating_now=False,
            held_for_s=10,
            parameter=_parameter(min_on_seconds=300, min_off_seconds=30),
        )
        assert shadow_run._hysteresis_phase_still_holding(situation, "hysteresis")

    def test_false_when_the_off_minimum_has_elapsed_even_though_on_has_not(
        self,
    ) -> None:
        # Kills a `heating_now`/`not heating_now` swap: with distinct minimums,
        # a swap would read `min_on_seconds` (300, still not elapsed at 10s)
        # instead of `min_off_seconds` (30, elapsed at held_for_s=10? -- no,
        # chosen deliberately past 30s here so the swap's wrong answer (still
        # holding, from min_on) is observably different from the correct one
        # (elapsed, from min_off).
        situation = _lage(
            heating_now=False,
            held_for_s=31,
            parameter=_parameter(min_on_seconds=300, min_off_seconds=30),
        )
        assert not shadow_run._hysteresis_phase_still_holding(situation, "hysteresis")

    def test_false_for_a_phase_pi_itself_started(self) -> None:
        situation = _lage(
            heating_now=True, held_for_s=10, parameter=_parameter(min_on_seconds=300)
        )
        assert not shadow_run._hysteresis_phase_still_holding(situation, "pi")

    def test_false_when_who_started_the_phase_is_unknown(self) -> None:
        situation = _lage(
            heating_now=True, held_for_s=10, parameter=_parameter(min_on_seconds=300)
        )
        assert not shadow_run._hysteresis_phase_still_holding(situation, None)


class TestControllerTransitionsMidHold:
    """Two deliberate, conservative decisions about a controller change while a
    minimum-duration hold is already running (task instruction: decide and test
    both directions explicitly, "keine sofortige Umschaltung, die die
    Hysterese-Mindestdauer umgeht").

    Neither direction needed a code change beyond the reason/outcome-code fix
    above -- both already follow from mechanisms this file already tests in
    isolation; these two tests compose them explicitly for this bug's scenario.

    - **PI -> Hysterese** (PI becomes ineligible or is disabled mid-run): falling
      back does *not* reset how long the current physical state has held. Rule 5
      in `decide()` reads `Situation.held_for_s` from the *effective* `would_heat`
      history (`_previous_state()`), which already includes the time PI itself
      held that state -- so the hysteresis minimum, once it is the one governing
      again, is measured from when the state actually started, not from the
      moment of the fallback. A state PI just started 90s ago stays held for the
      *remaining* 210s of the 300s hysteresis minimum, not for a fresh 300s.
    - **Hysterese -> PI** (PI newly enabled or newly eligible): `_pi_outcome()`'s
      safe-start wait (`needs_safe_start`, tested in `TestSafeStart`) makes the
      zone decide by hysteresis alone until the next full 15-minute window
      boundary -- **and, since a security review of this fix found the boundary
      alone is not enough (2026-09-27), until the hysteresis minimum for the
      currently held state has *also* elapsed, whichever of the two ends later**
      (`_hysteresis_minimum_still_running()`). Without that extension, a state
      held for less than the hysteresis minimum at the moment PI takes over could
      switch immediately once the (unrelated) window boundary arrived, bypassing
      the remainder of a minimum duration that was still running -- exactly the
      counter-example the review gave: heating starts at 12:13, PI is enabled at
      12:14, the next boundary at 12:15 let PI switch off after only 120s instead
      of the 300s hysteresis minimum still running from 12:13. The project
      owner's conservative decision: PI's own, shorter minimums never apply
      mid-hold -- not even once the window boundary has passed -- until the
      *original* hysteresis deadline for the currently held state is reached.
    """

    def test_falling_back_to_hysteresis_keeps_the_accumulated_hold(
        self, session: Session
    ) -> None:
        """Cross-review correction (2026-09-27): the previous version of this test
        had the timing wrong in its own comments (it switches on at t=90s, not
        t=120s as claimed) and, worse, never actually distinguished "the original
        90s-old deadline keeps counting" from "the deadline silently restarted at
        the fallback moment" -- both produce `gesperrt_mindestdauer` on the very
        next cycle regardless, since *any* `held_for_s` below 300s blocks. Fixed
        by warming the room right at the fallback point (so rule 6 genuinely wants
        to switch off from then on) and running cycles all the way to where the
        two hypotheses disagree: the true deadline (300s after the state actually
        started at t=90s) is t=390s; a silently-restarted one (300s after the
        fallback at t=120s) would be t=420s. Observing the switch-off exactly at
        t=390s -- not still blocked, and not already switched off earlier -- is
        what actually proves the accumulated hold, not just its label.
        """
        zone = _pi_zone(
            session,
            "pi-faellt-zurueck",
            measured_c=Decimal("20.9"),
            pi_min_on=60,
            pi_min_off=60,
        )
        now = NOW
        for i in range(4):  # t=0, 30, 60, 90 -- switches on at t=90s
            row = _row_for(shadow_run.cycle(session, now), zone)
            if i < 3:
                assert row.would_heat is False
            now += timedelta(seconds=30)
        assert row.would_heat is True
        assert row.effective_controller == "pi"
        heating_started_at = now - timedelta(seconds=30)  # t=90s

        # PI becomes ineligible right here, at t=120s -- 30s into the "Heizen"
        # state, not 120s into it. Removing its only ordinary actuator is the
        # simplest way to force that without touching the measurement or the
        # clock. The room is warmed *in the same cycle* so rule 6 genuinely wants
        # to switch off from t=120s onward -- without this, every cycle up to the
        # deadline would trivially stay "Heizen" because rule 6 itself agrees,
        # proving nothing about which deadline governs.
        actuator_link = session.execute(
            select(ZoneDevice).where(ZoneDevice.zone_id == zone.id)
        ).scalar_one()
        session.delete(actuator_link)
        state = session.get(ZoneState, zone.id)
        assert state is not None
        state.temperature_c = Decimal("23.0")  # well above setpoint + hysteresis
        session.flush()

        true_deadline = heating_started_at + timedelta(seconds=300)  # t=390s
        restarted_deadline = now + timedelta(seconds=300)  # t=420s, if it reset
        assert true_deadline != restarted_deadline  # the two hypotheses disagree

        fallen_back = _row_for(shadow_run.cycle(session, now), zone)
        assert fallen_back.requested_controller == "pi"
        assert fallen_back.effective_controller == "hysteresis"
        assert fallen_back.controller_fallback_reason == PI_FALLBACK_INELIGIBLE
        assert fallen_back.outcome_code == "gesperrt_mindestdauer"
        assert "Mindestdauer 300s" in fallen_back.reason
        assert fallen_back.would_heat is True  # still blocked, 30s into the hold
        now += timedelta(seconds=30)

        # Run every cycle up to (but not including) the true deadline: rule 6
        # would switch off immediately (the room is warm) if not for the
        # minimum-duration block, so `would_heat` staying `True` throughout is
        # exactly the accumulated hold from t=90s continuing to apply.
        while now < true_deadline:
            row = _row_for(shadow_run.cycle(session, now), zone)
            assert row.would_heat is True, f"switched off early, at {now}"
            assert row.outcome_code == "gesperrt_mindestdauer"
            now += timedelta(seconds=30)

        assert now == true_deadline
        at_deadline = _row_for(shadow_run.cycle(session, now), zone)
        # The decisive assertion: switched off exactly at the *original*
        # deadline (t=390s) -- a silently-restarted one would still be blocked
        # here (30s short of t=420s), the exact bug this test was meant to catch.
        assert at_deadline.would_heat is False
        assert at_deadline.outcome_code == control_loop.REASON_CODE_OFF
        assert fallen_back.would_heat is True  # held, not switched off early

    def test_enabling_pi_mid_hold_waits_for_the_next_boundary_first(
        self, session: Session
    ) -> None:
        zone = _pi_zone(
            session,
            "hysterese-zu-pi",
            measured_c=Decimal("20.9"),
            pi_enabled=False,
            pi_min_on=60,
            pi_min_off=60,
        )
        now = NOW
        for _ in range(4):
            row = _row_for(shadow_run.cycle(session, now), zone)
            now += timedelta(seconds=30)

        # Enable PI mid-hold -- whatever state hysteresis is holding right now
        # continues exactly as hysteresis decided it, not by PI's shorter minimums.
        zone.pi_enabled = True
        session.flush()
        just_enabled = _row_for(shadow_run.cycle(session, now), zone)
        assert just_enabled.requested_controller == "pi"
        assert just_enabled.effective_controller == "hysteresis"
        # `RESET_REASON_INVALID_STATE`, not `RESET_REASON_ARMING`: a disabled zone's
        # PI state is fully neutralised every cycle (`_neutralize_pi_state()`,
        # `pi_last_control_armed` included), so re-enabling starts exactly as clean
        # as a first-ever PI cycle -- `TestDisablingNeutralizes` above proves this
        # for the disable step; this is its mirror on the enable step.
        assert just_enabled.controller_fallback_reason == RESET_REASON_INVALID_STATE
        assert just_enabled.would_heat == row.would_heat  # unchanged by the switch

    def test_a_phase_starting_after_pi_is_enabled_is_still_protected(
        self, session: Session
    ) -> None:
        """Bug (B) from the second security review, 2026-09-27: the *first*
        version of this fix computed the extended wait only once, at the moment
        `needs_safe_start` fires -- and found nothing held yet if PI was enabled
        before any phase had started. Reproduced with the review's own scenario,
        `NOW` (a Monday, exactly on a 15-minute UTC boundary) standing in for
        12:00: PI is enabled at "12:01", long before anything is held (the
        one-time wait it computed then ended at the very next boundary, "12:15",
        with nothing to extend). A phase then starts *under hysteresis* at
        "12:13" (PI is still waiting out its own safe-start at that point) and
        must be held until "12:18" (300s later) -- 120s short of that ("12:15")
        is exactly where the first fix's one-time computation would have let PI
        take over.

        The general invariant checked fresh every cycle (not stored anywhere)
        has no "moment it was computed" to be too early for -- it is
        automatically re-evaluated once the phase actually exists.
        """
        zone = _pi_zone(
            session, "spaettransfer", measured_c=Decimal("20.9"), pi_min_on=60, pi_min_off=60
        )
        # "12:01" -- enabling PI before anything is held at all (`held_for_s`
        # is `None`): the safe-start wait fires with nothing yet to protect.
        # The room starts inside the hysteresis band ("aus", nothing held) so
        # nothing heats yet -- the phase below must start freshly at "12:13",
        # not already be running since "12:01".
        now = NOW + timedelta(minutes=1)
        state = session.get(ZoneState, zone.id)
        assert state is not None
        state.pi_last_control_armed = None  # as if never armed before, like `_neutralize_pi_state`
        session.flush()
        enabled = _row_for(shadow_run.cycle(session, now), zone)
        assert enabled.would_heat is False
        assert enabled.effective_controller == "hysteresis"
        assert enabled.controller_fallback_reason == RESET_REASON_INVALID_STATE

        # "12:13" -- the safe-start wait (until "12:15") is still running; the
        # room turns cold right in this cycle, so the "Heizen" phase genuinely
        # starts here, under hysteresis (`effective_controller` for this very
        # row is still "hysteresis", per the safe-start wait above).
        heating_started_at = NOW + timedelta(minutes=13)
        state.temperature_c = COLD_C
        session.flush()
        started = _row_for(shadow_run.cycle(session, heating_started_at), zone)
        assert started.would_heat is True
        assert started.effective_controller == "hysteresis"

        hysteresis_deadline = heating_started_at + timedelta(seconds=300)  # "12:18"
        boundary = NOW + timedelta(minutes=15)  # "12:15" -- the bug's premature moment
        assert hysteresis_deadline > boundary

        # Every cycle from "12:14" up to, but not including, "12:18": still
        # hysteresis, including every cycle *after* "12:15" -- the boundary the
        # first fix's one-time computation would have stopped at. "12:14" itself
        # is still inside the arming wait proper (until "12:15"); the decisive
        # cycles are "12:15", "12:16", "12:17", where the *new* invariant is the
        # only thing left holding PI back.
        now = heating_started_at + timedelta(minutes=1)
        while now < hysteresis_deadline:
            row = _row_for(shadow_run.cycle(session, now), zone)
            assert row.effective_controller == "hysteresis", f"took over PI early, at {now}"
            if now >= boundary:
                assert row.pi_reset_reason == PI_FALLBACK_HYSTERESIS_MINIMUM
            now += timedelta(minutes=1)

        # "12:18": the hysteresis minimum for the phase held since "12:13" has
        # now genuinely elapsed -- PI computes its first real candidate.
        assert now == hysteresis_deadline
        at_deadline = _row_for(shadow_run.cycle(session, now), zone)
        assert at_deadline.effective_controller == "pi"
        assert at_deadline.pi_min_duration_decision is not None

    def test_a_gate_reset_during_the_safe_start_wait_does_not_erase_it(
        self, session: Session
    ) -> None:
        """The second half of bug (A): every gate reset (window/frost/sensor/
        valve) calls `reset_pi_state()` without `await_next_boundary`, so it
        used to write `pi_awaiting_boundary_until=None` -- silently erasing an
        already-pending safe-start wait, not just leaving it alone. Reproduced
        by opening a window right in the middle of the wait; without the fix in
        `_write_reset_state()`, `pi_cycle()` would then find no wait pending at
        all once the window closes again and let PI decide immediately.
        """
        zone = _pi_zone(
            session, "wartefrist-durch-gate", measured_c=Decimal("20.9"),
        )
        # Mixed actuator, same reason as the window tests above: a plain
        # switch-only zone is `on_off_actuators_only`, exempt from rule 3
        # entirely -- the window gate would never fire and this test would
        # prove nothing about it.
        _assign_actuator(
            session, zone, self_regulating=True, capability_code="thermostat",
            suffix="-heizkoerper",
        )
        state = session.get(ZoneState, zone.id)
        assert state is not None
        state.pi_last_control_armed = None
        session.flush()

        now = NOW  # exactly on a boundary -- the wait ends at `NOW` + 15 minutes
        enabled = _row_for(shadow_run.cycle(session, now), zone)
        assert enabled.effective_controller == "hysteresis"
        wait_until = state.pi_awaiting_boundary_until
        assert wait_until == NOW + timedelta(minutes=15)

        # A window opens and closes again a minute later, well before the wait
        # would end on its own.
        now += timedelta(minutes=1)
        state.window_open = True
        session.flush()
        opened = _row_for(shadow_run.cycle(session, now), zone)
        assert opened.pi_reset_reason == RESET_REASON_WINDOW_OPEN

        now += timedelta(minutes=1)
        state.window_open = False
        session.flush()
        closed = _row_for(shadow_run.cycle(session, now), zone)
        assert closed.effective_controller == "hysteresis"

        # The decisive assertion: the *original* safe-start wait is still
        # exactly what it was -- not erased, not shortened, and not extended
        # either (the window gate does not know about it and must not touch it).
        assert state.pi_awaiting_boundary_until == wait_until

    def test_a_pre_upgrade_wait_is_extended_not_discarded_by_a_later_arming(
        self, session: Session
    ) -> None:
        """Review finding, 2026-09-27: the `else` branch in `_write_reset_state()`
        is reachable after all -- not through two calls of the *current* code
        (whose boundary-only formula can never produce a strictly later
        `new_wait` for an existing, still-pending one -- either the two land in
        the same 15-minute window, giving an equal value, or the first has
        already elapsed by the second call, landing in the first branch
        instead), but when an *earlier* version of this fix left a wait behind
        in a different format. Reproduced by writing that leftover state
        directly into `ZoneState`, the way a predecessor version actually
        would have -- not by running old code, which no longer exists in this
        checkout: heating started at "12:13", PI was enabled at "12:14" under
        the version of this fix that still extended the wait to the hysteresis
        deadline itself (300s after "12:13", so "12:18") -- superseded by the
        general invariant, but a zone upgraded mid-wait still has exactly that
        value sitting in the column. Arming the installation for real at
        "12:16", before "12:18" elapses, fires `needs_safe_start` a second
        time (`RESET_REASON_ARMING`) and computes a fresh boundary-only wait of
        "12:30" -- later than the still-pending "12:18", so it must win here
        instead of being discarded in favour of the earlier, now-stale value.
        """
        zone = _pi_zone(session, "upgrade-wartefrist", measured_c=COLD_C)
        state = session.get(ZoneState, zone.id)
        assert state is not None

        # Leftover state exactly as the superseded version of this fix would
        # have written it at "12:14": PI newly enabled while the installation
        # itself was still in dry run (`RESET_REASON_ARMING`, `pi_last_control_
        # armed=False`), the wait already extended past the next boundary
        # ("12:15") to the hysteresis deadline itself ("12:18").
        state.pi_integral = Decimal("0")
        state.pi_last_evaluated_at = NOW + timedelta(minutes=14)
        state.pi_setpoint_context_key = None
        state.pi_window_started_at = None
        state.pi_window_duty = None
        state.pi_time_balance_seconds = Decimal("0")
        state.pi_last_switch_at = None
        state.pi_last_switch_heating = None
        state.pi_awaiting_boundary_until = NOW + timedelta(minutes=18)
        state.pi_last_reset_reason = RESET_REASON_ARMING
        state.pi_last_control_armed = False
        session.flush()

        # "12:16" -- the installation is armed for real, firing
        # `needs_safe_start` a second time (`previous_armed=False`,
        # `armed=True`).
        now = NOW + timedelta(minutes=16)
        settings = session.get(Setting, 1)
        assert settings is not None
        settings.control_armed = True
        session.flush()

        row = _row_for(shadow_run.cycle(session, now), zone)
        assert row.effective_controller == "hysteresis"
        assert row.controller_fallback_reason == RESET_REASON_ARMING

        # The decisive assertion: the fresh boundary ("12:30") wins, extending
        # the still-pending leftover wait ("12:18") rather than being discarded.
        assert state.pi_awaiting_boundary_until == NOW + timedelta(minutes=30)
        assert state.pi_awaiting_boundary_until != NOW + timedelta(minutes=18)


class TestGeneralInvariantSurvivesEveryNamedGate:
    """Bug (A) from the second security review, 2026-09-27, one variant per gate
    it named (Sensor, Fenster, Aus-Modus/Frost, Wiederanlauf): a phase that
    started under hysteresis because *any* of these gates was active at the
    time must still be held for the full hysteresis minimum once the gate
    clears again -- not just for whatever shorter window the gate itself
    covers. Before the general invariant, this depended on `_pi_gate_reason()`
    (and `needs_safe_start`) individually, and a temporary gate could let PI
    resume with its own, shorter minimums the moment it cleared, regardless of
    how long the phase itself had actually been held. The general invariant
    (`_hysteresis_phase_still_holding()`, checked fresh every cycle from
    `phase_started_by`) needs no per-gate awareness at all -- these tests prove
    that once, per gate, rather than assume it from the mechanism's generality.

    "Aus-Modus" and "Frost" share one test: both set `frost_effective` in
    `_pi_outcome`, the exact same boolean and the exact same gate branch --
    `zone.operating_mode.code == "off"` and `setpoint.mode_id ==
    settings.frost_protection_mode_id` are the two sides of one `or`, with no
    separate code path for either side to diverge in.
    """

    def test_a_temporary_sensor_failure_does_not_let_pi_resume_early(
        self, session: Session
    ) -> None:
        """Soll 21, Ist 18 (`COLD_C`) -- well above the 16.0°C frost default,
        so the sensor-failure fallback (rule 1) itself answers "Aus". Sensor
        veraltet ab t=0 (the phase's own first row), gültig wieder ab t=30.
        Before this fix: `_pi_gate_reason()` no longer saw the sensor failure
        at t=30 (already fixed by the *first* review), but nothing then stopped
        PI from applying its own 60s minimum to a phase hysteresis had started
        -- this is the general invariant's own contribution on top of that.
        """
        zone = _pi_zone(
            session, "sensor-temporaer", measured_c=COLD_C, pi_min_on=60, pi_min_off=60
        )
        state = session.get(ZoneState, zone.id)
        assert state is not None
        state.sensor_status_id = sensor_status_of(session, "veraltet").id
        session.flush()

        now = NOW
        row = _row_for(shadow_run.cycle(session, now), zone)
        assert row.would_heat is False
        assert row.effective_controller == "hysteresis"

        now += timedelta(seconds=30)
        state.sensor_status_id = sensor_status_of(session, "ok").id
        session.flush()

        while now < NOW + timedelta(seconds=300):
            row = _row_for(shadow_run.cycle(session, now), zone)
            assert row.would_heat is False, f"PI resumed early, at {now}"
            assert row.effective_controller == "hysteresis"
            assert row.pi_reset_reason == PI_FALLBACK_HYSTERESIS_MINIMUM
            now += timedelta(seconds=30)

        at_deadline = _row_for(shadow_run.cycle(session, now), zone)
        assert at_deadline.effective_controller == "pi"

    def test_a_temporarily_open_window_does_not_let_pi_resume_early(
        self, session: Session
    ) -> None:
        zone = _pi_zone(
            session, "fenster-temporaer", measured_c=COLD_C, pi_min_on=60, pi_min_off=60
        )
        _assign_actuator(
            session, zone, self_regulating=True, capability_code="thermostat",
            suffix="-heizkoerper",
        )
        zone.window_resume_delay_seconds = 0  # isolate the window gate from rule 4
        state = session.get(ZoneState, zone.id)
        assert state is not None
        state.window_open = True
        session.flush()

        now = NOW
        row = _row_for(shadow_run.cycle(session, now), zone)
        assert row.would_heat is False
        assert row.effective_controller == "hysteresis"

        now += timedelta(seconds=30)
        state.window_open = False
        session.flush()

        while now < NOW + timedelta(seconds=300):
            row = _row_for(shadow_run.cycle(session, now), zone)
            assert row.would_heat is False, f"PI resumed early, at {now}"
            assert row.effective_controller == "hysteresis"
            assert row.pi_reset_reason == PI_FALLBACK_HYSTERESIS_MINIMUM
            now += timedelta(seconds=30)

        at_deadline = _row_for(shadow_run.cycle(session, now), zone)
        assert at_deadline.effective_controller == "pi"

    def test_ending_off_mode_or_frost_does_not_let_pi_resume_early(
        self, session: Session
    ) -> None:
        zone = _pi_zone(
            session, "aus-modus-temporaer", measured_c=COLD_C, pi_min_on=60, pi_min_off=60
        )
        zone.operating_mode = operating_mode(session, "off")
        session.flush()

        now = NOW
        row = _row_for(shadow_run.cycle(session, now), zone)
        assert row.would_heat is False
        assert row.effective_controller == "hysteresis"

        now += timedelta(seconds=30)
        zone.operating_mode = operating_mode(session, "auto")
        session.flush()

        while now < NOW + timedelta(seconds=300):
            row = _row_for(shadow_run.cycle(session, now), zone)
            assert row.would_heat is False, f"PI resumed early, at {now}"
            assert row.effective_controller == "hysteresis"
            assert row.pi_reset_reason == PI_FALLBACK_HYSTERESIS_MINIMUM
            now += timedelta(seconds=30)

        at_deadline = _row_for(shadow_run.cycle(session, now), zone)
        assert at_deadline.effective_controller == "pi"

    def test_the_window_resume_delay_elapsing_does_not_let_pi_resume_early(
        self, session: Session
    ) -> None:
        """`window_resume_delay_seconds=60` -- deliberately shorter than the
        300s hysteresis minimum -- so the resume delay itself clears at t=60,
        well before the phase (held since t=0) may switch. Device history
        establishes the window as having closed exactly at t=0."""
        zone = _pi_zone(
            session, "wiederanlauf-temporaer", measured_c=COLD_C, pi_min_on=60, pi_min_off=60
        )
        _assign_actuator(
            session, zone, self_regulating=True, capability_code="thermostat",
            suffix="-heizkoerper",
        )
        zone.window_resume_delay_seconds = 60
        session.flush()

        contact_capability = capability(session, "contact")
        window_role = role(session, "window_contact")
        sensor = Device(
            integration_id=integration(session).id,
            external_id=f"{zone.name}-fenster",
            display_name=f"{zone.name}-fenster",
        )
        session.add(sensor)
        session.flush()
        session.add(ZoneDevice(zone_id=zone.id, device_id=sensor.id, device_role_id=window_role.id))
        session.add(Measurement(
            device_id=sensor.id, capability_id=contact_capability.id, value_text="false",
            measured_at=NOW - timedelta(minutes=30), received_at=NOW - timedelta(minutes=30),
        ))
        session.add(Measurement(
            device_id=sensor.id, capability_id=contact_capability.id, value_text="true",
            measured_at=NOW, received_at=NOW,  # closed exactly at t=0
        ))
        state = session.get(ZoneState, zone.id)
        assert state is not None
        # `window_open` stays `False` (set by `_pi_zone`) -- `_window_situation`
        # only walks the device history when it is exactly `False`.
        session.flush()

        now = NOW
        row = _row_for(shadow_run.cycle(session, now), zone)
        assert row.would_heat is False
        assert row.effective_controller == "hysteresis"

        now += timedelta(seconds=60)  # the 60s resume delay has now elapsed

        while now < NOW + timedelta(seconds=300):
            row = _row_for(shadow_run.cycle(session, now), zone)
            assert row.would_heat is False, f"PI resumed early, at {now}"
            assert row.effective_controller == "hysteresis"
            assert row.pi_reset_reason == PI_FALLBACK_HYSTERESIS_MINIMUM
            now += timedelta(seconds=30)

        at_deadline = _row_for(shadow_run.cycle(session, now), zone)
        assert at_deadline.effective_controller == "pi"


class TestValveProtectionMarkerClearsWhenPiOverridesTheBlock:
    """Second affected spot named in the task: `_apply_decision_to_state()` only
    clears a stale `valve_protection_started_at` marker when the *effective*
    decision's `reason_code` is not `REASON_CODE_BLOCKED_MINIMUM_DURATION` (see its
    own docstring, and the fix from 2026-09-02 it refers to). Before this fix,
    `effective_decision.reason_code` stayed `gesperrt_mindestdauer` even when PI's
    own candidate had just switched heating on -- so the marker was never cleared,
    and the next cycle could misread a genuine PI-driven heat as a still-running
    protection cycle. The fix (rebuilding `reason_code` from PI's own outcome)
    clears it correctly."""

    def test_a_pi_driven_switch_on_clears_a_stale_protection_marker(
        self, session: Session
    ) -> None:
        zone = _pi_zone(
            session,
            "ventilschutz-pi",
            measured_c=Decimal("20.9"),
            pi_min_on=60,
            pi_min_off=60,
        )
        state = session.get(ZoneState, zone.id)
        assert state is not None
        # Simulate a stale marker left over from a valve-protection run that ended
        # -- the exact situation `_apply_decision_to_state()`'s docstring describes.
        state.valve_protection_started_at = NOW - timedelta(minutes=1)
        session.flush()

        now = NOW
        row = None
        for _ in range(4):  # switches on at 90s, see the first test in this file
            row = _row_for(shadow_run.cycle(session, now), zone)
            now += timedelta(seconds=30)
        assert row is not None
        assert row.would_heat is True
        assert row.outcome_code == REASON_CODE_HEATING

        state = session.get(ZoneState, zone.id)
        assert state is not None
        assert state.valve_protection_started_at is None


class TestPiReasonText:
    """Wortlaut des PI-Entscheidungsgrunds: Regelabweichung statt "Fehler",
    Tastgrad als gerundeter Prozentwert mit Dezimalkomma (Befund 2026-10-04: der
    Grund zeigte ``Fehler 0.10K, Tastgrad 0.03649823433564814814814814815``)."""

    def test_wording_and_rounding(self) -> None:
        text = shadow_run._pi_reason_text(
            Decimal("0.10"), Decimal("0.03649823433564814814814814815"), "regulaer", True
        )
        assert text == "PI-Regelung: Abweichung 0,10 K, Tastgrad 3,6 %, regulaer -> Heizen."
        assert "Fehler" not in text

    def test_zero_percent(self) -> None:
        text = shadow_run._pi_reason_text(Decimal("-1.5"), Decimal("0"), "regulaer", False)
        assert text == "PI-Regelung: Abweichung -1,50 K, Tastgrad 0,0 %, regulaer -> Aus."

    def test_hundred_percent(self) -> None:
        text = shadow_run._pi_reason_text(Decimal("4"), Decimal("1"), "regulaer", True)
        assert "Tastgrad 100,0 %" in text

    def test_very_small_value_rounds_to_zero_without_scientific_notation(self) -> None:
        text = shadow_run._pi_reason_text(
            Decimal("-0.0001"), Decimal("0.00000001"), "regulaer", False
        )
        assert text == "PI-Regelung: Abweichung 0,00 K, Tastgrad 0,0 %, regulaer -> Aus."

    def test_the_persisted_reason_has_no_raw_decimal(self, session: Session) -> None:
        zone = _pi_zone(session, "pi-grund-wortlaut", measured_c=Decimal("20.9"))
        row = _row_for(shadow_run.cycle(session, NOW), zone)
        assert row.effective_controller == "pi"
        assert "Abweichung" in row.reason and "Fehler" not in row.reason
        assert re.search(r"Tastgrad \d+,\d %", row.reason)
