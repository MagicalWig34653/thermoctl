"""Die Plan-Uhr (`time.schedule_clock`) und der Zeitplan über echte Zeit, belegt gegen ein
unabhängiges Orakel.

Der Plan wird nicht gegen die Wanduhr ausgewertet, sondern gegen eine monotone Plan-Uhr:
Plan-Uhr(t) = Wanduhr(t), außer im zweiten Durchlauf einer Wiederholstunde -- dort steht sie
auf dem letzten Wanduhrwert des ersten Durchlaufs.

Das Orakel (`_walk`) teilt keinen Code mit der Umsetzung (weder `schedule_clock` noch
`wall_clock_reached_at` noch `current_point`). Es läuft in 1-Minuten-Schritten durch echte
UTC-Zeit, beobachtet nur die Wanduhr von `zoneinfo` und führt die Plan-Uhr als "laufendes
Maximum der Wanduhr seit Wochenbeginn". Daraus bestimmt es den aktiven Punkt und die Zeitpunkte,
zu denen ein Punkt feuert, und wird damit verglichen:

1. aktiver Punkt = letzter Punkt mit Minute <= Plan-Uhr im Wochenring (`_schedule_setpoint`);
2. `next_switch_utc` = frühester Zeitpunkt > jetzt, zu dem ein Punkt feuert (jedes Wechsels
   des aktiven Punkts); die Kette `next_switch_utc` ab dem Fensterbeginn liefert genau die
   Schaltungen des Orakels, jede genau einmal;
3. die Plan-Uhr läuft in echter Zeit nirgends rückwärts (innerhalb der Wochenwand).

Dazu ein Differenztest: Außerhalb des zweiten Durchlaufs ist das Ergebnis identisch zu einer
eingefrorenen Kopie der alten Wanduhr-Berechnung (Stand ffdfb477).

Feste Zeiten, feste Seeds; nie die echte Uhr.
"""

import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from functools import cache
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.orm import Session

from tests.helpers import create_mode, create_settings, create_zone, source
from thermoctl.db.models.operations import Setting
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.zone import SetpointMode, Zone, ZoneSetpoint
from thermoctl.domain.schedule import (
    BulkSetpointContext,
    _schedule_setpoint,
    current_point,
    next_switch_utc,
    resolved_setpoint,
)
from thermoctl.domain.time import local_time, schedule_clock, wall_clock_reached_at

UTC = ZoneInfo("UTC")
MINUTE = timedelta(minutes=1)
WINDOW = timedelta(hours=36)

ZONES = [
    "Europe/Berlin",
    "America/New_York",
    "Europe/London",
    "Australia/Lord_Howe",  # 30-Minuten-Sprung
    "Pacific/Chatham",  # Versatz :45
    "UTC",
    "Asia/Kolkata",
]


# -- Plan-Uhr: von Hand gegen zoneinfo gerechnete Tabelle ----------------------------------


