"""Hysterese mit vier Nachkommastellen speichern

`zone.hysteresis_k`, `setting.default_hysteresis_k` und die beiden Spalten
`warm_restart_hysteresis_k` (Profil und Episode) waren `NUMERIC(4, 2)` und hätten
Werte wie 0,0001 K still auf 0,00 gerundet. Jetzt `NUMERIC(6, 4)`; bestehende Werte
bleiben unverändert (Erweiterung der Nachkommastellen). Das Zurückstufen rundet auf
Hundertstel, hebt aber Werte über null auf mindestens 0,01, damit aus einer kleinen
Hysterese beim Downgrade keine Hysterese null (Schalten am Sollwert) wird.

Revision ID: f7c2a91d4e08
Revises: e9b4c27a1f60
Create Date: 2026-11-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f7c2a91d4e08"
down_revision: str | Sequence[str] | None = "e9b4c27a1f60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMNS = (
    ("zone", "hysteresis_k", True),
    ("setting", "default_hysteresis_k", False),
    ("sensor_failure_profile", "warm_restart_hysteresis_k", False),
    ("sensor_failure_episode", "warm_restart_hysteresis_k", False),
)


def upgrade() -> None:
    for table, column, nullable in COLUMNS:
        with op.batch_alter_table(table) as batch_op:
            batch_op.alter_column(
                column,
                existing_type=sa.Numeric(4, 2),
                type_=sa.Numeric(6, 4),
                existing_nullable=nullable,
            )


def downgrade() -> None:
    for table, column, nullable in COLUMNS:
        col = sa.column(column, sa.Numeric(6, 4))
        tbl = sa.table(table, col)
        rounded = sa.func.round(col, 2)
        op.execute(
            tbl.update()
            .where(col > 0)
            .values({column: sa.case((rounded < 0.01, 0.01), else_=rounded)})
        )
        with op.batch_alter_table(table) as batch_op:
            batch_op.alter_column(
                column,
                existing_type=sa.Numeric(6, 4),
                type_=sa.Numeric(4, 2),
                existing_nullable=nullable,
            )
