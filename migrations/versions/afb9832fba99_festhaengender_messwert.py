"""Festhängender Messwert

Fügt die Erkennung aus `domain/fault.py::stuck_reading` an:

* `setting.stuck_reading_hours` -- wie lange ein Messwert unbewegt bleiben darf, bevor
  er als `festhängend` gilt. Vorgabe 12 Stunden, anlagenweit, kein Wert je Zone (wie
  `measurement_retention_days`).
* `setting.notify_stuck_sensor` -- eine vierte, eigene Meldungsart neben den
  bestehenden drei (`notify_sensor_faults`, `notify_bridge_faults`,
  `notify_command_failures`); ein festhängender Messwert ist keine Sensorstörung --
  die Zone regelt unverändert weiter -- und teilt sich deshalb keinen Schalter mit
  echten Sensorausfällen.
* `zone_state.sensor_stuck` -- das abgeleitete Ergebnis je Zone
  (`services/ingest.py::advance_zone_state`), unabhängig von `sensor_status_id` und
  von `decide()` in `domain/control_loop.py` ungelesen.

Revision ID: afb9832fba99
Revises: bb4a0ff63b2d
Create Date: 2026-09-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "afb9832fba99"
down_revision: str | Sequence[str] | None = "bb4a0ff63b2d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("setting") as batch_op:
        batch_op.add_column(
            sa.Column(
                "stuck_reading_hours",
                sa.Integer(),
                server_default=sa.text("12"),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "notify_stuck_sensor",
                sa.Boolean(),
                server_default=sa.true(),
                nullable=False,
            )
        )
    with op.batch_alter_table("zone_state") as batch_op:
        batch_op.add_column(
            sa.Column(
                "sensor_stuck",
                sa.Boolean(),
                server_default=sa.false(),
                nullable=False,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("zone_state") as batch_op:
        batch_op.drop_column("sensor_stuck")
    with op.batch_alter_table("setting") as batch_op:
        batch_op.drop_column("notify_stuck_sensor")
        batch_op.drop_column("stuck_reading_hours")
