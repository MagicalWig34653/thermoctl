"""Das Recht `audit.read` wird raumbezogen

Bisher galt `audit.read` nur anlagenweit: Protokoll, Schaltprotokoll, Relaisverschleiß und
Änderungsprotokoll zeigten auch Räume, die der Benutzer sonst nicht sieht. Jetzt lässt sich
das Recht auf einzelne Räume beschränken; dann zeigen die Ansichten nur deren Einträge.

Bestehende Vergaben bleiben unverändert: sie sind anlagenweit (`zone_id` NULL) und sehen
weiterhin alles. Die Migration setzt nur das Kennzeichen `is_zone_scoped`.

Rückwärts wird das Kennzeichen zurückgesetzt und zuvor jede raumbeschränkte Vergabe von
`audit.read` gelöscht -- ein nicht raumbezogenes Recht darf keine Zone tragen (sonst stünde
eine Vergabe in der Liste, die nie wirkt).

Revision ID: b3f5d7a92c61
Revises: a8d4f12c6e53
Create Date: 2026-10-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b3f5d7a92c61"
down_revision: str | Sequence[str] | None = "a8d4f12c6e53"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CODE = "audit.read"


def upgrade() -> None:
    op.get_bind().execute(
        sa.text("UPDATE permission SET is_zone_scoped = :wert WHERE code = :code"),
        {"wert": True, "code": CODE},
    )


def downgrade() -> None:
    verbindung = op.get_bind()
    for tabelle in ("api_token_permission", "group_permission"):
        verbindung.execute(
            sa.text(
                f"DELETE FROM {tabelle} WHERE zone_id IS NOT NULL AND permission_id IN "  # noqa: S608
                "(SELECT id FROM permission WHERE code = :code)"
            ),
            {"code": CODE},
        )
    verbindung.execute(
        sa.text("UPDATE permission SET is_zone_scoped = :wert WHERE code = :code"),
        {"wert": False, "code": CODE},
    )
