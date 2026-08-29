from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class ZoneAntwort(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    display_name: str


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
