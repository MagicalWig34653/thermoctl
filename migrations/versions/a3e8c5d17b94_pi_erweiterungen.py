"""PI-Erweiterungen: Abbau bei Überschuss, Integraltrennung, Vorsteuerung, Kp bis 3,00.

Fünf neue Zonenspalten (alle NOT NULL, Vorgabe = bisheriges Verhalten: Faktor 1,00, keine
Integraltrennung, Vorsteuerung aus), die Kp-Obergrenze von 0,50 auf 3,00 und die neue
Spalte `shadow_decision.pi_feedforward_duty`.

Die Kp-Schrittprüfung wird dabei ersetzt: Die alte `(pi_gain_per_k * 100) % 5 = 0`
lehnt unter SQLite gültige Werte wie 1,15 ab (Gleitkomma: 114,99999...). Sie war für den
Bereich bis 0,50 nie betroffen.

Das Zurückstufen begrenzt Kp über 0,50 auf 0,50 (sonst verletzte die alte Obergrenze
bestehende Zeilen), nimmt die Spalten wieder heraus und stellt die alte Prüfung her.

Revision ID: a3e8c5d17b94
Revises: f7c2a91d4e08
Create Date: 2026-11-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a3e8c5d17b94"
down_revision: str | Sequence[str] | None = "f7c2a91d4e08"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_GAIN_CHECK = "pi_gain_per_k BETWEEN 0.05 AND 0.50 AND (pi_gain_per_k * 100) % 5 = 0"
NEW_GAIN_CHECK = (
    "pi_gain_per_k BETWEEN 0.05 AND 3.00 "
    "AND ABS(pi_gain_per_k * 20 - ROUND(pi_gain_per_k * 20)) < 0.000001"
)
GAIN_CHECK_NAME = "ck_zone_pi_gain_per_k_bereich_und_schritt"

NEW_CHECKS = (
    (
        "ck_zone_pi_surplus_decay_bereich_und_schritt",
        "pi_surplus_decay_factor BETWEEN 0 AND 1 "
        "AND ABS(pi_surplus_decay_factor * 20 - ROUND(pi_surplus_decay_factor * 20)) "
        "< 0.000001",
    ),
    (
        "ck_zone_pi_integral_separation_bereich_und_schritt",
        "pi_integral_separation_k BETWEEN 0 AND 2 "
        "AND ABS(pi_integral_separation_k * 10 - ROUND(pi_integral_separation_k * 10)) "
        "< 0.000001",
    ),
    (
        "ck_zone_pi_feedforward_gain_bereich_und_schritt",
        "pi_feedforward_gain BETWEEN 0.005 AND 0.050 "
        "AND ABS(pi_feedforward_gain * 1000 - ROUND(pi_feedforward_gain * 1000)) "
        "< 0.000001",
    ),
    (
        "ck_zone_pi_feedforward_smoothing_bereich",
        "pi_feedforward_smoothing_hours BETWEEN 1 AND 12",
    ),
)


def upgrade() -> None:
    with op.batch_alter_table("zone") as batch_op:
        batch_op.add_column(
            sa.Column(
                "pi_surplus_decay_factor",
                sa.Numeric(3, 2),
                server_default=sa.text("1.00"),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "pi_integral_separation_k",
                sa.Numeric(4, 2),
                server_default=sa.text("0.00"),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "pi_feedforward_enabled",
                sa.Boolean(),
                server_default=sa.false(),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "pi_feedforward_gain",
                sa.Numeric(5, 4),
                server_default=sa.text("0.0200"),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "pi_feedforward_smoothing_hours",
                sa.Integer(),
                server_default=sa.text("3"),
                nullable=False,
            )
        )
        batch_op.drop_constraint(op.f(GAIN_CHECK_NAME), type_="check")
        batch_op.create_check_constraint(op.f(GAIN_CHECK_NAME), NEW_GAIN_CHECK)
        for name, condition in NEW_CHECKS:
            batch_op.create_check_constraint(op.f(name), condition)

    with op.batch_alter_table("shadow_decision") as batch_op:
        batch_op.add_column(sa.Column("pi_feedforward_duty", sa.Numeric(12, 6), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("shadow_decision") as batch_op:
        batch_op.drop_column("pi_feedforward_duty")

    # Die alte Obergrenze würde Zeilen mit Kp über 0,50 verletzen: vorher begrenzen.
    zone = sa.table("zone", sa.column("pi_gain_per_k", sa.Numeric(3, 2)))
    op.execute(
        zone.update().where(zone.c.pi_gain_per_k > 0.50).values({"pi_gain_per_k": 0.50})
    )
    with op.batch_alter_table("zone") as batch_op:
        for name, _condition in reversed(NEW_CHECKS):
            batch_op.drop_constraint(op.f(name), type_="check")
        batch_op.drop_constraint(op.f(GAIN_CHECK_NAME), type_="check")
        batch_op.create_check_constraint(op.f(GAIN_CHECK_NAME), OLD_GAIN_CHECK)
        batch_op.drop_column("pi_feedforward_smoothing_hours")
        batch_op.drop_column("pi_feedforward_gain")
        batch_op.drop_column("pi_feedforward_enabled")
        batch_op.drop_column("pi_integral_separation_k")
        batch_op.drop_column("pi_surplus_decay_factor")
