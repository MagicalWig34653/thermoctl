"""sensor_failure

Revision ID: a0110b03c001
Revises: d31f6a04c7e9
Create Date: 2026-09-28 19:32:57.913014

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a0110b03c001"
down_revision: str | Sequence[str] | None = "d31f6a04c7e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "sensor_failure_profile",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("fixed_on_seconds", sa.Integer(), nullable=False),
        sa.Column("fixed_off_seconds", sa.Integer(), nullable=False),
        sa.Column("recovery_seconds", sa.Integer(), nullable=False),
        sa.Column("recovery_samples", sa.Integer(), nullable=False),
        sa.Column("warm_restart_hysteresis_k", sa.Numeric(precision=4, scale=2), nullable=False),
        sa.CheckConstraint(
            "fixed_off_seconds > 0", name=op.f("ck_sensor_failure_profile_off_positive")
        ),
        sa.CheckConstraint(
            "fixed_on_seconds >= 0", name=op.f("ck_sensor_failure_profile_on_nonnegative")
        ),
        sa.CheckConstraint(
            "recovery_samples > 0", name=op.f("ck_sensor_failure_profile_samples_positive")
        ),
        sa.CheckConstraint(
            "recovery_seconds >= 0", name=op.f("ck_sensor_failure_profile_recovery_nonnegative")
        ),
        sa.CheckConstraint(
            "warm_restart_hysteresis_k >= 0",
            name=op.f("ck_sensor_failure_profile_hysteresis_nonnegative"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sensor_failure_profile")),
    )
    op.create_table(
        "sensor_failure_curve_point",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("profile_id", sa.Integer(), nullable=False),
        sa.Column("outdoor_c", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("on_seconds", sa.Integer(), nullable=False),
        sa.Column("off_seconds", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "off_seconds > 0", name=op.f("ck_sensor_failure_curve_point_off_positive")
        ),
        sa.CheckConstraint(
            "on_seconds >= 0", name=op.f("ck_sensor_failure_curve_point_on_nonnegative")
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["sensor_failure_profile.id"],
            name=op.f("fk_sensor_failure_curve_point_profile_id_sensor_failure_profile"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sensor_failure_curve_point")),
        sa.UniqueConstraint("profile_id", "outdoor_c", name="curve_temperature_per_profile"),
    )
    op.create_table(
        "sensor_failure_episode",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("zone_id", sa.Integer(), nullable=True),
        sa.Column("zone_name", sa.String(length=128), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.Column("trigger_kind", sa.String(length=32), nullable=False),
        sa.Column("profile_version", sa.Integer(), nullable=False),
        sa.Column("notification_state", sa.String(length=32), nullable=False),
        sa.Column("fixed_on_seconds", sa.Integer(), nullable=False),
        sa.Column("fixed_off_seconds", sa.Integer(), nullable=False),
        sa.Column("recovery_seconds", sa.Integer(), nullable=False),
        sa.Column("recovery_samples", sa.Integer(), nullable=False),
        sa.Column("warm_restart_hysteresis_k", sa.Numeric(precision=4, scale=2), nullable=False),
        sa.Column("emergency_setpoint_c", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("sensor_timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("source_device_id", sa.Integer(), nullable=True),
        sa.Column("source_device_name", sa.String(length=128), nullable=True),
        sa.CheckConstraint(
            "notification_state IN ('offen', 'gemeldet', 'entwarnung_gesendet')",
            name=op.f("ck_sensor_failure_episode_notification_state"),
        ),
        sa.CheckConstraint(
            "trigger_kind IN ('wandfuehler', 'alle_quellen')",
            name=op.f("ck_sensor_failure_episode_trigger_kind"),
        ),
        sa.ForeignKeyConstraint(
            ["source_device_id"],
            ["device.id"],
            name=op.f("fk_sensor_failure_episode_source_device_id_device"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["zone_id"],
            ["zone.id"],
            name=op.f("fk_sensor_failure_episode_zone_id_zone"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sensor_failure_episode")),
    )
    with op.batch_alter_table("sensor_failure_episode", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_sensor_failure_episode_started_at"), ["started_at"], unique=False
        )

    op.create_table(
        "actuator_decision",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("episode_id", sa.Integer(), nullable=True),
        sa.Column("zone_device_id", sa.Integer(), nullable=True),
        sa.Column("zone_name", sa.String(length=128), nullable=False),
        sa.Column("device_name", sa.String(length=128), nullable=False),
        sa.Column("decided_at", sa.DateTime(), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("reason_code", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("phase", sa.String(length=8), nullable=True),
        sa.Column("phase_deadline_at", sa.DateTime(), nullable=True),
        sa.Column("simulated", sa.Boolean(), nullable=False),
        sa.Column("cycle_source", sa.String(length=16), nullable=True),
        sa.Column("on_seconds", sa.Integer(), nullable=True),
        sa.Column("off_seconds", sa.Integer(), nullable=True),
        sa.Column("outdoor_c", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("profile_version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "action IN ('normal', 'no_write', 'switch_on', 'switch_off', 'handover')",
            name=op.f("ck_actuator_decision_action"),
        ),
        sa.CheckConstraint(
            "cycle_source IN ('kennlinie', 'festtakt')",
            name=op.f("ck_actuator_decision_cycle_source"),
        ),
        sa.CheckConstraint("phase IN ('ein', 'aus')", name=op.f("ck_actuator_decision_phase")),
        sa.ForeignKeyConstraint(
            ["episode_id"],
            ["sensor_failure_episode.id"],
            name=op.f("fk_actuator_decision_episode_id_sensor_failure_episode"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["zone_device_id"],
            ["zone_device.id"],
            name=op.f("fk_actuator_decision_zone_device_id_zone_device"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_actuator_decision")),
    )
    with op.batch_alter_table("actuator_decision", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_actuator_decision_decided_at"), ["decided_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_actuator_decision_episode_id"), ["episode_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_actuator_decision_zone_device_id"), ["zone_device_id"], unique=False
        )

    op.create_table(
        "actuator_emergency_state",
        sa.Column("zone_device_id", sa.Integer(), nullable=False),
        sa.Column("episode_id", sa.Integer(), nullable=False),
        sa.Column("phase", sa.String(length=8), nullable=True),
        sa.Column("phase_deadline_at", sa.DateTime(), nullable=True),
        sa.Column("on_seconds", sa.Integer(), nullable=True),
        sa.Column("off_seconds", sa.Integer(), nullable=True),
        sa.Column("simulated_phase", sa.String(length=8), nullable=True),
        sa.Column("simulated_phase_deadline_at", sa.DateTime(), nullable=True),
        sa.Column("simulated_on_seconds", sa.Integer(), nullable=True),
        sa.Column("simulated_off_seconds", sa.Integer(), nullable=True),
        sa.Column("handover_attempted_at", sa.DateTime(), nullable=True),
        sa.Column("handover_result", sa.String(length=64), nullable=True),
        sa.Column("simulated_handover_attempted_at", sa.DateTime(), nullable=True),
        sa.Column("last_successful_command_state", sa.Boolean(), nullable=True),
        sa.Column("last_successful_command_at", sa.DateTime(), nullable=True),
        sa.Column("simulated_last_command_state", sa.Boolean(), nullable=True),
        sa.Column("simulated_last_command_at", sa.DateTime(), nullable=True),
        sa.Column("last_evaluated_at", sa.DateTime(), nullable=True),
        sa.Column("profile_version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "phase IN ('ein', 'aus')", name=op.f("ck_actuator_emergency_state_phase")
        ),
        sa.CheckConstraint(
            "simulated_phase IN ('ein', 'aus')",
            name=op.f("ck_actuator_emergency_state_simulated_phase"),
        ),
        sa.CheckConstraint(
            "off_seconds > 0", name=op.f("ck_actuator_emergency_state_off_positive")
        ),
        sa.CheckConstraint(
            "on_seconds >= 0", name=op.f("ck_actuator_emergency_state_on_nonnegative")
        ),
        sa.CheckConstraint(
            "simulated_off_seconds > 0",
            name=op.f("ck_actuator_emergency_state_simulated_off_positive"),
        ),
        sa.CheckConstraint(
            "simulated_on_seconds >= 0",
            name=op.f("ck_actuator_emergency_state_simulated_on_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["episode_id"],
            ["sensor_failure_episode.id"],
            name=op.f("fk_actuator_emergency_state_episode_id_sensor_failure_episode"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["zone_device_id"],
            ["zone_device.id"],
            name=op.f("fk_actuator_emergency_state_zone_device_id_zone_device"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("zone_device_id", name=op.f("pk_actuator_emergency_state")),
    )
    op.create_table(
        "zone_sensor_failure_state",
        sa.Column("zone_id", sa.Integer(), nullable=False),
        sa.Column("episode_id", sa.Integer(), nullable=True),
        sa.Column("stage", sa.String(length=32), nullable=False),
        sa.Column("failure_started_at", sa.DateTime(), nullable=True),
        sa.Column("active_source_device_id", sa.Integer(), nullable=True),
        sa.Column("source_measured_at", sa.DateTime(), nullable=True),
        sa.Column("recovery_started_at", sa.DateTime(), nullable=True),
        sa.Column("last_counted_measurement_at", sa.DateTime(), nullable=True),
        sa.Column("recovery_sample_count", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "stage IN ('normal', 'ersatzquelle', 'notbetrieb', 'rueckkehrpruefung')",
            name=op.f("ck_zone_sensor_failure_state_stage"),
        ),
        sa.CheckConstraint(
            "recovery_sample_count >= 0",
            name=op.f("ck_zone_sensor_failure_state_samples_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["active_source_device_id"],
            ["device.id"],
            name=op.f("fk_zone_sensor_failure_state_active_source_device_id_device"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["episode_id"],
            ["sensor_failure_episode.id"],
            name=op.f("fk_zone_sensor_failure_state_episode_id_sensor_failure_episode"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["zone_id"],
            ["zone.id"],
            name=op.f("fk_zone_sensor_failure_state_zone_id_zone"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("zone_id", name=op.f("pk_zone_sensor_failure_state")),
    )
    with op.batch_alter_table("device_command", schema=None) as batch_op:
        batch_op.add_column(sa.Column("reason_code", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("episode_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("actuator_decision_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            batch_op.f("fk_device_command_episode_id_sensor_failure_episode"),
            "sensor_failure_episode",
            ["episode_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            batch_op.f("fk_device_command_actuator_decision_id_actuator_decision"),
            "actuator_decision",
            ["actuator_decision_id"],
            ["id"],
            ondelete="SET NULL",
        )

    with op.batch_alter_table("setting", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("sensor_failure_default_profile_id", sa.Integer(), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "sensor_failure_default_emergency_setpoint_c",
                sa.Numeric(precision=5, scale=2),
                server_default=sa.text("(20)"),
                nullable=False,
            )
        )
        batch_op.create_foreign_key(
            batch_op.f("fk_setting_sensor_failure_default_profile_id_sensor_failure_profile"),
            "sensor_failure_profile",
            ["sensor_failure_default_profile_id"],
            ["id"],
            ondelete="SET NULL",
        )

    with op.batch_alter_table("zone", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "sensor_failure_enabled", sa.Boolean(), server_default=sa.text("0"), nullable=False
            )
        )
        batch_op.add_column(sa.Column("sensor_failure_profile_id", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "sensor_failure_emergency_setpoint_c",
                sa.Numeric(precision=5, scale=2),
                nullable=True,
            )
        )
        batch_op.create_foreign_key(
            batch_op.f("fk_zone_sensor_failure_profile_id_sensor_failure_profile"),
            "sensor_failure_profile",
            ["sensor_failure_profile_id"],
            ["id"],
            ondelete="SET NULL",
        )

    with op.batch_alter_table("zone_device", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "temperature_backup_offset_k",
                sa.Numeric(precision=4, scale=2),
                server_default="0",
                nullable=True,
            )
        )

    # Data only: no runtime model imports and no fixed profile ID.
    connection = op.get_bind()
    profile = sa.table(
        "sensor_failure_profile",
        sa.column("id", sa.Integer),
        sa.column("name", sa.String),
        sa.column("version", sa.Integer),
        sa.column("fixed_on_seconds", sa.Integer),
        sa.column("fixed_off_seconds", sa.Integer),
        sa.column("recovery_seconds", sa.Integer),
        sa.column("recovery_samples", sa.Integer),
        sa.column("warm_restart_hysteresis_k", sa.Numeric(4, 2)),
    )
    connection.execute(
        profile.insert().values(
            name="Notbetrieb Vorgabe",
            version=1,
            fixed_on_seconds=600,
            fixed_off_seconds=1200,
            recovery_seconds=60,
            recovery_samples=2,
            warm_restart_hysteresis_k=1,
        )
    )
    profile_id = connection.execute(sa.select(profile.c.id)).scalar_one()
    points = sa.table(
        "sensor_failure_curve_point",
        sa.column("profile_id", sa.Integer),
        sa.column("outdoor_c", sa.Numeric(5, 2)),
        sa.column("on_seconds", sa.Integer),
        sa.column("off_seconds", sa.Integer),
    )
    op.bulk_insert(
        points,
        [
            dict(profile_id=profile_id, outdoor_c=-10, on_seconds=1200, off_seconds=600),
            dict(profile_id=profile_id, outdoor_c=0, on_seconds=600, off_seconds=1200),
            dict(profile_id=profile_id, outdoor_c=15, on_seconds=0, off_seconds=1800),
        ],
    )
    setting = sa.table("setting", sa.column("sensor_failure_default_profile_id", sa.Integer))
    connection.execute(setting.update().values(sensor_failure_default_profile_id=profile_id))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("zone_device", schema=None) as batch_op:
        batch_op.drop_column("temperature_backup_offset_k")

    with op.batch_alter_table("zone", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("fk_zone_sensor_failure_profile_id_sensor_failure_profile"),
            type_="foreignkey",
        )
        batch_op.drop_column("sensor_failure_emergency_setpoint_c")
        batch_op.drop_column("sensor_failure_profile_id")
        batch_op.drop_column("sensor_failure_enabled")

    with op.batch_alter_table("setting", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("fk_setting_sensor_failure_default_profile_id_sensor_failure_profile"),
            type_="foreignkey",
        )
        batch_op.drop_column("sensor_failure_default_emergency_setpoint_c")
        batch_op.drop_column("sensor_failure_default_profile_id")

    with op.batch_alter_table("device_command", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("fk_device_command_actuator_decision_id_actuator_decision"),
            type_="foreignkey",
        )
        batch_op.drop_constraint(
            batch_op.f("fk_device_command_episode_id_sensor_failure_episode"), type_="foreignkey"
        )
        batch_op.drop_column("actuator_decision_id")
        batch_op.drop_column("episode_id")
        batch_op.drop_column("reason_code")

    op.drop_table("zone_sensor_failure_state")
    op.drop_table("actuator_emergency_state")
    # Drop tables with their indexes: MariaDB needs the FK-supporting indexes
    # until the owning table (and its foreign keys) has been removed.
    op.drop_table("actuator_decision")
    op.drop_table("sensor_failure_episode")
    op.drop_table("sensor_failure_curve_point")
    op.drop_table("sensor_failure_profile")
