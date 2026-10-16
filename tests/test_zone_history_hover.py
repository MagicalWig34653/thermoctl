"""Verlauf: Daten für die Anzeige am Mauszeiger und die Seite „Verläufe aller Zonen".

Grundsatz wie beim Diagramm selbst: Die Daten enthalten nur, was protokollierte Zeilen belegen.
Wo keine Zeile liegt, liegt auch kein Eintrag -- die Anzeige sagt dort "keine Daten".
"""

import json
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from tests.helpers import create_shadow_decision
from thermoctl.domain import zone_history_chart as chart_module
from thermoctl.domain.zone_history_chart import Sample, build_chart
from thermoctl.web.navigation import NAVIGATION_ITEMS

BERLIN = "Europe/Berlin"


def _sample(
    at: datetime,
    temperature: float | None = 19.5,
    effective: float | None = 20.5,
    scheduled: float | None = 20.5,
    heat: bool = False,
    solar_k: float | None = 0.0,
) -> Sample:
    return Sample(at, temperature, effective, scheduled, heat, solar_k)


def _payload(
    samples: list[Sample], start: datetime, end: datetime, tz: str | None = BERLIN
) -> dict:
    chart = build_chart(samples, start, end, tz)
    return chart_module.hover_payload(chart, tz)  # type: ignore[return-value]


def _cells(payload: dict) -> list[tuple[int, int, int, int, int, int, int]]:
    """Dieselbe Auflösung wie `parse` in zone_history.js: absolute Sekunden je Stück."""
    flat = payload["c"]
    cells = []
    at = 0
    for i in range(0, len(flat), 7):
        first = at + flat[i]
        end = first + flat[i + 1]
        at = end
        mean = flat[i + 3]
        cells.append(
            (first, end, flat[i + 2], mean, mean + flat[i + 4], mean + flat[i + 5], flat[i + 6])
        )
    return cells


def _covered(payload: dict) -> list[tuple[int, int]]:
    """Zeitspannen, für die es Zeilen gibt: aneinanderstoßende Stücke zusammengefasst."""
    spans: list[tuple[int, int]] = []
    for first, end, *_ in _cells(payload):
        if spans and spans[-1][1] == first:
            spans[-1] = (spans[-1][0], end)
        else:
            spans.append((first, end))
    return spans


def _runs(entries: list[list]) -> list[list]:
    """Läufe wie in der Anzeige ausgebreitet: ein Eintrag je Stück."""
    spread: list[list] = []
    for run in entries:
        spread += [run] * run[0]
    return spread


# --- Datenformat ---------------------------------------------------------------------


def test_a_gap_leaves_no_cell_and_so_no_value_to_show() -> None:
    start = datetime(2026, 10, 9, 4)
    before = [_sample(start + timedelta(minutes=i)) for i in range(5)]
    after = [_sample(start + timedelta(minutes=40 + i)) for i in range(5)]
    payload = _payload(before + after, start, start + timedelta(hours=24))
    # Zwei Spannen, dazwischen 36 Minuten, in denen es keinen Eintrag gibt: dort zeigt die
    # Anzeige "keine Daten", sie behauptet weder den letzten noch einen mittleren Wert. Eine
    # Spanne endet 90 Sekunden nach ihrer letzten Zeile (Zyklus), nicht erst bei der nächsten.
    assert _covered(payload) == [(0, 4 * 60 + 90), (40 * 60, 44 * 60 + 90)]


def test_the_time_after_the_last_row_belongs_to_no_cell() -> None:
    start = datetime(2026, 10, 9, 4)
    samples = [_sample(start + timedelta(minutes=i)) for i in range(10)]
    payload = _payload(samples, start, start + timedelta(hours=24))
    ((first, end),) = _covered(payload)
    assert first == 0 and end == 9 * 60 + 90
    assert payload["e"] == 24 * 3600
    assert end < payload["e"]


def test_rows_from_before_the_recording_are_unknown_not_zero() -> None:
    start = datetime(2026, 10, 9, 4)
    old = [_sample(start + timedelta(minutes=i), scheduled=None, solar_k=None) for i in range(3)]
    new = [_sample(start + timedelta(minutes=3 + i), solar_k=2.0, effective=18.5) for i in range(3)]
    payload = _payload(old + new, start, start + timedelta(hours=24))
    scheduled = _runs(payload["sc"])
    solar = _runs(payload["so"])
    # Das erste Stück (nur Altzeilen) kennt weder den Sollwert vor der Absenkung noch die
    # Absenkung -- weder
    # "gleich dem wirksamen Soll" noch "keine Absenkung".
    assert scheduled[0][1] is None and solar[0][1] is None
    # Die Stücke danach tragen belegte Werte: Sollwert vor der Absenkung 20,5 und Absenkung 2,0 K.
    assert scheduled[-1][1:3] == [205, 205] and solar[-1][1:3] == [20, 20]
    assert all(run[1] is not None for run in solar[1:])


