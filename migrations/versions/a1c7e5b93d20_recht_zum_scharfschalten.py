"""Eigenes Recht zum Scharfschalten der Regelung

Bis hierher gab es nur `setting.manage` fuer alle globalen Einstellungen. Scharfschalten
ist aber die einzige davon, deren Umlegen unmittelbar ein Ventil bewegt -- wer Zeitzone
und Aufbewahrungsdauer pflegen darf, soll das nicht nebenbei koennen.

Das Recht bekommen alle Gruppen, die bereits `setting.manage` halten. Auf einer frisch
eingerichteten Anlage ist das die Gruppe *Verwaltung*; bei einer bestehenden bleibt die
Zuteilung damit an derselben Stelle, an der die globale Verwaltung ohnehin liegt, statt
still bei niemandem zu landen.

Revision ID: a1c7e5b93d20
Revises: 53e02fa9682b
Create Date: 2026-08-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1c7e5b93d20"
down_revision: str | Sequence[str] | None = "53e02fa9682b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CODE = "control.arm"
BESCHREIBUNG = "Die Regelung scharf schalten"


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

    # Nur anlagenweit (zone_id IS NULL) und nur dort, wo die Zuteilung nicht schon steht:
    # Die Revision muss sich wiederholen lassen, ohne doppelte Zeilen zu hinterlassen.
    verbindung.execute(
        sa.text(
            "INSERT INTO group_permission (access_group_id, permission_id, zone_id) "
            "SELECT gp.access_group_id, :neu, NULL "
            "FROM group_permission gp "
            "JOIN permission p ON p.id = gp.permission_id "
            "WHERE p.code = 'setting.manage' AND gp.zone_id IS NULL "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM group_permission vorhanden "
            "  WHERE vorhanden.access_group_id = gp.access_group_id "
            "    AND vorhanden.permission_id = :neu AND vorhanden.zone_id IS NULL"
            ")"
        ),
        {"neu": vorhanden},
    )


def downgrade() -> None:
    verbindung = op.get_bind()
    # Erst die Zuteilungen, dann das Recht: umgekehrt haelt der Fremdschluessel dagegen.
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
