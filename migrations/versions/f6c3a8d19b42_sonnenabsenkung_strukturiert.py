"""sonnenabsenkung_strukturiert

`shadow_decision` bekommt die Sonnenabsenkung als eigene Angabe. Bisher stand sie nur im
Fließtext von `setpoint_reason` ("Sonnenabsenkung: -2,0 K wegen erwarteter ...") und war
damit weder auswertbar noch im Schaltprotokoll erkennbar.

* `scheduled_setpoint_c` (`NUMERIC(5,2)`, nullable): der Sollwert vor der Absenkung.
* `solar_setback_k` (`NUMERIC(4,1)`, nullable): die angewandte Absenkung in Kelvin;
  NULL heißt: keine Absenkung.
* Index `ix_shadow_decision_solar_setback` (`solar_setback_k`, `zone_id`, `decided_at`):
  Das Schaltprotokoll leitet seine Absenkungseinträge aus Zustandswechseln zwischen
  aufeinanderfolgenden Zeilen ab und muss dafür wissen, welche Zonen überhaupt je eine
  Absenkung hatten -- ohne die gesamte Tabelle (Größenordnung 10^5 bis 10^6 Zeilen) zu
  lesen. Fast alle Zeilen haben hier NULL; der Bereichszugriff auf die Zeilen mit Wert
  ist der billige Weg.

Bestehende Zeilen bleiben NULL: Aus dem Text wird bewusst nichts zurückgeraten. Weder
ENUM noch JSON, nur einfache Spalten; Batch-Modus für SQLite.

Revision ID: f6c3a8d19b42
Revises: e5b92d7f3a18
Create Date: 2026-10-09 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f6c3a8d19b42"
down_revision: str | Sequence[str] | None = "e5b92d7f3a18"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX = "ix_shadow_decision_solar_setback"


def upgrade() -> None:
    """Upgrade schema: add the two columns and the lookup index."""
    with op.batch_alter_table("shadow_decision", schema=None) as batch_op:
        batch_op.add_column(sa.Column("scheduled_setpoint_c", sa.Numeric(5, 2), nullable=True))
        batch_op.add_column(sa.Column("solar_setback_k", sa.Numeric(4, 1), nullable=True))
    op.create_index(_INDEX, "shadow_decision", ["solar_setback_k", "zone_id", "decided_at"])


def downgrade() -> None:
    """Downgrade schema: drop the index first, then the columns."""
    op.drop_index(_INDEX, table_name="shadow_decision")
    with op.batch_alter_table("shadow_decision", schema=None) as batch_op:
        batch_op.drop_column("solar_setback_k")
        batch_op.drop_column("scheduled_setpoint_c")
