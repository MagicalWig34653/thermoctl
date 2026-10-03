"""notbetrieb_rueckstellung

Projektinhaber-Entscheidung (nach Auftrag 7b, siehe `docs/STATUS.md`): die
Notbetrieb-Übergabe (`operating_mode="manual"`) wird bei Rückkehr zu `normal`
genau einmal auf den vor der Übergabe tatsächlich vom Gerät gemeldeten Wert
zurückgeschrieben -- "exakt der Zustand wie davor", nicht ein erfundener
Vorgabewert. `handover_previous_operating_mode` hält diesen Vorwert fest
(`NULL` heißt ausdrücklich "unbekannt", nicht "pause" oder irgendein anderer
geratener Wert); `restore_attempted_at`/`restore_result` spiegeln
`handover_attempted_at`/`handover_result` für den Rückweg -- derselbe
Einmal-Versand-ohne-Wiederholung-Vertrag, nur in die andere Richtung.

`action='restore'` ergänzt die bestehende Vorrangtabellen-Wertemenge in
`actuator_decision` um genau diesen neuen, scharfen Entscheidungstyp.

Revision ID: b2e6f1a9c374
Revises: 9d3f1a7c2b84
Create Date: 2026-10-02 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b2e6f1a9c374"
down_revision: str | Sequence[str] | None = "9d3f1a7c2b84"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("actuator_emergency_state", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("handover_previous_operating_mode", sa.String(length=64), nullable=True)
        )
        batch_op.add_column(sa.Column("restore_attempted_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("restore_result", sa.String(length=64), nullable=True))

    with op.batch_alter_table("actuator_decision", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("ck_actuator_decision_action"), type_="check")
        batch_op.create_check_constraint(
            batch_op.f("ck_actuator_decision_action"),
            "action IN ('normal', 'no_write', 'switch_on', 'switch_off', 'handover', 'restore')",
        )


def downgrade() -> None:
    """Downgrade schema."""
    # Der alte Check-Constraint kennt `restore` nicht; mit vorhandenen Zeilen scheitert
    # sein Wiederherstellen. Zeilen zu loeschen waere der groessere Verlust (Protokoll,
    # `device_command.actuator_decision_id` zeigt darauf), daher werden sie auf den
    # zulaessigen Wert `no_write` umgeschrieben; `reason_code`/`reason` behalten den
    # tatsaechlichen Hergang.
    entscheidung = sa.table("actuator_decision", sa.column("action", sa.String))
    op.execute(
        entscheidung.update().where(entscheidung.c.action == "restore").values(action="no_write")
    )

    with op.batch_alter_table("actuator_decision", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("ck_actuator_decision_action"), type_="check")
        batch_op.create_check_constraint(
            batch_op.f("ck_actuator_decision_action"),
            "action IN ('normal', 'no_write', 'switch_on', 'switch_off', 'handover')",
        )

    with op.batch_alter_table("actuator_emergency_state", schema=None) as batch_op:
        batch_op.drop_column("restore_result")
        batch_op.drop_column("restore_attempted_at")
        batch_op.drop_column("handover_previous_operating_mode")
