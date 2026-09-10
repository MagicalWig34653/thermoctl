"""Abwesenheit eines Mieters für eigene Räume

Legt die Tabelle `absence` an -- die Klammer über den Übersteuerungen, die eine
Abwesenheit ausmachen -- und hängt `zone_override.absence_id` als Zuordnung daran.
Bestehende Übersteuerungen bleiben `NULL` und gehören damit zu keiner Abwesenheit;
sonst ließen sie sich später über „Abwesenheit beenden" mitbeenden, ohne dass sie
je eine gewesen wären.

Ausdrücklich **nicht** derselbe Vorgang wie der anlagenweite Urlaubsbetrieb
(Tabelle `vacation`): der senkt jede Zone der Anlage ab, eine Abwesenheit nur die
Räume, die der Handelnde bedienen darf. Siehe `thermoctl/domain/absence.py`.

Revision ID: c724de89a13f
Revises: c4d18b7e2a95
Create Date: 2026-09-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c724de89a13f"
down_revision: str | Sequence[str] | None = "c4d18b7e2a95"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "absence",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("starts_at", sa.DateTime(), nullable=False),
        sa.Column("ends_at", sa.DateTime(), nullable=False),
        sa.Column("setback_temperature_c", sa.Numeric(4, 1), nullable=False),
        sa.Column("cancelled_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(),
                  sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_by_token_id", sa.Integer(),
                  sa.ForeignKey("api_token.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("actor_source.id"), nullable=False),
        sa.CheckConstraint("ends_at > starts_at", name="abwesenheit_ende_nach_beginn"),
    )
    with op.batch_alter_table("zone_override") as batch_op:
        batch_op.add_column(sa.Column("absence_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            batch_op.f("fk_zone_override_absence_id_absence"),
            "absence", ["absence_id"], ["id"], ondelete="SET NULL",
        )
        batch_op.create_index(
            batch_op.f("ix_zone_override_absence_id"), ["absence_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("zone_override") as batch_op:
        # **Reihenfolge:** erst der Fremdschlüssel, dann der Index. Umgekehrt
        # scheitert der Rückbau unter MariaDB mit
        # `Cannot drop index 'ix_zone_override_absence_id': needed in a foreign key
        # constraint` -- InnoDB braucht für jeden Fremdschlüssel einen Index und
        # lässt den letzten passenden nicht fallen, solange die Bedingung steht.
        # Unter SQLite fiel das nicht auf: `batch_alter_table` baut die Tabelle dort
        # ohnehin neu und kennt das Problem nicht. Genau dafür läuft die Suite gegen
        # beide Datenbanken.
        batch_op.drop_constraint(
            batch_op.f("fk_zone_override_absence_id_absence"), type_="foreignkey"
        )
        batch_op.drop_index(batch_op.f("ix_zone_override_absence_id"))
        batch_op.drop_column("absence_id")
    op.drop_table("absence")
