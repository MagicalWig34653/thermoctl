"""Fenster-Erkennung aus Temperatursturz: Obergrenze gegen Rueckkopplung

Kreuzreview-Nacharbeit (2026-09-06): die Vermutung konnte sich nach Ablauf des
Halts (`window_temp_drop_hold_minutes`) durch ihre eigene Wirkung immer wieder
neu ausloesen, weil die Erkennung selbst die Waerme abstellt und der Raum danach
womoeglich steiler auskuehlt als die Abschaetzung im Docstring annimmt (die gilt
fuer normalen Betrieb, nicht fuer einen Raum, dem wiederholt die Waerme entzogen
wurde). Fuegt an:

* `setting.window_temp_drop_max_suspected_minutes` (Vorgabe 90 -- drei Halte) --
  harte Obergrenze fuer eine ununterbrochene Vermutungssträhne
  (`domain.window_temperature_drop.temperature_detection_cap_exceeded`).
* `setting.window_temp_drop_silence_minutes` (Vorgabe 60) -- wie lange die
  Erkennung danach schweigt, bevor sie ueberhaupt wieder ausloesen darf
  (`domain.window_temperature_drop.temperature_detection_still_silenced`).
* `zone_state.window_temp_drop_silence_until` -- die Zwangspausen-Frist je Zone,
  fortgeschrieben in `services/ingest.py::advance_zone_state`.

Beide Schwellen bleiben wie die drei bestehenden ausserhalb von
`domain.control.LIMITS` und damit ausserhalb von REST und MCP
(`domain.control.WINDOW_TEMP_DROP_LIMITS`).

Revision ID: 1b7bad26c13a
Revises: e741133296d2
Create Date: 2026-09-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "1b7bad26c13a"
down_revision: str | Sequence[str] | None = "e741133296d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("setting") as batch_op:
        batch_op.add_column(
            sa.Column(
                "window_temp_drop_max_suspected_minutes",
                sa.Integer(),
                server_default=sa.text("90"),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "window_temp_drop_silence_minutes",
                sa.Integer(),
                server_default=sa.text("60"),
                nullable=False,
            )
        )
    with op.batch_alter_table("zone_state") as batch_op:
        batch_op.add_column(
            sa.Column("window_temp_drop_silence_until", sa.DateTime(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("zone_state") as batch_op:
        batch_op.drop_column("window_temp_drop_silence_until")
    with op.batch_alter_table("setting") as batch_op:
        batch_op.drop_column("window_temp_drop_silence_minutes")
        batch_op.drop_column("window_temp_drop_max_suspected_minutes")
