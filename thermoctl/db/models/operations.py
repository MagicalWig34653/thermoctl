from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    false,
    text,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from thermoctl.db.base import Base, utcnow


class Setting(Base):
    """Exactly one row with typed columns — replaces the EAV table `heizung_conf`.

    A new setting is an Alembic migration instead of a string that only shows up as an
    error at runtime.
    """

    __tablename__ = "setting"
    __table_args__ = (CheckConstraint("id = 1", name="genau_eine_zeile"),)

    # autoincrement=False is mandatory here, not cosmetic: MariaDB otherwise assigns
    # AUTO_INCREMENT and then forbids any CHECK constraint on the same column (error
    # 1901). It is the correct choice on the merits anyway — a table with exactly one
    # row does not need an automatically assigned key.
    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=False, default=1
    )
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Berlin", nullable=False)
    polling_interval_seconds: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    default_hysteresis_k: Mapped[Decimal] = mapped_column(
        Numeric(4, 2), default=Decimal("0.30"), nullable=False
    )
    default_min_on_seconds: Mapped[int] = mapped_column(Integer, default=300, nullable=False)
    default_min_off_seconds: Mapped[int] = mapped_column(Integer, default=300, nullable=False)
    default_sensor_timeout_seconds: Mapped[int] = mapped_column(
        Integer, default=1800, nullable=False
    )
    default_window_resume_delay_seconds: Mapped[int] = mapped_column(
        Integer, default=120, nullable=False
    )
    frost_protection_mode_id: Mapped[int] = mapped_column(
        ForeignKey("setpoint_mode.id"), nullable=False
    )
    session_lifetime_seconds: Mapped[int] = mapped_column(
        Integer,
        default=1209600,
        nullable=False,  # 14 days
    )
    control_armed: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False
    )
    measurement_retention_days: Mapped[int] = mapped_column(
        Integer, default=30, server_default=text("30"), nullable=False
    )
    shadow_decision_retention_days: Mapped[int] = mapped_column(
        Integer, default=365, server_default=text("365"), nullable=False
    )
    shadow_interval_seconds: Mapped[int] = mapped_column(
        Integer, default=60, server_default=text("60"), nullable=False
    )
    # Replaces the module constant `domain.statistics.
    # DEFAULT_ASSUMED_RELAY_LIFETIME_OPERATIONS` as the number the relay-wear
    # statistic compares against. Still an explicitly replaceable assumption, not a
    # measurement -- see the reasoning next to that constant and the bounds in
    # `domain.control.LIMITS`.
    assumed_relay_lifetime_operations: Mapped[int] = mapped_column(
        Integer, default=500_000, server_default=text("500000"), nullable=False
    )
    # --- Solar setback ---------------------------------------------------------
    # Off by default, and effectively off regardless of this flag while latitude or
    # longitude is unset (there is no sensible default location -- CLAUDE.md
    # principle 1). The location is domain configuration, not a secret, which is why
    # it lives here and not in `thermoctl.config.Settings`.
    solar_forecast_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False
    )
    solar_forecast_latitude: Mapped[Decimal | None] = mapped_column(
        Numeric(6, 3), nullable=True
    )
    solar_forecast_longitude: Mapped[Decimal | None] = mapped_column(
        Numeric(6, 3), nullable=True
    )
    solar_setback_lookahead_hours: Mapped[int] = mapped_column(
        Integer, default=3, server_default=text("3"), nullable=False
    )
    default_solar_setback_max_k: Mapped[Decimal] = mapped_column(
        Numeric(3, 1), default=Decimal("2.0"), server_default=text("2.0"), nullable=False
    )
    # --- Benachrichtigungen -----------------------------------------------------
    # One on/off switch per `domain.fault_notice` notice kind, anlagenweit -- not
    # per zone, not per severity (the project owner's own words: "Einstellungen,
    # welche Benachrichtigungen wobei gesendet werden"). Default `True` for all
    # three: whoever gets notices today keeps getting them after an upgrade
    # introduces these switches, instead of silently going quiet. Gated only in
    # front of the generic delivery path (log + webhook,
    # `integrations/notification.py`) -- Home Assistant has its own path for the
    # notices it already reaches and is deliberately not coupled to these, see
    # `services/publishing.py::send_fault_notice`.
    notify_sensor_faults: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=true(), nullable=False
    )
    notify_bridge_faults: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=true(), nullable=False
    )
    notify_command_failures: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=true(), nullable=False
    )
    # The webhook's delivery state -- what the interface's second part shows.
    # `notify_last_attempt_at` and `notify_last_ok` are `NULL` together until the
    # first attempt ever happens (no webhook configured, or the service has never
    # tried to reach one yet); `notify_last_ok` alone tells success from failure
    # once an attempt has happened. `notify_last_error` carries a short,
    # operator-facing reason on failure -- deliberately shortened, and never the
    # webhook's token or its full address, because this column is shown back in
    # the interface (see `integrations/notification.py::deliver`).
    notify_last_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    notify_last_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    notify_last_error: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class AuditEvent(Base):
    """What should still be answerable weeks later.

    Written in the same transaction as the change, so that no entry exists for a
    change that did not take place.
    """

    __tablename__ = "audit_event"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False, index=True
    )
    source_id: Mapped[int] = mapped_column(ForeignKey("actor_source.id"), nullable=False)
    actor_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    actor_token_id: Mapped[int | None] = mapped_column(
        ForeignKey("api_token.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    object_type: Mapped[str] = mapped_column(String(64), nullable=False)
    object_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    summary: Mapped[str] = mapped_column(String(255), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
