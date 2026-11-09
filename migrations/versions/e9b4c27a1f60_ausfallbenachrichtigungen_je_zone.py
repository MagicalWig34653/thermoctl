"""Ausfallbenachrichtigungen je Zone abschaltbar

Fügt `zone.fault_notifications_enabled` an (Vorgabe an). Betrifft allein die Zustellung der
Störungsmeldungen dieses Raums (Webhook); die Störung selbst bleibt in Oberfläche,
Schaltprotokoll und Home-Assistant-Problemsensor sichtbar. Bestehende Zonen bekommen
über den `server_default` den Wert wahr -- das bisherige Verhalten bleibt.

Revision ID: e9b4c27a1f60
Revises: d4a8e1b73c52
Create Date: 2026-11-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e9b4c27a1f60"
down_revision: str | Sequence[str] | None = "d4a8e1b73c52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("zone") as batch_op:
        batch_op.add_column(
            sa.Column(
                "fault_notifications_enabled",
                sa.Boolean(),
                server_default=sa.true(),
                nullable=False,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("zone") as batch_op:
        batch_op.drop_column("fault_notifications_enabled")
