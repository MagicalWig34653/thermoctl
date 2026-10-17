# ruff: noqa: E501
"""Vorlagen für die Übersicht neuer Benutzer: Vorlage, Abschnitte, Kacheln.

Spiegel der persönlichen Tabellen (kein JSON); eine Vorlage wird beim Anlegen eines
Benutzers in dessen Layout kopiert.

Revision ID: e7a3c15b9d28
Revises: d4b8f2a60c19
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e7a3c15b9d28'
down_revision: str | Sequence[str] | None = 'd4b8f2a60c19'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('dashboard_template',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_dashboard_template')),
    sa.UniqueConstraint('name', name=op.f('uq_dashboard_template_name'))
    )
    op.create_table('dashboard_template_section',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('template_id', sa.Integer(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=80), nullable=False),
    sa.CheckConstraint('position >= 0', name=op.f('ck_dashboard_template_section_position_nichtnegativ')),
    sa.ForeignKeyConstraint(['template_id'], ['dashboard_template.id'], name=op.f('fk_dashboard_template_section_template_id_dashboard_template'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_dashboard_template_section')),
    sa.UniqueConstraint('template_id', 'position', name='uq_dashboard_template_section_pos')
    )
    with op.batch_alter_table('dashboard_template_section', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_dashboard_template_section_template_id'), ['template_id'], unique=False)

    op.create_table('dashboard_template_tile',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('section_id', sa.Integer(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('zone_id', sa.Integer(), nullable=True),
    sa.Column('title', sa.String(length=80), nullable=False),
    sa.Column('period_days', sa.Integer(), nullable=False),
    sa.Column('width', sa.Integer(), nullable=False),
    sa.Column('metric', sa.String(length=16), nullable=True),
    sa.Column('destination', sa.String(length=16), nullable=True),
    sa.Column('note', sa.Text(), nullable=False),
    sa.CheckConstraint("destination IS NULL OR destination IN ('schedule', 'absence', 'history', 'devices', 'mapping', 'solar', 'operation', 'account')", name=op.f('ck_dashboard_template_tile_destination_bekannt')),
    sa.CheckConstraint("kind IN ('room', 'chart', 'metric', 'agenda', 'schedule', 'heating', 'devices', 'system', 'note', 'shortcut', 'control')", name=op.f('ck_dashboard_template_tile_kind_bekannt')),
    sa.CheckConstraint("metric IS NULL OR metric IN ('rooms', 'heating', 'issues', 'outdoor')", name=op.f('ck_dashboard_template_tile_metric_bekannt')),
    sa.CheckConstraint('period_days IN (1, 7, 30, 90)', name=op.f('ck_dashboard_template_tile_period_bekannt')),
    sa.CheckConstraint('position >= 0', name=op.f('ck_dashboard_template_tile_position_nichtnegativ')),
    sa.CheckConstraint('width IN (3, 4, 6, 8, 12)', name=op.f('ck_dashboard_template_tile_width_bekannt')),
    sa.ForeignKeyConstraint(['section_id'], ['dashboard_template_section.id'], name=op.f('fk_dashboard_template_tile_section_id_dashboard_template_section'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['zone_id'], ['zone.id'], name=op.f('fk_dashboard_template_tile_zone_id_zone'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_dashboard_template_tile')),
    sa.UniqueConstraint('section_id', 'position', name='uq_dashboard_template_tile_pos')
    )
    with op.batch_alter_table('dashboard_template_tile', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_dashboard_template_tile_section_id'), ['section_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_dashboard_template_tile_zone_id'), ['zone_id'], unique=False)



def downgrade() -> None:
    # Die Indizes gehören den Fremdschlüsseln und gehen mit den Tabellen; sie einzeln zu
    # löschen scheitert auf MariaDB (Fehler 1553).
    op.drop_table("dashboard_template_tile")
    op.drop_table("dashboard_template_section")
    op.drop_table("dashboard_template")
