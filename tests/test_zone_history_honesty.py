"""Verlauf: das Diagramm zeichnet nur, was die Zeilen belegen.

Jeder Test hier gehört zu einem Befund des unabhängigen Reviews: Altzeilen ohne Zeitplan-Sollwert,
Sonnenbänder über Phasen ohne Absenkung, Linien über Lücken, Heizspur und Stufen mit
künstlicher Breite, Legende mit Bucket-Mittel statt letzter Messung.
"""

import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from tests.helpers import create_shadow_decision, create_zone
from thermoctl.domain.zone_history_chart import (
    DESKTOP,
    LAYOUTS,
    WINDOWS,
    Sample,
    _duration_text,
    _minutes_text,
    build_chart,
    chart_for_zone,
    line_path,
    range_bars,
    range_path,
    step_path,
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


def _row(
    session: Session,
    zone,
    at: datetime,
    *,
    temperature: str | None = "19.5",
    setpoint: str = "20.5",
    scheduled: str | None = None,
    solar: str | None = None,
    heat: bool = False,
) -> None:
    row = create_shadow_decision(session, zone)
    row.decided_at = at
    row.temperature_c = Decimal(temperature) if temperature is not None else None
    row.setpoint_c = Decimal(setpoint)
    row.scheduled_setpoint_c = Decimal(scheduled) if scheduled is not None else None
    row.solar_setback_k = Decimal(solar) if solar is not None else None
    row.would_heat = heat


def _history_html(client_als, zone, period: str = "24h") -> str:
    response = client_als([("zone.manage", zone.id)]).get(
        f"/zones/{zone.id}/history?period={period}"
    )
    assert response.status_code == 200
    return response.text


def _covered(bands, moment: datetime) -> bool:
    return any(band[0] <= moment < band[1] for band in bands)


# --- Befund 1: Altzeilen ----------------------------------------------------------------


def test_rows_without_a_recorded_schedule_value_leave_the_schedule_line_open(
    session: Session,
) -> None:
    zone = create_zone(session, "altzeitplan")
    now = datetime(2026, 10, 9, 12)
    for minute in range(60):
        # Vor der Aufzeichnung: wirksamer Soll vorhanden, Zeitplan-Soll und Absenkung NULL.
        _row(session, zone, now - timedelta(minutes=120 - minute), setpoint="18.5")
    for minute in range(60):
        _row(session, zone, now - timedelta(minutes=60 - minute), setpoint="20.5", scheduled="20.5")
    session.flush()
    chart = chart_for_zone(session, zone.id, "24h", now, BERLIN)
    # Der wirksame Soll gilt über beide Abschnitte; der Zeitplan-Sollwert beginnt erst dort,
    # wo er aufgezeichnet wurde -- nichts wird aus dem wirksamen Soll abgeleitet.
    assert [v for _, _, v in chart.effective_runs] == [18.5, 20.5]
    assert len(chart.scheduled_runs) == 1
    assert chart.scheduled_runs[0][0] == now - timedelta(minutes=60)


def test_legacy_rows_are_marked_unknown_and_never_reported_as_no_setback(
    session: Session, client_als
) -> None:
    zone = create_zone(session, "altabsenkung")
    now = datetime.now(UTC).replace(tzinfo=None)
    for minute in range(60):
        _row(session, zone, now - timedelta(minutes=120 - minute), setpoint="18.5")
    session.flush()
    chart = chart_for_zone(session, zone.id, "24h", now, BERLIN)
    assert chart.scheduled_runs == ()
    assert "Keine Sonnenabsenkung" not in chart.summary
    assert "Absenkung für 1,0 Stunden unbekannt (vor der Aufzeichnung)." in chart.summary
    assert "Zuletzt Zeitplan-Soll" not in chart.summary
    html = _history_html(client_als, zone)
    assert "Absenkung unbekannt (vor der Aufzeichnung)" in html
    assert html.count('class="tc-history-unknown"') == len(LAYOUTS)
    assert re.findall(r'<path d="([^"]*)" class="tc-history-scheduled"', html) == [""] * len(
        LAYOUTS
    )


def test_recorded_rows_without_setback_keep_the_statement_that_there_was_none(
    session: Session, client_als
) -> None:
    zone = create_zone(session, "neueabsenkung")
    now = datetime.now(UTC).replace(tzinfo=None)
    for minute in range(30):
        _row(session, zone, now - timedelta(minutes=60 - minute), scheduled="20.5")
    session.flush()
    chart = chart_for_zone(session, zone.id, "24h", now, BERLIN)
    assert "Keine Sonnenabsenkung." in chart.summary
    assert "unbekannt" not in chart.summary
    assert "unbekannt" not in _history_html(client_als, zone)


# --- Befund 2: Sonnenband ---------------------------------------------------------------


@pytest.mark.parametrize("window", ["24h", "7d"])
def test_alternating_setback_rows_never_form_one_continuous_band(window: str) -> None:
    start = datetime(2026, 10, 2, 4)
    end = start + WINDOWS[window]
    samples = [
        _sample(start + timedelta(minutes=i), solar_k=(2.0 if i % 2 == 0 else 0.0))
        for i in range(60)
    ]
    chart = build_chart(samples, start, end, BERLIN)
    for sample in samples:
        inside = _covered(chart.solar_bands, sample.at + timedelta(seconds=30))
        assert inside == (sample.solar_k > 0), sample.at
    covered = sum((b - a).total_seconds() for a, b, *_ in chart.solar_bands)
    assert covered == 30 * 60


def test_a_changing_setback_is_labelled_with_its_range_not_its_maximum(
    session: Session, client_als
) -> None:
    zone = create_zone(session, "bereich")
    now = datetime.now(UTC).replace(tzinfo=None)
    for minute in range(360):
        _row(
            session,
            zone,
            now - timedelta(minutes=370 - minute),
            setpoint="18.5",
            scheduled="20.5",
            solar="1.5" if minute < 180 else "2.0",
        )
    session.flush()
    html = _history_html(client_als, zone)
    assert "Sonnenabsenkung 1,5 bis 2,0 K</text>" in html
    assert "Sonnenabsenkung 2,0 K</text>" not in html


# --- Befund 3: Lücken --------------------------------------------------------------------


@pytest.mark.parametrize("window", ["24h", "3d", "7d"])
def test_a_gap_of_eleven_minutes_breaks_the_measured_curve_in_every_window(window: str) -> None:
    start = datetime(2026, 10, 2, 4)
    first = [_sample(start + timedelta(minutes=i)) for i in range(3)]
    second = [_sample(start + timedelta(minutes=13 + i)) for i in range(3)]
    chart = build_chart(first + second, start, start + WINDOWS[window], BERLIN)
    assert line_path(chart, DESKTOP).count("M") == 2


@pytest.mark.parametrize("window", ["24h", "3d", "7d"])
def test_rows_within_ten_minutes_stay_connected_in_every_window(window: str) -> None:
    start = datetime(2026, 10, 2, 4)
    samples = [_sample(start + timedelta(minutes=9 * i)) for i in range(40)]
    chart = build_chart(samples, start, start + WINDOWS[window], BERLIN)
    assert line_path(chart, DESKTOP).count("M") == 1


def test_a_hole_inside_one_compressed_bucket_is_not_averaged_over() -> None:
    start = datetime(2026, 10, 2, 4)
    # 7 Tage: ein Bucket ist 16,8 Minuten breit; Zeilen bei 0-2 und 13-16 Minuten liegen darin.
    samples = [_sample(start + timedelta(minutes=m)) for m in (0, 1, 2, 13, 14, 15, 16)]
    chart = build_chart(samples, start, start + timedelta(days=7), BERLIN)
    assert line_path(chart, DESKTOP).count("M") == 2


def test_setpoint_steps_break_at_the_same_fixed_gap() -> None:
    start = datetime(2026, 10, 2, 4)
    samples = [_sample(start + timedelta(minutes=m)) for m in (0, 1, 2, 30, 31, 32)]
    chart = build_chart(samples, start, start + timedelta(days=7), BERLIN)
    assert step_path(chart.effective_runs, chart, DESKTOP).count("M") == 2


# --- Befund 4: Heizspur, Stufen, Bänder ohne künstliche Breite -------------------------


@pytest.mark.parametrize("window", ["24h", "7d"])
def test_the_heat_lane_covers_only_the_time_of_the_rows(window: str) -> None:
    start = datetime(2026, 10, 2, 4)
    samples = [
        _sample(start + timedelta(minutes=m), heat=(m == 0)) for m in (0, 1, 2, 13, 14, 15, 16)
    ]
    chart = build_chart(samples, start, start + WINDOWS[window], BERLIN)
    assert chart.heat_lane, "die Heizanforderung der ersten Zeile muss sichtbar bleiben"
    for first, last, _ in chart.heat_lane:
        assert first >= samples[0].at and last <= samples[-1].at + timedelta(seconds=90)
        # Die Lücke zwischen Minute 2 und 13 trägt keine Zeile und bleibt ungefärbt.
        assert not first < start + timedelta(minutes=8) < last


def test_a_single_heating_row_colours_at_most_its_own_cycle() -> None:
    start = datetime(2026, 10, 2, 4)
    chart = build_chart(
        [_sample(start + timedelta(hours=2), heat=True)], start, start + timedelta(days=7), BERLIN
    )
    ((first, last, _),) = chart.heat_lane
    assert first == start + timedelta(hours=2)
    assert last - first <= timedelta(seconds=90)


def test_drawn_widths_carry_no_minimum_that_would_claim_time(session: Session, client_als) -> None:
    zone = create_zone(session, "mindestbreite")
    now = datetime.now(UTC).replace(tzinfo=None)
    _row(session, zone, now - timedelta(minutes=300), heat=True, scheduled="20.5")
    _row(session, zone, now - timedelta(minutes=100), heat=False, scheduled="20.5")
    session.flush()
    html = _history_html(client_als, zone)
    # 24 h auf 880 Einheiten: 60 s sind 0,6 Einheiten, nicht 1,5.
    widths = [float(w) for w in re.findall(r'width="([\d.]+)"[^>]*class="tc-history-demand"', html)]
    assert len(widths) == len(LAYOUTS) and max(widths) < 1.0
    for d in re.findall(r'<path d="([^"]*)" class="tc-history-(?:scheduled|effective)"', html):
        # Eine einzelne Zeile ergibt einen Punkt: Anfang und Ende gleich, keine Zugabe.
        for x1, x2 in re.findall(r"M([\d.]+),[\d.]+ H([\d.]+)", d):
            assert x1 == x2 or float(x2) - float(x1) < 0.3


def test_a_setback_shorter_than_a_pixel_gets_a_marker_but_no_stretched_band(
    session: Session, client_als
) -> None:
    zone = create_zone(session, "kurzband2")
    now = datetime.now(UTC).replace(tzinfo=None)
    for minutes, solar in ((300, None), (299, "2.0"), (298, None)):
        _row(session, zone, now - timedelta(minutes=minutes), scheduled="20.0", solar=solar)
    session.flush()
    html = _history_html(client_als, zone, "7d")
    assert html.count("tc-history-solar-mark") == len(LAYOUTS)
    widths = re.findall(r'<rect [^>]*width="([\d.]+)"[^>]*class="tc-history-solar"', html)
    assert all(float(w) < 0.5 for w in widths)


# --- Befund 5: Legende und Verdichtung --------------------------------------------------


def test_the_legend_shows_the_last_measurement_with_its_time_not_a_bucket_mean(
    session: Session, client_als
) -> None:
    zone = create_zone(session, "legende")
    now = datetime.now(UTC).replace(tzinfo=None)
    for minute in range(30):
        # Letzter Wert 24,0: das Mittel seines Buckets (7 Tage: 17 Minuten) liegt darunter.
        temperature = "24.0" if minute == 29 else "19.0"
        _row(
            session,
            zone,
            now - timedelta(minutes=30 - minute),
            temperature=temperature,
            scheduled="20.5",
        )
    session.flush()
    html = _history_html(client_als, zone, "7d")
    assert re.search(r"Ist: 24,0 °C \(letzte Messung \d\d:\d\d Uhr\)", html)


def test_compressed_windows_show_the_range_of_each_section_and_say_so(
    session: Session, client_als
) -> None:
    zone = create_zone(session, "huelle")
    now = datetime.now(UTC).replace(tzinfo=None)
    for minute in range(60):
        _row(
            session,
            zone,
            now - timedelta(minutes=60 - minute),
            temperature="22.0" if minute == 30 else "19.0",
            scheduled="20.5",
        )
    session.flush()
    html = _history_html(client_als, zone, "7d")
    assert html.count("tc-history-range") >= len(LAYOUTS)
    assert "Mittelwert je Abschnitt von bis zu 17 Minuten" in html
    assert "Kleinster bis größter Wert je Abschnitt" in html


def test_x_position_is_unchanged_for_reference() -> None:
    start = datetime(2026, 10, 2, 4)
    chart = build_chart([_sample(start)], start, start + timedelta(hours=24), BERLIN)
    assert x_position(chart.end, chart, DESKTOP) == DESKTOP.right


# --- Beschriftung der Legende und Zusammenfassung ---------------------------------------


def test_summary_and_legend_for_a_chart_whose_schedule_value_is_unknown() -> None:
    start = datetime(2026, 10, 9, 4)
    samples = [
        _sample(start + timedelta(minutes=i), scheduled=None, solar_k=None) for i in range(5)
    ]
    chart = build_chart(samples, start, start + timedelta(hours=1), BERLIN)
    assert "Zuletzt wirksamer Soll 20,5 °C (Zeitplan-Soll unbekannt)." in chart.summary
    # Die Daten enden 56 Minuten vor dem Fensterende: der Stand steht dabei (06:04 Ortszeit).
    assert chart.scheduled_text == "unbekannt"
    assert chart.effective_text == "20,5 °C (Stand 09.10. 06:04 Uhr)"
    assert [first for first, _ in chart.unknown_bands] == [start]


def test_legend_of_the_last_measurement_names_the_day_when_it_is_not_today() -> None:
    start = datetime(2026, 10, 8, 4)
    end = datetime(2026, 10, 9, 20)
    samples = [_sample(start + timedelta(minutes=i), temperature=19.0 + i / 10) for i in range(3)]
    chart = build_chart(samples, start, end, BERLIN)
    assert chart.actual_text == "19,2 °C (letzte Messung 08.10. 06:02 Uhr)"
    nothing = build_chart([_sample(start, temperature=None)], start, end, BERLIN)
    assert nothing.actual_text == "keine Messung"
    assert build_chart([], start, end, BERLIN).scheduled_text == "unbekannt"


def test_the_range_is_only_drawn_where_a_section_actually_varies() -> None:
    start = datetime(2026, 10, 2, 4)
    steady = [_sample(start + timedelta(minutes=i)) for i in range(40)]
    chart = build_chart(steady, start, start + timedelta(days=7), BERLIN)
    assert range_path(chart, DESKTOP) == "" and range_bars(chart, DESKTOP) == ""
    varying = [
        _sample(start + timedelta(minutes=i), temperature=19.0 + (i % 5)) for i in range(120)
    ]
    path = range_path(build_chart(varying, start, start + timedelta(days=7), BERLIN), DESKTOP)
    assert path.startswith("M") and path.endswith("Z")


# Bucketbreite im 7-Tage-Fenster: 604800 s / 600 = 1008 s. Zeilen ab einem Vielfachen davon
# liegen in genau einem Bucket.
_BUCKET = timedelta(seconds=1008)


def _outlier_rows(start: datetime) -> list[Sample]:
    """16 Minuten Rohdaten: 15 mal 20,0 Grad und ein Ausreißer mit 30,0 Grad."""
    return [
        _sample(start + timedelta(minutes=i), temperature=30.0 if i == 7 else 20.0)
        for i in range(16)
    ]


def test_a_single_compressed_section_still_shows_its_spike_as_a_bar() -> None:
    start = datetime(2026, 10, 2, 4)
    rows = _outlier_rows(start + _BUCKET * 257)
    chart = build_chart(rows, start, start + timedelta(days=7), BERLIN)
    (point,) = chart.points
    assert (point.temperature, point.low, point.high) == (20.625, 20.0, 30.0)
    # Mitte zwischen Minute 0 und 15 ist 7,5 Minuten nach dem Bucketanfang (257 * 1008 s):
    # 60 + 880 * 259506 / 604800 = 437,6; der Balken ist 1,6 Einheiten breit.
    # Wertebereich 19 bis 31 Grad auf 184 Einheiten: 30 Grad bei y = 43,3, 20 Grad bei y = 196,7.
    assert range_path(chart, DESKTOP) == ""
    assert range_bars(chart, DESKTOP) == "M436.8,43.3 L438.4,43.3 L438.4,196.7 L436.8,196.7 Z"


def test_every_isolated_section_gets_its_own_bar_and_flat_ones_get_none() -> None:
    start = datetime(2026, 10, 2, 4)
    first = _outlier_rows(start + _BUCKET * 100)
    at = start + _BUCKET * 300
    flat = [_sample(at + timedelta(minutes=i), temperature=20.0) for i in range(16)]
    second = _outlier_rows(start + _BUCKET * 500)
    chart = build_chart(first + flat + second, start, start + timedelta(days=7), BERLIN)
    assert len(chart.points) == 3
    bars = range_bars(chart, DESKTOP)
    assert bars.count("M") == 2 and bars.count("Z") == 2


def test_the_spike_bar_reaches_every_variant_of_the_page(session: Session, client_als) -> None:
    zone = create_zone(session, "ausreisser")
    now = datetime.now(UTC).replace(tzinfo=None)
    for minute in range(16):
        _row(
            session,
            zone,
            now - timedelta(minutes=16 - minute),
            temperature="30.0" if minute == 7 else "20.0",
            scheduled="20.5",
        )
    session.flush()
    html = _history_html(client_als, zone, "7d")
    areas = re.findall(r'<path d="([^"]*)" class="tc-history-range"', html)
    bars = re.findall(r'<path d="([^"]*)" class="tc-history-range-bar"', html)
    assert len(areas) == len(bars) == len(LAYOUTS)
    # Je nach Lage der 16 Minuten zu den Bucketgrenzen: ein Abschnitt (Balken) oder zwei (Fläche).
    for area, bar in zip(areas, bars, strict=True):
        assert re.fullmatch(r"M[\d., LZ]+", area + bar)


# --- Befund 1 der dritten Runde: gemischte Fenster --------------------------------------


def test_a_mixed_window_never_says_none_where_part_of_it_is_unknown(session: Session) -> None:
    zone = create_zone(session, "gemischt")
    now = datetime(2026, 10, 9, 12)
    for minute in range(60):  # vor der Aufzeichnung: Absenkung unbekannt
        _row(session, zone, now - timedelta(minutes=120 - minute), setpoint="18.5")
    for minute in range(30):  # danach belegt: keine Absenkung
        _row(session, zone, now - timedelta(minutes=60 - minute), scheduled="20.5", solar="0")
    session.flush()
    summary = chart_for_zone(session, zone.id, "24h", now, BERLIN).summary
    assert "Keine Sonnenabsenkung" not in summary
    assert "In den aufgezeichneten 30 Minuten keine Sonnenabsenkung." in summary
    assert "Absenkung für 1,0 Stunden unbekannt (vor der Aufzeichnung)." in summary


def test_a_mixed_window_with_a_setback_names_only_what_the_rows_prove(session: Session) -> None:
    zone = create_zone(session, "gemischt2")
    now = datetime(2026, 10, 9, 12)
    for minute in range(60):
        _row(session, zone, now - timedelta(minutes=120 - minute), setpoint="18.5")
    for minute in range(30):
        _row(session, zone, now - timedelta(minutes=60 - minute), scheduled="20.5", solar="1.5")
    session.flush()
    summary = chart_for_zone(session, zone.id, "24h", now, BERLIN).summary
    assert "Keine Sonnenabsenkung" not in summary and "keine Sonnenabsenkung" not in summary
    assert "Sonnenabsenkung 30 Minuten." in summary
    assert "Absenkung für 1,0 Stunden unbekannt (vor der Aufzeichnung)." in summary


def test_the_legend_of_a_mixed_window_has_no_statement_of_none(
    session: Session, client_als
) -> None:
    zone = create_zone(session, "gemischt3")
    now = datetime.now(UTC).replace(tzinfo=None)
    for minute in range(20):
        _row(session, zone, now - timedelta(minutes=60 - minute), setpoint="18.5")
    for minute in range(20):
        _row(session, zone, now - timedelta(minutes=30 - minute), scheduled="20.5", solar="0")
    session.flush()
    html = _history_html(client_als, zone)
    assert "Absenkung unbekannt (vor der Aufzeichnung)" in html
    assert "Keine Sonnenabsenkung" not in html


# --- Befunde 3 bis 5 der dritten Runde: Legende sagt, was das Bild zeigt -----------------


def test_the_legend_of_the_heat_lane_names_a_share_per_section_not_a_duration(
    session: Session, client_als
) -> None:
    zone = create_zone(session, "heizlegende")
    now = datetime.now(UTC).replace(tzinfo=None)
    _row(session, zone, now - timedelta(minutes=20), heat=True, scheduled="20.5")
    session.flush()
    html = _history_html(client_als, zone)
    assert "Anteil der Zeit mit Heizanforderung je Abschnitt" in html
    assert "blasser = seltener" not in html


def test_one_heating_minute_among_sixteen_is_the_palest_level_not_a_full_stroke() -> None:
    start = datetime(2026, 10, 2, 4)
    rows = [_sample(start + timedelta(minutes=i), heat=(i == 3)) for i in range(17)]
    chart = build_chart(rows, start, start + timedelta(days=7), BERLIN)
    ((first, last, level),) = chart.heat_lane
    assert level == 1 and (first, last) == (start, start + timedelta(minutes=17, seconds=30))


def test_the_curve_legend_says_up_to_and_not_a_fixed_length(session: Session, client_als) -> None:
    zone = create_zone(session, "kurvenlegende")
    now = datetime.now(UTC).replace(tzinfo=None)
    _row(session, zone, now - timedelta(minutes=20), scheduled="20.5")
    session.flush()
    html = _history_html(client_als, zone, "7d")
    assert "Mittelwert je Abschnitt von bis zu 17 Minuten" in html
    assert "ein Punkt kann aus einer einzigen Messung stammen" in html
    assert "Mittelwert je 17 Minuten" not in html


def test_a_ten_minute_setback_in_seven_days_has_a_thin_band_and_a_marker(
    session: Session, client_als
) -> None:
    zone = create_zone(session, "zehnminuten")
    now = datetime.now(UTC).replace(tzinfo=None)
    for minute in range(60):
        solar = "2.0" if 20 <= minute < 30 else "0"
        _row(session, zone, now - timedelta(minutes=120 - minute), scheduled="20.5", solar=solar)
    session.flush()
    html = _history_html(client_als, zone, "7d")
    # Das Verhalten, das die Anleitung beschreibt: schmales, unverbreitertes Band UND Dreieck.
    assert html.count("tc-history-solar-mark") == len(LAYOUTS)
    widths = [float(w) for w in re.findall(r'width="([\d.]+)"[^>]*class="tc-history-solar"', html)]
    assert len(widths) == len(LAYOUTS) and all(0.25 <= w < 1.0 for w in widths)
    assert "Dreieck oben: kurze Absenkung, im Bild nur als schmaler Streifen" in html
    assert "zu schmal für ein Band" not in html


def test_rows_with_the_same_timestamp_do_not_colour_or_cover_anything() -> None:
    start = datetime(2026, 10, 9, 4)
    samples = [_sample(start, heat=True, solar_k=2.0), _sample(start, heat=True, solar_k=2.0)]
    chart = build_chart(samples, start, start + timedelta(hours=1), BERLIN)
    # Die erste Zeile hat keine Zeitspanne (die zweite beginnt im selben Augenblick); die
    # zweite gilt bis zum Ende ihres Zyklus.
    assert chart.heat_lane == ((start, start + timedelta(seconds=90), 4),)
    assert chart.solar_bands == ((start, start + timedelta(seconds=90), 2.0, 2.0),)


# --- Vierte Runde: Ausschlag in Ketten, Texte, Stufung nach Zeit ------------------------


def _spike_chart(window: timedelta, offset: timedelta):
    """16 Minuten Rohdaten (ein Wert 30,0 Grad) am Ende des Fensters, ab `offset` nach Beginn."""
    start = datetime(2026, 10, 2, 4)
    rows = _outlier_rows(start + offset)
    return build_chart(rows, start, start + window, BERLIN)


@pytest.mark.parametrize(
    ("window", "offset"),
    [
        (timedelta(hours=24), timedelta(hours=10)),  # Bucket 144 s: etwa sieben Abschnitte
        (timedelta(days=3), timedelta(hours=30)),  # Bucket 432 s: drei bis vier Abschnitte
        (timedelta(days=7), _BUCKET * 258 - timedelta(minutes=7)),  # zwei Abschnitte
    ],
)
@pytest.mark.parametrize("layout", LAYOUTS, ids=lambda layout: layout.name)
def test_a_spike_inside_a_chain_of_sections_gets_a_bar_up_to_its_extreme(
    window: timedelta, offset: timedelta, layout
) -> None:
    chart = _spike_chart(window, offset)
    assert len([chain for chain in chart.points if chain.joined]) >= 1, "Kette erwartet"
    bars = range_bars(chart, layout)
    # Genau ein Balken: der Abschnitt mit dem Ausschlag; er reicht bis zum Extremwert.
    assert bars.count("M") == 1
    assert f",{y_position(30.0, chart, layout):.1f} " in bars


def test_ordinary_noise_in_a_chain_gets_no_bars_only_the_area() -> None:
    start = datetime(2026, 10, 9, 0)
    rows = [
        _sample(start + timedelta(minutes=i), temperature=19.5 + 0.1 * (i % 4)) for i in range(600)
    ]
    for window in WINDOWS.values():
        chart = build_chart(rows, start, start + window, BERLIN)
        for layout in LAYOUTS:
            assert range_bars(chart, layout) == ""
    assert range_path(build_chart(rows, start, start + WINDOWS["24h"], BERLIN), DESKTOP) != ""


def test_a_steady_climb_is_not_taken_for_a_spike() -> None:
    start = datetime(2026, 10, 9, 0)
    rows = [_sample(start + timedelta(minutes=i), temperature=18.0 + i / 60) for i in range(240)]
    chart = build_chart(rows, start, start + WINDOWS["24h"], BERLIN)
    assert range_bars(chart, DESKTOP) == ""


def test_a_dip_inside_a_chain_gets_a_bar_down_to_its_minimum() -> None:
    start = datetime(2026, 10, 9, 0)
    rows = [
        _sample(start + timedelta(seconds=30 * i), temperature=10.0 if i == 200 else 20.0)
        for i in range(400)
    ]
    chart = build_chart(rows, start, start + WINDOWS["24h"], BERLIN)
    bars = range_bars(chart, DESKTOP)
    assert bars.count("M") == 1
    assert f",{y_position(10.0, chart, DESKTOP):.1f} " in bars


def test_the_duration_text_has_singular_and_never_says_zero_hours() -> None:
    assert _duration_text(1 / 60) == "1 Minute"
    assert _duration_text(2 / 60) == "2 Minuten"
    assert _duration_text(0.0) == "0 Minuten"
    assert _duration_text(1.5) == "1,5 Stunden"


def test_a_single_row_reports_its_minutes_not_zero_hours() -> None:
    end = datetime(2026, 10, 9, 12)
    chart = build_chart(
        [_sample(end - timedelta(seconds=45))], end - timedelta(hours=24), end, BERLIN
    )
    assert "Daten für 1 Minute von 24,0 Stunden." in chart.summary
    assert "0,0" not in chart.summary


def test_up_to_n_minutes_is_rounded_up_so_it_never_understates() -> None:
    assert _minutes_text(144) == "3 Minuten"  # 2,4
    assert _minutes_text(432) == "8 Minuten"  # 7,2
    assert _minutes_text(1008) == "17 Minuten"  # 16,8
    assert _minutes_text(180) == "3 Minuten"  # genau: nicht 4
    assert _minutes_text(30.2) == "31 Sekunden"
    assert _minutes_text(0.4) == "1 Sekunde"


def test_the_legend_of_the_24_hour_window_names_three_minutes(session: Session, client_als) -> None:
    zone = create_zone(session, "dreiminuten")
    now = datetime.now(UTC).replace(tzinfo=None)
    _row(session, zone, now - timedelta(minutes=20), scheduled="20.5")
    session.flush()
    assert "Mittelwert je Abschnitt von bis zu 3 Minuten" in _history_html(client_als, zone, "24h")


def test_setpoint_legend_carries_a_stamp_when_the_data_is_stale_and_not_otherwise() -> None:
    end = datetime(2026, 10, 10, 12)
    start = end - timedelta(hours=24)
    last = end - timedelta(hours=3)  # 09:00 UTC = 11:00 Ortszeit
    stale = build_chart([_sample(last, scheduled=21.0, effective=19.0)], start, end, BERLIN)
    assert stale.scheduled_text == "21,0 °C (Stand 10.10. 11:00 Uhr)"
    assert stale.effective_text == "19,0 °C (Stand 10.10. 11:00 Uhr)"
    fresh = build_chart(
        [_sample(end - timedelta(minutes=2), scheduled=21.0, effective=19.0)], start, end, BERLIN
    )
    assert fresh.scheduled_text == "21,0 °C" and fresh.effective_text == "19,0 °C"
    unknown = build_chart([_sample(last, scheduled=None)], start, end, BERLIN)
    assert unknown.scheduled_text == "unbekannt"
    assert unknown.effective_text == "20,5 °C (Stand 10.10. 11:00 Uhr)"


def test_the_stamp_reaches_the_legend_of_the_page(session: Session, client_als) -> None:
    zone = create_zone(session, "veraltet")
    now = datetime.now(UTC).replace(tzinfo=None)
    _row(session, zone, now - timedelta(hours=3), scheduled="21.0", setpoint="19.0")
    session.flush()
    html = _history_html(client_als, zone)
    assert re.search(r"Zeitplan-Soll: 21,0 °C \(Stand \d\d\.\d\d\. \d\d:\d\d Uhr\)", html)
    assert re.search(r"Wirksamer Soll: 19,0 °C \(Stand \d\d\.\d\d\. \d\d:\d\d Uhr\)", html)


def test_the_heat_lane_weighs_rows_by_their_seconds_like_the_summary() -> None:
    start = datetime(2026, 10, 9, 4)
    # Eine Sekunde mit Heizanforderung, dann 90 Sekunden ohne: der Anteil ist 1/91, nicht 1/2.
    rows = [_sample(start, heat=True), _sample(start + timedelta(seconds=1), heat=False)]
    end = start + timedelta(hours=1)
    chart = build_chart(rows, start, end, BERLIN)
    assert [level for _, _, level in chart.heat_lane] == [1]
    assert "Heizanforderung 1 % der erfassten Zeit." in chart.summary
    # Umgekehrt: 90 Sekunden mit Anforderung und eine Sekunde ohne ist fast durchgehend.
    rows = [_sample(start, heat=True), _sample(start + timedelta(seconds=90), heat=False)]
    chart = build_chart(rows, start, start + timedelta(seconds=91), BERLIN)
    assert [level for _, _, level in chart.heat_lane] == [4]


def test_the_summary_names_the_time_without_a_temperature_reading() -> None:
    start = datetime(2026, 10, 9, 0)
    end = start + timedelta(minutes=10)
    mixed = [
        _sample(start + timedelta(seconds=30 * i), temperature=None if i < 10 else 19.5)
        for i in range(20)
    ]
    text = build_chart(mixed, start, end, BERLIN).summary
    assert "Für 5 Minuten der erfassten Zeit lag kein Temperaturmesswert vor." in text
    whole = [_sample(start + timedelta(seconds=30 * i)) for i in range(20)]
    assert "Temperaturmesswert" not in build_chart(whole, start, end, BERLIN).summary
