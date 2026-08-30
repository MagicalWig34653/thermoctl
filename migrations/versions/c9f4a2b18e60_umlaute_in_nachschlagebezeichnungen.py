"""Umlaute in den Nachschlagebezeichnungen

Vier Bezeichnungen standen seit den Nachschlagetabellen transliteriert da --
"Verbindungsqualitaet", "Beleuchtungsstaerke", "Bediengeraet", "Weboberflaeche". Sie sind
keine Codes, sondern die Woerter, die in der Oberflaeche stehen: auf jeder Geraetekarte,
in der Rollenspalte der Zuordnung und in jeder Zeile des Audit-Protokolls. Aufgefallen ist
es beim Ansehen der Geraeteseite, nicht in einem Test -- Tests lesen Codes.

Die Codes bleiben unberuehrt. Geaendert wird nur, was noch den alten Wortlaut traegt:
Wer eine Bezeichnung von Hand angepasst hat, behaelt seine.

Revision ID: c9f4a2b18e60
Revises: c8e21a5f4d70
Create Date: 2026-08-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c9f4a2b18e60"
down_revision: str | Sequence[str] | None = "c8e21a5f4d70"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (Tabelle, Code, alte Bezeichnung, neue Bezeichnung)
BEZEICHNUNGEN = [
    ("device_capability", "illuminance", "Beleuchtungsstaerke", "Beleuchtungsstärke"),
    ("device_capability", "link_quality", "Verbindungsqualitaet", "Verbindungsqualität"),
    ("device_role", "controller", "Bediengeraet", "Bediengerät"),
    ("actor_source", "web", "Weboberflaeche", "Weboberfläche"),
]


def _umbenennen(paare: list[tuple[str, str, str, str]]) -> None:
    verbindung = op.get_bind()
    for tabelle, code, alt, neu in paare:
        # `label = :alt` als Bedingung, nicht nur der Code: Eine von Hand geaenderte
        # Bezeichnung soll diese Revision nicht ueberschreiben -- und die Rueckwaerts-
        # richtung soll nur zuruecknehmen, was sie selbst gesetzt hat.
        verbindung.execute(
            sa.text(
                f"UPDATE {tabelle} SET label = :neu "  # noqa: S608 - Tabellennamen aus
                "WHERE code = :code AND label = :alt"  # der Konstante oben, nicht aus Eingabe
            ),
            {"neu": neu, "code": code, "alt": alt},
        )


def upgrade() -> None:
    _umbenennen(BEZEICHNUNGEN)


def downgrade() -> None:
    _umbenennen([(t, c, neu, alt) for t, c, alt, neu in BEZEICHNUNGEN])
