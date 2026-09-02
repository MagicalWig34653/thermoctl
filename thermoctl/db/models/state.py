from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    false,
    text,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column

from thermoctl.db.base import Base


class ZoneState(Base):
    """The most recently derived zone state, used for decisions."""

    __tablename__ = "zone_state"
    __table_args__ = (
        CheckConstraint("pi_integral BETWEEN 0 AND 1", name="pi_integral_0_bis_1"),
        CheckConstraint(
            "pi_window_duty IS NULL OR pi_window_duty BETWEEN 0 AND 1",
            name="pi_window_duty_0_bis_1",
        ),
        CheckConstraint(
            "pi_time_balance_seconds BETWEEN -900 AND 900",
            name="pi_time_balance_minus_900_bis_900",
        ),
    )

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
    # Durable PI controller state. Unlike `shadow_decision`, this row is not subject
    # to retention and therefore remains the single source for the next cycle.
    pi_integral: Mapped[Decimal] = mapped_column(
        Numeric(12, 6), default=Decimal("0"), server_default=text("0"), nullable=False
    )
    pi_last_evaluated_at: Mapped[datetime | None] = mapped_column(
        DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql", "mariadb"), nullable=True
    )
    pi_setpoint_context_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    pi_last_control_armed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    pi_window_started_at: Mapped[datetime | None] = mapped_column(
        DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql", "mariadb"), nullable=True
    )
    pi_window_duty: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 6), nullable=True
    )
    pi_time_balance_seconds: Mapped[Decimal] = mapped_column(
        Numeric(12, 6), default=Decimal("0"), server_default=text("0"), nullable=False
    )
    pi_last_switch_at: Mapped[datetime | None] = mapped_column(
        DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql", "mariadb"), nullable=True
    )
    pi_last_switch_heating: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    pi_awaiting_boundary_until: Mapped[datetime | None] = mapped_column(
        DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql", "mariadb"), nullable=True
    )
    # Also carries the reason of a visible fallback; both cases neutralise the same
    # PI state and section 5 deliberately asks for one last-reason marker.
    pi_last_reset_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)


class ShadowDecision(Base):
    """Traceable result of a single shadow cycle."""

    __tablename__ = "shadow_decision"
    __table_args__ = (
        Index(
            "ix_shadow_decision_retention",
            "decided_at",
            "id",
        ),
        Index(
            "ix_shadow_decision_zone_decided_id",
            "zone_id",
            "decided_at",
            "id",
        ),
        CheckConstraint(
            "pi_integral_before IS NULL OR pi_integral_before BETWEEN 0 AND 1",
            name="pi_integral_before_0_bis_1",
        ),
        CheckConstraint(
            "pi_integral_after IS NULL OR pi_integral_after BETWEEN 0 AND 1",
            name="pi_integral_after_0_bis_1",
        ),
        CheckConstraint(
            "pi_raw_duty IS NULL OR pi_raw_duty BETWEEN 0 AND 1",
            name="pi_raw_duty_0_bis_1",
        ),
        CheckConstraint(
            "pi_frozen_duty IS NULL OR pi_frozen_duty BETWEEN 0 AND 1",
            name="pi_frozen_duty_0_bis_1",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    decided_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
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
    # Structured PI diagnostics are snapshots only. The durable values that feed the
    # next decision live above in `zone_state` and cannot disappear with retention.
    requested_controller: Mapped[str] = mapped_column(
        String(32), default="hysteresis", server_default=text("'hysteresis'"), nullable=False
    )
    effective_controller: Mapped[str] = mapped_column(
        String(32), default="hysteresis", server_default=text("'hysteresis'"), nullable=False
    )
    controller_fallback_reason: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    pi_error_k: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    pi_proportional_term: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 6), nullable=True
    )
    pi_integral_before: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 6), nullable=True
    )
    pi_integral_after: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 6), nullable=True
    )
    pi_raw_duty: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    pi_frozen_duty: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    pi_window_started_at: Mapped[datetime | None] = mapped_column(
        DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql", "mariadb"), nullable=True
    )
    pi_time_balance_before_seconds: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 6), nullable=True
    )
    pi_time_balance_after_seconds: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 6), nullable=True
    )
    pi_state_runtime_seconds: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 6), nullable=True
    )
    pi_integrator_action: Mapped[str | None] = mapped_column(String(32), nullable=True)
    pi_min_duration_decision: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pi_reset_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pi_candidate_would_heat: Mapped[bool | None] = mapped_column(Boolean, nullable=True)


class DeviceCommand(Base):
    """Every command actually sent towards -- or deliberately withheld from -- an
    actuator: when, to which device and zone, what payload, with what outcome, and
    why the regulation wanted it.

    `shadow_decision` says what the control loop *decided*. `audit_event` says what
    a *person* did. Neither says what really left the service towards a device --
    this table does. Once the plant is armed, this is the only place that stays
    answerable for what happened at a real actuator, possibly weeks later.

    **Deliberately not `ondelete="CASCADE"` like `shadow_decision`.** That table is
    operational data of a zone and may disappear with it -- that the zone was
    deleted is recorded in the audit log instead. This table plays the audit log's
    role for the physical side: what happened at a device should stay answerable
    even after the zone or the device that carried it out is later renamed or
    removed. `zone_id` and `device_id` therefore go `SET NULL` on deletion, and the
    name at the moment of sending is kept in `zone_name`/`device_name` so the row
    stays legible entirely on its own, independent of whether the referenced row
    still exists.
    """

    __tablename__ = "device_command"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("actor_source.id"), nullable=False)
    zone_id: Mapped[int | None] = mapped_column(
        ForeignKey("zone.id", ondelete="SET NULL"), nullable=True
    )
    zone_name: Mapped[str] = mapped_column(String(128), nullable=False)
    device_id: Mapped[int | None] = mapped_column(
        ForeignKey("device.id", ondelete="SET NULL"), nullable=True
    )
    device_name: Mapped[str] = mapped_column(String(128), nullable=False)
    # A short label, not a lookup: what a command is called is not a finite,
    # user-facing vocabulary the way an operating mode or an actor source is --
    # it grows with whatever adapters send next. Same reasoning as
    # `audit_event.action`, which is unconstrained for the same reason.
    command: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    outcome_id: Mapped[int] = mapped_column(ForeignKey("command_outcome.id"), nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
