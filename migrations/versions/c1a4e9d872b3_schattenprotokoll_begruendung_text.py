"""Begründungsspalten des Schattenprotokolls auf TEXT erweitert.

`shadow_decision.reason` und `shadow_decision.setpoint_reason` waren `VARCHAR(255)`.
Die Fensterausnahme für Frostschutz (2026-09-06) hat einen Überlauf sichtbar gemacht,
den nur der MariaDB-Testlauf meldet (SQLite erzwingt die Spaltenlänge nicht): die
Kombination aus einem frei benennbaren Zeitplan-Modus (`setpoint_mode.name`, bis zu 64
Zeichen), der Sonnenabsenkung, der neuen Fensterausnahme und dem PI-Zusatz kann 255
Zeichen deutlich überschreiten -- gemessen, nicht geschätzt, bis über 400 Zeichen im
worst case. Das ist kein neuer Fehler dieser Änderung, nur ihr erster sichtbarer
Auslöser: `reason` und `setpoint_reason` sind Protokolltext für Grundsatz 5
(Debuggbarkeit), keine Datenmodellspalte mit fachlicher Bedeutung -- eine Obergrenze
hier ist beliebig und schneidet früher oder später wieder etwas ab. `TEXT` statt einer
größeren, aber weiterhin willkürlichen `VARCHAR`-Länge.

Revision ID: c1a4e9d872b3
Revises: afb9832fba99
Create Date: 2026-09-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c1a4e9d872b3"
down_revision: str | Sequence[str] | None = "afb9832fba99"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("shadow_decision") as batch_op:
        batch_op.alter_column(
            "setpoint_reason",
            existing_type=sa.String(255),
            type_=sa.Text(),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "reason",
            existing_type=sa.String(255),
            type_=sa.Text(),
            existing_nullable=False,
        )


def downgrade() -> None:
    # A downgrade after real long values have been written would need truncation to
    # fit back into VARCHAR(255) -- silently, or the downgrade fails outright. Neither
    # is the right default for a reversibility test that runs against empty or
    # short-lived test databases; production never runs downgrade on a live table.
    with op.batch_alter_table("shadow_decision") as batch_op:
        batch_op.alter_column(
            "reason",
            existing_type=sa.Text(),
            type_=sa.String(255),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "setpoint_reason",
            existing_type=sa.Text(),
            type_=sa.String(255),
            existing_nullable=False,
        )
