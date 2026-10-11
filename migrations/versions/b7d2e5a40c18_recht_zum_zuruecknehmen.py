"""Eigenes Recht zum Zurücknehmen der Regelung in den Trockenlauf

`force_dry_run` im MCP-Server verlangte bisher `control.arm`. Das koppelte die sichere
Richtung (zurück in den Trockenlauf) an das Recht zur gefährlichen (scharf schalten):
ein Token, das die Regelung nur anhalten können soll, hätte sie auch scharf schalten
dürfen. `control.disarm` trennt beides.

Damit niemand eine Fähigkeit verliert, die er hatte, bekommen alle Gruppen und alle
bestehenden API-Token das neue Recht, die `control.arm` anlagenweit halten. Wer
`control.arm` nicht hatte, bekommt nichts.

Revision ID: b7d2e5a40c18
Revises: f6c3a8d19b42
Create Date: 2026-10-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7d2e5a40c18"
down_revision: str | Sequence[str] | None = "f6c3a8d19b42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CODE = "control.disarm"
BESCHREIBUNG = "Die Regelung in den Trockenlauf zurücknehmen"


def upgrade() -> None:
    verbindung = op.get_bind()
    vorhanden = verbindung.execute(
        sa.text("SELECT id FROM permission WHERE code = :code"), {"code": CODE}
    ).scalar()
    if vorhanden is None:
        verbindung.execute(
            sa.text(
                "INSERT INTO permission (code, description, is_zone_scoped) "
                "VALUES (:code, :beschreibung, :zonenbezogen)"
            ),
            {"code": CODE, "beschreibung": BESCHREIBUNG, "zonenbezogen": False},
        )
        vorhanden = verbindung.execute(
            sa.text("SELECT id FROM permission WHERE code = :code"), {"code": CODE}
        ).scalar()

    # Nur anlagenweit (zone_id IS NULL) und nur, wo die Zuteilung noch nicht steht:
    # die Revision muss sich wiederholen lassen, ohne doppelte Zeilen zu hinterlassen.
    verbindung.execute(
        sa.text(
            "INSERT INTO group_permission (access_group_id, permission_id, zone_id) "
            "SELECT gp.access_group_id, :neu, NULL "
            "FROM group_permission gp "
            "JOIN permission p ON p.id = gp.permission_id "
            "WHERE p.code = 'control.arm' AND gp.zone_id IS NULL "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM group_permission vorhanden "
            "  WHERE vorhanden.access_group_id = gp.access_group_id "
            "    AND vorhanden.permission_id = :neu AND vorhanden.zone_id IS NULL"
            ")"
        ),
        {"neu": vorhanden},
    )
    # Dasselbe für API-Token: ihr Recht liegt in api_token_permission und gilt als
    # Obergrenze zusätzlich zur Gruppe; ohne diese Zuteilung verlöre ein Token mit
    # control.arm still die Fähigkeit, force_dry_run zu nutzen.
    verbindung.execute(
        sa.text(
            "INSERT INTO api_token_permission (api_token_id, permission_id, zone_id) "
            "SELECT atp.api_token_id, :neu, NULL "
            "FROM api_token_permission atp "
            "JOIN permission p ON p.id = atp.permission_id "
            "WHERE p.code = 'control.arm' AND atp.zone_id IS NULL "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM api_token_permission vorhanden "
            "  WHERE vorhanden.api_token_id = atp.api_token_id "
            "    AND vorhanden.permission_id = :neu AND vorhanden.zone_id IS NULL"
            ")"
        ),
        {"neu": vorhanden},
    )


def downgrade() -> None:
    verbindung = op.get_bind()
    # Erst die Zuteilungen, dann das Recht: umgekehrt hält der Fremdschlüssel dagegen.
    verbindung.execute(
        sa.text(
            "DELETE FROM api_token_permission WHERE permission_id IN "
            "(SELECT id FROM permission WHERE code = :code)"
        ),
        {"code": CODE},
    )
    verbindung.execute(
        sa.text(
            "DELETE FROM group_permission WHERE permission_id IN "
            "(SELECT id FROM permission WHERE code = :code)"
        ),
        {"code": CODE},
    )
    verbindung.execute(
        sa.text("DELETE FROM permission WHERE code = :code"), {"code": CODE}
    )
