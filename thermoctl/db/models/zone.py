from decimal import Decimal

from sqlalchemy import Boolean, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from thermoctl.db.base import Base, TimestampMixin
from thermoctl.db.models.lookup import OperatingMode


class SetpointMode(Base):
    """Frei anlegbarer Sollwert-Modus: Tag, Nacht, Frostschutz, Urlaub, …

    Welcher Modus der Frostschutz ist, steht ausschliesslich in
    `setting.frost_protection_mode_id` und nicht zusaetzlich hier — zwei Quellen fuer
    dieselbe Aussage geraten auseinander.
    """

    __tablename__ = "setpoint_mode"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class Zone(TimestampMixin, Base):
    """Ersetzt `rooms` und `thermostate` gemeinsam.

    Die sechs Regelparameter sind nullbar: leer heisst 'globaler Standard aus `setting`'.
    So steht jeder Wert genau einmal irgendwo.
    """

    __tablename__ = "zone"

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


class ZoneSetpoint(Base):
    __tablename__ = "zone_setpoint"

    zone_id: Mapped[int] = mapped_column(
        ForeignKey("zone.id", ondelete="CASCADE"), primary_key=True
    )
    setpoint_mode_id: Mapped[int] = mapped_column(
        ForeignKey("setpoint_mode.id"), primary_key=True
    )
    temperature_c: Mapped[Decimal] = mapped_column(Numeric(4, 1), nullable=False)
