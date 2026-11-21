"""Einstellungen für die befristete Übersteuerung am Sollwertrad.

Zwei neue Spalten an `setting`: `temporary_setpoint_until_next_switch` (1 = die
Übersteuerung endet an der nächsten Schaltung des Zeitplans, 0 = feste Dauer) und
`temporary_setpoint_minutes` (feste Dauer und zugleich Ersatzfrist, wenn es keine nächste
Schaltung gibt). Bestehende Anlagen bekommen 1 und 120: Das Rad wirkt bis zur nächsten
Schaltung, ohne dass jemand etwas einstellen muss. Beide Wertemengen sind per CHECK
gesichert.

Das Zurückstufen nimmt beide Spalten samt Prüfungen wieder heraus.

Revision ID: b5d1f8a3c264
Revises: a3e8c5d17b94
Create Date: 2026-11-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b5d1f8a3c264"
down_revision: str | Sequence[str] | None = "a3e8c5d17b94"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CHECKS = (
    (
        "ck_setting_temporary_setpoint_until_next_switch_wertemenge",
        "temporary_setpoint_until_next_switch IN (0, 1)",
    ),
    (
        "ck_setting_temporary_setpoint_minutes_bereich",
        "temporary_setpoint_minutes BETWEEN 5 AND 1440",
    ),
)


def upgrade() -> None:
    with op.batch_alter_table("setting") as batch_op:
        batch_op.add_column(
            sa.Column(
                "temporary_setpoint_until_next_switch",
                sa.Integer(),
                server_default=sa.text("1"),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "temporary_setpoint_minutes",
                sa.Integer(),
                server_default=sa.text("120"),
                nullable=False,
            )
        )
        for name, condition in CHECKS:
            batch_op.create_check_constraint(op.f(name), condition)


def downgrade() -> None:
    with op.batch_alter_table("setting") as batch_op:
        for name, _condition in reversed(CHECKS):
            batch_op.drop_constraint(op.f(name), type_="check")
        batch_op.drop_column("temporary_setpoint_minutes")
        batch_op.drop_column("temporary_setpoint_until_next_switch")
