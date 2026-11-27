"""I-Anteil manuell zurücksetzen/setzen: vorgemerkter Eingriff in `zone_state`.

Zwei Spalten (Grund, Zielwert), beide leer, solange nichts ansteht. Der nächste Regelzyklus
übernimmt den Eingriff und leert sie. Keine bestehende Zeile ändert sich.

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

_RANGE = "pi_manual_integral_0_bis_1"
_TOGETHER = "pi_manual_gemeinsam"


def upgrade() -> None:
    with op.batch_alter_table("zone_state", schema=None) as batch_op:
        batch_op.add_column(sa.Column("pi_manual_reason", sa.String(length=64), nullable=True))
        batch_op.add_column(
            sa.Column("pi_manual_integral", sa.Numeric(precision=12, scale=6), nullable=True)
        )
        batch_op.create_check_constraint(
            _RANGE, "pi_manual_integral IS NULL OR pi_manual_integral BETWEEN 0 AND 1"
        )
        batch_op.create_check_constraint(
            _TOGETHER, (
            "(pi_manual_reason IS NULL AND pi_manual_integral IS NULL) "
            "OR (pi_manual_reason IS NOT NULL AND pi_manual_integral IS NOT NULL)"
        )
        )


def downgrade() -> None:
    with op.batch_alter_table("zone_state", schema=None) as batch_op:
        batch_op.drop_constraint(_TOGETHER, type_="check")
        batch_op.drop_constraint(_RANGE, type_="check")
        batch_op.drop_column("pi_manual_integral")
        batch_op.drop_column("pi_manual_reason")
