"""Solar setback wired into the shadow cycle (`thermoctl.services.shadow_run`).

Complements `test_solar_setback.py` (the pure correction in isolation) and
`test_shadow_run.py` (the cycle without solar setback at all): here the forecast is
handed to `shadow_run.cycle()` the way `thermoctl.app._shadowschleife` hands it in,
and the assertions are on the actual `ShadowDecision` row -- the setpoint that reaches
`decide()`, and the reason text an operator would actually read.
"""

from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from tests.helpers import create_settings, create_zone, source
from thermoctl.db.models.override import ZoneOverride
from thermoctl.db.models.zone import Zone
from thermoctl.domain.solar_setback import HourlyForecast
from thermoctl.services import shadow_run

NOW = datetime(2026, 8, 30, 8, 0)


def _sunny_forecast(radiation: Decimal = Decimal(300)) -> list[HourlyForecast]:
    return [HourlyForecast(NOW + timedelta(hours=1), Decimal(20), radiation)]


def _cloudy_forecast() -> list[HourlyForecast]:
    return [HourlyForecast(NOW + timedelta(hours=1), Decimal(90), Decimal(30))]


def _fixed_setpoint(session: Session, zone: Zone, temperature_c: Decimal) -> None:
    """A permanent override effective well before `NOW` -- inserted directly rather
    than through `domain.schedule.create_override`, which stamps `starts_at` with the
    real wall-clock `utcnow()` and would make the override silently inactive whenever
    a test run happens to fall after `NOW`'s time of day."""
    session.add(
        ZoneOverride(
            zone_id=zone.id,
            temperature_c=temperature_c,
            starts_at=NOW - timedelta(days=1),
            ends_at=None,
            source_id=source(session, "system").id,
        )
    )


def test_without_a_forecast_the_setpoint_is_never_corrected(session: Session) -> None:
    """`forecast=None` -- the default -- must behave exactly like before this
    feature existed: no mention of a setback anywhere in the reason."""
    create_settings(session)
    zone = create_zone(session, "keine-prognose")
    zone.solar_gain_factor = Decimal("1.0")
    session.flush()

    row = shadow_run.cycle(session, NOW)[0]

    assert "Sonnenabsenkung" not in row.setpoint_reason


def test_a_forecast_without_expected_sun_leaves_the_setpoint_untouched(
    session: Session,
) -> None:
    create_settings(session)
    zone = create_zone(session, "bewoelkt")
    zone.solar_gain_factor = Decimal("1.0")
    session.flush()

    row = shadow_run.cycle(session, NOW, _cloudy_forecast())[0]

    assert "Sonnenabsenkung" not in row.setpoint_reason


def test_a_setpoint_already_at_frost_protection_gets_no_setback(session: Session) -> None:
    """No schedule and no override configured: the setpoint falls back to frost
    protection (16.0 °C -- see `_frost_setpoint`'s own fallback), leaving no room
    above it for even a full-strength, sunny zone to reduce."""
    settings = create_settings(session)
    settings.default_solar_setback_max_k = Decimal("2.0")
    zone = create_zone(session, "dachzimmer-ohne-zeitplan")
    zone.solar_gain_factor = Decimal("1.0")
    session.flush()

    row = shadow_run.cycle(session, NOW, _sunny_forecast())[0]

    assert row.setpoint_c == Decimal("16.0")
    assert "Sonnenabsenkung" not in row.setpoint_reason


def test_expected_sun_reduces_a_setpoint_that_has_headroom_above_frost(
    session: Session,
) -> None:
    """The task's requirement on traceability: the reasoning must state *that* and
    *how much* the setpoint was lowered -- a number, not just 'because of the sun'."""
    settings = create_settings(session)
    settings.default_solar_setback_max_k = Decimal("2.0")
    zone = create_zone(session, "dachzimmer-mit-uebersteuerung")
    zone.solar_gain_factor = Decimal("1.0")
    _fixed_setpoint(session, zone, Decimal("21.0"))
    session.flush()

    row = shadow_run.cycle(session, NOW, _sunny_forecast())[0]

    assert row.setpoint_c == Decimal("19.0")  # 21.0 - (1.0 * 2.0)
    assert "Sonnenabsenkung: -2.0 K" in row.setpoint_reason
    assert "in den nächsten 3 Stunden" in row.setpoint_reason  # setting's default


def test_a_zone_with_factor_zero_is_unaffected_by_any_forecast(session: Session) -> None:
    """Task requirement: a zone with factor 0 -- the documented default -- stays
    unchanged even with a strongly sunny forecast."""
    create_settings(session)
    zone = create_zone(session, "nordzimmer")
    assert zone.solar_gain_factor == Decimal("0")  # the documented default
    _fixed_setpoint(session, zone, Decimal("21.0"))
    session.flush()

    row = shadow_run.cycle(session, NOW, _sunny_forecast())[0]

    assert row.setpoint_c == Decimal("21.0")
    assert "Sonnenabsenkung" not in row.setpoint_reason


def test_the_setback_is_capped_at_the_configured_maximum(session: Session) -> None:
    settings = create_settings(session)
    settings.default_solar_setback_max_k = Decimal("1.0")
    zone = create_zone(session, "gedeckelt")
    zone.solar_gain_factor = Decimal("1.0")
    _fixed_setpoint(session, zone, Decimal("30.0"))
    session.flush()

    row = shadow_run.cycle(session, NOW, _sunny_forecast())[0]

    assert row.setpoint_c == Decimal("29.0")  # capped at 1.0 K, not the full headroom
    assert "Sonnenabsenkung: -1.0 K" in row.setpoint_reason


def test_the_setback_never_drops_the_setpoint_below_frost_protection(
    session: Session,
) -> None:
    """Task requirement: frost protection is a floor the setback must never cross,
    even with a generous per-zone cap and a strong factor."""
    settings = create_settings(session)
    settings.default_solar_setback_max_k = Decimal("5.0")
    zone = create_zone(session, "knapp-ueber-frostschutz")
    zone.solar_gain_factor = Decimal("1.0")
    # Only 1 K of headroom above the 16.0 °C frost-protection fallback.
    _fixed_setpoint(session, zone, Decimal("17.0"))
    session.flush()

    row = shadow_run.cycle(session, NOW, _sunny_forecast())[0]

    assert row.setpoint_c == Decimal("16.0")
    assert "Sonnenabsenkung: -1.0 K" in row.setpoint_reason


def test_a_zone_override_setting_the_maximum_takes_precedence(session: Session) -> None:
    """Confirms the setback reads the per-zone override (`ControlParameters`
    inheritance), not only the global default."""
    settings = create_settings(session)
    settings.default_solar_setback_max_k = Decimal("2.0")
    zone = create_zone(session, "eigene-obergrenze")
    zone.solar_gain_factor = Decimal("1.0")
    zone.solar_setback_max_k = Decimal("0.5")  # zone deviation, below the global default
    _fixed_setpoint(session, zone, Decimal("21.0"))
    session.flush()

    row = shadow_run.cycle(session, NOW, _sunny_forecast())[0]

    assert row.setpoint_c == Decimal("20.5")
    assert "Sonnenabsenkung: -0.5 K" in row.setpoint_reason