@pytest.mark.parametrize(
    ("timezone_name", "moment", "expected"),
    [
        # Berlin 2026-10-25: Wiederholstunde 00:00-01:00 UTC (02:xx CEST), dann 01:00-02:00 UTC
        ("Europe/Berlin", datetime(2026, 10, 24, 23, 59), datetime(2026, 10, 25, 1, 59)),
        ("Europe/Berlin", datetime(2026, 10, 25, 0, 0), datetime(2026, 10, 25, 2, 0)),
        ("Europe/Berlin", datetime(2026, 10, 25, 0, 59, 59), datetime(2026, 10, 25, 2, 59, 59)),
        ("Europe/Berlin", datetime(2026, 10, 25, 1, 0), datetime(2026, 10, 25, 2, 59, 59, 999999)),
        ("Europe/Berlin", datetime(2026, 10, 25, 1, 30), datetime(2026, 10, 25, 2, 59, 59, 999999)),
        (
            "Europe/Berlin", datetime(2026, 10, 25, 1, 59, 59, 999999),
            datetime(2026, 10, 25, 2, 59, 59, 999999),
        ),
        ("Europe/Berlin", datetime(2026, 10, 25, 2, 0), datetime(2026, 10, 25, 3, 0)),
        (
            "Europe/Berlin", datetime(2026, 10, 25, 2, 0, 0, 500),
            datetime(2026, 10, 25, 3, 0, 0, 500),
        ),
        # Sommer, Winter, Frühjahrslücke: Wanduhr
        ("Europe/Berlin", datetime(2026, 8, 1, 10, 0, 0, 123), datetime(2026, 8, 1, 12, 0, 0, 123)),
        ("Europe/Berlin", datetime(2026, 1, 15, 10, 0), datetime(2026, 1, 15, 11, 0)),
        ("Europe/Berlin", datetime(2026, 3, 29, 0, 59, 59), datetime(2026, 3, 29, 1, 59, 59)),
        ("Europe/Berlin", datetime(2026, 3, 29, 1, 0), datetime(2026, 3, 29, 3, 0)),
        # New York 2026-11-01: 05:00-06:00 UTC (01:xx EDT), 06:00-07:00 UTC (01:xx EST)
        ("America/New_York", datetime(2026, 11, 1, 5, 59), datetime(2026, 11, 1, 1, 59)),
        ("America/New_York", datetime(2026, 11, 1, 6, 0), datetime(2026, 11, 1, 1, 59, 59, 999999)),
        (
            "America/New_York", datetime(2026, 11, 1, 6, 45),
            datetime(2026, 11, 1, 1, 59, 59, 999999),
        ),
        ("America/New_York", datetime(2026, 11, 1, 7, 0), datetime(2026, 11, 1, 2, 0)),
        # London 2026-10-25: 00:00-01:00 UTC (01:xx BST), 01:00-02:00 UTC (01:xx GMT)
        ("Europe/London", datetime(2026, 10, 25, 0, 30), datetime(2026, 10, 25, 1, 30)),
        ("Europe/London", datetime(2026, 10, 25, 1, 30), datetime(2026, 10, 25, 1, 59, 59, 999999)),
        ("Europe/London", datetime(2026, 10, 25, 2, 0), datetime(2026, 10, 25, 2, 0)),
        # Lord Howe 2026-04-05: nur 30 Minuten Rücksprung, 14:30-15:00 UTC, dann 15:00-15:30 UTC
        ("Australia/Lord_Howe", datetime(2026, 4, 4, 14, 59), datetime(2026, 4, 5, 1, 59)),
        (
            "Australia/Lord_Howe", datetime(2026, 4, 4, 15, 0),
            datetime(2026, 4, 5, 1, 59, 59, 999999),
        ),
        (
            "Australia/Lord_Howe", datetime(2026, 4, 4, 15, 29, 59),
            datetime(2026, 4, 5, 1, 59, 59, 999999),
        ),
        ("Australia/Lord_Howe", datetime(2026, 4, 4, 15, 30), datetime(2026, 4, 5, 2, 0)),
        # Chatham 2026-04-05: 02:45-03:45 doppelt, 13:00-14:00 UTC, dann 14:00-15:00 UTC
        ("Pacific/Chatham", datetime(2026, 4, 4, 13, 30), datetime(2026, 4, 5, 3, 15)),
        (
            "Pacific/Chatham", datetime(2026, 4, 4, 14, 0),
            datetime(2026, 4, 5, 3, 44, 59, 999999),
        ),
        ("Pacific/Chatham", datetime(2026, 4, 4, 15, 0), datetime(2026, 4, 5, 3, 45)),
        # ohne Sommerzeit: unverändert
        ("UTC", datetime(2026, 10, 25, 1, 30, 0, 7), datetime(2026, 10, 25, 1, 30, 0, 7)),
        (None, datetime(2026, 10, 25, 1, 30), datetime(2026, 10, 25, 1, 30)),
        ("Asia/Kolkata", datetime(2026, 10, 25, 1, 30), datetime(2026, 10, 25, 7, 0)),
    ],
)
def test_schedule_clock_table(
    timezone_name: str | None, moment: datetime, expected: datetime
) -> None:
    assert schedule_clock(moment, timezone_name) == expected


