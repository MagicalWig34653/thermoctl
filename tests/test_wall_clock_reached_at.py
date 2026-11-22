"""`wall_clock_reached_at`: die echten Zeitpunkte, zu denen die Wanduhr eine Ortszeit zeigt.

Werte sind von Hand gegen `zoneinfo` gerechnet (Europe/Berlin 2026-10-25 und 2026-03-29,
America/New_York, Australia/Lord_Howe), nicht aus der Umsetzung abgeleitet.
"""

from datetime import datetime, timedelta

import pytest

from thermoctl.domain.time import local_time, wall_clock_reached_at


@pytest.mark.parametrize(
    ("timezone_name", "wall", "expected"),
    [
        # gewöhnliche Zeit: genau ein Zeitpunkt
        ("Europe/Berlin", datetime(2026, 8, 1, 12, 0), (datetime(2026, 8, 1, 10, 0),)),
        ("Europe/Berlin", datetime(2026, 1, 15, 12, 0), (datetime(2026, 1, 15, 11, 0),)),
        ("UTC", datetime(2026, 10, 25, 2, 30), (datetime(2026, 10, 25, 2, 30),)),
        (None, datetime(2026, 10, 25, 2, 30), (datetime(2026, 10, 25, 2, 30),)),
        ("Asia/Kolkata", datetime(2026, 10, 26, 6, 0), (datetime(2026, 10, 26, 0, 30),)),
        # Wiederholstunde: zwei Zeitpunkte, der frühere zuerst
        (
            "Europe/Berlin", datetime(2026, 10, 25, 2, 30),
            (datetime(2026, 10, 25, 0, 30), datetime(2026, 10, 25, 1, 30)),
        ),
        (
            "America/New_York", datetime(2026, 11, 1, 1, 30),
            (datetime(2026, 11, 1, 5, 30), datetime(2026, 11, 1, 6, 30)),
        ),
        (
            "Australia/Lord_Howe", datetime(2026, 4, 5, 1, 45),
            (datetime(2026, 4, 4, 14, 45), datetime(2026, 4, 4, 15, 15)),
        ),
        # Randminuten der Wiederholstunde: 02:00 doppelt, 03:00 nur einmal (nach der Stunde)
        (
            "Europe/Berlin", datetime(2026, 10, 25, 2, 0),
            (datetime(2026, 10, 25, 0, 0), datetime(2026, 10, 25, 1, 0)),
        ),
        ("Europe/Berlin", datetime(2026, 10, 25, 3, 0), (datetime(2026, 10, 25, 2, 0),)),
        # ausgefallene Stunde: der Umstellungsmoment selbst (Wanduhr springt auf >= wall)
        ("Europe/Berlin", datetime(2026, 3, 29, 2, 30), (datetime(2026, 3, 29, 1, 0),)),
        ("Europe/Berlin", datetime(2026, 3, 29, 2, 0), (datetime(2026, 3, 29, 1, 0),)),
        ("Europe/Berlin", datetime(2026, 3, 29, 2, 59), (datetime(2026, 3, 29, 1, 0),)),
        ("Europe/Berlin", datetime(2026, 3, 29, 3, 0), (datetime(2026, 3, 29, 1, 0),)),
        ("America/New_York", datetime(2026, 3, 8, 2, 30), (datetime(2026, 3, 8, 7, 0),)),
        ("Australia/Lord_Howe", datetime(2026, 10, 4, 2, 15), (datetime(2026, 10, 3, 15, 30),)),
    ],
)
def test_wall_clock_reached_at(
    timezone_name: str | None, wall: datetime, expected: tuple[datetime, ...]
) -> None:
    result = wall_clock_reached_at(wall, timezone_name)

    assert result == expected
    # Gegenprobe: zu jedem Ergebnis zeigt die Wanduhr `wall`, in der Lücke (nur ein
    # Ergebnis, das nicht `wall` zeigt) steht sie dahinter.
    for moment in result:
        assert local_time(moment, timezone_name).replace(tzinfo=None) >= wall


def test_in_a_skipped_hour_the_instant_before_the_transition_still_shows_an_earlier_time() -> None:
    """Der Umstellungsmoment ist der *erste* mit Wanduhr >= 02:30, nicht ein späterer."""
    (transition,) = wall_clock_reached_at(datetime(2026, 3, 29, 2, 30), "Europe/Berlin")

    assert local_time(transition - timedelta(seconds=1), "Europe/Berlin").replace(
        tzinfo=None
    ) == datetime(2026, 3, 29, 1, 59, 59)
    assert local_time(transition, "Europe/Berlin").replace(tzinfo=None) == datetime(
        2026, 3, 29, 3, 0
    )
