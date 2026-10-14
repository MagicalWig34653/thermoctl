"""Thermostat-Betriebsart statt Ja/Nein: controlled, self_regulating, reading_only.

`zone_device.self_regulating` (Boolean) wird durch `zone_device.thermostat_mode`
(kurzer String, kein ENUM) ersetzt. Daten: False -> controlled, True -> self_regulating.
Neu ist `reading_only` (thermoctl schreibt nur den Istwert, nie einen Sollwert).

Downgrade: controlled -> False, self_regulating -> True, reading_only -> True. Das Letzte
ist verlustbehaftet: Das alte Modell kennt keine reine Istwert-Betriebsart, und
`self_regulating` ist die nächste Entsprechung (das Ventil wird nicht mehr von
thermoctl geschaltet). Nach dem Downgrade schreibt thermoctl solchen Ventilen
zusätzlich einen Sollwert -- das ist ausdrücklich ein Unterschied zu vorher.

Revision ID: d8e3a51c7f24
Revises: c4a9e2f71b35
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d8e3a51c7f24"
down_revision: str | Sequence[str] | None = "c4a9e2f71b35"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("zone_device", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "thermostat_mode",
                sa.String(length=16),
                server_default="controlled",
                nullable=False,
            )
        )
    op.execute(
        sa.text(
            "UPDATE zone_device SET thermostat_mode = 'self_regulating' "
            "WHERE self_regulating = :yes"
        ).bindparams(sa.bindparam("yes", True, type_=sa.Boolean()))
    )
    with op.batch_alter_table("zone_device", schema=None) as batch_op:
        batch_op.drop_column("self_regulating")


def downgrade() -> None:
    with op.batch_alter_table("zone_device", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "self_regulating",
                sa.Boolean(),
                server_default=sa.false(),
                nullable=False,
            )
        )
    op.execute(
        sa.text(
            "UPDATE zone_device SET self_regulating = :yes "
            "WHERE thermostat_mode IN ('self_regulating', 'reading_only')"
        ).bindparams(sa.bindparam("yes", True, type_=sa.Boolean()))
    )
    with op.batch_alter_table("zone_device", schema=None) as batch_op:
        batch_op.drop_column("thermostat_mode")
