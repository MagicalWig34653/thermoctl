"""Indizes für die Laufermittlung im Schattenprotokoll

`shadow_run._previous_state` ermittelt je Zone und Zyklus, seit wann der aktuelle
`would_heat`-Wert gilt. Das lud früher die ganze Historie der Zone; jetzt sind es wenige
Einzelzugriffe: die neueste Zeile mit dem *anderen* Wert und der neueste Rückkehr-Marker.
Beide brauchen einen Index, der mit `zone_id` und dem gesuchten Wert beginnt und mit
`decided_at, id` endet, sonst läse die Datenbank den ganzen Lauf.

Reine Indexänderung, keine Daten betroffen; auf großen Tabellen dauert das Anlegen
Sekunden. Rückwärts werden beide Indizes wieder entfernt.

Revision ID: d4a8e1b73c52
Revises: c7e2a4f91d38
Create Date: 2026-11-08
"""

from collections.abc import Sequence

from alembic import op

revision: str = "d4a8e1b73c52"
down_revision: str | Sequence[str] | None = "c7e2a4f91d38"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_shadow_decision_zone_heat_decided_id",
        "shadow_decision",
        ["zone_id", "would_heat", "decided_at", "id"],
    )
    op.create_index(
        "ix_shadow_decision_zone_outcome_decided_id",
        "shadow_decision",
        ["zone_id", "outcome_code", "decided_at", "id"],
    )


def downgrade() -> None:
    op.drop_index("ix_shadow_decision_zone_outcome_decided_id", table_name="shadow_decision")
    op.drop_index("ix_shadow_decision_zone_heat_decided_id", table_name="shadow_decision")
