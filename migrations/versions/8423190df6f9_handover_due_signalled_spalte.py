"""handover_due_signalled_spalte

Revision ID: 8423190df6f9
Revises: a0110b03c001
Create Date: 2026-09-29 05:38:48.316665

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "8423190df6f9"
down_revision: str | Sequence[str] | None = "a0110b03c001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("zone_sensor_failure_state", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "handover_due_signalled",
                sa.Boolean(),
                server_default=sa.text("0"),
                nullable=False,
            )
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("zone_sensor_failure_state", schema=None) as batch_op:
        batch_op.drop_column("handover_due_signalled")
