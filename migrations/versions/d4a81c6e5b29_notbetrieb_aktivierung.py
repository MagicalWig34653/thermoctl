"""notbetrieb_aktivierung

Auftrag 9 (automatische Aktivierung): Der Notbetrieb bei Sensorausfall wird
für **alle Bestandszonen** eingeschaltet (`zone.sensor_failure_enabled = true`).
Das ist eine bewusste Verhaltensänderung beim Upgrade -- Fußbodenkreise takten
bei Sensorausfall, Thermostate werden einmal auf `manual` + Notsollwert
gestellt, es gibt Meldungen. Profil (`sensor_failure_profile_id`) und
Notsollwert (`sensor_failure_emergency_setpoint_c`) der Zonen bleiben
unangetastet: `NULL` heißt "Anlagenvorgabe erben" und bleibt so.

Neu angelegte Zonen starten ebenfalls mit `true`; das setzt der ORM-Default im
Modell (`Zone.sensor_failure_enabled`), nicht diese Migration. Der
`server_default` der Spalte bleibt `0` -- ihn zu ändern würde unter SQLite einen
Tabellenneubau erzwingen (`zone` hängt an vielen Fremdschlüsseln) und bringt
nichts, weil keine Anwendungsstelle eine Zone ohne ORM anlegt.

Downgrade: setzt die Spalte für **alle** Zonen zurück auf `false`. Er kann
nicht unterscheiden, welche Zonen vor dem Upgrade schon von Hand eingeschaltet
waren (die Migration speichert den Vorzustand nicht, und eine zusätzliche
Merkspalte wäre für einen Rückweg unverhältnismäßig). Gewählt ist die sicherste
Variante: der Zustand, den jede Fassung vor dieser Migration als Vorgabe kannte
(Notbetrieb aus, nichts schaltet von selbst in einen Ersatzbetrieb). Ein
Betreiber, der den Notbetrieb vorher schon von Hand aktiviert hatte, muss ihn
nach einem Downgrade erneut einschalten -- das Gegenteil (alles an lassen)
würde einen Rückweg ins Altverhalten mit aktivem Notbetrieb hinterlassen.

Datenbankagnostisch: ein Update mit gebundenem Boolean-Parameter über die
SQLAlchemy-Tabellenbeschreibung, keine datenbankspezifische Funktion.

Revision ID: d4a81c6e5b29
Revises: c3f7a92e8d15
Create Date: 2026-10-03 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4a81c6e5b29"
down_revision: str | Sequence[str] | None = "c3f7a92e8d15"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_zone = sa.table("zone", sa.column("sensor_failure_enabled", sa.Boolean()))


def upgrade() -> None:
    """Upgrade schema: switch the emergency operation on for every existing zone."""
    op.execute(_zone.update().values(sensor_failure_enabled=True))


def downgrade() -> None:
    """Downgrade schema: switch it off everywhere (see module docstring)."""
    op.execute(_zone.update().values(sensor_failure_enabled=False))