def test_a_cell_with_only_old_rows_is_marked_unknown_everywhere() -> None:
    start = datetime(2026, 10, 9, 4)
    old = [_sample(start + timedelta(minutes=i), scheduled=None, solar_k=None) for i in range(3)]
    payload = _payload(old, start, start + timedelta(hours=24))
    assert _runs(payload["sc"]) == [[1, None, None, 1]]
    assert _runs(payload["so"]) == [[1, None, None, 1]]
    assert _runs(payload["ef"]) == [[1, 205, 205, 0]]


def test_a_dense_period_gives_mean_minimum_and_maximum_per_section() -> None:
    start = datetime(2026, 10, 2, 4)
    temperatures = [19.0, 20.0, 21.0, 20.0]
    samples = [
        _sample(start + timedelta(minutes=i), temperature=t) for i, t in enumerate(temperatures)
    ]
    payload = _payload(samples, start, start + timedelta(days=7))
    assert payload["b"] == "17 Minuten"
    ((first, end, count, mean, low, high, heat),) = _cells(payload)
    assert (count, mean, low, high, heat) == (4, 200, 190, 210, 0)
    assert first == 0 and end == 3 * 60 + 90


def test_the_heat_share_is_weighted_by_seconds_and_never_rounds_to_all_or_nothing() -> None:
    start = datetime(2026, 10, 9, 4)
    # eine Sekunde Anforderung vor neunzig Sekunden ohne: ein Anteil, nicht "nein"
    samples = [_sample(start, heat=True), _sample(start + timedelta(seconds=1), heat=False)]
    payload = _payload(
        samples + [_sample(start + timedelta(seconds=91))], start, start + timedelta(hours=1)
    )
    assert _cells(payload)[0][6] == 1
    always = _payload(
        [_sample(start + timedelta(minutes=i), heat=True) for i in range(3)],
        start,
        start + timedelta(hours=1),
    )
    never = _payload(
        [_sample(start + timedelta(minutes=i)) for i in range(3)], start, start + timedelta(hours=1)
    )
    assert _cells(always)[0][6] == 100 and _cells(never)[0][6] == 0


def test_a_row_without_temperature_still_covers_time_but_shows_no_measurement() -> None:
    start = datetime(2026, 10, 9, 4)
    payload = _payload([_sample(start, temperature=None)], start, start + timedelta(hours=24))
    ((first, end, count, mean, low, high, _heat),) = _cells(payload)
    assert (count, mean, low, high) == (0, 0, 0, 0)
    assert end > first


def test_the_payload_carries_the_start_as_utc_and_the_configured_zone_across_a_clock_change() -> (
    None
):
    start = datetime(2026, 10, 24, 20)  # UTC; die Umstellung auf Winterzeit liegt im Fenster
    end = start + timedelta(hours=24)
    payload = _payload([_sample(start + timedelta(hours=3))], start, end)
    assert payload["tz"] == BERLIN
    assert payload["s"] == int(start.replace(tzinfo=UTC).timestamp())
    assert payload["e"] == 24 * 3600
    assert _payload([_sample(start)], start, end, None)["tz"] == "UTC"


def test_the_payload_stays_compact_for_a_busy_week() -> None:
    start = datetime(2026, 10, 2, 4)
    samples = [
        _sample(start + timedelta(seconds=60 * i), temperature=20 + (i % 7) / 10)
        for i in range(60 * 24 * 7)
    ]
    text = json.dumps(_payload(samples, start, start + timedelta(days=7)), separators=(",", ":"))
    assert len(text) < 20_000




# --- Seiten --------------------------------------------------------------------------


def _fill(session: Session, zone, minutes: int = 30) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    for index in range(minutes):
        row = create_shadow_decision(session, zone)
        row.decided_at = now - timedelta(minutes=minutes - index)
        row.temperature_c = Decimal("19.5")
        row.setpoint_c = Decimal("18.5")
        row.scheduled_setpoint_c = Decimal("20.5")
        row.solar_setback_k = Decimal("2.0")
    session.flush()


def _tile_ids(html: str) -> list[int]:
    return [int(value) for value in re.findall(r'id="zone-history-(\d+)"', html)]
























def test_the_navigation_entry_is_bound_to_the_right_the_view_checks() -> None:
    (entry,) = [item for item in NAVIGATION_ITEMS if item.path == "/zones/history"]
    assert (entry.permission, entry.scope, entry.section) == ("zone.manage", "any_zone", "analysis")




