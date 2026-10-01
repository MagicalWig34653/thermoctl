"""notbetrieb_takt_persistenz_und_vergleich

Revision ID: 05f7842e4d69
Revises: 8423190df6f9
Create Date: 2026-09-29 12:00:00.000000

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "05f7842e4d69"
down_revision: str | Sequence[str] | None = "8423190df6f9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("actuator_emergency_state", schema=None) as batch_op:
        batch_op.add_column(sa.Column("cycle_source", sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column("warm_locked", sa.Boolean(), nullable=True))
        batch_op.add_column(
            sa.Column("simulated_cycle_source", sa.String(length=16), nullable=True)
        )
        batch_op.add_column(sa.Column("simulated_warm_locked", sa.Boolean(), nullable=True))
        batch_op.create_check_constraint(
            batch_op.f("ck_actuator_emergency_state_cycle_source"),
            "cycle_source IN ('kennlinie', 'festtakt')",
        )
        batch_op.create_check_constraint(
            batch_op.f("ck_actuator_emergency_state_simulated_cycle_source"),
            "simulated_cycle_source IN ('kennlinie', 'festtakt')",
        )

    op.create_table(
        "sensor_failure_source_comparison",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("zone_id", sa.Integer(), nullable=True),
        sa.Column("zone_name", sa.String(length=128), nullable=False),
        sa.Column("device_id", sa.Integer(), nullable=True),
        sa.Column("device_name", sa.String(length=128), nullable=False),
        sa.Column("measured_at", sa.DateTime(), nullable=False),
        sa.Column("wall_probe_c", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("raw_c", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("corrected_c", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("echo", sa.Boolean(), nullable=False),
        sa.Column("usable", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["device_id"],
            ["device.id"],
            name=op.f("fk_sensor_failure_source_comparison_device_id_device"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["zone_id"],
            ["zone.id"],
            name=op.f("fk_sensor_failure_source_comparison_zone_id_zone"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sensor_failure_source_comparison")),
    )
    with op.batch_alter_table("sensor_failure_source_comparison", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_sensor_failure_source_comparison_device_id"),
            ["device_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_sensor_failure_source_comparison_measured_at"),
            ["measured_at"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_sensor_failure_source_comparison_zone_id"), ["zone_id"], unique=False
        )


def downgrade() -> None:
    """Downgrade schema."""
    # Drop the table with its indexes in one step, not via explicit
    # `drop_index` first: MariaDB refuses to drop an index that still backs a
    # foreign key constraint on the same table (error 1553) -- same reasoning
    # already documented in `a0110b03c001_sensor_failure.py`'s own downgrade.
    op.drop_table("sensor_failure_source_comparison")

    with op.batch_alter_table("actuator_emergency_state", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("ck_actuator_emergency_state_simulated_cycle_source"), type_="check"
        )
        batch_op.drop_constraint(
            batch_op.f("ck_actuator_emergency_state_cycle_source"), type_="check"
        )
        batch_op.drop_column("simulated_warm_locked")
        batch_op.drop_column("simulated_cycle_source")
        batch_op.drop_column("warm_locked")
        batch_op.drop_column("cycle_source")
