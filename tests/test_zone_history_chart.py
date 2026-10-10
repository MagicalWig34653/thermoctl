"""Verlauf: Verdichtung, Beschriftung, Lücken, Zeitzone, Altzeilen und Berechtigung."""

import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.helpers import create_settings, create_shadow_decision, create_zone
from thermoctl.db.models.operations import Setting
from thermoctl.domain.zone_history_chart import (
    DESKTOP,
    LAYOUTS,
    MOBILE,
    Sample,
    build_chart,
    chart_for_zone,
    line_path,
    solar_bands_for,
    solar_marks_for,
    step_path,
    tick_x,
    ticks_for,
    x_position,
    y_position,
)

BERLIN = "Europe/Berlin"


def _sample(
    at: datetime,
    temperature: float | None = 19.5,
    effective: float | None = 20.5,
    scheduled: float | None = 20.5,
    heat: bool = False,
    solar_k: float = 0.0,
) -> Sample:
    return Sample(at, temperature, effective, scheduled, heat, solar_k)


# --- Sollwertstufen -------------------------------------------------------------------


def test_setpoint_runs_keep_exact_change_times_and_are_not_bucketed() -> None:
    start = datetime(2026, 10, 9, 4)
    samples = [
        _sample(
            start + timedelta(seconds=i * 37),
            effective=18.5 if i >= 10 else 20.5,
            solar_k=2.0 if i >= 10 else 0.0,
        )
        for i in range(30)
    ]
    chart = build_chart(samples, start, start + timedelta(hours=24), BERLIN)
    change = start + timedelta(seconds=370)
    # Der Wechsel liegt auf dem Zeitpunkt der ersten Zeile mit neuem Wert und berührt
    # den vorigen Lauf: eine senkrechte Kante, keine Schräge, kein Bucket-Rand.
    assert chart.effective_runs == (
        (start, change, 20.5),
        (change, samples[-1].at, 18.5),
    )
    assert chart.scheduled_runs == ((start, samples[-1].at, 20.5),)


def test_a_short_setpoint_inside_one_bucket_stays_inside_the_value_range() -> None:
    # 7 Tage: ein Bucket ist knapp 17 Minuten breit. Der Sollwert 14 liegt nur in einer
    # Zeile und ist nie die letzte eines Buckets -- er darf trotzdem nicht aus dem Bild laufen.
    start = datetime(2026, 10, 2, 4)
    samples = [_sample(start + timedelta(seconds=60 * i), temperature=20.0) for i in range(40)]
    samples[10] = _sample(samples[10].at, temperature=20.0, effective=14.0)
    chart = build_chart(samples, start, start + timedelta(days=7), BERLIN)
    assert chart.minimum <= 14.0
    for layout in LAYOUTS:
        for _, _, value in chart.effective_runs:
            assert layout.top <= y_position(value, chart, layout) <= layout.bottom


def test_missing_data_breaks_lines_and_steps_instead_of_bridging_the_hole() -> None:
    start = datetime(2026, 10, 9, 0)
    first = [_sample(start + timedelta(minutes=i)) for i in range(5)]
    second = [_sample(start + timedelta(hours=3, minutes=i)) for i in range(5)]
    chart = build_chart(first + second, start, start + timedelta(hours=6), BERLIN)
    # Gleicher Sollwert vor und nach dem Ausfall: zwei getrennte Läufe.
    assert len(chart.effective_runs) == 2
    for layout in LAYOUTS:
        assert line_path(chart, layout).count("M") == 2
        assert step_path(chart.effective_runs, chart, layout).count("M") == 2
        assert " V" not in step_path(chart.effective_runs, chart, layout)


def test_setpoint_lines_end_at_the_last_value_not_at_the_right_edge() -> None:
    start = datetime(2026, 10, 9, 0)
    samples = [_sample(start + timedelta(minutes=i)) for i in range(60)]
    chart = build_chart(samples, start, start + timedelta(hours=24), BERLIN)
    assert chart.stale
    last_x = x_position(samples[-1].at, chart, DESKTOP)
    assert step_path(chart.effective_runs, chart, DESKTOP).endswith(f"H{last_x:.1f}")
    assert last_x < DESKTOP.left + 0.05 * (DESKTOP.right - DESKTOP.left)


