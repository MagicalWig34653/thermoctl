"""meldezustand_zwischenstufen

`sensor_failure_episode.notification_state` bekommt zwei Zwischenzustaende,
`melden_laeuft` und `entwarnung_laeuft` (Projektinhaber-Entscheidung): Vor dem
Versand einer Stoerungsmeldung bzw. Entwarnung wird der Zwischenzustand
committet, nach dem Versand der Endzustand (`gemeldet` bzw.
`entwarnung_gesendet`). Bricht der Prozess dazwischen ab, findet der naechste
Lauf die Zwischenstufe und versucht den Versand genau einmal erneut -- lieber
eine doppelte als eine fehlende Meldung.

Es gibt keinen ENUM: die Wertemenge steht in einem CHECK-Constraint, der hier
ersetzt wird (datenbankagnostisch, Batch-Modus fuer SQLite).

Downgrade: Der alte Constraint kennt die Zwischenzustaende nicht; mit
vorhandenen Zeilen scheitert sein Wiederherstellen. Die Zeilen werden deshalb
auf den passenden Vorwert zurueckgeschrieben -- `melden_laeuft` -> `offen`
(die alte Logik meldet dann erneut), `entwarnung_laeuft` -> `gemeldet` (die alte
Logik sendet die Entwarnung erneut). Beides ist die sichere Richtung: eher eine
doppelte als eine fehlende Meldung.

Revision ID: e5b92d7f3a18
Revises: d4a81c6e5b29
Create Date: 2026-10-03 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5b92d7f3a18"
down_revision: str | Sequence[str] | None = "d4a81c6e5b29"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINT = "ck_sensor_failure_episode_notification_state"

_episode = sa.table("sensor_failure_episode", sa.column("notification_state", sa.String))


def upgrade() -> None:
    """Upgrade schema: allow the two intermediate states."""
    with op.batch_alter_table("sensor_failure_episode", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f(_CONSTRAINT), type_="check")
        batch_op.create_check_constraint(
            batch_op.f(_CONSTRAINT),
            "notification_state IN ('offen', 'melden_laeuft', 'gemeldet', "
            "'entwarnung_laeuft', 'entwarnung_gesendet')",
        )


def downgrade() -> None:
    """Downgrade schema: map intermediate states back, then restore the old constraint."""
    op.execute(
        _episode.update()
        .where(_episode.c.notification_state == "melden_laeuft")
        .values(notification_state="offen")
    )
    op.execute(
        _episode.update()
        .where(_episode.c.notification_state == "entwarnung_laeuft")
        .values(notification_state="gemeldet")
    )
    with op.batch_alter_table("sensor_failure_episode", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f(_CONSTRAINT), type_="check")
        batch_op.create_check_constraint(
            batch_op.f(_CONSTRAINT),
            "notification_state IN ('offen', 'gemeldet', 'entwarnung_gesendet')",
        )
