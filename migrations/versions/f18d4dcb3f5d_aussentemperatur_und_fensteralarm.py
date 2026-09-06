"""Außentemperatur und Fenster-Alarm

Fügt an:

* `setting.outdoor_temperature_source_device_id` -- die anlagenweite Außentemperaturquelle,
  ausgewählt aus den bekannten Zigbee2MQTT-Geräten, genau wie
  `zone.temperature_source_device_id` für eine Zone, aber genau eine für die ganze Anlage.
* `setting.window_alarm_open_minutes` (Vorgabe 30) und
  `setting.window_alarm_outdoor_threshold_c` (Vorgabe 5.0) -- die beiden Schwellen des
  Fenster-Alarms (`domain.window_alarm`), anlagenweit, kein Wert je Zone.
* `setting.notify_window_alarm` -- eine fünfte, eigene Meldungsart neben den bestehenden
  vier (`notify_sensor_faults`, `notify_bridge_faults`, `notify_command_failures`,
  `notify_stuck_sensor`).
* `zone_state.window_open_since` -- seit wann das Fenster der Zone ununterbrochen offen
  ist, fortgeschrieben in `services/ingest.py::advance_zone_state`.
* `zone_state.window_alarm` -- das abgeleitete Ergebnis je Zone, tri-state (`NULL` heißt
  "unbekannt", nicht "kein Alarm") -- unabhängig von `sensor_status_id` und von `decide()`
  in `domain/control_loop.py`.

Revision ID: f18d4dcb3f5d
Revises: afb9832fba99
Create Date: 2026-09-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f18d4dcb3f5d"
down_revision: str | Sequence[str] | None = "c1a4e9d872b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("setting") as batch_op:
        batch_op.add_column(
            sa.Column(
                "outdoor_temperature_source_device_id",
                sa.Integer(),
                nullable=True,
            )
        )
        batch_op.create_foreign_key(
            batch_op.f("fk_setting_outdoor_temperature_source_device_id_device"),
            "device",
            ["outdoor_temperature_source_device_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.add_column(
            sa.Column(
                "window_alarm_open_minutes",
                sa.Integer(),
                server_default=sa.text("30"),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "window_alarm_outdoor_threshold_c",
                sa.Numeric(4, 1),
                server_default=sa.text("5.0"),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "notify_window_alarm",
                sa.Boolean(),
                server_default=sa.true(),
                nullable=False,
            )
        )
    with op.batch_alter_table("zone_state") as batch_op:
        batch_op.add_column(sa.Column("window_open_since", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("window_alarm", sa.Boolean(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("zone_state") as batch_op:
        batch_op.drop_column("window_alarm")
        batch_op.drop_column("window_open_since")
    with op.batch_alter_table("setting") as batch_op:
        batch_op.drop_column("notify_window_alarm")
        batch_op.drop_column("window_alarm_outdoor_threshold_c")
        batch_op.drop_column("window_alarm_open_minutes")
        batch_op.drop_constraint(
            batch_op.f("fk_setting_outdoor_temperature_source_device_id_device"),
            type_="foreignkey",
        )
        batch_op.drop_column("outdoor_temperature_source_device_id")
