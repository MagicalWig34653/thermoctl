"""Meldungsarten einzeln schaltbar, Zustellzustand des Webhooks

Fügt `setting` sechs Spalten hinzu, für die beiden im selben Auftrag gebauten
Teile der Benachrichtigungen -- Auslieferung ans Domänen-Tor
(`domain.fault_notice.notice_enabled`) und Anzeige in der späteren Oberfläche:

* `notify_sensor_faults`, `notify_bridge_faults`, `notify_command_failures`
  (`bool`, Vorgabe `True`) -- je ein Schalter pro Meldungsart aus
  `domain.fault_notice`, anlagenweit statt je Zone oder Schweregrad. Vorgabe
  `True` in allen drei Fällen: eine Anlage, die heute Meldungen bekommt, bekommt
  sie nach dieser Migration unverändert weiter.
* `notify_last_attempt_at` (`DateTime`, nullable), `notify_last_ok` (`bool`,
  nullable), `notify_last_error` (`String(255)`, nullable) -- wann zuletzt
  versucht wurde, eine Meldung an den Webhook zu geben, ob es gelang, und im
  Fehlerfall eine knappe, für die Oberfläche taugliche Begründung. Alle drei
  bleiben `NULL`, solange kein Webhook eingetragen ist oder noch nie ein Versuch
  stattfand.

Revision ID: 67e794059830
Revises: 635612893955
Create Date: 2026-09-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "67e794059830"
down_revision: str | Sequence[str] | None = "635612893955"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("setting") as batch_op:
        batch_op.add_column(
            sa.Column(
                "notify_sensor_faults",
                sa.Boolean(),
                server_default=sa.true(),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "notify_bridge_faults",
                sa.Boolean(),
                server_default=sa.true(),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "notify_command_failures",
                sa.Boolean(),
                server_default=sa.true(),
                nullable=False,
            )
        )
        batch_op.add_column(sa.Column("notify_last_attempt_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("notify_last_ok", sa.Boolean(), nullable=True))
        batch_op.add_column(sa.Column("notify_last_error", sa.String(255), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("setting") as batch_op:
        batch_op.drop_column("notify_last_error")
        batch_op.drop_column("notify_last_ok")
        batch_op.drop_column("notify_last_attempt_at")
        batch_op.drop_column("notify_command_failures")
        batch_op.drop_column("notify_bridge_faults")
        batch_op.drop_column("notify_sensor_faults")
