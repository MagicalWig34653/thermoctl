"""Schaltprotokoll: was wirklich an ein Geraet hinausging

Bislang gibt es zwei Protokolle, und beide sind etwas anderes: `shadow_decision` hält
fest, was die Regelung entschieden hätte oder hat -- eine Entscheidung ist keine
Wirkung. `audit_event` hält fest, was ein Mensch getan hat. Was fehlte, ist das
Dritte: was wirklich an ein Geraet hinausgegangen ist, wann, mit welchem Ergebnis.

`command_outcome` ist eine gewöhnliche Nachschlagetabelle (kein ENUM, Grundsatz 3).
`device_command` hängt an Zone und Geraet -- anders als `shadow_decision` bewusst mit
`ondelete="SET NULL"` statt CASCADE, und mit einer Namens-Momentaufnahme
(`zone_name`, `device_name`), damit der Eintrag auch nach dem Umbenennen oder Löschen
der Zone oder des Geraets noch sagt, was passiert ist. Begründet im Docstring von
`DeviceCommand`.

Revision ID: 3a3e44c560fb
Revises: b7e4c2a91f30
Create Date: 2026-09-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3a3e44c560fb"
down_revision: str | Sequence[str] | None = "b7e4c2a91f30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Ausgeschrieben statt importiert, aus demselben Grund wie bei jeder Nachschlagetabelle
# nach der allerersten Migration: Eine Migration beschreibt, was zu EINEM Zeitpunkt
# geschah, und darf sich nicht rückwirkend ändern, wenn die Konstante im Modell
# später wächst.
COMMAND_OUTCOMES = [
    ("executed", "Ausgeführt"),
    ("suppressed", "Unterdrückt (Trockenlauf)"),
    ("failed", "Gescheitert"),
]


def _nachschlage_tabelle(name: str) -> sa.TableClause:
    return sa.table(name, sa.column("code", sa.String), sa.column("label", sa.String))


def upgrade() -> None:
    op.create_table(
        "command_outcome",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("label", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_command_outcome")),
        sa.UniqueConstraint("code", name=op.f("uq_command_outcome_code")),
    )
    op.bulk_insert(
        _nachschlage_tabelle("command_outcome"),
        [{"code": code, "label": label} for code, label in COMMAND_OUTCOMES],
    )

    op.create_table(
        "device_command",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sent_at", sa.DateTime(), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("zone_id", sa.Integer(), nullable=True),
        sa.Column("zone_name", sa.String(length=128), nullable=False),
        sa.Column("device_id", sa.Integer(), nullable=True),
        sa.Column("device_name", sa.String(length=128), nullable=False),
        sa.Column("command", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("outcome_id", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["source_id"], ["actor_source.id"],
            name=op.f("fk_device_command_source_id_actor_source"),
        ),
        sa.ForeignKeyConstraint(
            ["zone_id"], ["zone.id"], ondelete="SET NULL",
            name=op.f("fk_device_command_zone_id_zone"),
        ),
        sa.ForeignKeyConstraint(
            ["device_id"], ["device.id"], ondelete="SET NULL",
            name=op.f("fk_device_command_device_id_device"),
        ),
        sa.ForeignKeyConstraint(
            ["outcome_id"], ["command_outcome.id"],
            name=op.f("fk_device_command_outcome_id_command_outcome"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_device_command")),
    )
    op.create_index(op.f("ix_device_command_sent_at"), "device_command", ["sent_at"])


def downgrade() -> None:
    # Der Index wird bewusst nicht einzeln entfernt -- beide Datenbanken räumen ihn
    # mit der Tabelle mit ab (siehe derselbe Kommentar in 8b2d6e8a7f10).
    op.drop_table("device_command")
    op.drop_table("command_outcome")
