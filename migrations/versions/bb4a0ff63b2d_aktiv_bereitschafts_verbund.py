"""Aktiv-Bereitschafts-Verbund

Legt `cluster_claim` an -- die eine Zeile, um die zwei Instanzen konkurrieren, die
dieselbe Datenbank und denselben MQTT-Broker teilen (`services/cluster.py`). Seed-Zeile
`id=1`, `holder_id=''`, `expires_at` weit in der Vergangenheit -- unbeansprucht, sodass
die erste Instanz, die je danach fragt, sie sofort bekommt.

Fügt `setting.cluster_takeover_cycles` hinzu (Vorgabe 5, einstellbar): wie viele
Regelzyklen die aktive Instanz ihre Erneuerung verpassen darf, bevor eine Bereitschaft
übernimmt. Multipliziert mit `shadow_interval_seconds` ergibt sich der tatsächliche
Zeitraum (`services/cluster.py::takeover_timeout_seconds`).

Revision ID: bb4a0ff63b2d
Revises: 67e794059830
Create Date: 2026-09-06
"""

from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op

revision: str = "bb4a0ff63b2d"
down_revision: str | Sequence[str] | None = "67e794059830"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Far enough in the past that "unclaimed" holds regardless of which timezone a
# database connection reports its own clock in -- see `services/cluster.py`'s
# `_UNCLAIMED`, which this must stay equal to.
_UNCLAIMED = datetime(1970, 1, 1)

_cluster_claim = sa.table(
    "cluster_claim",
    sa.column("id", sa.Integer()),
    sa.column("holder_id", sa.String(length=64)),
    sa.column("expires_at", sa.DateTime()),
)


def upgrade() -> None:
    op.create_table(
        "cluster_claim",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("holder_id", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("id = 1", name=op.f("ck_cluster_claim_genau_eine_zeile")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cluster_claim")),
    )
    op.bulk_insert(
        _cluster_claim,
        [{"id": 1, "holder_id": "", "expires_at": _UNCLAIMED}],
    )
    with op.batch_alter_table("setting") as batch_op:
        batch_op.add_column(
            sa.Column(
                "cluster_takeover_cycles",
                sa.Integer(),
                server_default=sa.text("5"),
                nullable=False,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("setting") as batch_op:
        batch_op.drop_column("cluster_takeover_cycles")
    op.drop_table("cluster_claim")
