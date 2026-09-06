"""The control decision: a situation becomes a decision, together with its reasoning.

Pure in the sense of CLAUDE.md and section 3 of the specification: no database, no
network, no clock. Everything the decision needs sits in `Lage`. That is exactly why it
is exhaustively testable (`tests/test_regelung.py`), and exactly why sub-project 4 can
arm it unchanged.

This module only decides; `services/publishing.py` records the result and, once both
control latches are open, sends it to the wired actuator.
"""

from dataclasses import dataclass
from decimal import Decimal

from thermoctl.domain.zone_settings import ControlParameters

# These codes correspond literally to the shadow_decision.outcome_code column from
# section 4 of the specification. Anyone needing a new code here must add it there first.
REASON_CODE_HEATING = "heizen"
REASON_CODE_OFF = "aus"
REASON_CODE_UNCHANGED = "unveraendert"
REASON_CODE_BLOCKED_MINIMUM_DURATION = "gesperrt_mindestdauer"
REASON_CODE_WINDOW_OPEN = "fenster_offen"
REASON_CODE_FROST_SENSOR_FAILURE = "frostschutz_sensorausfall"
REASON_CODE_NO_SOURCE = "keine_quelle"
REASON_CODE_VALVE_PROTECTION = "ventilschutz"
# Added 2026-09-06, by explicit decision of the project owner, alongside
# `Situation.on_off_actuators_only` -- not part of the original specification's
# section 4 table; see the build report of that date for the reasoning.
REASON_CODE_FROST_OVERRIDES_WINDOW = "frostschutz_trotz_fenster_offen"


@dataclass(frozen=True)
class Situation:
    """Everything the decision sees — unchanged from section 6 of the specification."""

    measured_c: Decimal | None
    setpoint_c: Decimal
    setpoint_reason: str
    # The plant's frost-protection setpoint. Kept separate from `soll_c` because it
    # still applies even when `soll_c` currently says something completely different:
    # on a failed sensor, control falls back to it instead of relying on a setpoint it
    # can no longer verify.
    frost_c: Decimal
    operating_mode: str  # auto | manual | off
    heating_now: bool
    held_for_s: int | None  # how long the current state has already held
    window_open: bool
    window_closed_for_s: int | None
    sensor_status: str  # ok | veraltet | keine_quelle
    parameter: ControlParameters
    override_active: bool = False
    valve_protection_due: bool = False
    valve_protection_active: bool = False
    # Added 2026-09-06 (owner's decision): true exactly for a zone whose actuators are
    # all plain on/off valves -- it has at least one actuator and none of them is
    # `Device.self_regulating` (`db.models.device.ZoneDevice.self_regulating`, see
    # `services/shadow_run.py::_process_zone` for how this is derived). Floor heating
    # on such valves is too sluggish for a window-triggered shutoff to make sense, so
    # rules 3 and 4 below skip it entirely -- window detection, recording and the cold
    # alarm are untouched, only `decide()`'s own shutoff is. A zone with no actuators
    # assigned, or with at least one self-regulating (thermostatic) valve, keeps the
    # existing, more cautious behaviour -- default `False` is exactly that.
    on_off_actuators_only: bool = False


@dataclass(frozen=True)
class Decision:
    heating: bool
    reason_code: str
    reason: str


