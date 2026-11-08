"""`shadow_run._previous_state`: bit-identical to the former full-load implementation.

Grundsatz 7: this derivation feeds the minimum switch duration of the real heating
(`held_for_s`, `phase_started_by`). The reading was changed from "load the whole
history of the zone and walk it in Python" to a few targeted index lookups (newest row
with the other value, newest recovery marker, oldest row of the run).
`_reference_previous_state` below is the old implementation, verbatim;
every scenario runs through both, and a seeded random comparison covers
histories no hand-written case would think of.
"""

import random
from datetime import datetime, timedelta

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from tests.helpers import create_settings, create_zone
from thermoctl.db.models.operations import Setting
from thermoctl.db.models.state import ShadowDecision
from thermoctl.services import shadow_run

NOW = datetime(2026, 11, 8, 12, 0)
MARKER = shadow_run.OUTCOME_CODE_NOTBETRIEB_RUECKKEHR
Row = tuple[int, bool, str, str]  # (seconds before NOW, would_heat, controller, outcome code)


def _reference_previous_state(
    session: Session, zone_id: int, now: datetime
) -> tuple[bool, int | None, bool | None, str | None]:
    """The implementation before the targeted lookups (unchanged logic, full load)."""
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
        if outcome_code == MARKER:
            break
    return current_state, int((now - start).total_seconds()), current_state, phase_started_by


def _zone_with_history(session: Session, name: str, history: list[Row]) -> int:
    """Insert `history` in the given order (so ids ascend in that order)."""
    if session.get(Setting, 1) is None:
        create_settings(session)
    zone = create_zone(session, name)
    session.add_all(
        ShadowDecision(
            decided_at=NOW - timedelta(seconds=seconds_ago),
            zone_id=zone.id,
            setpoint_reason="Test",
            would_heat=heat,
            outcome_code=outcome,
            reason="Test",
            requested_controller=controller,
            effective_controller=controller,
        )
        for seconds_ago, heat, controller, outcome in history
    )
    session.flush()
    return zone.id


def _both(session: Session, zone_id: int) -> tuple[object, object]:
    return (
        shadow_run._previous_state(session, zone_id, NOW),
        _reference_previous_state(session, zone_id, NOW),
    )


def test_empty_history(session: Session) -> None:
    zone_id = _zone_with_history(session, "leer", [])
    new, old = _both(session, zone_id)
    assert new == old == (False, None, None, None)


def test_long_run_with_equal_would_heat_counts_back_to_its_oldest_row(session: Session) -> None:
    history: list[Row] = [(1000 - 10 * i, True, "hysteresis", "heizen") for i in range(40)]
    zone_id = _zone_with_history(session, "lang", history)
    new, old = _both(session, zone_id)
    assert new == old == (True, 1000, True, "hysteresis")


def test_state_change_ends_the_run_and_older_rows_are_ignored(session: Session) -> None:
    history: list[Row] = [(500 - i, False, "hysteresis", "aus") for i in range(10)]  # old, off
    history += [(300 - 10 * i, True, "pi", "heizen") for i in range(15)]  # newest run, on
    zone_id = _zone_with_history(session, "wechsel", history)
    new, old = _both(session, zone_id)
    assert new == old == (True, 300, True, "pi")


def test_phase_started_by_is_the_controller_of_the_oldest_row_of_the_run(session: Session) -> None:
    history: list[Row] = [(100, False, "pi", "aus")]
    history += [(90, True, "hysteresis", "heizen")]
    history += [(80 - 10 * i, True, "pi", "heizen") for i in range(5)]
    zone_id = _zone_with_history(session, "phase", history)
    new, old = _both(session, zone_id)
    assert new == old == (True, 90, True, "hysteresis")


def test_recovery_marker_cuts_the_run_even_with_older_equal_rows(session: Session) -> None:
    history: list[Row] = [(900 - 10 * i, True, "hysteresis", "heizen") for i in range(20)]
    history += [(700, True, "hysteresis", MARKER)]
    history += [(690 - 10 * i, True, "pi", "heizen") for i in range(30)]
    zone_id = _zone_with_history(session, "marker", history)
    new, old = _both(session, zone_id)
    assert new == old == (True, 700, True, "hysteresis")


def test_recovery_marker_as_newest_row(session: Session) -> None:
    history: list[Row] = [(500 - i, True, "pi", "heizen") for i in range(12)]
    history += [(0, False, "hysteresis", MARKER)]
    zone_id = _zone_with_history(session, "marker-neu", history)
    new, old = _both(session, zone_id)
    assert new == old == (False, 0, False, "hysteresis")


