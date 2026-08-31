"""Selbstregelnde Thermostatventile

Ein Thermostatventil kann auf zwei Arten betrieben werden. Bisher gab es nur die
eine: thermoctl entscheidet an/aus und fährt das Ventil. Neu ist die zweite --
das Ventil regelt selbst, und thermoctl schreibt ihm nur den Sollwert (und, wo
das Gerät es annimmt, die anderswo gemessene Raumtemperatur).

Die Angabe hängt an der Zuordnung, nicht am Gerät: Sie beschreibt, wie diese Zone
dieses Ventil fährt. `server_default` false, damit bestehende Zuordnungen bleiben,
was sie sind -- eine Umstellung ist eine Entscheidung, keine Nebenwirkung einer
Migration.

Revision ID: 7943b9d915c6
Revises: d07073d9abdf
Create Date: 2026-08-31 16:03:52.246942

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7943b9d915c6'
down_revision: str | Sequence[str] | None = 'd07073d9abdf'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("zone_device", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "self_regulating",
                sa.Boolean(),
                server_default=sa.false(),
                nullable=False,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("zone_device", schema=None) as batch_op:
        batch_op.drop_column("self_regulating")
