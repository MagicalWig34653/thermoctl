"""`thermoctl.app._solar_forecast` -- the glue between `setting` and `ForecastCache`.

Confirms the three ways this collapses to "no forecast, no setback" (feature off,
no location configured, cache unavailable) and the one way it actually fetches.
"""

import types
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import update
from sqlalchemy.orm import Session

from tests.helpers import create_settings
from thermoctl import app as app_modul
from thermoctl.config import Settings
from thermoctl.db.base import Base
from thermoctl.db.engine import create_engine_from_settings, session_factory, session_scope
from thermoctl.db.models.operations import Setting
from thermoctl.domain.solar_setback import HourlyForecast

NOW = datetime(2026, 8, 30, 8, 0)


class _FakeCache:
    def __init__(self, forecast: list[HourlyForecast] | None) -> None:
        self.forecast = forecast
        self.calls: list[tuple[Decimal, Decimal, datetime]] = []

    async def get(
        self, latitude: Decimal, longitude: Decimal, now: datetime
    ) -> list[HourlyForecast] | None:
        self.calls.append((latitude, longitude, now))
        return self.forecast


def _fake_app(cache: object | None) -> object:
    return types.SimpleNamespace(state=types.SimpleNamespace(forecast_cache=cache))


@pytest.mark.anyio
async def test_disabled_by_default_never_touches_the_cache(session: Session) -> None:
    create_settings(session)  # solar_forecast_enabled defaults to False
    cache = _FakeCache([HourlyForecast(NOW, None, Decimal(300))])

    result = await app_modul._solar_forecast(_fake_app(cache), lambda: session, NOW)  # type: ignore[arg-type]

    assert result is None
    assert cache.calls == []


@pytest.mark.anyio
async def test_enabled_but_without_a_location_stays_off(session: Session) -> None:
    """CLAUDE.md principle 1: there is no sensible default location, so the switch
    alone must not be enough."""
    settings = create_settings(session)
    settings.solar_forecast_enabled = True
    session.flush()
    cache = _FakeCache([HourlyForecast(NOW, None, Decimal(300))])

    result = await app_modul._solar_forecast(_fake_app(cache), lambda: session, NOW)  # type: ignore[arg-type]

    assert result is None
    assert cache.calls == []


@pytest.mark.anyio
async def test_enabled_and_configured_fetches_through_the_cache(session: Session) -> None:
    settings = create_settings(session)
    settings.solar_forecast_enabled = True
    settings.solar_forecast_latitude = Decimal("52.520")
    settings.solar_forecast_longitude = Decimal("13.405")
    session.flush()
    forecast = [HourlyForecast(NOW, None, Decimal(300))]
    cache = _FakeCache(forecast)

    result = await app_modul._solar_forecast(_fake_app(cache), lambda: session, NOW)  # type: ignore[arg-type]

    assert result == forecast
    assert cache.calls == [(Decimal("52.520"), Decimal("13.405"), NOW)]


@pytest.mark.anyio
async def test_no_setting_row_at_all_stays_off(session: Session) -> None:
    """Setup not completed yet -- must not raise, exactly like the rest of the
    shadow loop's degraded-startup handling."""
    result = await app_modul._solar_forecast(
        _fake_app(_FakeCache([])), lambda: session, NOW  # type: ignore[arg-type]
    )
    assert result is None


@pytest.mark.anyio
async def test_a_missing_cache_is_treated_like_no_forecast(session: Session) -> None:
    """Reachable only when the lifespan never ran -- as in several existing shadow
    loop tests that build a bare `SimpleNamespace` app. Configuring the feature must
    not turn that into a crash."""
    settings = create_settings(session)
    settings.solar_forecast_enabled = True
    settings.solar_forecast_latitude = Decimal("52.520")
    settings.solar_forecast_longitude = Decimal("13.405")
    session.flush()

    result = await app_modul._solar_forecast(_fake_app(None), lambda: session, NOW)  # type: ignore[arg-type]

    assert result is None


@pytest.mark.anyio
async def test_network_fetch_does_not_hold_a_sqlite_transaction(tmp_path: Path) -> None:
    """A request updating the same SQLite file must commit while network I/O waits."""
    database = tmp_path / "forecast-lock.db"
    engine = create_engine_from_settings(Settings(database_url=f"sqlite:///{database}"))
    Base.metadata.create_all(engine)
    factory = session_factory(engine)
    with session_scope(factory) as setup_session:
        settings = create_settings(setup_session)
        settings.solar_forecast_enabled = True
        settings.solar_forecast_latitude = Decimal("52.520")
        settings.solar_forecast_longitude = Decimal("13.405")

    class _ConcurrentWriter:
        async def get(
            self, latitude: Decimal, longitude: Decimal, now: datetime
        ) -> list[HourlyForecast]:
            with session_scope(factory) as writer:
                writer.execute(
                    update(Setting).where(Setting.id == 1).values(timezone="Europe/Berlin")
                )
            return [HourlyForecast(NOW, None, Decimal(300))]

    try:
        result = await app_modul._solar_forecast(
            _fake_app(_ConcurrentWriter()), factory, NOW  # type: ignore[arg-type]
        )
    finally:
        engine.dispose()

    assert result == [HourlyForecast(NOW, None, Decimal(300))]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
