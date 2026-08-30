"""Faehigkeiten fuer Thermostatventile (z.B. WT-A03E)

Revision ID: b6e9f14d2a83
Revises: f2c6d90a41b8
Create Date: 2026-08-30

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "b6e9f14d2a83"
down_revision: str | Sequence[str] | None = "f2c6d90a41b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Written out here rather than imported from the model, same reasoning as in
# 8b2d6e8a7f10: a migration describes what happened at ONE point in time, and
# pulling from a constant that keeps growing would retroactively change what this
# step did.
#
# `thermostat` lets a Zigbee2MQTT thermostatic radiator valve (WT-A03E and similar)
# qualify for the actuator role even though it has no on/off `state` -- it is driven
# through `system_mode` and `occupied_heating_setpoint` instead. `running_state` and
# `window_open` are the device's own readable status fields, recorded like any other
# measurement.
NEW_CAPABILITIES = [
    ("thermostat", "Thermostatventil"),
    ("running_state", "Heizbetrieb"),
    ("window_open", "Fenster erkannt offen"),
]


def _lookup_table(name: str) -> sa.TableClause:
    return sa.table(name, sa.column("code", sa.String), sa.column("label", sa.String))


def upgrade() -> None:
    op.bulk_insert(
        _lookup_table("device_capability"),
        [{"code": code, "label": label} for code, label in NEW_CAPABILITIES],
    )


def downgrade() -> None:
    codes = [code for code, _label in NEW_CAPABILITIES]
    capabilities = _lookup_table("device_capability")
    op.execute(sa.delete(capabilities).where(capabilities.c.code.in_(codes)))