def test_a_missing_setpoint_ends_its_line_and_leaves_the_measured_curve_alone() -> None:
    start = datetime(2026, 10, 9, 4)
    samples = [
        _sample(start),
        _sample(start + timedelta(minutes=1), effective=None, scheduled=None),
        _sample(start + timedelta(minutes=2), effective=19.0, scheduled=19.0),
    ]
    chart = build_chart(samples, start, start + timedelta(hours=1), BERLIN)
    assert [(a, b, v) for a, b, v in chart.effective_runs] == [
        (start, start, 20.5),
        (samples[2].at, samples[2].at, 19.0),
    ]
    assert line_path(chart, DESKTOP).count("M") == 1


def test_single_samples_still_draw_a_visible_stroke() -> None:
    start = datetime(2026, 10, 9, 4)
    chart = build_chart([_sample(start)], start, start + timedelta(hours=1), BERLIN)
    assert re.fullmatch(r"M[\d.]+,[\d.]+ L[\d.]+,[\d.]+", line_path(chart, DESKTOP))
    assert re.fullmatch(r"M[\d.]+,[\d.]+ H[\d.]+", step_path(chart.scheduled_runs, chart, DESKTOP))


def test_charts_without_setpoints_draw_no_step_lines() -> None:
    start = datetime(2026, 10, 9, 4)
    chart = build_chart(
        [_sample(start, effective=None, scheduled=None)], start, start + timedelta(hours=1), BERLIN
    )
    assert step_path(chart.effective_runs, chart, DESKTOP) == ""
    assert step_path(chart.scheduled_runs, chart, MOBILE) == ""
    assert "Zuletzt Zeitplan-Soll" not in chart.summary


# --- Sonnenabsenkung und Heizspur -----------------------------------------------------


def test_solar_band_has_exact_edges_and_the_largest_setback() -> None:
    start = datetime(2026, 10, 9, 4)
    samples = [
        _sample(start + timedelta(seconds=60 * i), solar_k=(2.0 if 20 <= i < 40 else 0.0))
        for i in range(60)
    ]
    samples[30] = _sample(samples[30].at, solar_k=3.5)
    chart = build_chart(samples, start, start + timedelta(hours=24), BERLIN)
    # Ein Band, solange die Absenkung steht; der Betrag wechselt von 2,0 auf 3,5 K, also
    # trägt es den Bereich, nicht nur den Höchstwert.
    assert chart.solar_bands == (
        (samples[20].at, samples[39].at + timedelta(seconds=60), 2.0, 3.5),
    )


def test_a_one_cycle_setback_survives_seven_days_of_compression() -> None:
    start = datetime(2026, 10, 2, 4)
    samples = [
        _sample(
            start + timedelta(seconds=37 * index),
            temperature=19.5 + (index % 4) / 10,
            effective=18.5 if index >= 5000 else 20.5,
            heat=index % 2 == 0,
            solar_k=2.0 if index == 5000 else 0.0,
        )
        for index in range(16000)
    ]
    chart = build_chart(samples, start, start + timedelta(days=7), BERLIN)
    assert len(chart.points) <= 600
    assert len(chart.heat_lane) <= 600
    assert len(chart.solar_bands) == 1
    first, last, low, high = chart.solar_bands[0]
    assert first == samples[5000].at and last == samples[5000].at + timedelta(seconds=37)
    assert low == high == 2.0
    assert chart.effective_runs[1][0] == samples[5000].at


def test_flickering_setback_stays_exact_and_the_drawing_stays_bounded() -> None:
    start = datetime(2026, 10, 2, 4)
    samples = [
        _sample(start + timedelta(seconds=60 * i), solar_k=(1.0 if i % 2 == 0 else 0.0))
        for i in range(9000)
    ]
    chart = build_chart(samples, start, start + timedelta(days=7), BERLIN)
    # Jede Zeile mit Absenkung ist ein eigenes Band von 60 Sekunden: nichts wird überbrückt.
    assert len(chart.solar_bands) == 4500
    assert all((b - a).total_seconds() == 60 for a, b, *_ in chart.solar_bands)
    for layout in LAYOUTS:
        # Auf der Zeichnung ist ein Band von 60 s kein Bildpunkt: nur Markierungen, höchstens
        # eine je Bildpunkt, und keine Fläche.
        assert solar_bands_for(chart, layout) == ()
        assert len(solar_marks_for(chart, layout)) <= layout.right - layout.left


