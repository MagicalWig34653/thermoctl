"""Rechtebeschreibungen mit Umlauten

Die Beschreibung eines Rechts ist sichtbarer Text: Sie steht auf der Gruppenseite neben
jedem Kaestchen. Dort stand bis hierher die ASCII-Umschrift, die im Quelltext dieses
Projekts fuer Kommentare gilt -- "Sollwerte je Modus aendern". In der Oberflaeche liest
sich das wie ein Tippfehler.

Revision ID: c8e21a5f4d70
Revises: a1c7e5b93d20
Create Date: 2026-08-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c8e21a5f4d70"
down_revision: str | Sequence[str] | None = "a1c7e5b93d20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Absichtlich als eigene Tabelle und nicht aus PERMISSIONS importiert: Eine Revision
# soll denselben Stand einspielen, egal wie die Liste im Modell spaeter aussieht.
NEU: dict[str, str] = {
    "zone.manage": "Zonen anlegen, ändern, löschen",
    "setpoint.write": "Sollwerte je Modus ändern",
    "schedule.manage": "Zeitpläne ändern",
    "override.create": "Übersteuern",
    "override.cancel": "Fremde Übersteuerung aufheben",
    "device.read": "Geräte und Zuordnungen sehen",
    "device.manage": "Geräte zuordnen, tauschen, entfernen",
    "mode.manage": "Sollwert-Modi anlegen und ändern",
    "setting.manage": "Globale Einstellungen ändern",
}

ALT: dict[str, str] = {
    "zone.manage": "Zonen anlegen, aendern, loeschen",
    "setpoint.write": "Sollwerte je Modus aendern",
    "schedule.manage": "Zeitplaene aendern",
    "override.create": "Uebersteuern",
    "override.cancel": "Fremde Uebersteuerung aufheben",
    "device.read": "Geraete und Zuordnungen sehen",
    "device.manage": "Geraete zuordnen, tauschen, entfernen",
    "mode.manage": "Sollwert-Modi anlegen und aendern",
    "setting.manage": "Globale Einstellungen aendern",
}


def _schreiben(texte: dict[str, str]) -> None:
    verbindung = op.get_bind()
    for code, beschreibung in texte.items():
        verbindung.execute(
            sa.text("UPDATE permission SET description = :text WHERE code = :code"),
            {"text": beschreibung, "code": code},
        )


def upgrade() -> None:
    _schreiben(NEU)


def downgrade() -> None:
    _schreiben(ALT)
