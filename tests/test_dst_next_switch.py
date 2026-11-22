"""Der nächste Schaltzeitpunkt rund um die Zeitumstellung.

Die Wanduhr springt: im Herbst wiederholt sich eine Stunde (Europe/Berlin 2026-10-25,
02:00-03:00 gibt es zweimal), im Frühjahr fällt eine aus (2026-03-29, 02:00-03:00 gibt es
nicht). `end_of_next_switch` rechnete in naiver Ortszeit und las eine doppelte Ortszeit
immer als ihr erstes Auftreten -- im zweiten Durchlauf lag das Ende dann in der
Vergangenheit, und eine Frühjahrslücke wurde um die Lückenbreite zu spät getroffen.
Boost und das Sollwertrad endeten dadurch sofort bzw. wirkten nie.

Festgelegt ist: Der nächste Schaltzeitpunkt ist der früheste echte Zeitpunkt strikt nach
`now`, zu dem die Plan-Uhr die Zeit eines Zeitplanpunkts erreicht. Die Plan-Uhr ist die
Wanduhr, steht aber im zweiten Durchlauf einer Wiederholstunde still: ein Punkt in der
Wiederholstunde feuert nur im ersten Durchlauf (Entscheidung des Projektinhabers, "Plan
über echte Zeit führen"; die Plan-Uhr selbst prüft `test_schedule_plan_clock.py`). Die
Tabelle unten ist von Hand gegen `zoneinfo` gerechnet; dazu kommt ein Gegenrechner, der
Minute für Minute durch die echte Zeit läuft und die Plan-Uhr als laufendes Maximum der
Wanduhr führt (er teilt keinen Code mit der Umsetzung). Feste Zeiten per `now`, nie die
echte Uhr.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.orm import Session

from tests.helpers import create_mode, create_settings, create_zone, source
from thermoctl.db.models.operations import Setting
from thermoctl.db.models.override import ZoneOverride
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.zone import Zone, ZoneSetpoint
from thermoctl.domain import schedule as schedule_module
from thermoctl.domain.remote_control import boost, set_temporary_setpoint
from thermoctl.domain.schedule import (
    end_of_next_switch,
    jump_to_next_switch,
    next_switch,
    resolved_setpoint,
    schedule_forecast,
)

SUNDAY = 7
MONDAY = 1
UTC = ZoneInfo("UTC")
NIGHT_C = Decimal("18.0")
DAY_C = Decimal("21.0")


def _zone(
    session: Session, timezone_name: str, points: list[tuple[int, int, str]]
) -> Zone:
    """Eine Zone mit Zeitplan; `points` sind (Wochentag, Minute des Tages, 'nacht'|'tag')."""
    create_settings(session).timezone = timezone_name
    source(session, "system")
    zone = create_zone(session, "umstellung")
    modes = {"nacht": create_mode(session, "nacht"), "tag": create_mode(session, "tag")}
    session.add_all(
        [
            ZoneSetpoint(
                zone_id=zone.id, setpoint_mode_id=modes["nacht"].id, temperature_c=NIGHT_C
            ),
            ZoneSetpoint(
                zone_id=zone.id, setpoint_mode_id=modes["tag"].id, temperature_c=DAY_C
            ),
        ]
    )
    for weekday, minute, mode in points:
        session.add(
            SchedulePoint(
                zone_id=zone.id, weekday=weekday, minute_of_day=minute,
                setpoint_mode_id=modes[mode].id,
            )
        )
    session.flush()
    return zone


def _zone_with_minutes(
    session: Session, timezone_name: str, points: list[tuple[int, int]]
) -> Zone:
    return _zone(session, timezone_name, [(day, minute, "nacht") for day, minute in points])


# (Zone, Punkte (Wochentag, Minute), jetzt UTC, erwartetes Ende UTC) -- von Hand gegen
# zoneinfo gerechnet; die Fälle der Karte stehen vorn.
CASES = {
    "karte_zweiter_durchlauf": (
        # 02:30 hat im ersten Durchlauf (00:30 UTC) schon gefeuert und feuert nicht noch
        # einmal: nächste Schaltung ist die Woche darauf
        "Europe/Berlin", [(SUNDAY, 150)],
        datetime(2026, 10, 25, 1, 15), datetime(2026, 11, 1, 1, 30),
    ),
    "erster_durchlauf_bleibt": (
        "Europe/Berlin", [(SUNDAY, 150)],
        datetime(2026, 10, 25, 0, 15), datetime(2026, 10, 25, 0, 30),
    ),
    "jetzt_genau_auf_erstem_auftreten": (
        # jetzt = 02:30 CEST, der Punkt ist soeben gefeuert; die zweite Lesart (01:30 UTC)
        # feuert nicht, nächste Schaltung ist 02:30 CET am Sonntag darauf
        "Europe/Berlin", [(SUNDAY, 150)],
        datetime(2026, 10, 25, 0, 30), datetime(2026, 11, 1, 1, 30),
    ),
    "jetzt_genau_auf_zweitem_auftreten": (
        "Europe/Berlin", [(SUNDAY, 150)],
        datetime(2026, 10, 25, 1, 30), datetime(2026, 11, 1, 1, 30),
    ),
    "punkt_vor_wandzeit_zweiter_durchlauf": (
        # 02:10: beide Auftreten (00:10 und 01:10 UTC) liegen vor 01:15 -> nächste Woche
        "Europe/Berlin", [(SUNDAY, 130)],
        datetime(2026, 10, 25, 1, 15), datetime(2026, 11, 1, 1, 10),
    ),
    "punkt_vor_wandzeit_erster_durchlauf": (
        # 00:15 UTC = 02:15 CEST; 02:10 ist schon gefeuert (00:10 UTC) und kommt im zweiten
        # Durchlauf nicht wieder -> nächste Woche
        "Europe/Berlin", [(SUNDAY, 130)],
        datetime(2026, 10, 25, 0, 15), datetime(2026, 11, 1, 1, 10),
    ),
    "punkt_in_wiederholstunde_noch_ausstehend": (
        # 00:15 UTC = 02:15 CEST; 02:30 gilt ab 00:30 UTC (erste Lesart), bleibt
        "Europe/Berlin", [(SUNDAY, 150)],
        datetime(2026, 10, 25, 0, 15), datetime(2026, 10, 25, 0, 30),
    ),
    "punkt_nach_wiederholstunde_im_zweiten_durchlauf": (
        # 03:00 CET feuert erst, wenn die Wanduhr 03:00 erreicht (02:00 UTC), nicht im
        # zweiten Durchlauf
        "Europe/Berlin", [(SUNDAY, 180)],
        datetime(2026, 10, 25, 1, 15), datetime(2026, 10, 25, 2, 0),
    ),
    "mehrere_punkte_naechster_nach_wiederholstunde": (
        # 02:10 und 02:30 liegen in der Wiederholstunde und haben gefeuert; "bis zur nächsten
        # Schaltung" ist der Punkt NACH der Wiederholstunde: 03:30 CET = 02:30 UTC
        "Europe/Berlin", [(SUNDAY, 130), (SUNDAY, 150), (SUNDAY, 210)],
        datetime(2026, 10, 25, 1, 15), datetime(2026, 10, 25, 2, 30),
    ),
    "fruehjahr_luecke": (
        # 02:30 gibt es nicht; die Wanduhr springt um 01:00 UTC von 01:59 auf 03:00
        "Europe/Berlin", [(SUNDAY, 150)],
        datetime(2026, 3, 29, 0, 15), datetime(2026, 3, 29, 1, 0),
    ),
    "fruehjahr_luecke_eine_sekunde_davor": (
        "Europe/Berlin", [(SUNDAY, 150)],
        datetime(2026, 3, 29, 0, 59, 59), datetime(2026, 3, 29, 1, 0),
    ),
    "fruehjahr_luecke_jetzt_genau_auf_umstellung": (
        "Europe/Berlin", [(SUNDAY, 150)],
        datetime(2026, 3, 29, 1, 0), datetime(2026, 4, 5, 0, 30),
    ),
    "fruehjahr_luecke_danach": (
        "Europe/Berlin", [(SUNDAY, 150)],
        datetime(2026, 3, 29, 1, 15), datetime(2026, 4, 5, 0, 30),
    ),
    "fruehjahr_punkt_unmittelbar_nach_luecke": (
        # 03:00 existiert und ist die Umstellung selbst (= 01:00 UTC)
        "Europe/Berlin", [(SUNDAY, 180)],
        datetime(2026, 3, 29, 0, 15), datetime(2026, 3, 29, 1, 0),
    ),
    "fruehjahr_mehrere_punkte": (
        # 01:50 CET (00:50 UTC), 02:30 (Lücke, 01:00 UTC), 03:10 CEST (01:10 UTC)
        "Europe/Berlin", [(SUNDAY, 110), (SUNDAY, 150), (SUNDAY, 190)],
        datetime(2026, 3, 29, 0, 50), datetime(2026, 3, 29, 1, 0),
    ),
    "fruehjahr_mehrere_punkte_nach_luecke": (
        "Europe/Berlin", [(SUNDAY, 110), (SUNDAY, 150), (SUNDAY, 190)],
        datetime(2026, 3, 29, 1, 0), datetime(2026, 3, 29, 1, 10),
    ),
    "wochenuebergang_nach_wiederholstunde": (
        # Montag 00:00 CET = Sonntag 23:00 UTC
        "Europe/Berlin", [(MONDAY, 0), (SUNDAY, 150)],
        datetime(2026, 10, 25, 1, 45), datetime(2026, 10, 25, 23, 0),
    ),
    "wochenuebergang_nur_montag_in_wiederholstunde": (
        "Europe/Berlin", [(MONDAY, 0)],
        datetime(2026, 10, 25, 1, 15), datetime(2026, 10, 25, 23, 0),
    ),
    "wochenuebergang_nur_montag_fruehjahr": (
        # Montag 00:00 CEST = Sonntag 22:00 UTC
        "Europe/Berlin", [(MONDAY, 0)],
        datetime(2026, 3, 29, 0, 15), datetime(2026, 3, 29, 22, 0),
    ),
    "wochenuebergang_jetzt_genau_montag": (
        "Europe/Berlin", [(MONDAY, 0)],
        datetime(2026, 10, 25, 23, 0), datetime(2026, 11, 1, 23, 0),
    ),
    "utc_ohne_sommerzeit": (
        "UTC", [(3, 600)],
        datetime(2026, 10, 25, 1, 15), datetime(2026, 10, 28, 10, 0),
    ),
    "kolkata_ohne_sommerzeit": (
        "Asia/Kolkata", [(MONDAY, 360)],
        datetime(2026, 10, 25, 1, 15), datetime(2026, 10, 26, 0, 30),
    ),
    "new_york_wiederholstunde_zweiter_durchlauf": (
        # 01:00-02:00 am 2026-11-01: erst EDT (05:00-06:00 UTC), dann EST (06:00-07:00 UTC)
        "America/New_York", [(SUNDAY, 90)],
        datetime(2026, 11, 1, 6, 15), datetime(2026, 11, 8, 6, 30),
    ),
    "new_york_wiederholstunde_erster_durchlauf": (
        "America/New_York", [(SUNDAY, 90)],
        datetime(2026, 11, 1, 5, 15), datetime(2026, 11, 1, 5, 30),
    ),
    "new_york_fruehjahr_luecke": (
        "America/New_York", [(SUNDAY, 150)],
        datetime(2026, 3, 8, 6, 30), datetime(2026, 3, 8, 7, 0),
    ),
    "lord_howe_wiederholstunde_zweiter_durchlauf": (
        # 30-Minuten-Sprung: 01:30-02:00 doppelt; 14:30-15:00 UTC, dann 15:00-15:30 UTC
        "Australia/Lord_Howe", [(SUNDAY, 105)],
        datetime(2026, 4, 4, 15, 10), datetime(2026, 4, 11, 15, 15),
    ),
    "lord_howe_wiederholstunde_erster_durchlauf": (
        "Australia/Lord_Howe", [(SUNDAY, 105)],
        datetime(2026, 4, 4, 14, 40), datetime(2026, 4, 4, 14, 45),
    ),
    "lord_howe_fruehjahr_luecke": (
        # 02:00-02:30 fällt aus; Umstellung um 15:30 UTC
        "Australia/Lord_Howe", [(SUNDAY, 135)],
        datetime(2026, 10, 3, 15, 10), datetime(2026, 10, 3, 15, 30),
    ),
}


@pytest.mark.parametrize("name", list(CASES))
def test_next_switch_instant_in_the_wall_clock_jumps(session: Session, name: str) -> None:
    timezone_name, points, now, expected = CASES[name]
    zone = _zone_with_minutes(session, timezone_name, points)

    result = end_of_next_switch(session, zone, now)

    assert result == expected
    assert result is not None and result > now


# -- Gegenrechner ------------------------------------------------------------------------


def _wall(moment_utc: datetime, timezone_name: str) -> datetime:
    return moment_utc.replace(tzinfo=UTC).astimezone(ZoneInfo(timezone_name)).replace(tzinfo=None)


def _week_minute(wall: datetime) -> int:
    return (wall.isoweekday() - 1) * 1440 + wall.hour * 60 + wall.minute


def _fired_moments(
    points: list[tuple[int, int]], timezone_name: str, start: datetime, minutes: int
) -> list[datetime]:
    """Läuft Minute für Minute durch die echte Zeit und führt die Plan-Uhr als laufendes
    Maximum der Wanduhr seit Wochenbeginn. Ein Punkt feuert, wenn die Plan-Uhr seine Minute
    erreicht oder überspringt (Vorwärtssprung der Lücke); im zweiten Durchlauf der
    Wiederholstunde steht die Plan-Uhr, dort feuert nichts. Am Wochenwechsel feuern die
    Punkte bis zur neuen Plan-Minute."""
    wanted = {(weekday - 1) * 1440 + minute for weekday, minute in points}
    fired: list[datetime] = []
    week: datetime | None = None
    plan = 0
    for offset in range(minutes):
        moment = start + timedelta(minutes=offset)
        wall = _wall(moment, timezone_name)
        wall_minute = _week_minute(wall)
        wall_week = wall - timedelta(
            days=wall.isoweekday() - 1, hours=wall.hour, minutes=wall.minute
        )
        if week != wall_week:
            low, plan, week = -1, wall_minute, wall_week
        else:
            low, plan = plan, max(plan, wall_minute)
        if offset and any(low < m <= plan for m in wanted):
            fired.append(moment)
    return fired


# Sonntag dicht besetzt: deckt Wiederholstunde und Lücke der drei Zonen ab; dazu Montag 00:00.
DENSE = [
    (SUNDAY, minute) for minute in (30, 70, 90, 105, 110, 130, 135, 150, 170, 190, 240)
] + [(MONDAY, 0)]

WINDOWS = {
    "berlin_herbst": ("Europe/Berlin", datetime(2026, 10, 24, 22, 0), 8),
    "berlin_fruehjahr": ("Europe/Berlin", datetime(2026, 3, 28, 22, 0), 8),
    "new_york_herbst": ("America/New_York", datetime(2026, 11, 1, 3, 0), 7),
    "new_york_fruehjahr": ("America/New_York", datetime(2026, 3, 8, 4, 0), 6),
    "lord_howe_herbst": ("Australia/Lord_Howe", datetime(2026, 4, 4, 12, 0), 6),
    "lord_howe_fruehjahr": ("Australia/Lord_Howe", datetime(2026, 10, 3, 13, 0), 5),
    "kolkata": ("Asia/Kolkata", datetime(2026, 10, 24, 22, 0), 8),
    "utc": ("UTC", datetime(2026, 10, 24, 22, 0), 8),
}


@pytest.mark.parametrize("name", list(WINDOWS))
def test_next_switch_agrees_with_a_minute_by_minute_walk_through_real_time(
    session: Session, name: str
) -> None:
    timezone_name, start, hours = WINDOWS[name]
    zone = _zone_with_minutes(session, timezone_name, DENSE)
    # Der Gegenrechner startet 36 Stunden vor dem Fenster (außerhalb jeder Umstellung), damit
    # das laufende Maximum der Wanduhr an dessen Beginn stimmt, und läuft über zwei Wochen.
    fired = _fired_moments(
        DENSE, timezone_name, start - timedelta(hours=36), (36 + hours) * 60 + 2 * 10080
    )

    moment = start
    checked = 0
    while moment < start + timedelta(hours=hours):
        for now in (moment, moment + timedelta(seconds=30)):
            result = end_of_next_switch(session, zone, now)
            assert result == next(m for m in fired if m > now), (name, now)
            assert result is not None and result > now
            checked += 1
        moment += timedelta(minutes=7)
    assert checked >= 2 * hours * 60 // 7


# -- Ende der Übersteuerung = Moment, ab dem der Zeitplan den neuen Wert liefert -----------


def _schedule_value_at(session: Session, zone: Zone, moment: datetime) -> Decimal:
    return resolved_setpoint(session, zone, moment).temperature_c


def test_the_end_coincides_with_the_schedule_change_in_the_repeated_hour(
    session: Session,
) -> None:
    """02:30 'nacht' (18), 10:00 'tag' (21). Im zweiten Durchlauf (01:15 UTC = 02:15 CET)
    gilt 'nacht' schon (02:30 CEST hat gefeuert) und bleibt; das Ende ist der nächste echte
    Schaltpunkt, 10:00 CET (09:00 UTC), und genau dann liefert der Zeitplan 'tag'."""
    zone = _zone(
        session, "Europe/Berlin", [(SUNDAY, 150, "nacht"), (SUNDAY, 600, "tag")]
    )
    now = datetime(2026, 10, 25, 1, 15)

    end = end_of_next_switch(session, zone, now)

    assert end == datetime(2026, 10, 25, 9, 0)
    assert _schedule_value_at(session, zone, now) == NIGHT_C
    assert _schedule_value_at(session, zone, end - timedelta(seconds=1)) == NIGHT_C
    assert _schedule_value_at(session, zone, end) == DAY_C


def test_the_end_coincides_with_the_schedule_change_in_the_spring_gap(
    session: Session,
) -> None:
    """02:30 existiert am 2026-03-29 nicht. Der Zeitplan liefert den neuen Wert ab dem
    Umstellungsmoment (01:00 UTC, Wanduhr springt auf 03:00) -- nicht eine halbe Stunde
    später, wie es die naive Umrechnung (02:30 CET = 01:30 UTC) ergab."""
    zone = _zone(
        session, "Europe/Berlin", [(SUNDAY, 150, "nacht"), (SUNDAY, 600, "tag")]
    )
    now = datetime(2026, 3, 29, 0, 15)

    end = end_of_next_switch(session, zone, now)

    assert end == datetime(2026, 3, 29, 1, 0)
    assert _schedule_value_at(session, zone, end - timedelta(seconds=1)) == DAY_C
    assert _schedule_value_at(session, zone, end) == NIGHT_C


# -- Ende-zu-Ende: Boost, Sollwertrad, Sprung, Vorschau in der Wiederholstunde -------------


FOLD_NOW = datetime(2026, 10, 25, 1, 15)  # 02:15 CET, zweiter Durchlauf
# Der Punkt 02:30 hat im ersten Durchlauf gefeuert; die nächste Schaltung ist 10:00 CET.
FOLD_NEXT = datetime(2026, 10, 25, 9, 0)


def _fold_zone(session: Session) -> Zone:
    return _zone(session, "Europe/Berlin", [(SUNDAY, 150, "nacht"), (SUNDAY, 600, "tag")])


def _overrides(session: Session, zone: Zone) -> list[ZoneOverride]:
    return list(session.query(ZoneOverride).filter_by(zone_id=zone.id).order_by(ZoneOverride.id))


def test_boost_in_the_repeated_hour_ends_at_the_schedule_point(session: Session) -> None:
    """Im zweiten Durchlauf gilt 'nacht' (18); der Boost zieht die nächste echte Schaltung
    vor -- 'tag' ab 10:00 CET, nicht den Punkt 02:30, der schon gefeuert hat."""
    zone = _fold_zone(session)
    assert _schedule_value_at(session, zone, FOLD_NOW) == NIGHT_C

    result = boost(session, zone, FOLD_NOW, source="system")

    assert result.bis == FOLD_NEXT
    assert result.mode_code == "tag"
    assert result.temperature == DAY_C
    (row,) = _overrides(session, zone)
    assert row.ends_at == FOLD_NEXT and row.starts_at == FOLD_NOW
    assert resolved_setpoint(session, zone, FOLD_NOW).temperature_c == DAY_C
    before_end = resolved_setpoint(session, zone, FOLD_NEXT - timedelta(seconds=1))
    assert before_end.temperature_c == DAY_C and "Übersteuerung" in before_end.reason
    after = resolved_setpoint(session, zone, FOLD_NEXT)
    assert after.temperature_c == DAY_C and "Zeitplan" in after.reason


def test_the_dial_in_the_repeated_hour_ends_at_the_schedule_point_not_at_the_fallback(
    session: Session,
) -> None:
    """Die Ersatzfrist (120 Minuten) ist nur Absicherung. Das Ende muss der nächste echte
    Schaltpunkt sein (in 7 Stunden 45 Minuten, also weit hinter der Ersatzfrist), sonst
    hätte sie die Ursache verdeckt."""
    zone = _fold_zone(session)
    settings = session.get(Setting, 1)
    assert settings is not None
    assert settings.temporary_setpoint_minutes == 120

    result = set_temporary_setpoint(session, zone, Decimal("23.0"), FOLD_NOW, source="system")

    assert result.ends_at == FOLD_NEXT
    assert result.ends_at != FOLD_NOW + timedelta(minutes=settings.temporary_setpoint_minutes)
    assert result.ends_at - FOLD_NOW == timedelta(hours=7, minutes=45)


def test_the_dial_fallback_still_applies_when_the_setting_says_fixed_duration(
    session: Session,
) -> None:
    zone = _fold_zone(session)
    settings = session.get(Setting, 1)
    assert settings is not None
    settings.temporary_setpoint_until_next_switch = 0
    settings.temporary_setpoint_minutes = 45

    result = set_temporary_setpoint(session, zone, Decimal("23.0"), FOLD_NOW, source="system")

    assert result.ends_at == FOLD_NOW + timedelta(minutes=45)


def test_the_dial_in_the_first_pass_ends_at_the_first_occurrence(session: Session) -> None:
    zone = _fold_zone(session)
    now = datetime(2026, 10, 25, 0, 15)

    result = set_temporary_setpoint(session, zone, Decimal("23.0"), now, source="system")

    assert result.ends_at == datetime(2026, 10, 25, 0, 30)


def test_jumping_to_the_next_switch_in_the_repeated_hour(session: Session) -> None:
    zone = _fold_zone(session)

    switch = next_switch(session, zone, FOLD_NOW)
    assert switch is not None
    assert switch.at == FOLD_NEXT
    assert switch.setpoint.temperature_c == DAY_C

    override = jump_to_next_switch(session, zone, now=FOLD_NOW, source="system")
    assert override.ends_at == FOLD_NEXT
    assert override.temperature_c == DAY_C


def test_boost_in_the_spring_gap_ends_when_the_schedule_changes(session: Session) -> None:
    zone = _fold_zone(session)
    now = datetime(2026, 3, 29, 0, 15)

    result = boost(session, zone, now, source="system")

    assert result.bis == datetime(2026, 3, 29, 1, 0)
    assert result.temperature == NIGHT_C
    assert resolved_setpoint(session, zone, result.bis).temperature_c == NIGHT_C


def test_the_forecast_in_the_repeated_hour_has_no_bar_edge_in_the_second_pass(
    session: Session,
) -> None:
    """Im zweiten Durchlauf gibt es keine Schaltung (02:30 hat im ersten gefeuert): 'nacht'
    läuft ohne Balkenkante bis 10:00 CET (09:00 UTC), dann 'tag'. Früher lag dort eine
    Kante um 01:30 UTC mit dem Rücksprung des Sollwerts."""
    zone = _fold_zone(session)

    result = schedule_forecast(session, zone, FOLD_NOW)

    assert [(s.starts_at, s.ends_at, s.setpoint.temperature_c) for s in result] == [
        (FOLD_NOW, FOLD_NEXT, NIGHT_C),
        (FOLD_NEXT, FOLD_NOW + timedelta(hours=24), DAY_C),
    ]


def test_the_forecast_guard_still_terminates_if_the_next_switch_does_not_advance(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Schutzprüfung `next_utc > cursor`: gäbe eine künftige Änderung je einen nicht
    späteren Zeitpunkt zurück, zeichnet die Vorschau einen breiten Balken statt
    unendlich oft denselben Punkt."""
    zone = _fold_zone(session)
    asked: list[datetime] = []

    def stuck(points: object, now: datetime, timezone_name: str | None) -> datetime:
        asked.append(now)
        return now

    monkeypatch.setattr(schedule_module, "next_switch_utc", stuck)

    result = schedule_forecast(session, zone, FOLD_NOW)

    window_end = FOLD_NOW + timedelta(hours=24)
    assert [(s.starts_at, s.ends_at) for s in result] == [(FOLD_NOW, window_end)]
    # Ein einziger Balken bis zum Fensterende: die Schleife fragt einmal und ist fertig.
    # Ohne Schutz drehte sie, bis die Obergrenze der Schaltungen erreicht ist -- das
    # Ergebnis wäre nach dem Zusammenführen gleicher Balken dasselbe, die Aufrufzahl nicht.
    assert len(asked) == 1
