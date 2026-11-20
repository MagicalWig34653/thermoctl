"""Replay regression of the PI rules against private operating data.

Replay offene Schleife, kein Regelnachweis: the room curve is recorded, not reacting.

The raw data (room history, switching log, outdoor sensor of a real installation) are
**not part of the repository** -- it is public (AGPL) and the data belong to a
household. This test therefore reads them only from the directory named by the
environment variable `THERMOCTL_PI_REPLAY_DIR` (the unpacked data package) and is
skipped without it:

    THERMOCTL_PI_REPLAY_DIR=/path/to/paket_pi_regelung_thermoctl \\
        .venv/bin/python -m pytest tests/test_pi_replay.py

Nothing from the files is copied here: no excerpts, no number series, no dates. The
scenarios are *found* in the data by rule (the longest stretch above the setpoint, ...),
and the assertions are relations between two replays of the same data (rule on versus
rule off) or coarse bounds, never values read off the files.

Open loop: the recorded room temperature is replayed into the real `pi_cycle()`, the
controller's decisions do not feed back into the room. That shows what the integral
*would have done* on exactly these room curves -- not how the room would have reacted.
"""

from __future__ import annotations

import bisect
import csv
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from thermoctl.domain.pi_control import (
    NEUTRAL_MODULATOR_STATE,
    PiCycleInput,
    PiState,
    pi_cycle,
)
from thermoctl.domain.pi_feedforward import feedforward_duty, smoothed_outdoor

REPLAY_DIR_VARIABLE = "THERMOCTL_PI_REPLAY_DIR"
CYCLE = timedelta(seconds=30)
KP = Decimal("0.45")
TI = Decimal(90)

ROOM_FILE = "daten_2026-11-16_bis_18/raumverlauf_2026-11-16_bis_18.csv"
OUTDOOR_FILE = "daten_2026-11-16_bis_18/aussentemperatur_hue_outdoor_2026-11-15_bis_18.csv"
LOG_FILE = "daten_2026-11-16_bis_18/pi_schaltprotokoll_2026-11-16_bis_18.csv"


@dataclass(frozen=True)
class RoomPoint:
    at: datetime
    measured: float
    setpoint: float


@dataclass(frozen=True)
class LoggedCycle:
    at: datetime
    duty: float


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


@pytest.fixture(scope="module")
def replay_dir() -> Path:
    raw = os.environ.get(REPLAY_DIR_VARIABLE)
    if not raw:
        pytest.skip(
            f"{REPLAY_DIR_VARIABLE} nicht gesetzt: die privaten Betriebsdaten gehoeren "
            "nicht ins oeffentliche Repository."
        )
    directory = Path(raw)
    if not (directory / ROOM_FILE).is_file():
        pytest.skip(f"{REPLAY_DIR_VARIABLE} zeigt auf kein Datenpaket ({ROOM_FILE} fehlt).")
    return directory


@pytest.fixture(scope="module")
def room(replay_dir: Path) -> list[RoomPoint]:
    with (replay_dir / ROOM_FILE).open(encoding="utf-8") as handle:
        return [
            RoomPoint(_parse(row["time_utc"]), float(row["raum_c"]), float(row["soll_c"]))
            for row in csv.DictReader(handle)
        ]


@pytest.fixture(scope="module")
def outdoor(replay_dir: Path) -> list[tuple[datetime, Decimal | None]]:
    samples: list[tuple[datetime, Decimal | None]] = []
    with (replay_dir / OUTDOOR_FILE).open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            try:
                value: Decimal | None = Decimal(row["state"])
            except ArithmeticError:
                value = None  # "unavailable", "unknown"
            samples.append((_parse(row["last_changed"]), value))
    return samples


