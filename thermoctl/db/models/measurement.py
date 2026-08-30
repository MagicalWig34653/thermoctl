from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from thermoctl.db.base import Base


class Measurement(Base):
    """An immutable measured value with exactly one value representation."""

    __tablename__ = "measurement"
    __table_args__ = (
        CheckConstraint(
            "(value_numeric IS NULL) <> (value_text IS NULL)",
            name="genau_ein_wert",
        ),
        Index(
            "ix_measurement_device_capability_measured",
            "device_id",
            "capability_id",
            "measured_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_id: Mapped[int] = mapped_column(
        ForeignKey("device.id", ondelete="CASCADE"), nullable=False
    )
    capability_id: Mapped[int] = mapped_column(
        ForeignKey("device_capability.id"), nullable=False
    )
    value_numeric: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)
    value_text: Mapped[str | None] = mapped_column(String(32), nullable=True)
    measured_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class DeviceHealth(Base):
    """The last received sign of life from a device."""

    __tablename__ = "device_health"

    device_id: Mapped[int] = mapped_column(
        ForeignKey("device.id", ondelete="CASCADE"), primary_key=True
    )
    last_payload_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    link_quality: Mapped[int | None] = mapped_column(Integer, nullable=True)
    battery_percent: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    availability: Mapped[str | None] = mapped_column(String(16), nullable=True)
    payload_count: Mapped[int] = mapped_column(Integer, nullable=False)
