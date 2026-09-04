"""Die letzten zwei deutschen Spaltennamen

`user_passkey.bezeichnung` und `passkey_challenge.zeremonie` waren die einzigen deutschen
Namen in einem sonst durchgehend englischen Schema. Sie sind bei der Umstellung des Codes
stehengeblieben, weil eine Spalte umzubenennen eine Migration braucht und der damalige
Commit keine haben sollte.

`bezeichnung` wird zu `label` -- wie in jeder Nachschlagetabelle des Projekts, wo dasselbe
Wort dieselbe Sache benennt. `zeremonie` wird zu `ceremony`; sie bindet eine Challenge an
ihren Zweck, und ohne diese Bindung liesse sich eine zur Anmeldung ausgegebene Challenge
für eine Registrierung einreichen.

`batch_alter_table` und nicht `alter_column` direkt: Unter SQLite baut Alembic die Tabelle
damit neu und nimmt Indizes und Fremdschlüssel mit. Ein nacktes RENAME COLUMN kann SQLite
zwar auch, aber die beiden Tabellen tragen `UNIQUE`- und Fremdschlüsselbedingungen, und
der Umweg über den Stapel ist der Weg, den dieses Projekt an den anderen zwei Stellen
schon geht.

Revision ID: f2c6d90a41b8
Revises: e4b8a21c7f10
Create Date: 2026-08-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f2c6d90a41b8"
down_revision: str | Sequence[str] | None = "e4b8a21c7f10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (Tabelle, alter Name, neuer Name, Typ)
SPALTEN = [
    ("user_passkey", "bezeichnung", "label", sa.String(length=120)),
    ("passkey_challenge", "zeremonie", "ceremony", sa.String(length=16)),
]


def _umbenennen(paare: list[tuple[str, str, str, sa.types.TypeEngine[str]]]) -> None:
    for tabelle, alt, neu, typ in paare:
        with op.batch_alter_table(tabelle, schema=None) as batch_op:
            batch_op.alter_column(alt, new_column_name=neu, existing_type=typ,
                                  existing_nullable=False)


def upgrade() -> None:
    _umbenennen(SPALTEN)


def downgrade() -> None:
    _umbenennen([(t, neu, alt, typ) for t, alt, neu, typ in SPALTEN])