@pytest.fixture(scope="module")
def logged(replay_dir: Path) -> list[LoggedCycle]:
    with (replay_dir / LOG_FILE).open(encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    return [LoggedCycle(_parse(row[0]), float(row[7]) / 100) for row in rows[1:]]


def _replay(
    room: list[RoomPoint],
    start: datetime,
    end: datetime,
    *,
    integral: str,
    surplus_decay_factor: str = "1",
    separation_k: str = "0",
    outdoor: list[tuple[datetime, Decimal | None]] | None = None,
    feedforward_gain: str | None = None,
) -> list[tuple[datetime, Decimal, Decimal]]:
    """`(time, integral, raw duty)` per cycle, fed with the recorded room curve."""
    times = [point.at for point in room]
    state = PiState(
        integral=Decimal(integral),
        last_evaluated_at=start - CYCLE,
        setpoint_context_key="replay",
        modulator=NEUTRAL_MODULATOR_STATE,
        awaiting_boundary_until=None,
        last_reset_reason=None,
    )
    result: list[tuple[datetime, Decimal, Decimal]] = []
    now = start
    while now <= end:
        point = room[max(bisect.bisect_right(times, now) - 1, 0)]
        share = Decimal(0)
        if feedforward_gain is not None and outdoor is not None:
            visible = [sample for sample in outdoor if sample[0] <= now][-80:]
            share = feedforward_duty(
                enabled=True,
                gain=Decimal(feedforward_gain),
                setpoint_c=Decimal(str(point.setpoint)),
                outdoor_smoothed_c=smoothed_outdoor(visible, now, 3),
            )
        output = pi_cycle(
            state,
            PiCycleInput(
                now=now,
                error_k=Decimal(str(round(point.setpoint - point.measured, 4))),
                setpoint_context_key="replay",
                expected_cycle_seconds=30,
                gain_per_k=KP,
                integral_time_minutes=TI,
                pi_min_on_seconds=180,
                pi_min_off_seconds=60,
                surplus_decay_factor=Decimal(surplus_decay_factor),
                integral_separation_k=Decimal(separation_k),
                feedforward_duty=share,
            ),
        )
        state = output.state
        assert output.duty_raw is not None
        result.append((now, state.integral, output.duty_raw))
        now += CYCLE
    return result


def _longest_stretch(
    room: list[RoomPoint], predicate: Callable[[RoomPoint], bool]
) -> tuple[datetime, datetime]:
    """Longest run of consecutive room points for which `predicate(point)` holds."""
    best: tuple[datetime, datetime] = (room[0].at, room[0].at)
    run_start: datetime | None = None
    previous = room[0].at
    for point in room:
        if predicate(point):
            run_start = run_start or point.at
            if point.at - run_start > best[1] - best[0]:
                best = (run_start, point.at)
        else:
            run_start = None
        previous = point.at
    assert previous >= best[1]
    return best


class TestReplayOfTheRecordedRoom:
    def test_the_old_rules_reproduce_the_logged_duty_closely(
        self, room: list[RoomPoint], logged: list[LoggedCycle]
    ) -> None:
        """Fidelity of the replay itself: with every rule off, starting from the first
        logged duty, the computed duty tracks the logged one on average."""
        first = logged[0]
        point = room[max(bisect.bisect_right([p.at for p in room], first.at) - 1, 0)]
        integral = max(0.0, first.duty - float(KP) * (point.setpoint - point.measured))
        replay = _replay(room, first.at, logged[-1].at, integral=str(round(integral, 4)))
        times = [entry[0] for entry in replay]
        errors = [
            abs(
                float(replay[min(bisect.bisect_left(times, cycle.at), len(replay) - 1)][2])
                - cycle.duty
            )
            for cycle in logged
        ]
        assert sum(errors) / len(errors) < 0.05

    def test_surplus_decay_keeps_more_integral_through_the_longest_sunny_stretch(
        self, room: list[RoomPoint]
    ) -> None:
        start, end = _longest_stretch(room, lambda p: p.measured - p.setpoint >= 0.1)
        assert end - start >= timedelta(hours=1), "kein Ueberschuss-Abschnitt in den Daten"
        today = _replay(room, start, end, integral="0.30")
        decayed = _replay(room, start, end, integral="0.30", surplus_decay_factor="0.2")
        kept_today = today[-1][1]
        kept_decayed = decayed[-1][1]
        assert kept_decayed > kept_today
        # With the factor the integral is at least what it would be at five times the
        # time constant of the unwinding -- i.e. clearly not eaten away.
        assert kept_decayed >= Decimal("0.30") - (Decimal("0.30") - kept_today) / 2

    def test_separation_never_raises_the_integral_while_far_below(
        self, room: list[RoomPoint]
    ) -> None:
        start, end = _longest_stretch(room, lambda p: p.setpoint - p.measured >= 0.3)
        if end - start < timedelta(minutes=10):
            pytest.skip("kein Abschnitt deutlich unter Soll in den Daten")
        separated = _replay(room, start, end, integral="0.20", separation_k="0.25")
        free = _replay(room, start, end, integral="0.20")
        # e > k throughout: the integral must not move at all, while it does without.
        assert {entry[1] for entry in separated} == {Decimal("0.20")}
        assert free[-1][1] > Decimal("0.20")

    def test_feedforward_is_available_in_every_cycle_despite_invalid_sensor_readings(
        self, room: list[RoomPoint], outdoor: list[tuple[datetime, Decimal | None]]
    ) -> None:
        assert any(value is None for _, value in outdoor), "keine ungueltigen Werte in den Daten"
        first_valid = next(at for at, value in outdoor if value is not None)
        now = max(room[0].at, first_valid)
        missing = 0
        while now <= room[-1].at:
            visible = [sample for sample in outdoor if sample[0] <= now][-80:]
            if smoothed_outdoor(visible, now, 3) is None:
                missing += 1
            now += timedelta(minutes=10)
        assert missing == 0

    def test_feedforward_raises_the_duty_in_the_coldest_hours_without_touching_the_integral_start(
        self, room: list[RoomPoint], outdoor: list[tuple[datetime, Decimal | None]]
    ) -> None:
        coldest = min(room, key=lambda p: p.measured - p.setpoint)
        start = coldest.at - timedelta(hours=1)
        plain = _replay(room, start, coldest.at, integral="0.20")
        with_ff = _replay(
            room, start, coldest.at, integral="0.20", outdoor=outdoor, feedforward_gain="0.02"
        )
        assert with_ff[-1][2] > plain[-1][2]
        # The share is additive: the integral is the same arithmetic, so it stays close.
        assert abs(with_ff[-1][1] - plain[-1][1]) < Decimal("0.05")
