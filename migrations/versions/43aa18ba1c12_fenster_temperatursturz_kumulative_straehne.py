"""Fenster-Erkennung aus Temperatursturz: kumulative Straehne statt starrer Uhr

Zweite Kreuzreview-Runde (2026-09-06): die Obergrenze aus `1b7bad26c13a` mass die
Straehnendauer an `zone_state.window_open_since` -- derselben Uhr, die
`services/ingest.py::advance_zone_state` bei jedem Zyklus ohne erkannten Sturz auf
`NULL` zuruecksetzt. Ein einzelner verrauschter Zyklus genau am Rand des Halts
setzte damit die gesamte Straehne zurueck, und die Zwangspause feuerte in einer
Simulation ueber 20 Laeufe und 420 Minuten kein einziges Mal. Fuegt an:

* `setting.window_temp_drop_gap_tolerance_minutes` (Vorgabe 10) -- wie lange eine
  Unterbrechung noch als Fortsetzung derselben Straehne gilt
  (`domain.window_temperature_drop.temperature_detection_gap_within_tolerance`).
* `zone_state.window_temp_drop_streak_started_at` -- die Straehne misst sich jetzt
  an dieser eigenen, von `window_open_since` unabhaengigen Uhr.
* `zone_state.window_temp_drop_last_detected_at` -- wann die Straehne zuletzt
  tatsaechlich erkannt wurde, die Grundlage fuer die Toleranzpruefung.

Bleibt wie die vier bestehenden Schwellen ausserhalb von `domain.control.LIMITS`
und damit ausserhalb von REST und MCP (`domain.control.WINDOW_TEMP_DROP_LIMITS`).

Revision ID: 43aa18ba1c12
Revises: 1b7bad26c13a
Create Date: 2026-09-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "43aa18ba1c12"
down_revision: str | Sequence[str] | None = "1b7bad26c13a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("setting") as batch_op:
        batch_op.add_column(
            sa.Column(
                "window_temp_drop_gap_tolerance_minutes",
                sa.Integer(),
                server_default=sa.text("10"),
                nullable=False,
            )
        )
    with op.batch_alter_table("zone_state") as batch_op:
        batch_op.add_column(
            sa.Column("window_temp_drop_streak_started_at", sa.DateTime(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("window_temp_drop_last_detected_at", sa.DateTime(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("zone_state") as batch_op:
        batch_op.drop_column("window_temp_drop_last_detected_at")
        batch_op.drop_column("window_temp_drop_streak_started_at")
    with op.batch_alter_table("setting") as batch_op:
        batch_op.drop_column("window_temp_drop_gap_tolerance_minutes")