def test_heat_lane_colours_the_time_of_the_heating_rows_and_merges_touching_equal_levels() -> None:
    start = datetime(2026, 10, 9, 0)
    # Je Bucket (144 s) eine Zeile; ein Zyklus gilt höchstens 90 s, die Spur ebenso.
    samples = [
        _sample(start + timedelta(seconds=144 * i + 1), heat=(100 <= i < 110)) for i in range(600)
    ]
    chart = build_chart(samples, start, start + timedelta(hours=24), BERLIN)
    assert chart.heat_lane == tuple(
        (
            start + timedelta(seconds=144 * i + 1),
            start + timedelta(seconds=144 * i + 1 + 90),
            4,
        )
        for i in range(100, 110)
    )
    # Lückenlos aufeinanderfolgende Zeilen gleicher Stufe ergeben eine einzige Zelle.
    dense = [_sample(start + timedelta(seconds=72 * i), heat=(100 <= i < 110)) for i in range(300)]
    lane = build_chart(dense, start, start + timedelta(hours=24), BERLIN).heat_lane
    assert lane == ((start + timedelta(seconds=7200), start + timedelta(seconds=7920), 4),)
    # Ein einzelner Heizzyklus unter vielen bleibt sichtbar (Stufe 1), verschwindet nicht.
    mixed = [_sample(start + timedelta(seconds=36 * i), heat=(i == 7)) for i in range(2400)]
    lane = build_chart(mixed, start, start + timedelta(hours=24), BERLIN).heat_lane
    assert len(lane) == 1 and lane[0][2] == 1


# --- Beschriftung ---------------------------------------------------------------------


def _labels(chart_ticks: tuple[tuple[float, str], ...]) -> list[str]:
    return [label for _, label in chart_ticks]


def _chart_at(start: datetime, end: datetime, zone: str | None):
    return build_chart([_sample(end)], start, end, zone)


def test_ticks_lie_on_round_local_hours_with_the_date_at_midnight() -> None:
    now = datetime(2026, 10, 9, 18, 53)  # 20:53 Ortszeit -- nicht rund, die Marken schon
    chart = _chart_at(now - timedelta(hours=24), now, BERLIN)
    assert _labels(chart.ticks) == [
        "21:00", "Fr 09.10.", "03:00", "06:00", "09:00", "12:00", "15:00", "18:00",
    ]  # fmt: skip
    # 21:00 Ortszeit = 19:00 UTC, also 6 Minuten nach dem Fensterbeginn.
    assert round(chart.ticks[0][0] * 24 * 60) == 7
    assert [round(f * 24, 2) for f, _ in chart.ticks[1:3]] == [3.12, 6.12]
    assert chart.sparse_ticks == chart.ticks[::2]
    assert ticks_for(chart, DESKTOP) is chart.ticks
    assert ticks_for(chart, MOBILE) is chart.sparse_ticks
    assert _labels(chart.sparse_ticks) == ["21:00", "03:00", "09:00", "15:00"]


def test_tick_labels_follow_the_configured_timezone_and_default_to_utc() -> None:
    now = datetime(2026, 10, 9, 18, 53)
    start = now - timedelta(hours=24)
    assert _labels(_chart_at(start, now, "America/New_York").ticks) == [
        "15:00", "18:00", "21:00", "Fr 09.10.", "03:00", "06:00", "09:00", "12:00",
    ]  # fmt: skip
    assert _labels(_chart_at(start, now, None).ticks) == [
        "21:00", "Fr 09.10.", "03:00", "06:00", "09:00", "12:00", "15:00", "18:00",
    ]  # fmt: skip
    # Dieselbe Mitternacht liegt in Berlin und in UTC an verschiedenen Stellen der Achse.
    berlin = dict((label, f) for f, label in _chart_at(start, now, BERLIN).ticks)
    utc = dict((label, f) for f, label in _chart_at(start, now, None).ticks)
    assert round((utc["Fr 09.10."] - berlin["Fr 09.10."]) * 24, 2) == 2.0


