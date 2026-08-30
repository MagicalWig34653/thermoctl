"""The solar forecast source: Open-Meteo, an hourly cache, and nothing else.

No signup, no key -- so no secret ever needs to sit in this file or in configuration
(CLAUDE.md principle 2). Behind `SolarForecastTransport` so the test suite can supply
a fake and never touch the network, the same shape as `HttpTransport` in
`integrations/actuators.py`.

The forecast changes hourly, not by the minute, so a fetch is cached and reused for an
hour. A failed fetch clears nothing and predicts nothing: `ForecastCache.get()` returns
`None`, which `domain.solar_setback` and `services.shadow_run` already treat exactly
like "feature switched off" -- the safe direction when a third party is unreachable.
"""

import asyncio
import json
import logging
from datetime import datetime
from decimal import Decimal
from typing import Protocol
from urllib import parse, request

from thermoctl.domain.solar_setback import HourlyForecast

log = logging.getLogger(__name__)

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

# Two days is comfortably more than any reasonable lookahead window configured in
# `setting.solar_setback_lookahead_hours` (bounded to at most 12 hours, see
# `domain.control.LIMITS`), while staying a single, documented Open-Meteo parameter
# instead of a computed hour count that would need its own justification.
_FORECAST_DAYS = 2


class WeatherHttpTransport(Protocol):
    async def get(self, url: str) -> object: ...


class UrllibWeatherTransport:
    """Small HTTP wrapper, so the client doesn't need another dependency."""

    async def get(self, url: str) -> object:
        return await asyncio.to_thread(self._get_synchron, url)

    @staticmethod
    def _get_synchron(url: str) -> object:
        with request.urlopen(url, timeout=10) as response:  # noqa: S310 -- fixed, public API
            return json.loads(response.read())


class SolarForecastTransport(Protocol):
    async def fetch_hourly(
        self, latitude: Decimal, longitude: Decimal
    ) -> list[HourlyForecast]: ...


def _parse_hourly(response: object) -> list[HourlyForecast]:
    if not isinstance(response, dict):
        raise ValueError("Open-Meteo-Antwort ist kein Objekt")
    hourly = response.get("hourly")
    if not isinstance(hourly, dict):
        raise ValueError("Open-Meteo-Antwort ohne 'hourly'-Block")
    times = hourly.get("time")
    if not isinstance(times, list):
        raise ValueError("Open-Meteo-Antwort ohne Zeitstempel")
    clouds = hourly.get("cloud_cover")
    radiation = hourly.get("shortwave_radiation")
    clouds = clouds if isinstance(clouds, list) else [None] * len(times)
    radiation = radiation if isinstance(radiation, list) else [None] * len(times)
    return [
        HourlyForecast(
            time=datetime.fromisoformat(str(time_text)),
            cloud_cover_percent=None if cloud is None else Decimal(str(cloud)),
            shortwave_radiation_w_m2=None if sun is None else Decimal(str(sun)),
        )
        for time_text, cloud, sun in zip(times, clouds, radiation, strict=True)
    ]


class OpenMeteoForecastClient:
    """Fetches `cloud_cover` and `shortwave_radiation` for one location.

    Requested in `timezone=UTC` deliberately: `now` throughout the control loop is
    naive UTC (`thermoctl.db.base.utcnow`), and matching the forecast's own naive
    timestamps to that avoids a second, redundant timezone conversion here for a
    value that already gets converted once, correctly, in `domain.schedule`.
    """

    def __init__(self, transport: WeatherHttpTransport | None = None) -> None:
        self._transport = transport or UrllibWeatherTransport()

    async def fetch_hourly(
        self, latitude: Decimal, longitude: Decimal
    ) -> list[HourlyForecast]:
        query = parse.urlencode(
            {
                "latitude": str(latitude),
                "longitude": str(longitude),
                "hourly": "cloud_cover,shortwave_radiation",
                "timezone": "UTC",
                "forecast_days": str(_FORECAST_DAYS),
            }
        )
        response = await self._transport.get(f"{OPEN_METEO_URL}?{query}")
        return _parse_hourly(response)


class ForecastCache:
    """Keeps the last successful fetch for up to `ttl_seconds`, per location.

    A changed location invalidates the cache immediately -- there is exactly one
    installation-wide location (`setting.solar_forecast_latitude/longitude`), so this
    only ever matters right after an operator changes it.
    """

    def __init__(
        self, transport: SolarForecastTransport | None = None, ttl_seconds: int = 3600
    ) -> None:
        self._transport = transport or OpenMeteoForecastClient()
        self._ttl_seconds = ttl_seconds
        self._forecast: list[HourlyForecast] | None = None
        self._fetched_at: datetime | None = None
        self._coordinates: tuple[Decimal, Decimal] | None = None

    async def get(
        self, latitude: Decimal, longitude: Decimal, now: datetime
    ) -> list[HourlyForecast] | None:
        """The cached or freshly fetched forecast -- or `None` on any failure.

        Deliberately swallows every exception from the transport: a malformed
        response, a timeout, DNS failure, whatever the source does wrong is not this
        service's fault to propagate into the control loop (CLAUDE.md principle 7 --
        the plant keeps controlling on its existing setpoint, just without a
        setback). It is logged so the failure is still visible to an operator.
        """
        if (
            self._forecast is not None
            and self._coordinates == (latitude, longitude)
            and self._fetched_at is not None
            and (now - self._fetched_at).total_seconds() < self._ttl_seconds
        ):
            return self._forecast
        try:
            forecast = await self._transport.fetch_hourly(latitude, longitude)
        except Exception:
            log.warning(
                "Sonnenprognose nicht erreichbar -- keine Absenkung in diesem Zyklus",
                exc_info=True,
            )
            return None
        self._forecast = forecast
        self._fetched_at = now
        self._coordinates = (latitude, longitude)
        return forecast
