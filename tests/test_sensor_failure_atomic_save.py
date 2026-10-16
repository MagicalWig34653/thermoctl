"""Nichts wird teilweise gespeichert: Notbetriebsprofil, Notsollwert und Kennlinie
der Regelvorgaben-Seite (und der gleichwertigen MCP-/REST-Wege) gelten als Ganzes.

Zwei Befunde aus dem Review von Auftrag 8b: (A) gültige Taktzeiten + ungültiger
Notsollwert schrieb das Profil trotzdem; (B) unvollständige Kennlinienzeilen wurden
stillschweigend verworfen, der Rest gespeichert.
"""

from collections.abc import Callable
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.helpers import create_settings, source, user_with_permissions
from thermoctl.auth.tokens import issue_token
from thermoctl.db.models.operations import Setting
from thermoctl.domain.sensor_failure_policy import (
    CurvePoint,
    PolicyError,
    Profile,
    ProfileValues,
    migration_default_profile_id,
    read_profile,
    save_profile,
)
from thermoctl.mcp import server

ClientBuilder = Callable[[list[tuple[str, int | None]]], TestClient]

PERMISSIONS: list[tuple[str, int | None]] = [
    ("zone.read", None),
    ("setting.manage", None),
    ("control.arm", None),
]
SEED_CURVE = (
    CurvePoint(Decimal("-10"), 1200, 600),
    CurvePoint(Decimal("15"), 0, 1800),
)




def _seed(session: Session) -> None:
    create_settings(session)
    source(session, "web")
    save_profile(
        session,
        ProfileValues("Notbetrieb Vorgabe", 600, 1200, 60, 2, Decimal("1"), SEED_CURVE),
        profile_id=migration_default_profile_id(session),
    )
    session.flush()


def _stored(session: Session) -> tuple[Profile, Decimal | None]:
    # Kein rollback(): der Test-Client committet und rollt nicht zurück (die
    # Fixture reicht eine Session ohne Anfrage-Transaktion durch), also zählt
    # hier, was die Domäne tatsächlich geschrieben hat.
    session.expire_all()
    row = session.get(Setting, 1)
    assert row is not None
    profile_id = row.sensor_failure_default_profile_id or migration_default_profile_id(session)
    return read_profile(session, profile_id), row.sensor_failure_default_emergency_setpoint_c










def test_mcp_with_an_invalid_setpoint_changes_neither_profile_nor_setpoint(
    session: Session,
) -> None:
    _seed(session)
    user = user_with_permissions(
        session, "atomar-mcp", [("setting.manage", None), ("zone.read", None)]
    )
    _token, plaintext = issue_token(
        session, user, "atomar", [("setting.manage", None), ("zone.read", None)], None
    )
    before = server.read_sensor_failure_defaults_tool(session, plaintext)

    with pytest.raises(PolicyError):
        server.set_sensor_failure_defaults_tool(
            session,
            plaintext,
            fixed_on_seconds=900,
            fixed_off_seconds=1500,
            recovery_seconds=60,
            recovery_samples=2,
            warm_restart_hysteresis_k=Decimal("1"),
            emergency_setpoint_c=Decimal("31"),
            curve_points=[],
        )
    # Der MCP-Weg hat keine umgebende Anfrage, die zurückrollt: die Domäne muss
    # vor dem ersten Schreiben prüfen.
    assert server.read_sensor_failure_defaults_tool(session, plaintext) == before
