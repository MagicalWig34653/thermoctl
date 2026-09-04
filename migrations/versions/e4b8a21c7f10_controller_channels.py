# ruff: noqa: E501
"""Geraetemerkmale und Kanäle für Bediengeräte

Revision ID: e4b8a21c7f10
Revises: d1a7c3e59b40
Create Date: 2026-08-30
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from thermoctl.db.models.lookup import CHANNEL_KINDS

revision: str = "e4b8a21c7f10"
down_revision: str | Sequence[str] | None = "d1a7c3e59b40"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("channel_kind", sa.Column("id", sa.Integer(), primary_key=True),
                    sa.Column("code", sa.String(32), nullable=False, unique=True),
                    sa.Column("label", sa.String(64), nullable=False))
    op.create_table("device_property", sa.Column("id", sa.Integer(), primary_key=True),
                    sa.Column("device_id", sa.Integer(), sa.ForeignKey("device.id", ondelete="CASCADE"), nullable=False),
                    sa.Column("name", sa.String(191), nullable=False),
                    sa.Column("value_type", sa.String(16), nullable=False),
                    sa.Column("unit", sa.String(32)), sa.Column("min_value", sa.Numeric(12, 4)),
                    sa.Column("max_value", sa.Numeric(12, 4)),
                    sa.Column("is_readable", sa.Boolean(), nullable=False),
                    sa.Column("is_writable", sa.Boolean(), nullable=False),
                    sa.Column("last_value_text", sa.String(191)),
                    sa.Column("last_value_number", sa.Numeric(12, 4)),
                    sa.Column("last_value_at", sa.DateTime()),
                    sa.UniqueConstraint("device_id", "name", name="merkmal_je_geraet"))
    op.create_table("device_property_value", sa.Column("id", sa.Integer(), primary_key=True),
                    sa.Column("property_id", sa.Integer(), sa.ForeignKey("device_property.id", ondelete="CASCADE"), nullable=False),
                    sa.Column("value", sa.String(191), nullable=False),
                    sa.Column("sort_order", sa.Integer(), nullable=False),
                    sa.UniqueConstraint("property_id", "value", name="wert_je_merkmal"))
    op.create_table("controller_channel", sa.Column("id", sa.Integer(), primary_key=True),
                    sa.Column("device_id", sa.Integer(), sa.ForeignKey("device.id", ondelete="CASCADE"), nullable=False),
                    sa.Column("property_name", sa.String(191), nullable=False),
                    sa.Column("direction", sa.String(8), nullable=False),
                    sa.Column("kind_id", sa.Integer(), sa.ForeignKey("channel_kind.id"), nullable=False),
                    sa.Column("zone_id", sa.Integer(), sa.ForeignKey("zone.id", ondelete="CASCADE")),
                    sa.Column("source_device_id", sa.Integer(), sa.ForeignKey("device.id", ondelete="SET NULL")),
                    sa.Column("fixed_text", sa.String(191)), sa.Column("fixed_number", sa.Numeric(12, 4)),
                    sa.UniqueConstraint("device_id", "property_name", name="kanal_je_merkmal"))
    bind = op.get_bind()
    for code, label in CHANNEL_KINDS:
        bind.execute(sa.text("INSERT INTO channel_kind (code, label) VALUES (:code, :label)"), {"code": code, "label": label})


def downgrade() -> None:
    op.drop_table("controller_channel")
    op.drop_table("device_property_value")
    op.drop_table("device_property")
    op.drop_table("channel_kind")
