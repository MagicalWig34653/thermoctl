"""Neue Kachelart `control` (Räume direkt bedienen) im CHECK von `dashboard_tile`.

Revision ID: d4b8f2a60c19
Revises: c5e1a9d3b072
"""

from collections.abc import Sequence

from alembic import op

revision: str = "d4b8f2a60c19"
down_revision: str | Sequence[str] | None = "c5e1a9d3b072"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NAME = "ck_dashboard_tile_kind_bekannt"
_OLD = (
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
)


def _in(values: tuple[str, ...]) -> str:
    return "kind IN (" + ", ".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    with op.batch_alter_table("dashboard_tile", schema=None) as batch_op:
        batch_op.drop_constraint(op.f(_NAME), type_="check")
        batch_op.create_check_constraint(op.f(_NAME), _in((*_OLD, "control")))


def downgrade() -> None:
    # Kacheln der neuen Art gibt es in der alten Fassung nicht: vorher entfernen.
    op.execute("DELETE FROM dashboard_tile WHERE kind = 'control'")
    with op.batch_alter_table("dashboard_tile", schema=None) as batch_op:
        batch_op.drop_constraint(op.f(_NAME), type_="check")
        batch_op.create_check_constraint(op.f(_NAME), _in(_OLD))