def test_a_fold_carried_by_the_naive_input_does_not_count_as_a_second_pass() -> None:
    """`wall_clock_reached_at` liefert naive UTC-Zeitpunkte mit `fold=1`; für die Zone "UTC"
    gab `astimezone` die Eingabe unverändert zurück, und die Plan-Uhr hielt jeden solchen
    Zeitpunkt für einen zweiten Durchlauf (Boost am Montag 08:00: falscher Endmodus)."""
    moment = datetime(2026, 8, 31, 22, 0, fold=1)

    assert local_time(moment, "UTC").fold == 0
    assert schedule_clock(moment, "UTC") == datetime(2026, 8, 31, 22, 0)
    assert schedule_clock(moment, None) == datetime(2026, 8, 31, 22, 0)
    # und für eine Zone mit Sommerzeit zählt allein der Fold der Ortszeit
    assert schedule_clock(datetime(2026, 8, 31, 20, 0, fold=1), "Europe/Berlin") == datetime(
        2026, 8, 31, 22, 0
    )
    for reading in wall_clock_reached_at(datetime(2026, 10, 25, 2, 30), "Europe/Berlin"):
        assert reading.fold == 0


# -- Orakel ----------------------------------------------------------------------------------


def _wall(moment: datetime, zone: ZoneInfo) -> datetime:
    """Die Wanduhr (naiv) zu einem echten Zeitpunkt, nur über `zoneinfo`."""
    return moment.replace(tzinfo=UTC).astimezone(zone).replace(tzinfo=None)


def _offset(moment: datetime, zone: ZoneInfo) -> timedelta:
    offset = moment.replace(tzinfo=UTC).astimezone(zone).utcoffset()
    assert offset is not None
    return offset


