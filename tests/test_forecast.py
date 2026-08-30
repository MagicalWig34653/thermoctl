"""Tests for `thermoctl.integrations.forecast` -- the Open-Meteo client and its cache.

No test here ever touches the network (CLAUDE.md: 'the test suite must never go
online'): `OpenMeteoForecastClient` is exercised against a fake `WeatherHttpTransport`
that returns a canned payload, and `ForecastCache` against a fake `SolarForecastTransport`
that can be made to fail on demand.
"""

import json
from datetime import datetime
from decimal import Decimal

import pytest

from thermoctl.domain.solar_setback import HourlyForecast
from thermoctl.integrations import forecast as forecast_module
from thermoctl.integrations.forecast import (
    ForecastCache,
    OpenMeteoForecastClient,
    UrllibWeatherTransport,
)

NOW = datetime(2026, 8, 30, 8, 0)


class _FakeWeatherHttpTransport:
    """Records the last URL it was asked to fetch, and returns a fixed payload."""

    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.urls: list[str] = []

    async def get(self, url: str) -> object:
        self.urls.append(url)
        return self.payload


_SAMPLE_RESPONSE = {
    "hourly": {
        "time": ["2026-08-30T08:00", "2026-08-30T09:00", "2026-08-30T10:00"],
        "cloud_cover": [80, 40, None],
        "shortwave_radiation": [50, 220, None],
    }
}


@pytest.mark.anyio
async def test_the_client_parses_the_hourly_fields_it_was_told_are_there() -> None:
    transport = _FakeWeatherHttpTransport(_SAMPLE_RESPONSE)
    client = OpenMeteoForecastClient(transport)

    forecast = await client.fetch_hourly(Decimal("52.5"), Decimal("13.4"))

    assert forecast == [
        HourlyForecast(datetime(2026, 8, 30, 8, 0), Decimal("80"), Decimal("50")),
        HourlyForecast(datetime(2026, 8, 30, 9, 0), Decimal("40"), Decimal("220")),
        HourlyForecast(datetime(2026, 8, 30, 10, 0), None, None),
    ]


@pytest.mark.anyio
async def test_the_request_names_latitude_longitude_and_the_two_fields() -> None:
    transport = _FakeWeatherHttpTransport(_SAMPLE_RESPONSE)
    client = OpenMeteoForecastClient(transport)

    await client.fetch_hourly(Decimal("52.520"), Decimal("13.405"))

    (url,) = transport.urls
    assert "latitude=52.520" in url
    assert "longitude=13.405" in url
    assert "cloud_cover" in url
    assert "shortwave_radiation" in url


@pytest.mark.anyio
async def test_a_response_without_an_hourly_block_is_rejected() -> None:
    transport = _FakeWeatherHttpTransport({"unexpected": True})
    client = OpenMeteoForecastClient(transport)

    with pytest.raises(ValueError, match="hourly"):
        await client.fetch_hourly(Decimal("0"), Decimal("0"))


@pytest.mark.anyio
async def test_a_response_that_is_not_an_object_is_rejected() -> None:
    transport = _FakeWeatherHttpTransport([1, 2, 3])
    client = OpenMeteoForecastClient(transport)

    with pytest.raises(ValueError, match="kein Objekt"):
        await client.fetch_hourly(Decimal("0"), Decimal("0"))


@pytest.mark.anyio
async def test_a_response_without_a_time_series_is_rejected() -> None:
    transport = _FakeWeatherHttpTransport({"hourly": {"cloud_cover": [1]}})
    client = OpenMeteoForecastClient(transport)

    with pytest.raises(ValueError, match="Zeitstempel"):
        await client.fetch_hourly(Decimal("0"), Decimal("0"))


@pytest.mark.anyio
async def test_the_urllib_transport_reaches_urlopen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real HTTP wrapper -- exercised against a fake `urlopen`, the same pattern
    `test_actuators.py` uses for `UrllibHttpTransport`. No test in this suite ever
    reaches the actual network."""

    class _FakeResponse:
        def __enter__(self) -> _FakeResponse:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps(_SAMPLE_RESPONSE).encode()

    captured: dict[str, object] = {}

    def _fake_urlopen(url: str, timeout: int) -> _FakeResponse:
        captured["url"] = url
        captured["timeout"] = timeout
        return _FakeResponse()

    monkeypatch.setattr(forecast_module.request, "urlopen", _fake_urlopen)

    result = await UrllibWeatherTransport().get("https://example.invalid/forecast")

    assert result == _SAMPLE_RESPONSE
    assert captured["url"] == "https://example.invalid/forecast"


class _FakeSolarForecastTransport:
    def __init__(self) -> None:
        self.calls = 0
        self.fail = False

    async def fetch_hourly(
        self, latitude: Decimal, longitude: Decimal
    ) -> list[HourlyForecast]:
        self.calls += 1
        if self.fail:
            raise RuntimeError("Simulated network failure")
        return [HourlyForecast(NOW, Decimal("10"), Decimal("300"))]


@pytest.mark.anyio
async def test_a_fresh_fetch_is_cached_within_the_ttl() -> None:
    transport = _FakeSolarForecastTransport()
    cache = ForecastCache(transport, ttl_seconds=3600)

    first = await cache.get(Decimal("52.5"), Decimal("13.4"), NOW)
    from datetime import timedelta

    second = await cache.get(
        Decimal("52.5"), Decimal("13.4"), NOW + timedelta(minutes=30)
    )

    assert first == second
    assert transport.calls == 1  # the second call was served from the cache


@pytest.mark.anyio
async def test_the_cache_is_refetched_once_the_ttl_has_elapsed() -> None:
    from datetime import timedelta

    transport = _FakeSolarForecastTransport()
    cache = ForecastCache(transport, ttl_seconds=3600)

    await cache.get(Decimal("52.5"), Decimal("13.4"), NOW)
    await cache.get(Decimal("52.5"), Decimal("13.4"), NOW + timedelta(hours=2))

    assert transport.calls == 2


@pytest.mark.anyio
async def test_a_changed_location_is_never_served_from_the_old_cache() -> None:
    transport = _FakeSolarForecastTransport()
    cache = ForecastCache(transport, ttl_seconds=3600)

    await cache.get(Decimal("52.5"), Decimal("13.4"), NOW)
    await cache.get(Decimal("48.1"), Decimal("11.5"), NOW)

    assert transport.calls == 2


@pytest.mark.anyio
async def test_a_failed_fetch_returns_none_instead_of_raising() -> None:
    """The task's safety requirement: a source failure must not disturb control --
    the caller (`thermoctl.app._solar_forecast`) treats `None` exactly like the
    feature being switched off."""
    transport = _FakeSolarForecastTransport()
    transport.fail = True
    cache = ForecastCache(transport)

    result = await cache.get(Decimal("52.5"), Decimal("13.4"), NOW)

    assert result is None


@pytest.mark.anyio
async def test_a_failure_does_not_poison_a_previously_cached_result() -> None:
    """A transient outage right after a successful fetch must not throw away the
    forecast that was already good for this hour."""
    from datetime import timedelta

    transport = _FakeSolarForecastTransport()
    cache = ForecastCache(transport, ttl_seconds=3600)

    good = await cache.get(Decimal("52.5"), Decimal("13.4"), NOW)
    assert good is not None

    transport.fail = True
    # Still within the TTL -- must be served from the cache, not attempt (and fail)
    # a refetch.
    still_good = await cache.get(
        Decimal("52.5"), Decimal("13.4"), NOW + timedelta(minutes=10)
    )

    assert still_good == good
    assert transport.calls == 1


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
