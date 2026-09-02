from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from thermoctl.domain.modes import MAXIMUM_TEMPERATURE_C, MINIMUM_TEMPERATURE_C
from thermoctl.domain.zone_settings import (
    MAXIMUM_VALVE_PROTECTION_DURATION_MINUTES,
    MAXIMUM_VALVE_PROTECTION_INTERVAL_DAYS,
)


class ZoneResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    display_name: str
    solar_gain_factor: Decimal


class WriteZone(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=128)
    operating_mode_id: int
    sort_order: int = 0
    temperature_source_device_id: int | None = None
    # How much this zone gains from sunshine: 0 for not at all, 1 for a lot. The
    # default is 0, so a caller that does not know about solar setback switches it
    # off for the zone rather than silently enabling it.
    solar_gain_factor: Decimal = Field(default=Decimal(0), ge=0, le=1)


class ModeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    sort_order: int
    is_builtin: bool


class CreateMode(BaseModel):
    code: str
    name: str
    sort_order: int = 0


class SetpointEntry(BaseModel):
    mode_id: int
    temperature_c: Decimal | None


class WriteSetpoints(BaseModel):
    setpoints: list[SetpointEntry]


class SetpointResponse(BaseModel):
    mode_id: int
    mode_code: str
    mode_name: str
    temperature_c: Decimal | None


class CreateSchedulePoint(BaseModel):
    weekday: int = Field(ge=1, le=7)
    minute_of_day: int = Field(ge=0, le=1439)
    mode_id: int


class SchedulePointResponse(BaseModel):
    id: int
    weekday: int
    minute_of_day: int
    mode_id: int
    mode_name: str


class WriteControlParameters(BaseModel):
    hysteresis_k: Decimal | None = Field(default=None, ge=0)
    min_on_seconds: int | None = Field(default=None, ge=0)
    min_off_seconds: int | None = Field(default=None, ge=0)
    sensor_timeout_seconds: int | None = Field(default=None, ge=0)
    temperature_offset_k: Decimal | None = None
    window_resume_delay_seconds: int | None = Field(default=None, ge=0)
    solar_setback_max_k: Decimal | None = Field(default=None, ge=0)
    valve_protection_enabled: bool = False
    valve_protection_interval_days: int = Field(
        default=30, gt=0, le=MAXIMUM_VALVE_PROTECTION_INTERVAL_DAYS
    )
    valve_protection_duration_minutes: int = Field(
        default=10, gt=0, le=MAXIMUM_VALVE_PROTECTION_DURATION_MINUTES
    )


class ControlParametersResponse(WriteControlParameters):
    pass


class DeviceResponse(BaseModel):
    id: int
    external_id: str
    display_name: str
    integration: str
    model: str | None
    is_group: bool
    capabilities: list[str]
    last_payload_at: datetime | None
    battery_percent: Decimal | None
    link_quality: int | None
    availability: str | None
    zones: list[str]


class ZoneStateResponse(BaseModel):
    zone_id: int
    temperature_c: Decimal | None
    measured_at: datetime | None
    sensor_status: str
    window_open: bool | None
    updated_at: datetime


class DeviceCommandResponse(BaseModel):
    """One entry of the actuator command log -- read-only, see `docs/api.md`.

    `sent_at` is always timezone-aware UTC (serialized with a `Z` suffix), never a naive
    value: the internal representation is naive UTC everywhere else in this project, and
    a naive value handed to a caller in a different zone would be read as local time.
    """

    id: int
    sent_at: datetime
    source: str
    zone: str
    device: str
    command: str
    payload: str
    outcome: str
    error: str | None
    reason: str | None


class TokenResponse(BaseModel):
    id: int
    name: str
    prefix: str
    user_id: int
    expires_at: datetime | None


class CreateOverride(BaseModel):
    # The numbers live in the domain; here only so they show up in the OpenAPI
    # description. Rejecting is done by the domain.
    temperature_c: Decimal = Field(
        ge=MINIMUM_TEMPERATURE_C, le=MAXIMUM_TEMPERATURE_C, decimal_places=1
    )
    duration_minutes: int | None = Field(default=None, gt=0)
    until_next_switch: bool = False


class OverrideResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    zone_id: int
    temperature_c: Decimal | None
    starts_at: datetime
    ends_at: datetime | None
    cancelled_at: datetime | None


class BoostResponse(BaseModel):
    """What the pulled-forward schedule point achieved.

    The mode is included, not just the temperature: "18.0 °C until 22:00" doesn't say
    *why* -- "night, pulled forward until 22:00" does.
    """

    zone_id: int
    mode_code: str
    temperature_c: Decimal
    gilt_bis: datetime


class WriteParameter(BaseModel):
    """A single control parameter.

    The limits live in the domain (`domain/zone_settings.PARAMETER`) and are checked
    there. Here only says that a number is expected -- a second pair of limits at this
    spot would have fallen behind on the next change.
    """

    value: Decimal


class ControlResponse(BaseModel):
    """The plant's operating state along with the defaults every zone inherits from."""

    control_armed: bool
    timezone: str
    polling_interval_seconds: int
    shadow_interval_seconds: int
    default_hysteresis_k: Decimal
    default_min_on_seconds: int
    default_min_off_seconds: int
    default_sensor_timeout_seconds: int
    default_window_resume_delay_seconds: int
    measurement_retention_days: int
    shadow_decision_retention_days: int
    session_lifetime_seconds: int
    default_solar_setback_max_k: Decimal
    solar_setback_lookahead_hours: int
    solar_forecast_enabled: bool
    solar_forecast_latitude: Decimal | None
    solar_forecast_longitude: Decimal | None


class SetArmed(BaseModel):
    """`reason` is required when arming and optional when disarming --
    the check for that lives in the domain, not here, so it's the same for
    all three adapters."""

    armed: bool
    reason: str = ""


class WriteControl(BaseModel):
    """The global defaults. The domain checks the limits: a `Field(ge=..., le=...)`
    here would be a second version of the same numbers, and two versions drift
    apart."""

    timezone: str = Field(min_length=1, max_length=64)
    polling_interval_seconds: int
    shadow_interval_seconds: int
    default_hysteresis_k: Decimal
    default_min_on_seconds: int
    default_min_off_seconds: int
    default_sensor_timeout_seconds: int
    default_window_resume_delay_seconds: int
    measurement_retention_days: int
    shadow_decision_retention_days: int
    session_lifetime_seconds: int
    default_solar_setback_max_k: Decimal
    solar_setback_lookahead_hours: int


class MoveSchedulePoint(BaseModel):
    weekday: int = Field(ge=1, le=7)
    minute_of_day: int = Field(ge=0, le=1439)


class WriteSolarLocation(BaseModel):
    """Switch and location for the solar forecast.

    Separate from `WriteControl` for the same reason the domain keeps
    `save_solar_location` apart from `save_settings`: those are bounded numbers with a
    global default, this is a location that has none. The coordinates are text so the
    domain performs the check -- a `Field(ge=-90, le=90)` here would be a second
    version of the same limits, and two versions drift apart.
    """

    enabled: bool
    latitude: str = ""
    longitude: str = ""