def test_seven_days_are_labelled_per_day_and_three_days_per_half_day() -> None:
    now = datetime(2026, 10, 9, 18, 53)
    assert _labels(_chart_at(now - timedelta(days=7), now, BERLIN).ticks) == [
        "Sa 03.10.", "So 04.10.", "Mo 05.10.", "Di 06.10.", "Mi 07.10.", "Do 08.10.", "Fr 09.10.",
    ]  # fmt: skip
    assert _labels(_chart_at(now - timedelta(days=3), now, BERLIN).ticks) == [
        "Mi 07.10.", "12:00", "Do 08.10.", "12:00", "Fr 09.10.", "12:00",
    ]  # fmt: skip


def test_ticks_keep_their_real_spacing_across_both_clock_changes() -> None:
    end = datetime(2026, 10, 25, 20)  # Winterzeit beginnt: der Tag hat 25 Stunden
    fall = _chart_at(end - timedelta(days=1), end, BERLIN)
    assert _labels(fall.ticks) == [
        "So 25.10.", "03:00", "06:00", "09:00", "12:00", "15:00", "18:00", "21:00",
    ]  # fmt: skip
    # 00:00 -> 03:00 sind an diesem Tag vier echte Stunden, nicht drei.
    assert round((fall.ticks[1][0] - fall.ticks[0][0]) * 24, 2) == 4.0
    end = datetime(2026, 3, 29, 20)  # Sommerzeit beginnt: der Tag hat 23 Stunden
    spring = _chart_at(end - timedelta(days=1), end, BERLIN)
    assert round((spring.ticks[2][0] - spring.ticks[1][0]) * 24, 2) == 2.0  # 00:00 -> 03:00
    for chart in (fall, spring):
        fractions = [f for f, _ in chart.ticks]
        assert fractions == sorted(set(fractions)) and 0 <= fractions[0] and fractions[-1] <= 1


def test_short_windows_get_hourly_ticks() -> None:
    start = datetime(2026, 10, 25, 0)  # 02:00 Sommerzeit; die Uhr springt um 03:00 zurück
    chart = _chart_at(start, start + timedelta(hours=4), BERLIN)
    assert _labels(chart.ticks) == ["02:00", "03:00", "04:00", "05:00"]
    assert [f for f, _ in chart.ticks] == [0.0, 0.5, 0.75, 1.0]


def test_axis_positions_stay_inside_every_layout() -> None:
    start = datetime(2026, 10, 9, 0)
    chart = build_chart(
        [_sample(start + timedelta(hours=1))], start, start + timedelta(hours=24), BERLIN
    )
    for layout in LAYOUTS:
        assert layout.left <= tick_x(0.0, layout) < tick_x(1.0, layout) == layout.right
        assert x_position(chart.start, chart, layout) == layout.left
        assert x_position(chart.end, chart, layout) == layout.right
        assert y_position(chart.minimum, chart, layout) == layout.bottom
        assert y_position(chart.maximum, chart, layout) == layout.top
        assert layout.bottom < layout.lane_top < layout.lane_bottom < layout.label_y < layout.height


# --- Text ----------------------------------------------------------------------------


def test_summary_uses_german_numbers_and_reports_coverage_and_staleness() -> None:
    start = datetime(2026, 10, 9, 0)
    samples = [
        _sample(
            start + timedelta(seconds=60 * i),
            temperature=19.5,
            effective=18.5,
            scheduled=20.5,
            heat=(i % 2 == 0),
            solar_k=2.0,
        )
        for i in range(120)
    ]
    chart = build_chart(samples, start, start + timedelta(hours=24), BERLIN)
    assert "Ist 19,5 °C bis 19,5 °C." in chart.summary
    assert "Zeitplan-Soll 20,5 °C, wirksamer Soll 18,5 °C" in chart.summary
    assert "Heizanforderung 50 % der erfassten Zeit." in chart.summary
    assert "Sonnenabsenkung 2,0 Stunden." in chart.summary
    assert "Daten für 2,0 Stunden von 24,0 Stunden." in chart.summary
    assert "Letzter Wert 09.10. 03:59 Uhr." in chart.summary  # 01:59 UTC = 03:59 Ortszeit


