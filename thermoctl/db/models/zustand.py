from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from thermoctl.db.base import Base


class ZoneState(Base):
    """Der zuletzt abgeleitete und fuer Entscheidungen verwendete Zonenzustand."""

    __tablename__ = "zone_state"

    zone_id: Mapped[int] = mapped_column(
        ForeignKey("zone.id", ondelete="CASCADE"), primary_key=True
    )
    temperature_c: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    measured_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    sensor_status_id: Mapped[int] = mapped_column(ForeignKey("sensor_status.id"), nullable=False)
    window_open: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class ShadowDecision(Base):
    """Nachvollziehbares Ergebnis eines einzelnen Schattenzyklus."""

    __tablename__ = "shadow_decision"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    decided_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    zone_id: Mapped[int] = mapped_column(ForeignKey("zone.id"), nullable=False)
    temperature_c: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    setpoint_c: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    setpoint_reason: Mapped[str] = mapped_column(String(255), nullable=False)
    would_heat: Mapped[bool] = mapped_column(Boolean, nullable=False)
    previous_would_heat: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    outcome_code: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
