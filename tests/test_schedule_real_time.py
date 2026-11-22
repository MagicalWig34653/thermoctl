"""Der Zeitplan läuft über echte Zeit: in der Wiederholstunde der Zeitumstellung springt
der Sollwert nicht zurück, und jede Schaltung feuert genau einmal.

Die Wanduhr springt im Herbst zurück (Europe/Berlin 2026-10-25: 02:00-03:00 Ortszeit
gibt es zweimal, 00:00-01:00 UTC und 01:00-02:00 UTC). Ein Zeitplanpunkt "Sonntag 02:30"
gilt ab 00:30 UTC; stünde im zweiten Durchlauf die Wanduhr wieder auf 02:00, lieferte
`resolved_setpoint` wieder den Vorgänger -- der Sollwert wäre zurückgesprungen. Festgelegt
(Entscheidung des Projektinhabers, "Plan über echte Zeit führen"): Der Plan wird gegen eine
monotone Plan-Uhr ausgewertet, die im zweiten Durchlauf auf dem letzten Wanduhrwert des
ersten Durchlaufs stehen bleibt (`time.schedule_clock`).

Dieser Teil der Datei enthält die Reproduktion an der Zeitumstellung selbst: Berlin, New
York und Lord Howe, dazu Boost, Sollwertrad und Vorschau im zweiten Durchlauf.
Feste Zeiten per `now`, nie die echte Uhr.
"""

from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from tests.helpers import create_mode, create_settings, create_zone, source
from thermoctl.db.models.override import ZoneOverride
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.zone import Zone, ZoneSetpoint
from thermoctl.domain.remote_control import boost, set_temporary_setpoint
from thermoctl.domain.schedule import (
    end_of_next_switch,
    next_switch,
    resolved_setpoint,
    schedule_forecast,
)

SUNDAY = 7
NIGHT_C = Decimal("18.0")
A_C = Decimal("21.0")
B_C = Decimal("23.0")


def _three_mode_zone(
    session: Session, timezone_name: str, a_minute: int, b_minute: int
) -> Zone:
    """Sonntag: Nacht 18 °C ab 00:00, A 21 °C ab `a_minute`, B 23 °C ab `b_minute`."""
    create_settings(session).timezone = timezone_name
    source(session, "system")
    zone = create_zone(session, "echtezeit")
    modes = [create_mode(session, code) for code in ("nacht", "a", "b")]
    for mode, temperature in zip(modes, (NIGHT_C, A_C, B_C), strict=True):
        session.add(
            ZoneSetpoint(zone_id=zone.id, setpoint_mode_id=mode.id, temperature_c=temperature)
        )
    for mode, minute in zip(modes, (0, a_minute, b_minute), strict=True):
        session.add(
            SchedulePoint(
                zone_id=zone.id, weekday=SUNDAY, minute_of_day=minute, setpoint_mode_id=mode.id
            )
        )
    session.flush()
    return zone


def _value(session: Session, zone: Zone, moment: datetime) -> Decimal:
    return resolved_setpoint(session, zone, moment).temperature_c


# (Zone, Minute A, Minute B, [(echter Zeitpunkt UTC, erwarteter Sollwert)]) -- von Hand
# gegen zoneinfo gerechnet. Jeweils: Zeitpunkt im ersten Durchlauf VOR dem Wechsel zu B,
# dann die Zeitpunkte, an denen die Wanduhr zurückgesprungen ist und heute den Vorgänger
# lieferte.
REPEATED_HOUR = {
    "berlin": (
        "Europe/Berlin", 130, 150,  # A ab 02:10, B ab 02:30; Wiederholstunde 00:00-02:00 UTC
        [
            (datetime(2026, 10, 25, 0, 5), NIGHT_C),    # 02:05 CEST
            (datetime(2026, 10, 25, 0, 15), A_C),       # 02:15 CEST
            (datetime(2026, 10, 25, 0, 35), B_C),       # 02:35 CEST
            (datetime(2026, 10, 25, 0, 59, 59), B_C),   # 02:59:59 CEST
            (datetime(2026, 10, 25, 1, 0), B_C),        # 02:00 CET, heute: Nacht
            (datetime(2026, 10, 25, 1, 10), B_C),       # 02:10 CET, heute: A
            (datetime(2026, 10, 25, 1, 30), B_C),       # 02:30 CET
            (datetime(2026, 10, 25, 1, 59, 59), B_C),
        ],
    ),
    "new_york": (
        # 01:00-02:00 Ortszeit doppelt: 05:00-06:00 UTC (EDT), 06:00-07:00 UTC (EST)
        "America/New_York", 70, 90,
        [
            (datetime(2026, 11, 1, 5, 5), NIGHT_C),
            (datetime(2026, 11, 1, 5, 15), A_C),
            (datetime(2026, 11, 1, 5, 35), B_C),
            (datetime(2026, 11, 1, 6, 0), B_C),         # 01:00 EST, heute: Nacht
            (datetime(2026, 11, 1, 6, 10), B_C),        # 01:10 EST, heute: A
            (datetime(2026, 11, 1, 6, 30), B_C),
        ],
    ),
    "lord_howe": (
        # 30-Minuten-Sprung: 01:30-02:00 doppelt, 14:30-15:00 UTC, dann 15:00-15:30 UTC
        "Australia/Lord_Howe", 95, 110,
        [
            (datetime(2026, 4, 4, 14, 32), NIGHT_C),
            (datetime(2026, 4, 4, 14, 36), A_C),
            (datetime(2026, 4, 4, 14, 55), B_C),
            (datetime(2026, 4, 4, 15, 0), B_C),         # 01:30 LHST, heute: Nacht
            (datetime(2026, 4, 4, 15, 10), B_C),        # 01:40 LHST, heute: A
            (datetime(2026, 4, 4, 15, 20), B_C),
        ],
    ),
}


