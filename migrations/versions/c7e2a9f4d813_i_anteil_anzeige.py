"""I-Anteil anzeigen: Kachelart `controller`, `show_pi` je Kachel, Index für den letzten Reset.

Reine Anzeige: keine Spalte und kein Wert der Regelung ändert sich. Der Index deckt die
Frage "wann wurde der I-Anteil eines Raums zuletzt zurückgesetzt" aus dem Schattenprotokoll.

Revision ID: c7e2a9f4d813
Revises: b5d1f8a3c264
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c7e2a9f4d813"
down_revision: str | Sequence[str] | None = "b5d1f8a3c264"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("dashboard_tile", "dashboard_template_tile")
_INDEX = "ix_shadow_decision_zone_integrator_decided"
_OLD = (
    "room",
    "chart",
    "metric",
    "agenda",
    "schedule",
    "heating",
    "devices",
    "system",
    "note",
    "shortcut",
    "control",
)


def _in(values: tuple[str, ...]) -> str:
    return "kind IN (" + ", ".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    for table in _TABLES:
        name = op.f(f"ck_{table}_kind_bekannt")
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.add_column(
                sa.Column("show_pi", sa.Boolean(), server_default=sa.false(), nullable=False)
            )
            batch_op.drop_constraint(name, type_="check")
            batch_op.create_check_constraint(name, _in((*_OLD, "controller")))
    op.create_index(
        _INDEX, "shadow_decision", ["zone_id", "pi_integrator_action", "decided_at"]
    )


def downgrade() -> None:
    op.drop_index(_INDEX, table_name="shadow_decision")
    for table in _TABLES:
        name = op.f(f"ck_{table}_kind_bekannt")
        # Kacheln der neuen Art gibt es in der alten Fassung nicht: vorher entfernen.
        op.execute(sa.text(f"DELETE FROM {table} WHERE kind = 'controller'"))  # noqa: S608
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_constraint(name, type_="check")
            batch_op.create_check_constraint(name, _in(_OLD))
            batch_op.drop_column("show_pi")
