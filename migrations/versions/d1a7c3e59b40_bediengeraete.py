"""Bediengeraete: Tastendruecke aufzeichnen und belegen

Ein Bediengeraet an der Wand -- etwa ein Aqara W100 -- schickt seine Tastendruecke als
Feld `action` in der Zustandsnachricht. Bis hierher fiel das Feld durch: Es stand nicht in
`FELD_ZU_FAEHIGKEIT`, und die Rolle `controller` war eine Zuordnung ohne Wirkung.

Drei Dinge kommen dazu:

* die Faehigkeit `action`, damit jeder Tastendruck wie jeder andere Messwert abgelegt wird;
* die Nachschlagetabelle `controller_command` mit dem, was eine Taste ausloesen kann;
* `controller_binding`, die Belegung je Geraet und Aktion.

**Warum die Belegung in der Datenbank steht und nicht im Quelltext:** Wie ein Geraet seine
Tasten benennt, entscheidet Zigbee2MQTT je Modell -- der eine schickt `single_plus`, der
naechste `button_1_single`. Eine Tabelle im Code waere harte Verdrahtung (Grundsatz 1) und
fuer jedes Geraet falsch, das noch nicht darin steht. Stattdessen zeichnet der Dienst auf,
welche Aktionen ein Geraet tatsaechlich geschickt hat, und die Oberflaeche laesst sie
zuordnen.

Revision ID: d1a7c3e59b40
Revises: c9f4a2b18e60
Create Date: 2026-08-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from thermoctl.db.models.lookup import CONTROLLER_COMMANDS

revision: str = "d1a7c3e59b40"
down_revision: str | Sequence[str] | None = "c9f4a2b18e60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

AKTIONSFAEHIGKEIT = ("action", "Tastendruck")


def upgrade() -> None:
    op.create_table(
        "controller_command",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(length=32), nullable=False, unique=True),
        sa.Column("label", sa.String(length=64), nullable=False),
    )
    op.create_table(
        "controller_binding",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "device_id",
            sa.Integer(),
            sa.ForeignKey("device.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("action_code", sa.String(length=64), nullable=False),
        sa.Column(
            "command_id",
            sa.Integer(),
            sa.ForeignKey("controller_command.id"),
            nullable=False,
        ),
        sa.Column("step_k", sa.Numeric(precision=3, scale=1), nullable=True),
        sa.UniqueConstraint("device_id", "action_code", name="aktion_je_geraet"),
    )

    verbindung = op.get_bind()
    for code, bezeichnung in CONTROLLER_COMMANDS:
        verbindung.execute(
            sa.text("INSERT INTO controller_command (code, label) VALUES (:code, :label)"),
            {"code": code, "label": bezeichnung},
        )
    # Die Faehigkeit nur anlegen, wenn sie fehlt: Eine frisch eingerichtete Anlage
    # bekommt sie schon beim Fuellen, weil die Seed-Revision die Konstanten aus dem
    # Code liest.
    code, bezeichnung = AKTIONSFAEHIGKEIT
    vorhanden = verbindung.execute(
        sa.text("SELECT id FROM device_capability WHERE code = :code"), {"code": code}
    ).scalar()
    if vorhanden is None:
        verbindung.execute(
            sa.text("INSERT INTO device_capability (code, label) VALUES (:code, :label)"),
            {"code": code, "label": bezeichnung},
        )


def downgrade() -> None:
    verbindung = op.get_bind()
    # Erst die Messwerte, dann die Faehigkeit: sonst haelt der Fremdschluessel dagegen.
    verbindung.execute(
        sa.text(
            "DELETE FROM measurement WHERE capability_id IN "
            "(SELECT id FROM device_capability WHERE code = :code)"
        ),
        {"code": AKTIONSFAEHIGKEIT[0]},
    )
    verbindung.execute(
        sa.text(
            "DELETE FROM device_capability_link WHERE capability_id IN "
            "(SELECT id FROM device_capability WHERE code = :code)"
        ),
        {"code": AKTIONSFAEHIGKEIT[0]},
    )
    verbindung.execute(
        sa.text("DELETE FROM device_capability WHERE code = :code"),
        {"code": AKTIONSFAEHIGKEIT[0]},
    )
    op.drop_table("controller_binding")
    op.drop_table("controller_command")