def test_marker_of_the_other_state_does_not_cut_a_run(session: Session) -> None:
    history: list[Row] = [(200, False, "hysteresis", MARKER)]
    history += [(100 - 10 * i, True, "pi", "heizen") for i in range(9)]
    zone_id = _zone_with_history(session, "marker-fremd", history)
    new, old = _both(session, zone_id)
    assert new == old == (True, 100, True, "pi")


def test_rows_with_the_same_decided_at_are_ordered_by_id(session: Session) -> None:
    # Same instant (as `_seed_recovery_phase_marker` + the natural decision of one cycle):
    # the higher id is the newer row.
    history: list[Row] = [(60, False, "hysteresis", "aus")]
    history += [(30, True, "hysteresis", "heizen")] * 6
    history += [(30, True, "pi", "heizen")] * 4
    history += [(0, True, "hysteresis", "heizen")] * 3
    zone_id = _zone_with_history(session, "gleichzeitig", history)
    new, old = _both(session, zone_id)
    assert new == old == (True, 30, True, "hysteresis")


def test_history_that_is_one_single_state_without_any_change(session: Session) -> None:
    history: list[Row] = [(100 - i, False, "hysteresis", "aus") for i in range(25)]
    zone_id = _zone_with_history(session, "ohne-wechsel", history)
    new, old = _both(session, zone_id)
    assert new == old == (False, 100, False, "hysteresis")


def test_other_zones_never_leak_into_the_lookups(session: Session) -> None:
    create_settings(session)
    other = create_zone(session, "fremd")
    session.add_all(
        ShadowDecision(
            decided_at=NOW - timedelta(seconds=i),
            zone_id=other.id,
            setpoint_reason="Test",
            would_heat=False,
            outcome_code="aus",
            reason="Test",
        )
        for i in range(30)
    )
    # Recovery markers of the other zone, newer than anything of the target's run.
    session.add_all(
        ShadowDecision(
            decided_at=NOW - timedelta(seconds=seconds_ago),
            zone_id=other.id,
            setpoint_reason="Test",
            would_heat=heat,
            outcome_code=MARKER,
            reason="Test",
        )
        for seconds_ago, heat in ((5, True), (3, False))
    )
    session.flush()
    history: list[Row] = [(50 - i, True, "hysteresis", "heizen") for i in range(10)]
    zone_id = _zone_with_history(session, "eigene", history)
    new, old = _both(session, zone_id)
    assert new == old == (True, 50, True, "hysteresis")


def test_cost_does_not_depend_on_the_length_of_the_run(session: Session) -> None:
    # A run of 1500 equal rows behind an opposite one: a constant number of statements,
    # each bounded by LIMIT 1 (this is what keeps a cycle fast with 100k+ rows per zone).
    history: list[Row] = [(9000 - i, False, "hysteresis", "aus") for i in range(300)]
    history += [(5000 - i, True, "pi", "heizen") for i in range(1500)]
    zone_id = _zone_with_history(session, "kosten", history)
    statements: list[str] = []
    engine = session.get_bind()

    def _count(conn: object, cursor: object, statement: str, *rest: object) -> None:
        if "FROM shadow_decision" in statement:
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", _count)
    try:
        assert shadow_run._previous_state(session, zone_id, NOW) == (True, 5000, True, "pi")
    finally:
        event.remove(engine, "before_cursor_execute", _count)
    assert len(statements) == 4
    assert all(" LIMIT " in s.upper() for s in statements)


def test_run_without_an_opposite_row_takes_the_oldest_row_of_the_zone(session: Session) -> None:
    history: list[Row] = [(5000 - i, True, "pi", "heizen") for i in range(1200)]
    zone_id = _zone_with_history(session, "nur-eins", history)
    new, old = _both(session, zone_id)
    assert new == old == (True, 5000, True, "pi")


@pytest.mark.parametrize("seed", range(150))
def test_random_histories_match_the_reference(session: Session, seed: int) -> None:
    rng = random.Random(seed)  # noqa: S311 -- reproducible test data, not security
    length = rng.randint(1, 80)
    seconds = 1000
    history: list[Row] = []
    heat = rng.random() < 0.5
    for _ in range(length):
        seconds -= rng.choice([0, 0, 1, 5, 30])  # ties in `decided_at` on purpose
        if rng.random() < 0.15:
            heat = not heat
        outcome = MARKER if rng.random() < 0.08 else "x"
        history.append((seconds + 1000, heat, rng.choice(["hysteresis", "pi"]), outcome))
    # Insert in a random order of ids among equal instants, but oldest-to-newest by time.
    history.sort(key=lambda row: -row[0])
    zone_id = _zone_with_history(session, f"zufall-{seed}", history)
    assert shadow_run._previous_state(session, zone_id, NOW) == _reference_previous_state(
        session, zone_id, NOW
    ), seed
