"""Persönliches Dashboard-Layout: Version, Abschnitte, Kacheln je Benutzer.

Kachelart, Kennzahl, Ziel, Breite und Zeitraum sind kurze Strings beziehungsweise Zahlen
mit CHECK-Constraint (kein ENUM, kein JSON). `dashboard_tile.zone_id` löscht mit der Zone
die Kachel (ON DELETE CASCADE); umgekehrt löscht keine Kachel je eine Zone.

Revision ID: a7c3e91d5b24
Revises: d8e3a51c7f24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7c3e91d5b24"
down_revision: str | Sequence[str] | None = "d8e3a51c7f24"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _in(column: str, values: tuple[str, ...] | tuple[int, ...]) -> str:
    quoted = ", ".join(f"'{v}'" if isinstance(v, str) else str(v) for v in values)
    return f"{column} IN ({quoted})"


def upgrade() -> None:
    op.create_table(
        "dashboard_layout",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("customized", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
            name=op.f("fk_dashboard_layout_user_id_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_dashboard_layout")),
    )
    op.create_table(
        "dashboard_section",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=80), nullable=False),
        sa.CheckConstraint(
            "position >= 0", name=op.f("ck_dashboard_section_position_nichtnegativ")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
            name=op.f("fk_dashboard_section_user_id_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dashboard_section")),
        sa.UniqueConstraint("user_id", "position", name="uq_dashboard_section_user_position"),
    )
    with op.batch_alter_table("dashboard_section", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_dashboard_section_user_id"), ["user_id"])
    op.create_table(
        "dashboard_tile",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("section_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("zone_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=80), nullable=False),
        sa.Column("period_days", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("metric", sa.String(length=16), nullable=True),
        sa.Column("destination", sa.String(length=16), nullable=True),
        sa.Column("note", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "position >= 0", name=op.f("ck_dashboard_tile_position_nichtnegativ")
        ),
        sa.CheckConstraint(
            _in(
                "kind",
                (
                    "room",
                    "chart",
                    "metric",
                    "agenda",
                    "schedule",
                    "heating",
                    "devices",
                    "system",
                    "note",
                    "shortcut",
                ),
            ),
            name=op.f("ck_dashboard_tile_kind_bekannt"),
        ),
        sa.CheckConstraint(
            _in("width", (3, 4, 6, 8, 12)), name=op.f("ck_dashboard_tile_width_bekannt")
        ),
        sa.CheckConstraint(
            _in("period_days", (1, 7, 30, 90)), name=op.f("ck_dashboard_tile_period_bekannt")
        ),
        sa.CheckConstraint(
            "metric IS NULL OR " + _in("metric", ("rooms", "heating", "issues", "outdoor")),
            name=op.f("ck_dashboard_tile_metric_bekannt"),
        ),
        sa.CheckConstraint(
            "destination IS NULL OR "
            + _in(
                "destination",
                (
                    "schedule",
                    "absence",
                    "history",
                    "devices",
                    "mapping",
                    "solar",
                    "operation",
                    "account",
                ),
            ),
            name=op.f("ck_dashboard_tile_destination_bekannt"),
        ),
        sa.ForeignKeyConstraint(
            ["section_id"],
            ["dashboard_section.id"],
            name=op.f("fk_dashboard_tile_section_id_dashboard_section"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["zone_id"],
            ["zone.id"],
            name=op.f("fk_dashboard_tile_zone_id_zone"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dashboard_tile")),
        sa.UniqueConstraint("section_id", "position", name="uq_dashboard_tile_section_position"),
    )
    with op.batch_alter_table("dashboard_tile", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_dashboard_tile_section_id"), ["section_id"])
        batch_op.create_index(batch_op.f("ix_dashboard_tile_zone_id"), ["zone_id"])


def downgrade() -> None:
    op.drop_table("dashboard_tile")
    op.drop_table("dashboard_section")
    op.drop_table("dashboard_layout")