@pytest.mark.parametrize("name", list(REPEATED_HOUR))
def test_the_setpoint_never_steps_back_in_the_repeated_hour(
    session: Session, name: str
) -> None:
    timezone_name, a_minute, b_minute, expected = REPEATED_HOUR[name]
    zone = _three_mode_zone(session, timezone_name, a_minute, b_minute)

    seen = [(moment, _value(session, zone, moment)) for moment, _ in expected]

    assert seen == expected


# -- Ende-zu-Ende im zweiten Durchlauf ------------------------------------------------------

FOLD_NOW = datetime(2026, 10, 25, 1, 15)  # 02:15 CET, zweiter Durchlauf
NEXT_POINT_END = datetime(2026, 10, 25, 9, 0)  # Sonntag 10:00 CET


def _plan_with_a_point_in_the_repeated_hour(session: Session) -> Zone:
    """Sonntag: Nacht ab 00:00, A ab 02:30 (in der Wiederholstunde), B ab 10:00."""
    return _three_mode_zone(session, "Europe/Berlin", 150, 600)


def test_boost_in_the_second_pass_brings_forward_the_point_after_the_repeated_hour(
    session: Session,
) -> None:
    """02:30 hat in der Wiederholstunde schon gegolten (A, ab 00:30 UTC); 'nächste
    Schaltung' ist der Punkt NACH der Wiederholstunde (10:00 CET = 09:00 UTC)."""
    zone = _plan_with_a_point_in_the_repeated_hour(session)

    result = boost(session, zone, FOLD_NOW, source="system")

    assert result.bis == NEXT_POINT_END
    assert result.mode_code == "b"
    assert result.temperature == B_C
    (row,) = session.query(ZoneOverride).filter_by(zone_id=zone.id).all()
    assert row.ends_at == NEXT_POINT_END


def test_the_dial_in_the_second_pass_ends_at_the_next_real_switch(session: Session) -> None:
    zone = _plan_with_a_point_in_the_repeated_hour(session)

    result = set_temporary_setpoint(session, zone, Decimal("25.0"), FOLD_NOW, source="system")

    assert result.ends_at == NEXT_POINT_END
    assert _value(session, zone, FOLD_NOW) == Decimal("25.0")
    assert _value(session, zone, NEXT_POINT_END - timedelta(seconds=1)) == Decimal("25.0")
    assert _value(session, zone, NEXT_POINT_END) == B_C


def test_the_next_switch_in_the_second_pass_is_not_a_switch_that_already_fired(
    session: Session,
) -> None:
    zone = _plan_with_a_point_in_the_repeated_hour(session)

    switch = next_switch(session, zone, FOLD_NOW)

    assert switch is not None
    assert switch.at == NEXT_POINT_END
    assert switch.setpoint.temperature_c == B_C
    assert end_of_next_switch(session, zone, FOLD_NOW) == NEXT_POINT_END


def test_the_forecast_has_one_bar_per_real_switch_and_none_steps_back(
    session: Session,
) -> None:
    """Nacht 02:05 CEST, A ab 02:10 CEST (00:10 UTC), B ab 02:30 CEST (00:30 UTC). Heute
    zeichnet die Vorschau ab 01:10 UTC einen Balken 'A' und dann wieder 'B': den
    Rücksprung. Jetzt: genau ein Balken je echter Schaltung."""
    zone = _three_mode_zone(session, "Europe/Berlin", 130, 150)
    now = datetime(2026, 10, 25, 0, 5)

    result = schedule_forecast(session, zone, now)

    assert [(s.starts_at, s.ends_at, s.setpoint.temperature_c) for s in result] == [
        (now, datetime(2026, 10, 25, 0, 10), NIGHT_C),
        (datetime(2026, 10, 25, 0, 10), datetime(2026, 10, 25, 0, 30), A_C),
        (datetime(2026, 10, 25, 0, 30), now + timedelta(hours=24), B_C),
    ]
