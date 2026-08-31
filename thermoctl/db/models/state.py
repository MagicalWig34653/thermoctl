from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, false
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column

from thermoctl.db.base import Base


class ZoneState(Base):
    """The most recently derived zone state, used for decisions."""

    __tablename__ = "zone_state"

    zone_id: Mapped[int] = mapped_column(
        ForeignKey("zone.id", ondelete="CASCADE"), primary_key=True
    )
    temperature_c: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    measured_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    sensor_status_id: Mapped[int] = mapped_column(ForeignKey("sensor_status.id"), nullable=False)
    window_open: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_regular_heat_at: Mapped[datetime | None] = mapped_column(
        DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql", "mariadb"), nullable=True
    )
    regular_heat_history_compacted: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False
    )
    valve_protection_started_at: Mapped[datetime | None] = mapped_column(
        DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql", "mariadb"), nullable=True
    )
    last_valve_protection_at: Mapped[datetime | None] = mapped_column(
        DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql", "mariadb"), nullable=True
    )


class ShadowDecision(Base):
    """Traceable result of a single shadow cycle."""

    __tablename__ = "shadow_decision"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    decided_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    # CASCADE as with all other zone relationships. The shadow log is operational data
    # of a zone; if it is deleted, the log loses its reference. That the zone was
    # deleted is recorded in the audit log — that is the record meant to persist, and
    # it does not depend on the zone.
    zone_id: Mapped[int] = mapped_column(
        ForeignKey("zone.id", ondelete="CASCADE"), nullable=False
    )
    temperature_c: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    setpoint_c: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    setpoint_reason: Mapped[str] = mapped_column(String(255), nullable=False)
    would_heat: Mapped[bool] = mapped_column(Boolean, nullable=False)
    previous_would_heat: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    outcome_code: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
