"""notbetrieb_versand

Auftrag 7b of `lokal/plaene/0.11.0-notbetrieb.md`: the publisher now carries
out the scharfe Takt-/Handover-Entscheidung, in its own transaction, next to
(but independent of) the shadow run's `simulated_*` fields on the same
`actuator_emergency_state` row.

`armed_episode_id` is deliberately a *second* episode marker, not a reuse of
the existing `episode_id` column: that column is written on every shadow
cycle for a `sensor_failure_enabled` zone, regardless of whether the plant is
armed, so it can never answer "which episode do the *scharfe* fields on this
row currently belong to" -- shadow_run would silently overwrite the answer.
`armed_episode_id` is touched only by `services/publishing.py`.

`decided_no_command` is a new `command_outcome` -- the Schaltprotokoll
"Entscheidung ohne Befehlsversand" plan Auftrag 7b item 4 asks for: a
thermostat-actuator assignment this version has no confirmed device contract
for (no writable `operating_mode`/`occupied_heating_setpoint`) is written
here exactly once per armed episode, distinct from `suppressed` (a dry-run
command that *was* computed and withheld) and from `failed` (an attempt that
did not succeed) -- this assignment was never going to be attempted at all.

Revision ID: 9d3f1a7c2b84
Revises: 05f7842e4d69
Create Date: 2026-10-01 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9d3f1a7c2b84"
down_revision: str | Sequence[str] | None = "05f7842e4d69"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Ausgeschrieben statt aus dem Modell importiert -- eine Migration beschreibt
# einen Zeitpunkt, nicht eine Konstante, die später weiterwächst (siehe
# `d07073d9abdf_kiosk_actor_source.py`).
_NEUER_OUTCOME = ("decided_no_command", "Entscheidung ohne Befehlsversand")


def _command_outcome() -> sa.TableClause:
    return sa.table("command_outcome", sa.column("code", sa.String), sa.column("label", sa.String))


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("actuator_emergency_state", schema=None) as batch_op:
        batch_op.add_column(sa.Column("armed_episode_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            batch_op.f("fk_actuator_emergency_state_armed_episode_id_sensor_failure_episode"),
            "sensor_failure_episode",
            ["armed_episode_id"],
            ["id"],
            ondelete="SET NULL",
        )

    op.bulk_insert(
        _command_outcome(), [{"code": _NEUER_OUTCOME[0], "label": _NEUER_OUTCOME[1]}]
    )


def downgrade() -> None:
    """Downgrade schema."""
    tabelle = _command_outcome()
    op.execute(tabelle.delete().where(tabelle.c.code == _NEUER_OUTCOME[0]))

    with op.batch_alter_table("actuator_emergency_state", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("fk_actuator_emergency_state_armed_episode_id_sensor_failure_episode"),
            type_="foreignkey",
        )
        batch_op.drop_column("armed_episode_id")