def test_summary_without_setback_and_without_measurement_and_without_data() -> None:
    start = datetime(2026, 10, 9, 0)
    end = start + timedelta(minutes=10)
    full = [_sample(start + timedelta(seconds=30 * i), temperature=None) for i in range(20)]
    text = build_chart(full, start, end, BERLIN).summary
    assert text.startswith("Kein Istwert.")
    assert (
        "Keine Sonnenabsenkung." in text and "Daten für" not in text and "Letzter Wert" not in text
    )
    assert (
        build_chart([], start, end, BERLIN).summary == "Keine Verlaufsdaten im gewählten Zeitraum."
    )


# --- Altzeilen, Datenbank -------------------------------------------------------------


def test_old_null_columns_keep_the_effective_value_and_leave_the_rest_unknown(
    session: Session,
) -> None:
    zone = create_zone(session, "altverlauf")
    row = create_shadow_decision(session, zone)
    row.decided_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=5)
    row.temperature_c = Decimal("19.5")
    row.setpoint_c = Decimal("20.5")
    session.flush()
    chart = chart_for_zone(session, zone.id, "24h", datetime.now(UTC).replace(tzinfo=None), BERLIN)
    assert [v for _, _, v in chart.effective_runs] == [20.5]
    assert chart.scheduled_runs == ()
    assert chart.scheduled_text == "unbekannt" and chart.effective_text == "20,5 °C"
    assert not chart.solar_bands and len(chart.unknown_bands) == 1


def test_database_rows_become_exact_runs_and_a_solar_band(session: Session) -> None:
    zone = create_zone(session, "dbverlauf")
    other = create_zone(session, "andereszone")
    now = datetime(2026, 10, 9, 12)
    for minutes, setpoint, solar in ((90, "20.5", None), (60, "18.5", "2.0"), (30, "20.5", "0.0")):
        row = create_shadow_decision(session, zone)
        row.decided_at = now - timedelta(minutes=minutes)
        row.temperature_c = Decimal("19.5")
        row.setpoint_c = Decimal(setpoint)
        row.scheduled_setpoint_c = Decimal("20.5")
        row.solar_setback_k = Decimal(solar) if solar is not None else None
    stranger = create_shadow_decision(session, other)
    stranger.decided_at = now - timedelta(minutes=45)
    stranger.setpoint_c = Decimal("5.0")
    session.flush()
    chart = chart_for_zone(session, zone.id, "24h", now, BERLIN)
    assert [v for _, _, v in chart.effective_runs] == [20.5, 18.5, 20.5]
    assert len(chart.solar_bands) == 1 and chart.solar_bands[0][2] == 2.0
    assert 5.0 not in [v for _, _, v in chart.effective_runs]


# --- Adapter --------------------------------------------------------------------------


def test_partial_route_and_zone_isolation(
    client_als, tenant_client, client: TestClient, session: Session
) -> None:
    own = create_zone(session, "eigenerverlauf")
    foreign = create_zone(session, "fremderverlauf")
    anonymous = client.get(f"/zones/{own.id}/history", follow_redirects=False)
    assert anonymous.status_code in (303, 401) and "zone-history" not in anonymous.text
    user = client_als([("zone.manage", own.id)])
    response = user.get(f"/zones/{own.id}/history?period=3d", headers={"HX-Request": "true"})
    assert response.status_code == 200
    assert 'id="zone-history"' in response.text
    assert "Keine Verlaufsdaten" in response.text
    assert "<html" not in response.text
    assert user.get(f"/zones/{foreign.id}/history").status_code == 404
    # Auch die ungültige Eingabe verrät nichts über eine fremde Zone: erst Recht, dann Zeitraum.
    assert user.get(f"/zones/{foreign.id}/history?period=ungültig").status_code == 404
    assert client_als([]).get(f"/zones/{own.id}/history").status_code == 404
    assert (
        tenant_client([("zone.manage", own.id)]).get(f"/zones/{own.id}/history").status_code == 403
    )


