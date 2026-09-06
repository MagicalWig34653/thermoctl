"""Urlaubsbetrieb

Fügt den anlagenweiten Urlaubsbetrieb an, siehe `domain/schedule.py`:

* Tabelle `vacation` -- ein Absenkwert für die ganze Anlage über ein festes
  Zeitfenster, `starts_at`/`ends_at` **beide** verpflichtend (im Unterschied zu
  `zone_override`, wo ein offenes Ende zulässig ist). Zeilen werden nie gelöscht;
  ein vorzeitiges Ende setzt nur `cancelled_at`.
* Recht `vacation.manage` -- eigenständig statt über `setting.manage`
  mitzulaufen, siehe die Begründung in `db/models/lookup.py`. Zugeteilt an alle
  Gruppen, die bereits `setting.manage` halten, aus demselben Grund wie beim
  Vorbild `a1c7e5b93d20_recht_zum_scharfschalten.py`: die globale Verwaltung sitzt
  in einer bestehenden Anlage schon dort.

Revision ID: 4bfefd4c10a4
Revises: afb9832fba99
Create Date: 2026-09-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4bfefd4c10a4"
down_revision: str | Sequence[str] | None = "afb9832fba99"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CODE = "vacation.manage"
BESCHREIBUNG = "Urlaubsbetrieb ansetzen und vorzeitig beenden"


def upgrade() -> None:
    op.create_table(
        "vacation",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("starts_at", sa.DateTime(), nullable=False),
        sa.Column("ends_at", sa.DateTime(), nullable=False),
        sa.Column("setback_temperature_c", sa.Numeric(4, 1), nullable=False),
        sa.Column("cancelled_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column(
            "created_by_user_id",
            sa.Integer(),
            sa.ForeignKey("user.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_by_token_id",
            sa.Integer(),
            sa.ForeignKey("api_token.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "source_id", sa.Integer(), sa.ForeignKey("actor_source.id"), nullable=False
        ),
        sa.CheckConstraint("ends_at > starts_at", name="urlaub_ende_nach_beginn"),
    )

    verbindung = op.get_bind()
    vorhanden = verbindung.execute(
        sa.text("SELECT id FROM permission WHERE code = :code"), {"code": CODE}
    ).scalar()
    if vorhanden is None:
        verbindung.execute(
            sa.text(
                "INSERT INTO permission (code, description, is_zone_scoped) "
                "VALUES (:code, :beschreibung, :zonenbezogen)"
            ),
            {"code": CODE, "beschreibung": BESCHREIBUNG, "zonenbezogen": False},
        )
        vorhanden = verbindung.execute(
            sa.text("SELECT id FROM permission WHERE code = :code"), {"code": CODE}
        ).scalar()

    # Nur anlagenweit (zone_id IS NULL) und nur dort, wo die Zuteilung nicht schon steht:
    # die Revision muss sich wiederholen lassen, ohne doppelte Zeilen zu hinterlassen.
    verbindung.execute(
        sa.text(
            "INSERT INTO group_permission (access_group_id, permission_id, zone_id) "
            "SELECT gp.access_group_id, :neu, NULL "
            "FROM group_permission gp "
            "JOIN permission p ON p.id = gp.permission_id "
            "WHERE p.code = 'setting.manage' AND gp.zone_id IS NULL "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM group_permission vorhanden "
            "  WHERE vorhanden.access_group_id = gp.access_group_id "
            "    AND vorhanden.permission_id = :neu AND vorhanden.zone_id IS NULL"
            ")"
        ),
        {"neu": vorhanden},
    )


def downgrade() -> None:
    verbindung = op.get_bind()
    verbindung.execute(
        sa.text(
            "DELETE FROM group_permission WHERE permission_id IN "
            "(SELECT id FROM permission WHERE code = :code)"
        ),
        {"code": CODE},
    )
    verbindung.execute(
        sa.text("DELETE FROM permission WHERE code = :code"), {"code": CODE}
    )
    op.drop_table("vacation")
