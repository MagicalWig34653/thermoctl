"""UI-Profil je Gruppe (Admin- oder Mieter-Oberfläche)

Fügt `access_group.ui_profile` an: welche Weboberfläche die Mitglieder einer Gruppe
bekommen -- die Anlagensicht (`admin`) oder die Wohnungssicht (`tenant`), siehe
`thermoctl/domain/ui_profile.py`.

**Ausdrücklich keine Berechtigung.** Was ein Mitglied darf, steht weiterhin allein in
`group_permission`; die Spalte entscheidet nur, welche Oberfläche gerendert wird.

Bestehende Gruppen bekommen `admin`. Das ist der einzige vertretbare Wert für ein
Upgrade: keine bestehende Installation hat ihre Gruppen je als Mietergruppe
gekennzeichnet, und eine Umklassifizierung anhand des Gruppennamens ("Mieter",
"Bewohner") wäre geraten -- ein Name ist kein Modell. Wer eine Mietergruppe will,
setzt sie danach ausdrücklich in der Gruppenverwaltung.

Als `VARCHAR(16)` und nicht als ENUM: das Projekt bleibt datenbankagnostisch
(Grundsatz 3).

Revision ID: c4d18b7e2a95
Revises: 43aa18ba1c12
Create Date: 2026-09-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4d18b7e2a95"
down_revision: str | Sequence[str] | None = "43aa18ba1c12"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("access_group") as batch_op:
        batch_op.add_column(
            sa.Column(
                "ui_profile",
                sa.String(length=16),
                server_default="admin",
                nullable=False,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("access_group") as batch_op:
        batch_op.drop_column("ui_profile")