def test_invalid_periods_are_rejected_without_a_server_error(client_als, session: Session) -> None:
    zone = create_zone(session, "zeitraum")
    user = client_als([("zone.manage", zone.id)])
    for bad in ("ungültig", "", "24H", "1d", "24h;drop", "x" * 5000, "%00", "../etc"):
        response = user.get(f"/zones/{zone.id}/history", params={"period": bad})
        assert response.status_code == 422, bad
    assert user.get(f"/zones/{zone.id}/history?period=24h").status_code == 200
    assert user.get(f"/zones/{zone.id}/history?period=7d").status_code == 200
    assert user.get(f"/zones/{zone.id}/history").status_code == 200


def test_page_places_the_chart_above_the_form_with_accessible_summary(
    client_als, session: Session
) -> None:
    zone = create_zone(session, "verlauf")
    setting = session.get(Setting, 1)
    if setting is None:
        setting = create_settings(session)
    setting.timezone = "America/New_York"
    row = create_shadow_decision(session, zone)
    row.decided_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=5)
    row.temperature_c = Decimal("19.5")
    row.setpoint_c = Decimal("18.5")
    row.scheduled_setpoint_c = Decimal("20.5")
    row.solar_setback_k = Decimal("2.0")
    session.flush()
    response = client_als([("zone.manage", zone.id)]).get(f"/zones/{zone.id}")
    html = response.text
    assert response.status_code == 200
    assert html.index('id="zone-history"') < html.index('<form method="post" action="/zones/')
    assert html.count('role="img"') == len(LAYOUTS)
    assert 'aria-describedby="zone-history-summary"' in html
    assert re.search(r'id="zone-history-summary"[^>]*>Ist 19,5 °C bis 19,5 °C\.', html)
    assert "Sonnenabsenkung 2 Minuten." in html
    assert 'hx-get="/zones/' in html
    assert "tc-history-heat" not in html


def test_the_chart_escapes_the_zone_name(client_als, session: Session) -> None:
    zone = create_zone(session, "xssverlauf")
    zone.display_name = '"><script>alert(1)</script>'
    row = create_shadow_decision(session, zone)
    row.decided_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=5)
    row.setpoint_c = Decimal("20.0")
    session.flush()
    response = client_als([("zone.manage", zone.id)]).get(f"/zones/{zone.id}/history")
    assert "<script>alert" not in response.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in response.text


def test_every_layout_draws_inside_its_own_view_box(client_als, session: Session) -> None:
    zone = create_zone(session, "boxen")
    now = datetime.now(UTC).replace(tzinfo=None)
    for index in range(360):
        solar = index >= 60
        row = create_shadow_decision(session, zone)
        row.decided_at = now - timedelta(minutes=360 - index)
        row.temperature_c = Decimal("22.0")
        row.setpoint_c = Decimal("18.0" if solar else "20.5")
        row.scheduled_setpoint_c = Decimal("20.5")
        row.solar_setback_k = Decimal("2.5") if solar else None
        row.would_heat = index % 3 == 0
    session.flush()
    html = client_als([("zone.manage", zone.id)]).get(f"/zones/{zone.id}/history").text
    boxes = re.findall(r'<svg viewBox="0 0 (\d+) (\d+)"(.*?)</svg>', html, re.S)
    assert len(boxes) == len(LAYOUTS)
    # Die Beschriftung des Bandes erscheint nur dort, wo es breit genug ist (Breite 25 %
    # des Fensters: auf den beiden breiten Zeichnungen ja, auf den schmalen nein).
    assert html.count("Sonnenabsenkung 2,5 K</text>") == 2
    for width, height, body in boxes:
        for x in re.findall(r'\bx="(-?[\d.]+)"', body):
            assert 0 <= float(x) <= int(width)
        for y in re.findall(r'\by="(-?[\d.]+)"', body):
            assert 0 <= float(y) <= int(height)
        for d in re.findall(r' d="([^"]*)"', body):
            for number in re.findall(r"[-\d.]+", d):
                assert -1 <= float(number) <= max(int(width), int(height))
