"""I-Anteil manuell zurücksetzen/setzen: Tabelle der vorgemerkten Eingriffe.

Weboberfläche, REST und MCP fügen nur ein, der nächste Regelzyklus übernimmt die jüngste Zeile
je Raum und löscht die gesehenen. Keine bestehende Tabelle ändert sich.

Revision ID: e3b7c1d9a452
Revises: c7e2a9f4d813
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e3b7c1d9a452"
down_revision: str | Sequence[str] | None = "c7e2a9f4d813"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "pi_manual_request"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("zone_id", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=False),
        sa.Column("integral", sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column("requested_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "integral BETWEEN 0 AND 1", name=op.f("ck_pi_manual_request_integral_0_bis_1")
        ),
        sa.ForeignKeyConstraint(
            ["zone_id"],
            ["zone.id"],
            name=op.f("fk_pi_manual_request_zone_id_zone"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pi_manual_request")),
    )
    op.create_index(op.f("ix_pi_manual_request_zone_id"), _TABLE, ["zone_id"])


def downgrade() -> None:
    op.drop_table(_TABLE)
