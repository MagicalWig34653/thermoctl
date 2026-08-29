"""Schema fuer den Schattenbetrieb

Revision ID: 8b2d6e8a7f10
Revises: 4d43756aecd3
Create Date: 2026-08-29
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

from thermoctl.db.models.lookup import SENSOR_STATUS

revision: str = "8b2d6e8a7f10"
down_revision: str | Sequence[str] | None = "4d43756aecd3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEUE_FAEHIGKEITEN = [
    ("humidity", "Luftfeuchtigkeit"),
    ("illuminance", "Beleuchtungsstaerke"),
    ("occupancy", "Anwesenheit"),
    ("link_quality", "Verbindungsqualitaet"),
    ("power", "Leistung"),
    ("energy", "Energie"),
    ("valve_position", "Ventilstellung"),
    ("setpoint", "Sollwert"),
    ("availability", "Erreichbarkeit"),
]


def _nachschlage_tabelle(name: str) -> sa.TableClause:
    return sa.table(name, sa.column("code", sa.String), sa.column("label", sa.String))


def upgrade() -> None:
    op.create_table(
        "sensor_status",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("label", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sensor_status")),
        sa.UniqueConstraint("code", name=op.f("uq_sensor_status_code")),
    )
    op.bulk_insert(
        _nachschlage_tabelle("sensor_status"),
        [{"code": code, "label": label} for code, label in SENSOR_STATUS],
    )
    op.bulk_insert(
        _nachschlage_tabelle("device_capability"),
        [{"code": code, "label": label} for code, label in NEUE_FAEHIGKEITEN],
    )

    with op.batch_alter_table("device") as batch_op:
        batch_op.add_column(
            sa.Column("is_group", sa.Boolean(), server_default=sa.false(), nullable=False)
        )
    with op.batch_alter_table("setting") as batch_op:
        batch_op.add_column(
            sa.Column("control_armed", sa.Boolean(), server_default=sa.false(), nullable=False)
        )
        batch_op.add_column(
            sa.Column(
                "measurement_retention_days",
                sa.Integer(),
                server_default=sa.text("30"),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "shadow_interval_seconds",
                sa.Integer(),
                server_default=sa.text("60"),
                nullable=False,
            )
        )

    op.create_table(
        "device_health",
        sa.Column("device_id", sa.Integer(), nullable=False),
        sa.Column("last_payload_at", sa.DateTime(), nullable=False),
        sa.Column("link_quality", sa.Integer(), nullable=True),
        sa.Column("battery_percent", sa.Numeric(5, 2), nullable=True),
        sa.Column("availability", sa.String(16), nullable=True),
        sa.Column("payload_count", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["device_id"], ["device.id"], ondelete="CASCADE",
            name=op.f("fk_device_health_device_id_device")
        ),
        sa.PrimaryKeyConstraint("device_id", name=op.f("pk_device_health")),
    )
    op.create_table(
        "measurement",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("device_id", sa.Integer(), nullable=False),
        sa.Column("capability_id", sa.Integer(), nullable=False),
        sa.Column("value_numeric", sa.Numeric(12, 3), nullable=True),
        sa.Column("value_text", sa.String(32), nullable=True),
        sa.Column("measured_at", sa.DateTime(), nullable=False),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "(value_numeric IS NULL) <> (value_text IS NULL)",
            name=op.f("ck_measurement_genau_ein_wert"),
        ),
        sa.ForeignKeyConstraint(
            ["capability_id"], ["device_capability.id"],
            name=op.f("fk_measurement_capability_id_device_capability")
        ),
        sa.ForeignKeyConstraint(
            ["device_id"], ["device.id"], ondelete="CASCADE",
            name=op.f("fk_measurement_device_id_device")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_measurement")),
    )
    op.create_index(
        "ix_measurement_device_capability_measured",
        "measurement", ["device_id", "capability_id", "measured_at"]
    )
    op.create_table(
        "zone_state",
        sa.Column("zone_id", sa.Integer(), nullable=False),
        sa.Column("temperature_c", sa.Numeric(5, 2), nullable=True),
        sa.Column("measured_at", sa.DateTime(), nullable=True),
        sa.Column("sensor_status_id", sa.Integer(), nullable=False),
        sa.Column("window_open", sa.Boolean(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["sensor_status_id"], ["sensor_status.id"],
            name=op.f("fk_zone_state_sensor_status_id_sensor_status")
        ),
        sa.ForeignKeyConstraint(
            ["zone_id"], ["zone.id"], ondelete="CASCADE",
            name=op.f("fk_zone_state_zone_id_zone")
        ),
        sa.PrimaryKeyConstraint("zone_id", name=op.f("pk_zone_state")),
    )
    op.create_table(
        "shadow_decision",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("decided_at", sa.DateTime(), nullable=False),
        sa.Column("zone_id", sa.Integer(), nullable=False),
        sa.Column("temperature_c", sa.Numeric(5, 2), nullable=True),
        sa.Column("setpoint_c", sa.Numeric(5, 2), nullable=True),
        sa.Column("setpoint_reason", sa.String(255), nullable=False),
        sa.Column("would_heat", sa.Boolean(), nullable=False),
        sa.Column("previous_would_heat", sa.Boolean(), nullable=True),
        sa.Column("outcome_code", sa.String(32), nullable=False),
        sa.Column("reason", sa.String(255), nullable=False),
        sa.ForeignKeyConstraint(
            ["zone_id"], ["zone.id"], name=op.f("fk_shadow_decision_zone_id_zone")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_shadow_decision")),
    )
    op.create_index(op.f("ix_shadow_decision_decided_at"), "shadow_decision", ["decided_at"])


def downgrade() -> None:
    op.drop_index(op.f("ix_shadow_decision_decided_at"), table_name="shadow_decision")
    op.drop_table("shadow_decision")
    op.drop_table("zone_state")
    op.drop_index("ix_measurement_device_capability_measured", table_name="measurement")
    op.drop_table("measurement")
    op.drop_table("device_health")
    with op.batch_alter_table("setting") as batch_op:
        batch_op.drop_column("shadow_interval_seconds")
        batch_op.drop_column("measurement_retention_days")
        batch_op.drop_column("control_armed")
    with op.batch_alter_table("device") as batch_op:
        batch_op.drop_column("is_group")
    codes = [code for code, _label in NEUE_FAEHIGKEITEN]
    faehigkeiten = _nachschlage_tabelle("device_capability")
    op.execute(sa.delete(faehigkeiten).where(faehigkeiten.c.code.in_(codes)))
    op.drop_table("sensor_status")
