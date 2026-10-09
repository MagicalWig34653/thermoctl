"""Verlauf: Übergänge, Altzeilen, Zeitzone und Berechtigung."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from tests.helpers import create_settings, create_shadow_decision, create_zone
from thermoctl.db.models.operations import Setting
from thermoctl.domain.zone_history_chart import Sample, build_chart, chart_for_zone


def test_buckets_preserve_setpoint_steps_solar_and_heating_share() -> None:
    start = datetime(2026, 10, 9, 4)
    samples = [
        Sample(
            start + timedelta(seconds=i * 37),
            19.5,
            18.5 if i >= 10 else 20.5,
            20.5,
            i % 2 == 0,
            i >= 10,
        )
        for i in range(30)
    ]
    chart = build_chart(samples, start, start + timedelta(hours=24), "Europe/Berlin")
    assert len(chart.points) <= 600
    assert chart.effective_steps == ((start, 20.5), (start + timedelta(seconds=370), 18.5))
    assert chart.scheduled_steps == ((start, 20.5),)
    assert chart.solar_bands[0][0] <= samples[10].at <= chart.solar_bands[0][1]
    assert chart.points[0].heat_share == 0.5
    assert len(chart.heat_bands) <= 600
    assert all(0 < share <= 1 for _, _, share in chart.heat_bands)
    assert "Sonnenabsenkung" in chart.summary


def test_empty_and_dst_transition_are_safe() -> None:
    start = datetime(2026, 10, 25, 0)
    empty = build_chart([], start, start + timedelta(hours=4), "Europe/Berlin")
    assert not empty.points
    assert "Keine Verlaufsdaten" in empty.summary
    chart = build_chart(
        [Sample(start, 19.5, 20.5, 20.5, False, False)],
        start,
        start + timedelta(hours=4),
        "Europe/Berlin",
    )
    # Die Uhr springt zurück: zwei verschiedene UTC-Ticks dürfen lokal 02 Uhr zeigen.
    assert chart.ticks[0][1].endswith("02:00")
    assert chart.ticks[1][1].endswith("02:00")


def test_old_null_columns_use_effective_value_without_solar(session: Session) -> None:
    zone = create_zone(session, "altverlauf")
    row = create_shadow_decision(session, zone)
    row.decided_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=5)
    row.temperature_c = Decimal("19.5")
    row.setpoint_c = Decimal("20.5")
    session.flush()
    chart = chart_for_zone(
        session, zone.id, "24h", datetime.now(UTC).replace(tzinfo=None), "Europe/Berlin"
    )
    assert chart.points[0].scheduled == chart.points[0].effective == 20.5
    assert not chart.solar_bands


def test_partial_route_and_zone_isolation(client_als, tenant_client, session: Session) -> None:
    own = create_zone(session, "eigenerverlauf")
    foreign = create_zone(session, "fremderverlauf")
    client = client_als([("zone.manage", own.id)])
    response = client.get(f"/zones/{own.id}/history?period=3d", headers={"HX-Request": "true"})
    assert response.status_code == 200
    assert 'id="zone-history"' in response.text
    assert "Keine Verlaufsdaten" in response.text
    assert "<html" not in response.text
    assert client.get(f"/zones/{own.id}/history?period=ungültig").status_code == 422
    assert client.get(f"/zones/{foreign.id}/history").status_code == 404
    assert client_als([]).get(f"/zones/{own.id}/history").status_code == 404
    assert (
        tenant_client([("zone.manage", own.id)]).get(f"/zones/{own.id}/history").status_code == 403
    )


def test_page_summary_and_configured_timezone(client_als, session: Session) -> None:
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
    assert response.status_code == 200
    assert 'role="img"' in response.text
    assert "Ist 19.5 bis 19.5 °C" in response.text
    assert "Sonnenabsenkung" in response.text
    assert 'hx-get="/zones/' in response.text


def test_svg_paths_show_setpoint_changes_and_break_at_missing_measurements() -> None:
    from thermoctl.domain.zone_history_chart import (
        line_path,
        mobile_line_path,
        mobile_step_path,
        step_path,
    )

    start = datetime(2026, 10, 9, 4)
    samples = [
        Sample(start, 19.5, 20.5, 20.5, False, False),
        Sample(start + timedelta(minutes=10), None, 18.5, 20.5, False, True),
        Sample(start + timedelta(minutes=20), 19.7, 18.5, 21.0, True, True),
    ]
    chart = build_chart(samples, start, start + timedelta(hours=1), "Europe/Berlin")
    assert chart.maximum - chart.minimum >= 2
    assert line_path(chart.points, chart).count("M") == 2
    assert mobile_line_path(chart.points, chart).count("M") == 2
    assert " V" in step_path(chart.effective_steps, chart)
    assert " V" in mobile_step_path(chart.scheduled_steps, chart)
    assert chart.solar_bands and chart.heat_bands


def test_svg_paths_are_empty_without_setpoints() -> None:
    from thermoctl.domain.zone_history_chart import mobile_step_path, step_path

    start = datetime(2026, 10, 9, 4)
    chart = build_chart(
        [Sample(start, 19.5, None, None, False, False)],
        start,
        start + timedelta(hours=1),
        "Europe/Berlin",
    )
    assert step_path(chart.effective_steps, chart) == ""
    assert mobile_step_path(chart.scheduled_steps, chart) == ""


def test_seven_days_of_cycles_stay_bounded_without_losing_a_short_setback() -> None:
    start = datetime(2026, 10, 2, 4)
    samples = [
        Sample(
            start + timedelta(seconds=37 * index),
            19.5 + (index % 4) / 10,
            18.5 if index >= 5000 else 20.5,
            20.5,
            index % 2 == 0,
            index == 5000,
        )
        for index in range(16000)
    ]
    chart = build_chart(samples, start, start + timedelta(days=7), "Europe/Berlin")
    assert len(chart.points) <= 600
    assert len(chart.heat_bands) <= 600
    assert chart.effective_steps[1] == (samples[5000].at, 18.5)
    assert any(a <= samples[5000].at <= b for a, b in chart.solar_bands)
    assert 0.4 < chart.points[0].heat_share < 0.6
