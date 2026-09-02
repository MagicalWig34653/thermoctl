"""Index fuer zonenweise Abfragen der Schattenhistorie.

Revision ID: e8c21f4a9d70
Revises: 3a3e44c560fb
Create Date: 2026-09-02
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import inspect

revision: str = "e8c21f4a9d70"
down_revision: str | Sequence[str] | None = "3a3e44c560fb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_shadow_decision_zone_decided_id",
        "shadow_decision",
        ["zone_id", "decided_at", "id"],
    )
    op.drop_index(
        "ix_shadow_decision_decided_at",
        table_name="shadow_decision",
    )
    # A MariaDB downgrade of this migration has to create this helper index before
    # removing the composite one, because the foreign key starts with `zone_id`.
    # On the next upgrade the composite index takes over again, so the helper can go.
    if op.get_bind().dialect.name in {"mysql", "mariadb"}:
        existing = {
            index["name"]
            for index in inspect(op.get_bind()).get_indexes("shadow_decision")
        }
        if "ix_shadow_decision_zone_id_fk" in existing:
            op.drop_index(
                "ix_shadow_decision_zone_id_fk",
                table_name="shadow_decision",
            )


def downgrade() -> None:
    op.create_index(
        "ix_shadow_decision_decided_at",
        "shadow_decision",
        ["decided_at"],
    )
    # MariaDB discarded its implicit foreign-key index when the wider index above
    # became able to enforce the same constraint. Restore that prerequisite before
    # removing our index; SQLite does not need or create it.
    if op.get_bind().dialect.name in {"mysql", "mariadb"}:
        existing = {
            index["name"]
            for index in inspect(op.get_bind()).get_indexes("shadow_decision")
        }
        if "ix_shadow_decision_zone_id_fk" not in existing:
            op.create_index(
                "ix_shadow_decision_zone_id_fk",
                "shadow_decision",
                ["zone_id"],
            )
    op.drop_index(
        "ix_shadow_decision_zone_decided_id",
        table_name="shadow_decision",
    )
