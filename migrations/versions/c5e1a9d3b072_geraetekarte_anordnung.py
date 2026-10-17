"""Anordnung der Gerätekarte: persönlich je Benutzer und gemeinsame Vorgabe.

Je Knoten eine Zeile (Knotenschlüssel, Lage, eingeklappt), die Ansicht als drei
Zahlenspalten, Version zur Konflikterkennung -- kein JSON, kein ENUM. Der Knotenschlüssel
(`dev-<id>`, `zone-<id>`) ist bewusst kein Fremdschlüssel: Ein gelöschtes Gerät oder ein
gelöschter Raum darf die Anordnung nicht blockieren. Die Vorgabe hat genau eine Kopfzeile
(`id = 1`) und keinen Benutzer.

Revision ID: c5e1a9d3b072
Revises: a7c3e91d5b24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c5e1a9d3b072"
down_revision: str | Sequence[str] | None = "a7c3e91d5b24"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COORD = "BETWEEN -100000 AND 100000"
_VIEW_OK = (
    "(view_x IS NULL AND view_y IS NULL AND view_k IS NULL) OR "
    "(view_x IS NOT NULL AND view_y IS NOT NULL AND view_k IS NOT NULL AND "
    f"view_x {_COORD} AND view_y {_COORD} AND view_k BETWEEN 0.2 AND 2.0)"
)
_POS_OK = (
    "(x IS NULL AND y IS NULL) OR "
    f"(x IS NOT NULL AND y IS NOT NULL AND x {_COORD} AND y {_COORD})"
)


def _head_columns() -> list[sa.Column]:  # type: ignore[type-arg]
    return [
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("customized", sa.Boolean(), nullable=False),
        sa.Column("view_x", sa.Float(), nullable=True),
        sa.Column("view_y", sa.Float(), nullable=True),
        sa.Column("view_k", sa.Float(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    ]


def _node_columns() -> list[sa.Column]:  # type: ignore[type-arg]
    return [
        sa.Column("node_key", sa.String(length=32), nullable=False),
        sa.Column("x", sa.Float(), nullable=True),
        sa.Column("y", sa.Float(), nullable=True),
        sa.Column("collapsed", sa.Boolean(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "map_layout",
        sa.Column("user_id", sa.Integer(), nullable=False),
        *_head_columns(),
        sa.CheckConstraint(_VIEW_OK, name=op.f("ck_map_layout_ansicht_gueltig")),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
            name=op.f("fk_map_layout_user_id_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_map_layout")),
    )
    op.create_table(
        "map_layout_node",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        *_node_columns(),
        sa.CheckConstraint(_POS_OK, name=op.f("ck_map_layout_node_lage_gueltig")),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
            name=op.f("fk_map_layout_node_user_id_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_map_layout_node")),
        sa.UniqueConstraint("user_id", "node_key", name="uq_map_layout_node_user_key"),
    )
    with op.batch_alter_table("map_layout_node", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_map_layout_node_user_id"), ["user_id"])
    op.create_table(
        "map_layout_default",
        sa.Column("id", sa.Integer(), nullable=False),
        *_head_columns(),
        sa.CheckConstraint("id = 1", name=op.f("ck_map_layout_default_einzige_zeile")),
        sa.CheckConstraint(_VIEW_OK, name=op.f("ck_map_layout_default_ansicht_gueltig")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_map_layout_default")),
    )
    op.create_table(
        "map_layout_default_node",
        sa.Column("id", sa.Integer(), nullable=False),
        *_node_columns(),
        sa.CheckConstraint(_POS_OK, name=op.f("ck_map_layout_default_node_lage_gueltig")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_map_layout_default_node")),
        sa.UniqueConstraint("node_key", name="uq_map_layout_default_node_key"),
    )


def downgrade() -> None:
    op.drop_table("map_layout_default_node")
    op.drop_table("map_layout_default")
    op.drop_table("map_layout_node")
    op.drop_table("map_layout")
