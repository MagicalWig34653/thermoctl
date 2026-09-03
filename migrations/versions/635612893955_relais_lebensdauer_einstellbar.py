"""Angenommene Relaislebensdauer einstellbar (Vorgabe 500.000).

War bisher die feste Konstante ``ASSUMED_RELAY_LIFETIME_OPERATIONS`` in
``domain/statistics.py``. Bestehende Anlagen erhalten mit dieser Migration den
neuen Vorgabewert -- die alte Konstante stand ohnehin nur als austauschbare
Annahme im Code, keine gemessene Groesse, die eine Anlage individuell haette
einstellen koennen.

Revision ID: 635612893955
Revises: d2f4a7c91e63
Create Date: 2026-09-03 07:18:57.145431

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "635612893955"
down_revision: str | Sequence[str] | None = "d2f4a7c91e63"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("setting") as batch_op:
        batch_op.add_column(
            sa.Column(
                "assumed_relay_lifetime_operations",
                sa.Integer(),
                server_default=sa.text("500000"),
                nullable=False,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("setting") as batch_op:
        batch_op.drop_column("assumed_relay_lifetime_operations")
