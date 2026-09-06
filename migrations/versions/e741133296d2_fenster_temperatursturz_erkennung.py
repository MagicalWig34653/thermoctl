"""Fenster-Erkennung aus einem Temperatursturz

Fügt an:

* `zone.window_temp_drop_detection_enabled` (Vorgabe aus) -- je Zone einschaltbar, nur
  wirksam für eine Zone ohne zugeordneten Fensterkontakt
  (`services/ingest.py::_window_open`, `domain.window_temperature_drop`).
* `setting.window_temp_drop_window_minutes` (Vorgabe 15), `.window_temp_drop_threshold_k`
  (Vorgabe 1.5) und `.window_temp_drop_hold_minutes` (Vorgabe 30) -- die drei
  anlagenweiten Schwellen dieser Erkennung, kein Wert je Zone. Bewusst nicht in
  `domain.control.LIMITS`: bleiben wie die Fenster-Alarm-Schwellen außerhalb von REST
  und MCP (`domain.control.WINDOW_TEMP_DROP_LIMITS`).
* `zone_state.window_open_by_temperature` (Vorgabe aus) -- ob das aktuelle
  `window_open = true` aus dieser Vermutung stammt statt aus einem echten Kontakt,
  fortgeschrieben in `services/ingest.py::advance_zone_state`. Hält die beiden Quellen
  in der Oberfläche und im Protokoll (`shadow_decision.reason`) auseinander, Grundsatz 5.

Revision ID: e741133296d2
Revises: f18d4dcb3f5d
Create Date: 2026-09-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e741133296d2"
down_revision: str | Sequence[str] | None = "f18d4dcb3f5d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("zone") as batch_op:
        batch_op.add_column(
            sa.Column(
                "window_temp_drop_detection_enabled",
                sa.Boolean(),
                server_default=sa.false(),
                nullable=False,
            )
        )
    with op.batch_alter_table("setting") as batch_op:
        batch_op.add_column(
            sa.Column(
                "window_temp_drop_window_minutes",
                sa.Integer(),
                server_default=sa.text("15"),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "window_temp_drop_threshold_k",
                sa.Numeric(3, 1),
                server_default=sa.text("1.5"),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "window_temp_drop_hold_minutes",
                sa.Integer(),
                server_default=sa.text("30"),
                nullable=False,
            )
        )
    with op.batch_alter_table("zone_state") as batch_op:
        batch_op.add_column(
            sa.Column(
                "window_open_by_temperature",
                sa.Boolean(),
                server_default=sa.false(),
                nullable=False,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("zone_state") as batch_op:
        batch_op.drop_column("window_open_by_temperature")
    with op.batch_alter_table("setting") as batch_op:
        batch_op.drop_column("window_temp_drop_hold_minutes")
        batch_op.drop_column("window_temp_drop_threshold_k")
        batch_op.drop_column("window_temp_drop_window_minutes")
    with op.batch_alter_table("zone") as batch_op:
        batch_op.drop_column("window_temp_drop_detection_enabled")
