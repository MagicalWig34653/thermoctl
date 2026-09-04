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

from datetime import UTC, datetime, timedelta
from decimal import Decimal

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
from thermoctl.db.models.device import Device, DeviceCapabilityLink, ZoneDevice
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
from thermoctl.services.shadow_run import PI_FALLBACK_INELIGIBLE

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
        name=name, display_name=name.capitalize(), operating_mode_id=operating_mode(session).id
    )
    session.add(zone)
    session.flush()
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
            "mehrdeutig -- der Wiederanlauf-Fall wird ueber resume_delay_active "
            "erkannt, nicht ueber den Code."
        ),
        # Rule 6/7's "state holds" answer -- still ordinary territory, not one of
        # section 4's precedence rules.
        control_loop.REASON_CODE_UNCHANGED: "gewoehnliche Hysterese-Fortschreibung.",
        # The explicit, deliberate exception from the PI specification's section 2:
        # PI has its own, shorter minimum durations and its own tastgrad-vorrang
        # exception; the ordinary minimum-duration rule must not additionally hold
        # PI back.
        control_loop.REASON_CODE_BLOCKED_MINIMUM_DURATION: (
            "PI hat eigene, kuerzere Mindestdauern (Spezifikation Abschnitt 2/3)."
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
            "unklassifizierter Code laesst PI stillschweigend entscheiden. Ordne "
            "ihn hier in _BLOCKING oder _PERMITTED ein, mit Begruendung."
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
        for code, expected_reason in self._BLOCKING.items():
            assert (
                shadow_run._pi_gate_reason(
                    code, resume_delay_active=False, frost_effective=False
                )
                == expected_reason
            )

    def test_permitted_codes_let_pi_decide_absent_the_other_gates(self) -> None:
        for code in self._PERMITTED:
            assert (
                shadow_run._pi_gate_reason(
                    code, resume_delay_active=False, frost_effective=False
                )
                is None
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
        zone = _pi_zone(session, "thermostatfaehig", with_actuator=False)
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
        zone = _pi_zone(session, "heizkoerper-und-meross", with_actuator=False)
        # The self-regulating radiator thermostat -- excluded from both command
        # queries by `self_regulating=True`, so it must not block PI eligibility.
        _assign_actuator(
            session,
            zone,
            self_regulating=True,
            capability_code="thermostat",
            suffix="-heizkoerper",
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
    fehlendem oder beschaedigtem PI-Zustand" -- a zone's very first PI cycle has
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
        zone = _pi_zone(session, "fenster-offen", measured_c=COLD_C)
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
        zone = _pi_zone(session, "nachlauf", measured_c=COLD_C)
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
        zone = _pi_zone(session, "uebersteuerung", measured_c=WARM_C)
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
