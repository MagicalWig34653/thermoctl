from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class ZoneAntwort(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    display_name: str


class ZoneSchreiben(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=128)
    operating_mode_id: int
    sort_order: int = 0
    temperature_source_device_id: int | None = None


class ModusAntwort(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    sort_order: int
    is_builtin: bool


class ModusAnlegen(BaseModel):
    code: str
    name: str
    sort_order: int = 0


class SollwertEintrag(BaseModel):
    mode_id: int
    temperature_c: Decimal | None


class SollwerteSchreiben(BaseModel):
    setpoints: list[SollwertEintrag]


class SollwertAntwort(BaseModel):
    mode_id: int
    mode_code: str
    mode_name: str
    temperature_c: Decimal | None


class ZeitplanpunktAnlegen(BaseModel):
    weekday: int = Field(ge=1, le=7)
    minute_of_day: int = Field(ge=0, le=1439)
    mode_id: int


class ZeitplanpunktAntwort(BaseModel):
    id: int
    weekday: int
    minute_of_day: int
    mode_id: int
    mode_name: str


class RegelparameterSchreiben(BaseModel):
    hysteresis_k: Decimal | None = Field(default=None, ge=0)
    min_on_seconds: int | None = Field(default=None, ge=0)
    min_off_seconds: int | None = Field(default=None, ge=0)
    sensor_timeout_seconds: int | None = Field(default=None, ge=0)
    temperature_offset_k: Decimal | None = None
    window_resume_delay_seconds: int | None = Field(default=None, ge=0)


class RegelparameterAntwort(RegelparameterSchreiben):
    pass


class GeraetAntwort(BaseModel):
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


class ZonenzustandAntwort(BaseModel):
    zone_id: int
    temperature_c: Decimal | None
    measured_at: datetime | None
    sensor_status: str
    window_open: bool | None
    updated_at: datetime


class TokenAntwort(BaseModel):
    id: int
    name: str
    prefix: str
    user_id: int
    expires_at: datetime | None


class UebersteuerungAnlegen(BaseModel):
    temperature_c: Decimal = Field(ge=5, le=35, decimal_places=1)
    dauer_minuten: int | None = Field(default=None, gt=0)
    bis_naechste_schaltung: bool = False


class UebersteuerungAntwort(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    zone_id: int
    temperature_c: Decimal | None
    starts_at: datetime
    ends_at: datetime | None
    cancelled_at: datetime | None
