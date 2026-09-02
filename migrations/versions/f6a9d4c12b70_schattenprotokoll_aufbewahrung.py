"""Aufbewahrungsfrist fuer das Schattenprotokoll.

Revision ID: f6a9d4c12b70
Revises: e8c21f4a9d70
Create Date: 2026-09-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f6a9d4c12b70"
down_revision: str | Sequence[str] | None = "e8c21f4a9d70"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("setting") as batch_op:
        batch_op.add_column(
            sa.Column(
                "shadow_decision_retention_days",
                sa.Integer(),
                server_default=sa.text("365"),
                nullable=False,
            )
        )
    op.create_index(
        "ix_shadow_decision_retention",
        "shadow_decision",
        ["decided_at", "id"],
    )


def downgrade() -> None:
    op.drop_index("ix_shadow_decision_retention", table_name="shadow_decision")
    with op.batch_alter_table("setting") as batch_op:
        batch_op.drop_column("shadow_decision_retention_days")
