"""PI-Konfiguration, dauerhafter Zustand und strukturierte Diagnose.

Revision ID: d2f4a7c91e63
Revises: f6a9d4c12b70
Create Date: 2026-09-02
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision: str = "d2f4a7c91e63"
down_revision: str | Sequence[str] | None = "f6a9d4c12b70"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

precise_datetime = sa.DateTime().with_variant(
    mysql.DATETIME(fsp=6), "mysql", "mariadb"
)


def upgrade() -> None:
    with op.batch_alter_table("zone") as batch_op:
        batch_op.add_column(
            sa.Column(
                "pi_enabled", sa.Boolean(), server_default=sa.false(), nullable=False
            )
        )
        batch_op.add_column(
            sa.Column(
                "pi_gain_per_k",
                sa.Numeric(3, 2),
                server_default=sa.text("0.25"),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "pi_integral_time_minutes",
                sa.Integer(),
                server_default=sa.text("180"),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "pi_min_on_seconds",
                sa.Integer(),
                server_default=sa.text("60"),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "pi_min_off_seconds",
                sa.Integer(),
                server_default=sa.text("60"),
                nullable=False,
            )
        )
        batch_op.create_check_constraint(
            op.f("ck_zone_pi_gain_per_k_bereich_und_schritt"),
            "pi_gain_per_k BETWEEN 0.05 AND 0.50 "
            "AND (pi_gain_per_k * 100) % 5 = 0",
        )
        batch_op.create_check_constraint(
            op.f("ck_zone_pi_integral_time_bereich_und_schritt"),
            "pi_integral_time_minutes BETWEEN 60 AND 720 "
            "AND pi_integral_time_minutes % 30 = 0",
        )
        batch_op.create_check_constraint(
            op.f("ck_zone_pi_min_on_bereich_und_schritt"),
            "pi_min_on_seconds BETWEEN 60 AND 300 AND pi_min_on_seconds % 30 = 0",
        )
        batch_op.create_check_constraint(
            op.f("ck_zone_pi_min_off_bereich_und_schritt"),
            "pi_min_off_seconds BETWEEN 60 AND 300 AND pi_min_off_seconds % 30 = 0",
        )

    with op.batch_alter_table("zone_state") as batch_op:
        batch_op.add_column(
            sa.Column(
                "pi_integral",
                sa.Numeric(12, 6),
                server_default=sa.text("0"),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column("pi_last_evaluated_at", precise_datetime, nullable=True)
        )
        batch_op.add_column(
            sa.Column("pi_setpoint_context_key", sa.String(255), nullable=True)
        )
        batch_op.add_column(
            sa.Column("pi_last_control_armed", sa.Boolean(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("pi_window_started_at", precise_datetime, nullable=True)
        )
        batch_op.add_column(
            sa.Column("pi_window_duty", sa.Numeric(12, 6), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "pi_time_balance_seconds",
                sa.Numeric(12, 6),
                server_default=sa.text("0"),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column("pi_last_switch_at", precise_datetime, nullable=True)
        )
        batch_op.add_column(
            sa.Column("pi_last_switch_heating", sa.Boolean(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("pi_awaiting_boundary_until", precise_datetime, nullable=True)
        )
        batch_op.add_column(
            sa.Column("pi_last_reset_reason", sa.String(64), nullable=True)
        )
        batch_op.create_check_constraint(
            op.f("ck_zone_state_pi_integral_0_bis_1"),
            "pi_integral BETWEEN 0 AND 1",
        )
        batch_op.create_check_constraint(
            op.f("ck_zone_state_pi_window_duty_0_bis_1"),
            "pi_window_duty IS NULL OR pi_window_duty BETWEEN 0 AND 1",
        )
        batch_op.create_check_constraint(
            op.f("ck_zone_state_pi_time_balance_minus_900_bis_900"),
            "pi_time_balance_seconds BETWEEN -900 AND 900",
        )

    with op.batch_alter_table("shadow_decision") as batch_op:
        batch_op.add_column(
            sa.Column(
                "requested_controller",
                sa.String(32),
                server_default=sa.text("'hysteresis'"),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "effective_controller",
                sa.String(32),
                server_default=sa.text("'hysteresis'"),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column("controller_fallback_reason", sa.String(64), nullable=True)
        )
        batch_op.add_column(
            sa.Column("pi_error_k", sa.Numeric(12, 6), nullable=True)
        )
        batch_op.add_column(
            sa.Column("pi_proportional_term", sa.Numeric(12, 6), nullable=True)
        )
        batch_op.add_column(
            sa.Column("pi_integral_before", sa.Numeric(12, 6), nullable=True)
        )
        batch_op.add_column(
            sa.Column("pi_integral_after", sa.Numeric(12, 6), nullable=True)
        )
        batch_op.add_column(
            sa.Column("pi_raw_duty", sa.Numeric(12, 6), nullable=True)
        )
        batch_op.add_column(
            sa.Column("pi_frozen_duty", sa.Numeric(12, 6), nullable=True)
        )
        batch_op.add_column(
            sa.Column("pi_window_started_at", precise_datetime, nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "pi_time_balance_before_seconds", sa.Numeric(12, 6), nullable=True
            )
        )
        batch_op.add_column(
            sa.Column(
                "pi_time_balance_after_seconds", sa.Numeric(12, 6), nullable=True
            )
        )
        batch_op.add_column(
            sa.Column("pi_state_runtime_seconds", sa.Numeric(12, 6), nullable=True)
        )
        batch_op.add_column(
            sa.Column("pi_integrator_action", sa.String(32), nullable=True)
        )
        batch_op.add_column(
            sa.Column("pi_min_duration_decision", sa.String(64), nullable=True)
        )
        batch_op.add_column(
            sa.Column("pi_reset_reason", sa.String(64), nullable=True)
        )
        batch_op.add_column(
            sa.Column("pi_candidate_would_heat", sa.Boolean(), nullable=True)
        )
        batch_op.create_check_constraint(
            op.f("ck_shadow_decision_pi_integral_before_0_bis_1"),
            "pi_integral_before IS NULL OR pi_integral_before BETWEEN 0 AND 1",
        )
        batch_op.create_check_constraint(
            op.f("ck_shadow_decision_pi_integral_after_0_bis_1"),
            "pi_integral_after IS NULL OR pi_integral_after BETWEEN 0 AND 1",
        )
        batch_op.create_check_constraint(
            op.f("ck_shadow_decision_pi_raw_duty_0_bis_1"),
            "pi_raw_duty IS NULL OR pi_raw_duty BETWEEN 0 AND 1",
        )
        batch_op.create_check_constraint(
            op.f("ck_shadow_decision_pi_frozen_duty_0_bis_1"),
            "pi_frozen_duty IS NULL OR pi_frozen_duty BETWEEN 0 AND 1",
        )


def downgrade() -> None:
    with op.batch_alter_table("shadow_decision") as batch_op:
        batch_op.drop_constraint(
            op.f("ck_shadow_decision_pi_frozen_duty_0_bis_1"), type_="check"
        )
        batch_op.drop_constraint(
            op.f("ck_shadow_decision_pi_raw_duty_0_bis_1"), type_="check"
        )
        batch_op.drop_constraint(
            op.f("ck_shadow_decision_pi_integral_after_0_bis_1"), type_="check"
        )
        batch_op.drop_constraint(
            op.f("ck_shadow_decision_pi_integral_before_0_bis_1"), type_="check"
        )
        batch_op.drop_column("pi_candidate_would_heat")
        batch_op.drop_column("pi_reset_reason")
        batch_op.drop_column("pi_min_duration_decision")
        batch_op.drop_column("pi_integrator_action")
        batch_op.drop_column("pi_state_runtime_seconds")
        batch_op.drop_column("pi_time_balance_after_seconds")
        batch_op.drop_column("pi_time_balance_before_seconds")
        batch_op.drop_column("pi_window_started_at")
        batch_op.drop_column("pi_frozen_duty")
        batch_op.drop_column("pi_raw_duty")
        batch_op.drop_column("pi_integral_after")
        batch_op.drop_column("pi_integral_before")
        batch_op.drop_column("pi_proportional_term")
        batch_op.drop_column("pi_error_k")
        batch_op.drop_column("controller_fallback_reason")
        batch_op.drop_column("effective_controller")
        batch_op.drop_column("requested_controller")

    with op.batch_alter_table("zone_state") as batch_op:
        batch_op.drop_constraint(
            op.f("ck_zone_state_pi_time_balance_minus_900_bis_900"), type_="check"
        )
        batch_op.drop_constraint(
            op.f("ck_zone_state_pi_window_duty_0_bis_1"), type_="check"
        )
        batch_op.drop_constraint(
            op.f("ck_zone_state_pi_integral_0_bis_1"), type_="check"
        )
        batch_op.drop_column("pi_last_reset_reason")
        batch_op.drop_column("pi_awaiting_boundary_until")
        batch_op.drop_column("pi_last_switch_heating")
        batch_op.drop_column("pi_last_switch_at")
        batch_op.drop_column("pi_time_balance_seconds")
        batch_op.drop_column("pi_window_duty")
        batch_op.drop_column("pi_window_started_at")
        batch_op.drop_column("pi_last_control_armed")
        batch_op.drop_column("pi_setpoint_context_key")
        batch_op.drop_column("pi_last_evaluated_at")
        batch_op.drop_column("pi_integral")

    with op.batch_alter_table("zone") as batch_op:
        batch_op.drop_constraint(
            op.f("ck_zone_pi_min_off_bereich_und_schritt"), type_="check"
        )
        batch_op.drop_constraint(
            op.f("ck_zone_pi_min_on_bereich_und_schritt"), type_="check"
        )
        batch_op.drop_constraint(
            op.f("ck_zone_pi_integral_time_bereich_und_schritt"), type_="check"
        )
        batch_op.drop_constraint(
            op.f("ck_zone_pi_gain_per_k_bereich_und_schritt"), type_="check"
        )
        batch_op.drop_column("pi_min_off_seconds")
        batch_op.drop_column("pi_min_on_seconds")
        batch_op.drop_column("pi_integral_time_minutes")
        batch_op.drop_column("pi_gain_per_k")
        batch_op.drop_column("pi_enabled")