def _week_key(wall: datetime) -> datetime:
    """Montag 00:00 der Ortswoche."""
    return (wall - timedelta(days=wall.isoweekday() - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


def _week_minute(wall: datetime) -> int:
    return (wall.isoweekday() - 1) * 1440 + wall.hour * 60 + wall.minute


@dataclass(frozen=True)
class Transition:
    zone: str
    at: datetime  # echter Zeitpunkt (naiv UTC): erste Minute mit neuem Versatz
    step: timedelta  # neuer Versatz minus alter (negativ: Uhr springt zurück)

    @property
    def label(self) -> str:
        direction = "zurück" if self.step < timedelta(0) else "vor"
        return f"{self.zone} {self.at:%Y-%m-%d %H:%M} ({direction})"


@cache
def _transitions(zone_name: str, year: int) -> tuple[Transition, ...]:
    """Die Umstellungen eines Jahres: stündlich nach Versatzwechseln suchen, dann minutengenau."""
    zone = ZoneInfo(zone_name)
    found: list[Transition] = []
    moment = datetime(year, 1, 1)
    while moment < datetime(year + 1, 1, 1):
        later = moment + timedelta(hours=1)
        if _offset(later, zone) != _offset(moment, zone):
            probe = moment
            while _offset(probe, zone) == _offset(moment, zone):
                probe += MINUTE
            found.append(Transition(zone_name, probe, _offset(probe, zone) - _offset(moment, zone)))
        moment = later
    return tuple(found)


def _windows(zone_name: str) -> list[Transition]:
    """Fenster je Zone: alle Umstellungen 2026 und 2027; Zonen ohne Sommerzeit
    bekommen die Berliner Umstellungsmomente 2026 (sie dürfen sich dort nicht ändern)."""
    own = [*_transitions(zone_name, 2026), *_transitions(zone_name, 2027)]
    if own:
        return own
    return [Transition(zone_name, t.at, t.step) for t in _transitions("Europe/Berlin", 2026)]


@dataclass(frozen=True)
class Step:
    moment: datetime  # echter Zeitpunkt, naiv UTC, ganze Minute
    wall: datetime
    plan_minute: int  # Plan-Uhr als Wochenminute (laufendes Maximum der Wanduhr in der Woche)
    new_week: bool


@cache
def _walk(zone_name: str, start: datetime, minutes: int) -> tuple[Step, ...]:
    """Plan-Uhr als laufendes Maximum der Wanduhr seit Wochenbeginn, Minute für Minute."""
    zone = ZoneInfo(zone_name)
    steps: list[Step] = []
    week: datetime | None = None
    plan_minute = 0
    for offset in range(minutes):
        moment = start + offset * MINUTE
        wall = _wall(moment, zone)
        new_week = week is None or _week_key(wall) != week
        if new_week:
            week = _week_key(wall)
            plan_minute = _week_minute(wall)
        else:
            plan_minute = max(plan_minute, _week_minute(wall))
        steps.append(Step(moment, wall, plan_minute, new_week and offset > 0))
    return tuple(steps)


def _active(points: list[tuple[int, int]], plan_minute: int) -> int:
    """Index des aktiven Punkts: letzter mit Minute <= Plan-Minute, sonst der letzte des Rings."""
    minutes = [(weekday - 1) * 1440 + minute for weekday, minute in points]
    before = [(m, i) for i, m in enumerate(minutes) if m <= plan_minute]
    return max(before)[1] if before else max((m, i) for i, m in enumerate(minutes))[1]


def _fired(points: list[tuple[int, int]], previous: Step, step: Step) -> bool:
    """Feuert zu `step` ein Punkt? Plan-Minute überschreitet die Punktminute (auch im Sprung);
    am Wochenwechsel feuern die Punkte bis zur neuen Plan-Minute."""
    minutes = [(weekday - 1) * 1440 + minute for weekday, minute in points]
    low = -1 if step.new_week else previous.plan_minute
    return any(low < m <= step.plan_minute for m in minutes)


def _plans(transition: Transition) -> list[list[tuple[int, int]]]:
    """Vier Pläne je Fenster, deterministisch (feste Seeds). Punkte sind (Wochentag, Minute)."""
    zone = ZoneInfo(transition.zone)
    wall_after = _wall(transition.at, zone)
    wall_before = _wall(transition.at - MINUTE, zone)
    low, high = sorted((wall_after, wall_before))
    middle = low + (high + MINUTE - low) // 2 // MINUTE * MINUTE
    specials = [low - MINUTE, low, middle, high, high + MINUTE]

    def point(wall: datetime) -> tuple[int, int]:
        return (wall.isoweekday(), wall.hour * 60 + wall.minute)

    first_minute = int((low - timedelta(minutes=90) - low.replace(hour=0, minute=0)) / MINUTE)
    last_minute = int((high + timedelta(minutes=90) - low.replace(hour=0, minute=0)) / MINUTE)
    day_start = low.replace(hour=0, minute=0)

    def local_point(rng: random.Random) -> tuple[int, int]:
        return point(day_start + timedelta(minutes=rng.randint(first_minute, last_minute)))

    def anywhere(rng: random.Random) -> tuple[int, int]:
        return (rng.randint(1, 7), rng.randint(0, 1439))

    def unique(points: list[tuple[int, int]]) -> list[tuple[int, int]]:
        return list(dict.fromkeys(points))[:8]

    plans: list[list[tuple[int, int]]] = []
    seed = f"{transition.label}"
    rng = random.Random(f"{seed}-0")  # noqa: S311 -- reproducible fixture, not crypto
    plans.append(unique([
        local_point(rng) if rng.random() < 0.7 else anywhere(rng)
        for _ in range(rng.randint(1, 8))
    ]))
    rng = random.Random(f"{seed}-1")  # noqa: S311 -- reproducible fixture, not crypto
    plans.append(unique(
        [(7, 1439), (1, 0)] + [local_point(rng) for _ in range(rng.randint(1, 5))]
    ))
    rng = random.Random(f"{seed}-2")  # noqa: S311 -- reproducible fixture, not crypto
    plans.append(unique(
        [point(wall) for wall in specials]
        + [local_point(rng) if rng.random() < 0.5 else anywhere(rng) for _ in range(3)]
    ))
    plans.append([point(middle)])  # genau ein Punkt, auf der Mitte des Sprungs
    return plans


def _context(
    zone_name: str, points: list[tuple[int, int]]
) -> tuple[BulkSetpointContext, Zone, list[SchedulePoint], list[SetpointMode]]:
    """Ein Kontext ohne Datenbank; je Punkt ein eigener Modus mit eigener Temperatur."""
    zone = Zone(id=1, name="orakel", display_name="Orakel", operating_mode_id=1)
    modes = [SetpointMode(id=i + 1, code=f"m{i}", name=f"M{i}") for i in range(len(points))]
    schedule_points = [
        SchedulePoint(zone_id=1, weekday=weekday, minute_of_day=minute, setpoint_mode_id=i + 1)
        for i, (weekday, minute) in enumerate(points)
    ]
    ctx = BulkSetpointContext(
        settings=Setting(id=1, timezone=zone_name),
        frost_code="frost",
        temperatures={
            (1, mode.id): Decimal("15.0") + Decimal(i) / 2 for i, mode in enumerate(modes)
        },
        modes_by_id={mode.id: mode for mode in modes},
        overrides_by_zone={},
        vacation=None,
        points_by_zone={1: schedule_points},
    )
    return ctx, zone, schedule_points, modes


def _oracle_run(
    session: Session, transition: Transition, points: list[tuple[int, int]]
) -> None:
    start = transition.at.replace(second=0) - WINDOW
    window_end = transition.at + WINDOW
    # Das Orakel läuft eine Woche über das Fenster hinaus: die nächste Schaltung eines
    # Moments am Fensterende liegt dort.
    steps = _walk(transition.zone, start, int((window_end - start) / MINUTE) + 7 * 1440 + 60)
    ctx, zone, schedule_points, modes = _context(transition.zone, points)
    settings = ctx.settings
    near = timedelta(hours=3)
    close_by = timedelta(hours=3)

    # (1) aktiver Punkt, Minute für Minute nahe der Umstellung, sonst im 3-Minuten-Raster
    for index, step in enumerate(steps):
        if step.moment > window_end:
            break
        close = abs(step.moment - transition.at) <= near
        if not close and index % 3:
            continue
        result = _schedule_setpoint(session, zone, settings, step.moment, ctx)
        assert result is not None
        expected = _active(points, step.plan_minute)
        assert result.mode_id == modes[expected].id, (transition.label, points, step.moment)
        assert result.temperature_c == ctx.temperatures[(1, modes[expected].id)]

    # (2) die Kette next_switch_utc liefert genau die Schaltungen des Orakels, jede einmal,
    # und zu jedem Zeitpunkt dazwischen (volle Minute und +30 s) den frühesten späteren.
    fired = [
        step.moment
        for previous, step in zip(steps, steps[1:], strict=False)
        if _fired(points, previous, step)
    ]
    chain: list[datetime] = []
    moment = start
    while True:
        following = next_switch_utc(schedule_points, moment, transition.zone)
        assert following is not None and following > moment
        if following > window_end:
            break
        chain.append(following)
        moment = following
    assert chain == [m for m in fired if m <= window_end], (transition.label, points)

    for index, step in enumerate(steps):
        if step.moment >= window_end:
            break
        if abs(step.moment - transition.at) > close_by and index % 13:
            continue
        later = next(m for m in fired if m > step.moment)
        now = step.moment + timedelta(seconds=30 * (index % 2))
        assert next_switch_utc(schedule_points, now, transition.zone) == later, (
            transition.label, points, now,
        )

    # (3) die Plan-Uhr läuft nirgends rückwärts (innerhalb der Wochenwand); gegen die
    # Beobachtung gelesen: Wanduhr, solange sie nicht zurückspringt, sonst das Maximum
    previous_clock: datetime | None = None
    for step in steps:
        if step.moment > window_end:
            break
        clock = schedule_clock(step.moment, transition.zone)
        assert _week_minute(clock) == step.plan_minute, (transition.label, step.moment)
        if previous_clock is not None and not step.new_week:
            assert clock >= previous_clock, (transition.label, step.moment)
        previous_clock = clock


@pytest.mark.parametrize("zone_name", ZONES)
def test_the_plan_runs_over_real_time_against_the_oracle(
    session: Session, zone_name: str
) -> None:
    cases = 0
    for transition in _windows(zone_name):
        for points in _plans(transition):
            _oracle_run(session, transition, points)
            cases += 1
    # 4 Pläne je Fenster; DST-Zonen haben je 4 Umstellungen (2026, 2027), Zonen ohne
    # Sommerzeit zwei Fenster
    assert cases == 4 * len(_windows(zone_name))
    assert cases >= 8


def test_the_oracle_windows_cover_both_directions_of_every_dst_zone() -> None:
    """Die Fenster enthalten wirklich je Zone Rück- und Vorwärtssprünge 2026 und 2027 --
    sonst liefe der Orakeltest still über nichts."""
    for zone_name in ZONES[:5]:
        windows = _windows(zone_name)
        assert len(windows) == 4, zone_name
        assert sorted(w.step < timedelta(0) for w in windows) == [False, False, True, True]
        assert {w.at.year for w in windows} == {2026, 2027}
    assert {w.step for w in _windows("Australia/Lord_Howe")} == {
        timedelta(minutes=-30), timedelta(minutes=30),
    }
    assert {w.step for w in _windows("Europe/Berlin")} == {timedelta(hours=-1), timedelta(hours=1)}
    assert [w.at for w in _windows("Europe/Berlin") if w.at.year == 2026] == [
        datetime(2026, 3, 29, 1, 0), datetime(2026, 10, 25, 1, 0),
    ]
    for zone_name in ("UTC", "Asia/Kolkata"):
        assert _transitions(zone_name, 2026) == ()
        assert len(_windows(zone_name)) == 2


def test_the_oracle_plans_hit_the_repeated_hour_and_the_edges() -> None:
    """Die Pläne treffen die Stellen, an denen es kippen kann (sonst belegt der Test wenig)."""
    (back,) = [w for w in _windows("Europe/Berlin") if w.at == datetime(2026, 10, 25, 1, 0)]
    plans = _plans(back)
    assert len(plans) == 4
    assert all(1 <= len(p) <= 8 and len(set(p)) == len(p) for p in plans)
    assert plans[3] == [(7, 150)]  # Mitte der Wiederholstunde 02:00-03:00
    assert {(7, 119), (7, 120), (7, 150), (7, 179), (7, 180)} <= set(plans[2])
    assert {(7, 1439), (1, 0)} <= set(plans[1])
    assert _plans(back) == plans  # deterministisch


# -- Datenbankgestützt: resolved_setpoint / end_of_next_switch in der Wiederholstunde ----------


@pytest.mark.parametrize(
    "transition",
    [
        t for t in (*_transitions("Europe/Berlin", 2026), *_transitions("America/New_York", 2026))
        if t.step < timedelta(0)
    ],
    ids=lambda t: t.label,
)
def test_resolved_setpoint_in_the_repeated_hour_follows_the_oracle(
    session: Session, transition: Transition
) -> None:
    points = _plans(transition)[2]
    create_settings(session).timezone = transition.zone
    source(session, "system")
    zone = create_zone(session, "echtzeit")
    for index, (weekday, minute) in enumerate(points):
        mode = create_mode(session, f"p{index}")
        session.add(
            ZoneSetpoint(
                zone_id=zone.id, setpoint_mode_id=mode.id,
                temperature_c=Decimal("15.0") + Decimal(index) / 2,
            )
        )
        session.add(
            SchedulePoint(
                zone_id=zone.id, weekday=weekday, minute_of_day=minute,
                setpoint_mode_id=mode.id,
            )
        )
    session.flush()
    steps = _walk(transition.zone, transition.at - timedelta(hours=3), 6 * 60)

    for step in steps:
        expected = Decimal("15.0") + Decimal(_active(points, step.plan_minute)) / 2
        assert resolved_setpoint(session, zone, step.moment).temperature_c == expected, step.moment


# -- Differenztest gegen die eingefrorene alte Wanduhr-Berechnung (Stand ffdfb477) -----------


def _old_active_point(
    points: list[SchedulePoint], moment: datetime, timezone_name: str
) -> SchedulePoint | None:
    """Der alte `_schedule_setpoint`-Kern: `current_point` gegen die Wanduhr."""
    return current_point(points, local_time(moment, timezone_name).replace(tzinfo=None))


def _old_next_switch_utc(
    points: list[SchedulePoint], now_utc: datetime, timezone_name: str
) -> datetime | None:
    """Eingefrorene Kopie von `next_switch_utc` aus ffdfb477: beide Lesarten zählen."""
    if not points:
        return None
    local_now = local_time(now_utc, timezone_name).replace(tzinfo=None)
    week_start = (local_now - timedelta(days=local_now.isoweekday() - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    minutes = {(p.weekday - 1) * 1440 + p.minute_of_day for p in points}
    return min(
        (
            moment
            for week in (0, 1)
            for minute in minutes
            for moment in wall_clock_reached_at(
                week_start + timedelta(weeks=week, minutes=minute), timezone_name
            )
            if moment > now_utc
        ),
        default=None,
    )


@pytest.mark.parametrize("zone_name", ZONES)
def test_outside_the_second_pass_nothing_changed_against_the_old_wall_clock_result(
    session: Session, zone_name: str
) -> None:
    """Die Plan-Uhr ist außerhalb des zweiten Durchlaufs die Wanduhr, und der aktive Punkt
    ist identisch zur alten Berechnung. `next_switch_utc` weicht nur ab, wenn der alte Wert
    eine zweite Lesart war, die jetzt nicht mehr feuert: also für `now` ab dem Beginn der
    Wiederholstunde (der ersten, früheren Lesart) bis zu deren Ende; davor und danach
    stimmt es überein."""
    compared = 0
    differing_inside = 0
    for transition in _windows(zone_name):
        back = transition.step < timedelta(0)
        repeated_start = transition.at + transition.step
        repeated_end = transition.at - transition.step
        for points in _plans(transition):
            ctx, zone, schedule_points, _ = _context(transition.zone, points)
            for index, step in enumerate(_walk(transition.zone, transition.at - WINDOW, 72 * 60)):
                if abs(step.moment - transition.at) > timedelta(hours=3) and index % 13:
                    continue
                now = step.moment + timedelta(seconds=17 * (index % 2))
                in_second_pass = bool(local_time(now, transition.zone).fold)
                if not in_second_pass:
                    wall = local_time(now, transition.zone).replace(tzinfo=None)
                    assert schedule_clock(now, transition.zone) == wall
                    old = _old_active_point(schedule_points, now, transition.zone)
                    new = _schedule_setpoint(session, zone, ctx.settings, now, ctx)
                    assert old is not None and new is not None
                    assert new.mode_id == old.setpoint_mode_id, (transition.label, points, now)
                new_switch = next_switch_utc(schedule_points, now, transition.zone)
                old_switch = _old_next_switch_utc(schedule_points, now, transition.zone)
                if back and repeated_start <= now < repeated_end:
                    differing_inside += int(new_switch != old_switch)
                else:
                    assert new_switch == old_switch, (transition.label, points, now)
                compared += 1
    assert compared > 1000
    if zone_name in ("UTC", "Asia/Kolkata"):
        assert differing_inside == 0
    else:
        assert differing_inside > 0


@pytest.mark.parametrize("zone_name", ZONES[:5])
def test_the_schedule_clock_stands_still_in_the_second_pass_to_the_second(zone_name: str) -> None:
    """Auf Sekunden genau um jede Rück-Umstellung: Die Plan-Uhr läuft nie rückwärts, steht
    im zweiten Durchlauf auf einem festen Wert (letzter Wanduhrwert des ersten Durchlaufs +
    1 Minute - 1 µs) und folgt danach wieder der Wanduhr."""
    zone = ZoneInfo(zone_name)
    for transition in _windows(zone_name):
        if transition.step >= timedelta(0):
            continue
        frozen = _wall(transition.at - MINUTE, zone) + MINUTE - timedelta(microseconds=1)
        end = transition.at - transition.step
        edges = [
            edge + delta
            for edge in (transition.at, end)
            for delta in (-timedelta(seconds=1), -timedelta(microseconds=1), timedelta(0),
                          timedelta(microseconds=1), timedelta(seconds=1))
        ]
        stride = timedelta(seconds=7, microseconds=300_000)
        first = transition.at - timedelta(minutes=3)
        count = int((end + timedelta(minutes=3) - first) / stride)
        moments = sorted({*edges, *(first + k * stride for k in range(count))})
        previous: datetime | None = None
        for moment in moments:
            clock = schedule_clock(moment, zone_name)
            assert previous is None or clock >= previous, (transition.label, moment)
            if transition.at <= moment < end:
                assert clock == frozen, (transition.label, moment)
            else:
                assert clock == _wall(moment, zone), (transition.label, moment)
            previous = clock
