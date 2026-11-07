"""Mehrere Temperaturfühler je Zone

Bisher trug `zone.temperature_source_device_id` genau einen Fühler. Jetzt steht jeder Fühler
als eigene Zeile in `zone_temperature_source` (Reihenfolge in `sort_order`); die Spalte
entfällt.

Vorwärts wird jeder bestehende Wert als Zeile mit `sort_order` 0 übernommen (Zonen ohne
Quelle bekommen keine Zeile), danach wird die Spalte samt Fremdschlüssel entfernt.

Rückwärts wird die Spalte wieder angelegt (nullable, Fremdschlüssel `ON DELETE SET NULL`) und
je Zone mit dem Fühler gefüllt, der die kleinste `sort_order` trägt (bei Gleichstand die
kleinste `id`). **Der Downgrade ist verlustbehaftet:** hat eine Zone mehrere Fühler, bleibt
nur dieser eine erhalten, die übrigen Zeilen gehen mit der Tabelle verloren.

Revision ID: c7e2a4f91d38
Revises: b3f5d7a92c61
Create Date: 2026-11-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c7e2a4f91d38"
down_revision: str | Sequence[str] | None = "b3f5d7a92c61"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Beide Fremdschlüssel tragen denselben Namen (Namenskonvention: Tabelle `zone` mit Spalte
# `temperature_source_device_id` gegen Tabelle `zone_temperature_source` mit Spalte
# `device_id`). MariaDB vergibt Fremdschlüsselnamen je Schema; deshalb gibt es nie beide
# gleichzeitig: der neue entsteht erst, nachdem der alte weg ist (und umgekehrt).
FK_NAME = "fk_zone_temperature_source_device_id_device"


def upgrade() -> None:
    op.create_table(
        "zone_temperature_source",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("zone_id", sa.Integer(), nullable=False),
        sa.Column("device_id", sa.Integer(), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.ForeignKeyConstraint(
            ["zone_id"],
            ["zone.id"],
            name=op.f("fk_zone_temperature_source_zone_id_zone"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_zone_temperature_source")),
        sa.UniqueConstraint("zone_id", "device_id", name="fuehler_je_zone"),
    )
    op.get_bind().execute(
        sa.text(
            "INSERT INTO zone_temperature_source (zone_id, device_id, sort_order) "
            "SELECT id, temperature_source_device_id, 0 FROM zone "
            "WHERE temperature_source_device_id IS NOT NULL"
        )
    )
    with op.batch_alter_table("zone") as batch_op:
        batch_op.drop_constraint(FK_NAME, type_="foreignkey")
        batch_op.drop_column("temperature_source_device_id")
    with op.batch_alter_table("zone_temperature_source") as batch_op:
        batch_op.create_foreign_key(FK_NAME, "device", ["device_id"], ["id"], ondelete="CASCADE")


def downgrade() -> None:
    with op.batch_alter_table("zone") as batch_op:
        batch_op.add_column(sa.Column("temperature_source_device_id", sa.Integer(), nullable=True))
    # Der Fühler mit kleinster `sort_order` (dann kleinster `id`) je Zone.
    op.get_bind().execute(
        sa.text(
            "UPDATE zone SET temperature_source_device_id = ("
            "SELECT s.device_id FROM zone_temperature_source s WHERE s.zone_id = zone.id "
            "ORDER BY s.sort_order, s.id LIMIT 1)"
        )
    )
    op.drop_table("zone_temperature_source")
    with op.batch_alter_table("zone") as batch_op:
        batch_op.create_foreign_key(
            FK_NAME, "device", ["temperature_source_device_id"], ["id"], ondelete="SET NULL"
        )
