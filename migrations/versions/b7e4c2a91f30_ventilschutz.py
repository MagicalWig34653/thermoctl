"""Ventilschutz je Zone und sein Betriebszustand.

Revision ID: b7e4c2a91f30
Revises: 7943b9d915c6
Create Date: 2026-08-31
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision: str = "b7e4c2a91f30"
down_revision: str | Sequence[str] | None = "7943b9d915c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

precise_datetime = sa.DateTime().with_variant(
    mysql.DATETIME(fsp=6), "mysql", "mariadb"
)


def upgrade() -> None:
    with op.batch_alter_table("zone", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "valve_protection_enabled", sa.Boolean(),
                server_default=sa.false(), nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "valve_protection_interval_days", sa.Integer(),
                server_default="30", nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "valve_protection_duration_minutes", sa.Integer(),
                server_default="10", nullable=False,
            )
        )
    with op.batch_alter_table("zone_state", schema=None) as batch_op:
        batch_op.add_column(sa.Column("last_regular_heat_at", precise_datetime, nullable=True))
        batch_op.add_column(sa.Column(
            "regular_heat_history_compacted", sa.Boolean(),
            server_default=sa.false(), nullable=False,
        ))
        batch_op.add_column(sa.Column(
            "valve_protection_started_at", precise_datetime, nullable=True,
        ))
        batch_op.add_column(sa.Column(
            "last_valve_protection_at", precise_datetime, nullable=True,
        ))


def downgrade() -> None:
    with op.batch_alter_table("zone_state", schema=None) as batch_op:
        batch_op.drop_column("last_valve_protection_at")
        batch_op.drop_column("valve_protection_started_at")
        batch_op.drop_column("regular_heat_history_compacted")
        batch_op.drop_column("last_regular_heat_at")
    with op.batch_alter_table("zone", schema=None) as batch_op:
        batch_op.drop_column("valve_protection_duration_minutes")
        batch_op.drop_column("valve_protection_interval_days")
        batch_op.drop_column("valve_protection_enabled")
