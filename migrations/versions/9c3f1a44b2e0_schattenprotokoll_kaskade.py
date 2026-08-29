"""Schattenprotokoll folgt der Zone beim Loeschen

Der Fremdschluessel `shadow_decision.zone_id` hatte als einziger der Zonenbeziehungen
kein ON DELETE CASCADE. Dadurch scheiterte das Loeschen einer Zone, sobald ein einziger
Schattenlauf fuer sie gelaufen war — aufgefallen beim Bau der Zonenverwaltung.

Das Schattenprotokoll ist Betriebsdatum einer Zone. Dass die Zone geloescht wurde, steht
im Audit-Protokoll; das ist die Aufzeichnung, die ueberdauern soll, und sie haengt nicht
an der Zone.

Revision ID: 9c3f1a44b2e0
Revises: 8b2d6e8a7f10
Create Date: 2026-08-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9c3f1a44b2e0"
down_revision: str | Sequence[str] | None = "8b2d6e8a7f10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NAME = "fk_shadow_decision_zone_id_zone"


def upgrade() -> None:
    # batch_alter_table ist unter SQLite Pflicht: Dort laesst sich ein Fremdschluessel
    # nicht aendern, die Tabelle wird neu gebaut und umkopiert. Unter MariaDB uebersetzt
    # Alembic es in ein gewoehnliches ALTER TABLE.
    with op.batch_alter_table("shadow_decision") as batch_op:
        batch_op.drop_constraint(_NAME, type_="foreignkey")
        batch_op.create_foreign_key(_NAME, "zone", ["zone_id"], ["id"], ondelete="CASCADE")


def downgrade() -> None:
    with op.batch_alter_table("shadow_decision") as batch_op:
        batch_op.drop_constraint(_NAME, type_="foreignkey")
        batch_op.create_foreign_key(_NAME, "zone", ["zone_id"], ["id"])


_ = sa  # von Alembic mitgeneriert; hier nicht gebraucht
