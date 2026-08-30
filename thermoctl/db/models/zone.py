from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Integer, Numeric, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from thermoctl.db.base import Base, TimestampMixin
from thermoctl.db.models.lookup import OperatingMode


class SetpointMode(Base):
    """Freely creatable setpoint mode: day, night, frost protection, vacation, …

    Which mode is frost protection is stated exclusively in
    `setting.frost_protection_mode_id` and not additionally here — two sources for the
    same fact drift apart.
    """

    __tablename__ = "setpoint_mode"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class Zone(TimestampMixin, Base):
    """Replaces `rooms` and `thermostate` together.

    The six control parameters are nullable: empty means 'global default from
    `setting`'. This way every value lives in exactly one place.
    """

    __tablename__ = "zone"
    __table_args__ = (
        CheckConstraint(
            "solar_gain_factor BETWEEN 0 AND 1", name="solar_gain_faktor_0_bis_1"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    operating_mode_id: Mapped[int] = mapped_column(
        ForeignKey("operating_mode.id"), nullable=False
    )
    temperature_source_device_id: Mapped[int | None] = mapped_column(
        ForeignKey("device.id", ondelete="SET NULL"), nullable=True
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    operating_mode: Mapped[OperatingMode] = relationship(lazy="joined")

    hysteresis_k: Mapped[Decimal | None] = mapped_column(Numeric(4, 2), nullable=True)
    min_on_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    min_off_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sensor_timeout_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    temperature_offset_k: Mapped[Decimal | None] = mapped_column(Numeric(4, 2), nullable=True)
    window_resume_delay_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # How strongly this zone profits from sunshine, 0 (not at all, e.g. a north-facing
    # room) to 1 (strongly, e.g. a room with roof windows). Default 0 -- off, like the
    # rest of the solar setback feature until an operator explicitly configures it.
    # Not nullable and not inherited from a global default like the six fields above:
    # there is no meaningful installation-wide default for "how sunny is this room".
    solar_gain_factor: Mapped[Decimal] = mapped_column(
        Numeric(3, 2), default=Decimal("0"), server_default=text("0"), nullable=False
    )
    # The zone's own cap on the setback in Kelvin -- nullable and inherited exactly
    # like the six control parameters in `ControlParameters` above: empty means
    # `setting.default_solar_setback_max_k`.
    solar_setback_max_k: Mapped[Decimal | None] = mapped_column(Numeric(3, 1), nullable=True)


class ZoneSetpoint(Base):
    __tablename__ = "zone_setpoint"

    zone_id: Mapped[int] = mapped_column(
        ForeignKey("zone.id", ondelete="CASCADE"), primary_key=True
    )
    setpoint_mode_id: Mapped[int] = mapped_column(
        ForeignKey("setpoint_mode.id"), primary_key=True
    )
    temperature_c: Mapped[Decimal] = mapped_column(Numeric(4, 1), nullable=False)
