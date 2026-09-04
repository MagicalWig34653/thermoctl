"""Kiosk als eigene Aktionsquelle

Eine Sollwert-Aenderung oder ein Boost, ausgelöst über `/kiosk/...`, braucht einen
Eintrag in `actor_source` -- `create_override()` und `update_setpoints()` lehnen jede
Quelle ab, die dort nicht steht (siehe `ValueError` in
`thermoctl/domain/schedule.py`). Weder "web" noch "api" passt: "web" heißt eine
angemeldete Sitzung, die ein Kiosk-Token nicht hat, und "api" heißt ein Skript mit
einem `Authorization`-Header, nicht ein Knopf auf einem Wandtablett. Das Audit-Protokoll
soll den Unterschied zeigen, nicht verwischen.

Revision ID: d07073d9abdf
Revises: a84359d9d263
Create Date: 2026-08-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d07073d9abdf"
down_revision: str | Sequence[str] | None = "a84359d9d263"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Ausgeschrieben statt aus dem Modell importiert -- siehe die Begründung in
# 8b2d6e8a7f10_schema_schattenbetrieb.py: eine Migration beschreibt einen Zeitpunkt,
# nicht eine Konstante, die später weiterwächst.
_NEUE_QUELLE = ("kiosk", "Kiosk-Dashboard")


def _actor_source() -> sa.TableClause:
    return sa.table("actor_source", sa.column("code", sa.String), sa.column("label", sa.String))


def upgrade() -> None:
    op.bulk_insert(_actor_source(), [{"code": _NEUE_QUELLE[0], "label": _NEUE_QUELLE[1]}])


def downgrade() -> None:
    # One `sa.table()` instance, reused for both the statement and the `.where()`
    # column -- calling `_actor_source()` a second time here builds a second,
    # distinct table clause with the same name, and SQLAlchemy then compiles the
    # delete as a multi-table statement referencing "two" tables, which SQLite (and
    # not only SQLite) refuses with "This backend does not support multiple-table
    # criteria within DELETE".
    tabelle = _actor_source()
    op.execute(tabelle.delete().where(tabelle.c.code == _NEUE_QUELLE[0]))
