"""Aufbewahrung des Schaltprotokolls (Alter und Größe).

Revision ID: a8d4f12c6e53
Revises: e7a3c15b9d28
Create Date: 2026-10-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a8d4f12c6e53"
down_revision: str | Sequence[str] | None = "e7a3c15b9d28"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("setting") as batch_op:
        batch_op.add_column(
            sa.Column(
                "command_log_retention_days",
                sa.Integer(),
                server_default=sa.text("1095"),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "command_log_max_mb",
                sa.Integer(),
                server_default=sa.text("2048"),
                nullable=False,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("setting") as batch_op:
        batch_op.drop_column("command_log_max_mb")
        batch_op.drop_column("command_log_retention_days")
