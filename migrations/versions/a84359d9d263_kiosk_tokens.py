"""Kiosk tokens

Marks a subset of `api_token` rows as kiosk tokens: tokens meant to sit in a wall
tablet's bookmark and cookie instead of an `Authorization` header. They are otherwise
ordinary `ApiToken` rows -- same hash, same `ApiTokenPermission` scoping, same
`principal_for_token()`. The dashboard's narrow permission set (`zone.read`, and where
allowed `setpoint.write`/`override.create`, scoped to the assigned zones) comes for
free from the existing permission model; nothing new was needed there.

The flag exists only to keep the two admin pages apart: `/tokens` lists a user's own
developer tokens (a single, plant-wide permission code), `/kiosk-tokens` lists tokens
meant for a tablet on the wall (a name, a set of zones, and view-only vs. control).
Without it, a kiosk token issued by an admin would show up mixed into that admin's own
`/tokens` list -- both live in the same table and have the same owner.

Revision ID: a84359d9d263
Revises: f2c6d90a41b8
Create Date: 2026-08-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a84359d9d263"
down_revision: str | Sequence[str] | None = "f2c6d90a41b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("api_token") as batch_op:
        batch_op.add_column(
            sa.Column("is_kiosk", sa.Boolean(), server_default=sa.false(), nullable=False)
        )


def downgrade() -> None:
    with op.batch_alter_table("api_token") as batch_op:
        batch_op.drop_column("is_kiosk")