def decide(situation: Situation) -> Decision:
    """The precedence from section 6 of the specification, rule by rule.

    The first matching rule wins and returns immediately — deliberately written as an
    early exit per rule instead of one large condition, so the precedence in the code
    looks the same as in the specification and nobody has to reconstruct it from an
    expression.
    """
    # Rule 1 — sensor failure trumps everything. Without a reliable current value,
    # "heat fully" is wrong (overheating with no feedback) — but "permanently off" is
    # just as wrong, and more dangerous: that is exactly how a pipe freezes in January.
    # So control falls back to the frost-protection setpoint and keeps controlling
    # against the last known value. The frost-protection value is low; with it the
    # plant can heat at most to an unproblematic level, but keeps the home above the
    # freezing point.
    #
    # If there is no value at all, only "off" remains — there is nothing to control
    # against.
    if situation.sensor_status == "keine_quelle" or situation.measured_c is None:
        return Decision(
            heating=False,
            reason_code=REASON_CODE_NO_SOURCE,
            reason=(
                "Keine verwertbare Messung für die Zone — es gibt nichts, woran zu regeln "
                "wäre. Die Heizanforderung bleibt aus."
            ),
        )

    # The sensor's calibration is a property of the measurement, not of the rule —
    # which is why it is applied to the current value here, and only here, before any
    # further rule.
    measured_c = situation.measured_c + situation.parameter.temperature_offset_k

    # Rule 2 — operating mode 'off' means frost protection, not powered down. The
    # caller already resolves the setpoint before us (`aufgeloester_sollwert`), which
    # returns the frost-protection value for 'off'. So `lage.soll_c` is already the
    # frost-protection setpoint in the 'off' case, and the "normal rule" is exactly
    # what follows from rule 3 onward — this function therefore does not need a
    # separate branch for the operating mode, only the origin of the setpoint
    # (`soll_grund`) carries through unchanged into the reasoning.

    # The effective setpoint. On a failed sensor this is the frost-protection value
    # (rule 1), otherwise the zone's resolved setpoint. From here on the same rule
    # runs in both cases — that is the core of "off means frost protection, not
    # powered down".
    sensor_failed = situation.sensor_status == "veraltet"
    setpoint_c = situation.frost_c if sensor_failed else situation.setpoint_c
    setpoint_reason = (
        f"Sensorwert veraltet — Frostschutz {situation.frost_c} °C statt {situation.setpoint_c} °C"
        if sensor_failed
        else situation.setpoint_reason
    )

    # `h` (rule 6's hysteresis band) is needed already here, by rule 3's frost
    # exception below — moved up from its original position directly above rule 6.
    h = situation.parameter.hysteresis_k

    # An EIN/AUS-zone (`on_off_actuators_only`, see the field's docstring) skips both
    # rule 3 and rule 4 below entirely, unconditionally — the owner's decision this
    # zone's actuators are too sluggish for a window-triggered shutoff to make sense
    # (floor heating on plain on/off valves). Window detection, recording and the
    # later cold alarm are untouched; only `decide()`'s own shutoff is skipped. The
    # note below makes that visible in every reason this cycle falls through to,
    # exactly when it actually applies (Grundsatz 5 — nothing about the decision may
    # go unexplained).
    # Kept short on purpose (found via a MariaDB-only failure, 2026-09-06):
    # `shadow_decision.reason` is `String(255)`, and this note can end up appended
    # to a decision PI still gets to extend with its own suffix afterwards
    # (`services/shadow_run.py::_pi_outcome`) -- exactly the combination an
    # EIN/AUS-only zone with an open window newly makes possible, since rule 3
    # no longer applies to it at all and `_pi_gate_reason` therefore does not
    # block PI here either. SQLite does not enforce the column length and stayed
    # green; only the MariaDB run caught the truncation.
    on_off_zone_note = (
        " EIN/AUS-Aktor — Fensteröffnung wirkt hier nicht abschaltend."
        if situation.window_open and situation.on_off_actuators_only
        else ""
    )

    # Rule 3 — window open normally means off, regardless of temperature. One
    # exception, decided by the project owner (2026-09-06): an open window must not
    # be allowed to freeze the room. If the zone falls below its own frost-protection
    # setpoint despite the open window, it heats anyway — heating against an open
    # window is expensive, but a frozen pipe is more expensive still. Reached only for
    # a zone that is not exempted from this rule altogether (see `on_off_zone_note`
    # above).
    if situation.window_open and not situation.on_off_actuators_only:
        # The same hysteresis band as every other threshold in this function, against
        # the frost-protection value instead of the normal setpoint, so the exception
        # cannot flap at the frost value either. `already_engaged` reuses `heating_now`
        # instead of separately persisted state, on the assumption that while the
        # window stays open, `heating_now` can only be `True` because this exception
        # engaged it -- that assumption holds for as many cycles as `measured_c` stays
        # inside the band, not just one (found in review 2026-09-06, corrected from an
        # earlier version of this comment that claimed the opposite).
        #
        # Befund C (review 2026-09-06): that assumption is still not quite enough on
        # its own. Two things outlive their own cycle here with `heating_now=True`: a
        # zone that was heating for an ordinary reason (rule 6) at the moment the
        # window opened, or a valve-protection run (rule 7) already under way when it
        # did -- if either happens to sit inside the frost band right then,
        # `already_engaged` would read `True` on nothing but that coincidence, and a
        # continuing protection run would be misattributed to
        # `REASON_CODE_FROST_OVERRIDES_WINDOW`: the heating outcome stays correct (the
        # room genuinely is inside the frost band), but the recorded *reason* would be
        # wrong, which Grundsatz 5 asks be accurate, not merely the direction of the
        # outcome. Excluding a protection run is enough to close this: rule 6 already
        # draws exactly this line for the identical reason, via `regular_heating_now`
        # below -- a protection-created on-state is not evidence of ordinary demand,
        # here not evidence of a frost-driven engagement either. The other source (an
        # open window catching an ordinary rule-6 heat already running) needs no such
        # exclusion: that heat is regular room heating with the window not yet
        # accounted for, and re-attributing it to the frost exception the first time
        # this rule runs is exactly what "the exception was already engaged" is
        # supposed to mean once the room is inside the band -- there is no third,
        # more original reason to misattribute it away from.
        frost_low = situation.frost_c - h
        frost_high = situation.frost_c + h
        already_engaged = (
            situation.heating_now
            and not situation.valve_protection_active
            and measured_c <= frost_high
        )
        wants_frost_heat = measured_c < frost_low or already_engaged
        if not wants_frost_heat:
            return Decision(
                heating=False,
                reason_code=REASON_CODE_WINDOW_OPEN,
                reason=(
                    f"Fenster offen — Ist {measured_c} °C, "
                    f"Soll {setpoint_c} °C ({setpoint_reason})."
                ),
            )
        # Falls through instead of returning: rule 4 below never applies here in
        # practice (`window_closed_for_s` is always `None` while `window_open` is
        # `True`, see `services/shadow_run.py::_window_situation`); rule 5's minimum
        # switch duration still can hold the previous state unchanged — "Mindest-
        # schaltdauern gelten unverändert weiter"; and rule 6 further down decides
        # on/off exactly as it always does, just against the frost-protection value
        # instead of the normal setpoint, which is why both are substituted here.
        setpoint_c = situation.frost_c
        # On a failed sensor `setpoint_reason` already names that (rule 1/2 above) and
        # `setpoint_c` is already the frost value — append instead of replacing, so a
        # doubly unusual cycle (stale sensor *and* an open window below frost) still
        # says both, rather than the window exception silently displacing the sensor
        # one.
        setpoint_reason = (
            f"{setpoint_reason} Zusätzlich Ausnahmeregel: Fenster offen, aber "
            f"Frostschutz {situation.frost_c} °C unterschritten — es wird trotzdem "
            "geheizt."
            if sensor_failed
            else (
                f"Fenster offen, aber Frostschutz {situation.frost_c} °C unterschritten "
                "— Ausnahmeregel: es wird trotz offenem Fenster geheizt, um ein "
                "Einfrieren zu vermeiden."
            )
        )
        frost_override_active = True
    else:
        frost_override_active = False

    # Rule 4 — resume delay: the window is closed, but the room is still cooling down
    # from it. 'None' for fenster_zu_seit_s means "no pending resume delay" (the
    # window has never been open since recording began) — then there is nothing to
    # wait out. Skipped for an EIN/AUS-zone exactly like rule 3 above — for the same
    # reason: it never actually switched off, so there is nothing to resume from.
    delay = situation.parameter.window_resume_delay_seconds
    if (
        not situation.on_off_actuators_only
        and situation.window_closed_for_s is not None
        and situation.window_closed_for_s < delay
    ):
        return Decision(
            heating=False,
            reason_code=REASON_CODE_OFF,
            reason=(
                f"Fenster seit {situation.window_closed_for_s}s zu, Wiederanlauf erst nach "
                f"{delay}s — Raum kühlt noch nach."
            ),
        )

    # Whether rule 7 (valve protection) would win right now — computed once here so
    # rule 5's exemption below and rule 7's own decision further down share exactly
    # the same condition. `valve_protection_active` on its own is not enough for
    # that: it only says a previous cycle's marker is still set, not that protection
    # is still what wins this cycle. An override, a switch to operating mode 'off',
    # or a failed sensor can make rule 7 lose mid-run without clearing the marker —
    # the marker is only cleared once the run's persisted duration elapses, in
    # `services/shadow_run.py`. Exempting rule 5 from the bare marker would let such
    # a lapsed run bypass the minimum switch duration for the rest of that window —
    # exactly what rule 5 exists to prevent.
    protection_allowed = (
        situation.parameter.valve_protection_enabled
        and situation.sensor_status == "ok"
        and situation.operating_mode != "off"
        and not situation.override_active
        and situation.valve_protection_due
    )

    # Rule 5 — minimum switch duration protects the valve from short-cycling. 'None'
    # for seit_s means "duration of the current state unknown" — typically the first
    # cycle after a restart, with no history. Imposing a lock against a duration we do
    # not know would itself be arbitrary; so the lock only applies when seit_s is known
    # AND too short. A freshly started service may therefore decide by hysteresis right
    # away, instead of waiting out a deadline that never started running.
    #
    # A currently winning protection run (marker set AND rule 7 would still win) is
    # exempt from the *off* timer (`min_off_seconds`) — it must not be blocked from
    # starting by a minimum switch duration left over from before the run started,
    # including right after a restart, where the marker is the only memory of the
    # run (see `valve_protection_active` on `Situation`). A run whose marker is set
    # but that has since lost rule 7 gets no exemption from `min_off_seconds` and
    # falls back to the minimum switch duration like any other decision.
    #
    # The *on* timer (`min_on_seconds`) is different: it exists to protect against
    # short-cycling the valve, and a protection run does not short-cycle — it runs
    # for its own configured duration, once a month. So whenever the held-on state
    # currently traces back to a protection run (`valve_protection_active`), the on
    # timer does not apply, regardless of whether rule 7 would still win this cycle.
    # This is deliberately looser than the off-timer exemption above: without it, a
    # zone whose `min_on_seconds` outlives its protection run keeps the valve open
    # by the minimum duration alone, on the very cycle the run closes — the marker
    # is cleared right after in `services/shadow_run.py`, so the next cycle would
    # misread the still-open valve as ordinary heating (found and fixed 2026-09-02).
    # The traded-off risk is bounded
    # to the run's own duration: if an override, an off-mode switch, or a sensor
    # failure makes rule 7 lose mid-run while the marker is still set, the on-state
    # is no longer held for the usual minimum — it can now flip in the very next
    # cycle where ordinary hysteresis would ask for that. That window is at most the
    # configured protection-run duration, since the marker is unconditionally
    # cleared once that elapses.
    minimum_duration = (
        situation.parameter.min_on_seconds
        if situation.heating_now
        else situation.parameter.min_off_seconds
    )
    protection_exempt = (
        situation.valve_protection_active
        if situation.heating_now
        else situation.valve_protection_active and protection_allowed
    )
    if (
        not protection_exempt
        and situation.held_for_s is not None
        and situation.held_for_s < minimum_duration
    ):
        state = "Heizen" if situation.heating_now else "Aus"
        return Decision(
            heating=situation.heating_now,
            reason_code=REASON_CODE_BLOCKED_MINIMUM_DURATION,
            reason=(
                f"Zustand '{state}' erst seit {situation.held_for_s}s, "
                f"Mindestdauer {minimum_duration}s "
                "— die Heizanforderung bleibt unverändert." + on_off_zone_note
            ),
        )

    # Rule 6 — hysteresis. The legacy system does not have it
    # (`if ist < soll: an, sonst aus`) and switches at the setpoint on every cycle;
    # `h` (computed above, ahead of rule 3's frost exception) is exactly the band
    # that prevents that.
    # A protection run is not evidence that normal control currently wants heat.
    # Treat its temporary on-state as off for hysteresis, otherwise the ordinary
    # "keep current state" branch would make the run endless after a restart.
    regular_heating_now = situation.heating_now and not situation.valve_protection_active
    if not regular_heating_now and measured_c < setpoint_c - h:
        return Decision(
            heating=True,
            reason_code=(
                REASON_CODE_FROST_SENSOR_FAILURE if sensor_failed
                else REASON_CODE_FROST_OVERRIDES_WINDOW if frost_override_active
                else REASON_CODE_HEATING
            ),
            reason=(
                f"Ist {measured_c} °C unter Soll {setpoint_c} °C minus Hysterese {h}K "
                f"({setpoint_reason})." + on_off_zone_note
            ),
        )
    if regular_heating_now and measured_c > setpoint_c + h:
        # Unreachable while `frost_override_active` is true: rule 3's own check above
        # already established `measured_c <= setpoint_c(frost) + h` before falling
        # through, so this branch cannot fire for the same cycle. No ternary needed.
        return Decision(
            heating=False,
            reason_code=REASON_CODE_OFF,
            reason=(
                f"Ist {measured_c} °C über Soll {setpoint_c} °C plus Hysterese {h}K "
                f"({setpoint_reason})." + on_off_zone_note
            ),
        )
    # This branch is reached whenever heating is already on and the measured value has
    # not crossed the *upper* band edge (checked above) — not only when it actually
    # sits within the band. It can equally be reached far *below* the lower edge: the
    # decision (keep heating) is correct either way, but the two situations are not the
    # same fact and must not share one sentence (found 2026-09-04: 18,527 logged
    # decisions claiming "within ± h" with a mean actual distance of 5.90K and a max of
    # 11.40K from the setpoint).
    if regular_heating_now:
        if measured_c < setpoint_c - h:
            return Decision(
                heating=True,
                reason_code=(
                    REASON_CODE_FROST_SENSOR_FAILURE if sensor_failed
                    else REASON_CODE_FROST_OVERRIDES_WINDOW if frost_override_active
                    else REASON_CODE_UNCHANGED
                ),
                reason=(
                    f"Ist {measured_c} °C unter Soll {setpoint_c} °C minus Hysterese {h}K "
                    f"({setpoint_reason}) — Heizung läuft bereits, Zustand bleibt."
                    + on_off_zone_note
                ),
            )
        return Decision(
            heating=True,
            reason_code=(
                REASON_CODE_FROST_SENSOR_FAILURE if sensor_failed
                else REASON_CODE_FROST_OVERRIDES_WINDOW if frost_override_active
                else REASON_CODE_UNCHANGED
            ),
            reason=(
                f"Ist {measured_c} °C innerhalb der Hysterese um Soll {setpoint_c} °C ± {h}K "
                f"({setpoint_reason}) — Zustand bleibt." + on_off_zone_note
            ),
        )

    # Rule 7 — valve protection deliberately has the lowest precedence. Sensor
    # failure, frost-protection mode, windows, overrides, minimum durations and every
    # normal heating decision have already won above. An active run uses the same
    # path after a restart; its persisted start time decides when it is no longer due.
    # `protection_allowed` is computed once, above rule 5, so both rules agree on
    # exactly the same condition for "protection currently wins".
    if protection_allowed:
        return Decision(
            heating=True,
            reason_code=REASON_CODE_VALVE_PROTECTION,
            reason=(
                "Ventilschutzlauf — die Regelung entscheidet unabhängig von der "
                "Raumregelung für "
                f"{situation.parameter.valve_protection_duration_minutes} Minuten auf "
                "Heizen. Im Trockenlauf wird die Entscheidung nur protokolliert; im "
                "scharfen Betrieb nach einem Neustart geht sie an den zugeordneten Aktor."
                + on_off_zone_note
            ),
        )
    # Mirror image of the branch above: reached whenever heating is already off and the
    # measured value has not crossed the *lower* band edge — not only when it sits
    # within the band. It can equally be reached far *above* the upper edge (the
    # motivating case: room at 27.40 °C against a 16.0 °C frost-protection setpoint,
    # logged as "within ± 0.10K"). The decision (stay off) is correct; only the old,
    # single sentence for both cases was not.
    # `frost_override_active` cannot be true in either branch from here on: rule 3
    # only falls through once `measured_c <= setpoint_c(frost) + h` already holds,
    # so both remaining "stay off" branches are unreachable for that cycle.
    if measured_c > setpoint_c + h:
        return Decision(
            heating=False,
            reason_code=(
                REASON_CODE_FROST_SENSOR_FAILURE if sensor_failed
                else REASON_CODE_UNCHANGED
            ),
            reason=(
                f"Ist {measured_c} °C über Soll {setpoint_c} °C plus Hysterese {h}K "
                f"({setpoint_reason}) — Heizung ist bereits aus, Zustand bleibt."
                + on_off_zone_note
            ),
        )
    return Decision(
        heating=False,
        reason_code=(
            REASON_CODE_FROST_SENSOR_FAILURE if sensor_failed
            else REASON_CODE_UNCHANGED
        ),
        reason=(
            f"Ist {measured_c} °C innerhalb der Hysterese um Soll {setpoint_c} °C ± {h}K "
            f"({setpoint_reason}) — Zustand bleibt." + on_off_zone_note
        ),
    )
