"""Änderungsprotokoll: strukturierte Änderungen mit Vorher/Nachher (Zurückrollen).

Revision ID: c4a9e2f71b35
Revises: b721e49c6a30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4a9e2f71b35"
down_revision: str | Sequence[str] | None = "b721e49c6a30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "change_log",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("actor_token_id", sa.Integer(), nullable=True),
        sa.Column("change_type", sa.String(length=32), nullable=False),
        sa.Column("zone_id", sa.Integer(), nullable=True),
        sa.Column("zone_name", sa.String(length=255), nullable=True),
        sa.Column("object_ref", sa.String(length=64), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("revertible", sa.Boolean(), nullable=False),
        sa.Column("not_revertible_reason", sa.String(length=255), nullable=True),
        sa.Column("reverted_at", sa.DateTime(), nullable=True),
        sa.Column("reverted_by_id", sa.Integer(), nullable=True),
        sa.Column("reverts_id", sa.Integer(), nullable=True),
        sa.Column("forced", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_token_id"],
            ["api_token.id"],
            name=op.f("fk_change_log_actor_token_id_api_token"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["user.id"],
            name=op.f("fk_change_log_actor_user_id_user"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["reverted_by_id"],
            ["change_log.id"],
            name=op.f("fk_change_log_reverted_by_id_change_log"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["reverts_id"],
            ["change_log.id"],
            name=op.f("fk_change_log_reverts_id_change_log"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"], ["actor_source.id"], name=op.f("fk_change_log_source_id_actor_source")
        ),
        sa.ForeignKeyConstraint(
            ["zone_id"], ["zone.id"], name=op.f("fk_change_log_zone_id_zone"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_change_log")),
    )
    with op.batch_alter_table("change_log", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_change_log_occurred_at"), ["occurred_at"])
        batch_op.create_index(batch_op.f("ix_change_log_change_type"), ["change_type"])
        batch_op.create_index("ix_change_log_zone_id_occurred_at", ["zone_id", "occurred_at"])

    op.create_table(
        "change_log_field",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("change_id", sa.Integer(), nullable=False),
        sa.Column("field", sa.String(length=64), nullable=False),
        sa.Column("before_value", sa.String(length=512), nullable=True),
        sa.Column("after_value", sa.String(length=512), nullable=True),
        sa.ForeignKeyConstraint(
            ["change_id"],
            ["change_log.id"],
            name=op.f("fk_change_log_field_change_id_change_log"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_change_log_field")),
    )
    with op.batch_alter_table("change_log_field", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_change_log_field_change_id"), ["change_id"])


def downgrade() -> None:
    op.drop_table("change_log_field")
    op.drop_table("change_log")
