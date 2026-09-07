"""Problemmeldung aus einer Wohnung

Fügt an:

* Recht `report.create` (zonenbezogen) -- ein Mieter meldet aus seinem Raum heraus
  ein Problem, das über den bereits konfigurierten Webhook hinausgeht.
* `setting.notify_tenant_reports` (Vorgabe an) -- der sechste Meldungsschalter,
  neben den fünf Störungsarten. Wer solche Meldungen nicht bekommen will, stellt sie
  ab, ohne dafür den Webhook oder die Störungsmeldungen abzuräumen.

**Das Recht bekommt keine bestehende Gruppe automatisch.** Anders als bei
`control.arm`, das an alle Gruppen mit `setting.manage` ging: eine Meldung nach außen
auszulösen ist etwas, das jemand ausdrücklich vergeben soll. Eine bestehende Gruppe
mit `zone.read` würde sonst über Nacht die Möglichkeit dazubekommen, den Webhook des
Betreibers zu bedienen -- genau die Verwechslung, gegen die das eigene Recht steht.

Revision ID: d31f6a04c7e9
Revises: c724de89a13f
Create Date: 2026-09-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d31f6a04c7e9"
down_revision: str | Sequence[str] | None = "c724de89a13f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CODE = "report.create"
BESCHREIBUNG = "Ein Problem aus einem Raum melden"


def upgrade() -> None:
    with op.batch_alter_table("setting") as batch_op:
        batch_op.add_column(
            sa.Column(
                "notify_tenant_reports",
                sa.Boolean(),
                server_default=sa.true(),
                nullable=False,
            )
        )
    connection = op.get_bind()
    # Wiederholbar: eine Revision, die zweimal liefe, darf keine zweite Zeile
    # hinterlassen -- dieselbe Vorsicht wie in a1c7e5b93d20.
    existing = connection.execute(
        sa.text("SELECT id FROM permission WHERE code = :code"), {"code": CODE}
    ).scalar()
    if existing is None:
        connection.execute(
            sa.text(
                "INSERT INTO permission (code, description, is_zone_scoped) "
                "VALUES (:code, :beschreibung, :zonenbezogen)"
            ),
            {"code": CODE, "beschreibung": BESCHREIBUNG, "zonenbezogen": True},
        )


def downgrade() -> None:
    connection = op.get_bind()
    # Erst die Zuteilungen, dann das Recht: umgekehrt hält der Fremdschlüssel dagegen.
    connection.execute(
        sa.text(
            "DELETE FROM group_permission WHERE permission_id IN "
            "(SELECT id FROM permission WHERE code = :code)"
        ),
        {"code": CODE},
    )
    connection.execute(
        sa.text(
            "DELETE FROM api_token_permission WHERE permission_id IN "
            "(SELECT id FROM permission WHERE code = :code)"
        ),
        {"code": CODE},
    )
    connection.execute(
        sa.text("DELETE FROM permission WHERE code = :code"), {"code": CODE}
    )
    with op.batch_alter_table("setting") as batch_op:
        batch_op.drop_column("notify_tenant_reports")
