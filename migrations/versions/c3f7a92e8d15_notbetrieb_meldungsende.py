"""notbetrieb_meldungsende

Auftrag 8b (Anzeige, Schaltprotokoll, Meldung): `sensor_failure_episode` kennt
bislang nur, *dass* sie beendet wurde (`ended_at`), nicht *warum*. Für die
Entwarnungslogik ist das ein Unterschied, der zählt --
`domain.emergency_operation.advance()` kann eine Episode auch mit
`reason_code=REASON_DEAKTIVIERT` beenden (`sensor_failure_enabled` wurde
mitten in der Episode auf `False` gesetzt), und das ist **keine** Entwarnung:
die Störung ist nicht behoben, der Betreiber hat nur aufgehört, sie zu
verfolgen (siehe `emergency_operation.py` Modul-Docstring, Abschnitt
"Episodes"). Ohne eine eigene Spalte wäre der Grund im Moment des
Zyklusdurchlaufs bekannt, aber nirgends mehr auffindbar, sobald die
asynchrone Meldungszustellung (Auftrag 8b) ihn braucht.

`shadow_run.py::_persist_episode` schreibt `events.reason_code` genau in dem
Zyklus, in dem `events.episode_ended` wahr wird -- dieselbe Stelle, die schon
`ended_at` setzt. `NULL` bleibt für jede zuvor schon beendete Episode (vor
dieser Migration gab es die Unterscheidung nicht) und für jede weiterhin
offene Episode; beides liest die neue Meldungslogik als "kein
Deaktivierungsgrund bekannt", niemals als Entwarnungs-Blockade.

Revision ID: c3f7a92e8d15
Revises: b2e6f1a9c374
Create Date: 2026-10-02 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3f7a92e8d15"
down_revision: str | Sequence[str] | None = "b2e6f1a9c374"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("sensor_failure_episode", schema=None) as batch_op:
        batch_op.add_column(sa.Column("ended_reason_code", sa.String(length=64), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("sensor_failure_episode", schema=None) as batch_op:
        batch_op.drop_column("ended_reason_code")
