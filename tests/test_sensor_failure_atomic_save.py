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
from thermoctl.auth.csrf import CSRF_HEADER, csrf_token
from thermoctl.auth.sessions import COOKIE_NAME
from thermoctl.auth.tokens import issue_token
from thermoctl.config import get_settings
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


def _csrf(client: TestClient) -> dict[str, str]:
    http_session = client.cookies.get(COOKIE_NAME)
    assert http_session is not None
    return {CSRF_HEADER: csrf_token(http_session, get_settings().secret_key.get_secret_value())}


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


def _form(**overrides: object) -> dict[str, object]:
    form: dict[str, object] = {
        "fixed_on_seconds": "900",
        "fixed_off_seconds": "1500",
        "recovery_seconds": "60",
        "recovery_samples": "2",
        "warm_restart_hysteresis_k": "1",
        "emergency_setpoint_c": "16",
        "curve_outdoor_c": ["-10", "15"],
        "curve_on_seconds": ["1200", "0"],
        "curve_off_seconds": ["600", "1800"],
    }
    form.update(overrides)
    return form


def test_an_invalid_emergency_setpoint_leaves_the_profile_untouched(
    client_als: ClientBuilder, session: Session
) -> None:
    _seed(session)
    before, setpoint_before = _stored(session)
    client = client_als(PERMISSIONS)

    response = client.post(
        "/settings/sensor-failure",
        data=_form(emergency_setpoint_c="31"),
        headers=_csrf(client),
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert "emergency_setpoint_c" in response.text

    after, setpoint_after = _stored(session)
    assert after.values == before.values
    assert after.version == before.version
    assert setpoint_after == setpoint_before


@pytest.mark.parametrize(
    ("outdoor", "on", "off"),
    [
        (["-10", "15", "5"], ["1200", "0", "600"], ["600", "1800", ""]),
        (["-10", "15", "5"], ["1200", "0", ""], ["600", "1800", "900"]),
        (["-10", "15", ""], ["1200", "0", "600"], ["600", "1800", "900"]),
    ],
)
def test_an_incomplete_curve_row_rejects_the_whole_input(
    client_als: ClientBuilder,
    session: Session,
    outdoor: list[str],
    on: list[str],
    off: list[str],
) -> None:
    _seed(session)
    before, setpoint_before = _stored(session)
    client = client_als(PERMISSIONS)

    response = client.post(
        "/settings/sensor-failure",
        data=_form(curve_outdoor_c=outdoor, curve_on_seconds=on, curve_off_seconds=off),
        headers=_csrf(client),
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert "unvollständig" in response.text

    after, setpoint_after = _stored(session)
    assert after.values == before.values
    assert after.version == before.version
    assert setpoint_after == setpoint_before


def test_fully_blank_curve_rows_are_still_ignored(
    client_als: ClientBuilder, session: Session
) -> None:
    _seed(session)
    client = client_als(PERMISSIONS)

    response = client.post(
        "/settings/sensor-failure",
        data=_form(
            curve_outdoor_c=["-10", "", "15"],
            curve_on_seconds=["1200", "", "0"],
            curve_off_seconds=["600", "", "1800"],
        ),
        headers=_csrf(client),
        follow_redirects=False,
    )
    assert response.status_code == 303
    after, _ = _stored(session)
    assert after.values.fixed_on_seconds == 900
    assert [p.outdoor_c for p in after.values.curve_points] == [Decimal("-10"), Decimal("15")]


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
