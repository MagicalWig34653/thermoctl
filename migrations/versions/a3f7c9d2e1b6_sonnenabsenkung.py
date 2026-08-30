"""Sonnenabsenkung: Zonenfaktor und Prognose-Einstellungen

Fuegt hinzu:

* `zone.solar_gain_factor` (0..1) -- wie stark eine Zone von Sonneneinstrahlung
  profitiert. Voreinstellung 0, also aus wie das gesamte Merkmal.
* `zone.solar_setback_max_k` -- die zonenspezifische Obergrenze der Absenkung in
  Kelvin, nullable wie die uebrigen sechs Regelparameter der Zone: leer heisst
  globaler Standard.
* `setting.solar_forecast_enabled`, `.solar_forecast_latitude`,
  `.solar_forecast_longitude`, `.solar_setback_lookahead_hours`,
  `.default_solar_setback_max_k` -- ob die Funktion an ist, der Standort fuer den
  Open-Meteo-Abruf, das Vorschau-Zeitfenster und der anlagenweite Standardwert der
  Obergrenze. Ohne Standort (Breite/Laenge NULL) bleibt die Funktion faktisch aus,
  selbst wenn `solar_forecast_enabled` gesetzt ist -- es gibt keinen sinnvollen
  Vorgabewert fuer einen Standort (CLAUDE.md Grundsatz 1).

Revision ID: a3f7c9d2e1b6
Revises: f2c6d90a41b8
Create Date: 2026-08-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a3f7c9d2e1b6"
down_revision: str | Sequence[str] | None = "f2c6d90a41b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("zone") as batch_op:
        batch_op.add_column(
            sa.Column(
                "solar_gain_factor",
                sa.Numeric(3, 2),
                server_default=sa.text("0"),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column("solar_setback_max_k", sa.Numeric(3, 1), nullable=True)
        )
        batch_op.create_check_constraint(
            op.f("ck_zone_solar_gain_faktor_0_bis_1"),
            "solar_gain_factor BETWEEN 0 AND 1",
        )
    with op.batch_alter_table("setting") as batch_op:
        batch_op.add_column(
            sa.Column(
                "solar_forecast_enabled",
                sa.Boolean(),
                server_default=sa.false(),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column("solar_forecast_latitude", sa.Numeric(6, 3), nullable=True)
        )
        batch_op.add_column(
            sa.Column("solar_forecast_longitude", sa.Numeric(6, 3), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "solar_setback_lookahead_hours",
                sa.Integer(),
                server_default=sa.text("3"),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "default_solar_setback_max_k",
                sa.Numeric(3, 1),
                server_default=sa.text("2.0"),
                nullable=False,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("setting") as batch_op:
        batch_op.drop_column("default_solar_setback_max_k")
        batch_op.drop_column("solar_setback_lookahead_hours")
        batch_op.drop_column("solar_forecast_longitude")
        batch_op.drop_column("solar_forecast_latitude")
        batch_op.drop_column("solar_forecast_enabled")
    with op.batch_alter_table("zone") as batch_op:
        batch_op.drop_constraint(op.f("ck_zone_solar_gain_faktor_0_bis_1"), type_="check")
        batch_op.drop_column("solar_setback_max_k")
        batch_op.drop_column("solar_gain_factor")
