"""The control page -- operating state, arming, global defaults.

Arming is the only operation in the project that immediately moves a valve.
The tests here therefore check not only that it works, but also that it
**does not** work without the dedicated permission -- and that the way back
into dry run fails on nothing.
"""

from collections.abc import Callable
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.helpers import create_settings, create_zone, source
from thermoctl.auth.csrf import CSRF_HEADER, csrf_token
from thermoctl.auth.sessions import COOKIE_NAME
from thermoctl.config import get_settings
from thermoctl.db.models.operations import AuditEvent, Setting
from thermoctl.domain.control import (
    LIMITS,
    ControlError,
    arm,
    check_number,
)

ClientBuilder = Callable[[list[tuple[str, int | None]]], TestClient]

ALL_PERMISSIONS: list[tuple[str, int | None]] = [
    ("zone.read", None),
    ("setting.manage", None),
    ("control.arm", None),
]


def _csrf(client: TestClient) -> dict[str, str]:
    http_session = client.cookies.get(COOKIE_NAME)
    assert http_session is not None
    return {CSRF_HEADER: csrf_token(http_session, get_settings().secret_key.get_secret_value())}


def _defaults(**overrides: str) -> dict[str, str]:
    values = {field: str(LIMITS[field][0]) for field in LIMITS}
    values["timezone"] = "Europe/Berlin"
    values.update(overrides)
    return values


# --- Domain ---------------------------------------------------------------


def test_checking_a_number_accepts_the_comma() -> None:
    """On a German keyboard you type 0,5 -- that is not a mistake."""
    assert check_number("default_hysteresis_k", "0,5") == Decimal("0.5")


@pytest.mark.parametrize(
    ("field", "input_value"),
    [
        ("default_min_on_seconds", "0"),
        ("default_hysteresis_k", "0"),
        ("shadow_interval_seconds", "0"),
        ("default_min_on_seconds", "99999"),
        ("default_hysteresis_k", "keine Zahl"),
        ("default_min_on_seconds", "60,5"),
        ("polling_interval_seconds", ""),
    ],
)
def test_unusable_defaults_are_rejected(field: str, input_value: str) -> None:
    """Zero seconds minimum on-time and zero Kelvin hysteresis are exactly the
    legacy system's defect: cycling at the setpoint on every tick."""
    with pytest.raises(ControlError) as errors:
        check_number(field, input_value)
    assert errors.value.field == field


def test_arming_requires_a_justification(session: Session) -> None:
    create_settings(session)
    source(session, "web")
    with pytest.raises(ControlError):
        arm(session, True, reason="   ", user_id=None)
    assert session.get(Setting, 1).control_armed is False


def test_going_back_to_dry_run_requires_no_justification(session: Session) -> None:
    """The way back is the one someone takes in a hurry. It must not fail on
    any formality."""
    create_settings(session)
    source(session, "web")
    arm(session, True, reason="Schattenlauf geprüft", user_id=None)
    assert arm(session, False, reason="", user_id=None) is True
    assert session.get(Setting, 1).control_armed is False


def test_the_same_thing_twice_does_not_write_a_second_entry(session: Session) -> None:
    """Otherwise the audit log would show an arming that never actually happened."""
    create_settings(session)
    source(session, "web")
    assert arm(session, True, reason="erste", user_id=None) is True
    assert arm(session, True, reason="zweite", user_id=None) is False
    entries = list(
        session.scalars(select(AuditEvent).where(AuditEvent.action == "arm"))
    )
    assert len(entries) == 1
    assert entries[0].detail == "erste"


# --- Interface -----------------------------------------------------------


def test_the_page_shows_the_dry_run(client_als: ClientBuilder, session: Session) -> None:
    create_settings(session)
    create_zone(session, "bad")
    response = client_als(ALL_PERMISSIONS).get("/control")
    assert response.status_code == 200
    assert "Trockenlauf" in response.text


def test_arming_through_the_interface(
    client_als: ClientBuilder, session: Session
) -> None:
    create_settings(session)
    source(session, "web")
    client = client_als(ALL_PERMISSIONS)
    response = client.post(
        "/control/arm",
        data={"armed": "yes", "reason": "Vier Tage Schattenlauf verglichen"},
        headers=_csrf(client),
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert session.get(Setting, 1).control_armed is True
    entry = session.scalars(select(AuditEvent).where(AuditEvent.action == "arm")).one()
    assert entry.detail == "Vier Tage Schattenlauf verglichen"


def test_without_control_arm_the_installation_stays_in_dry_run(
    client_als: ClientBuilder, session: Session
) -> None:
    """`setting.manage` alone is not enough. Someone allowed to maintain
    timezone and retention period should not be able to arm the heating on
    the side."""
    create_settings(session)
    client = client_als([("zone.read", None), ("setting.manage", None)])
    response = client.post(
        "/control/arm",
        data={"armed": "yes", "reason": "trotzdem"},
        headers=_csrf(client),
        follow_redirects=False,
    )
    assert response.status_code == 403
    assert session.get(Setting, 1).control_armed is False


def test_a_missing_justification_returns_to_the_form(
    client_als: ClientBuilder, session: Session
) -> None:
    create_settings(session)
    client = client_als(ALL_PERMISSIONS)
    response = client.post(
        "/control/arm", data={"armed": "yes", "reason": ""},
        headers=_csrf(client),
    )
    assert response.status_code == 200
    assert "Bitte kurz festhalten" in response.text
    assert session.get(Setting, 1).control_armed is False


def test_saving_defaults(client_als: ClientBuilder, session: Session) -> None:
    create_settings(session)
    source(session, "web")
    client = client_als(ALL_PERMISSIONS)
    response = client.post(
        "/settings",
        data=_defaults(default_hysteresis_k="0,4", shadow_interval_seconds="90"),
        headers=_csrf(client),
        follow_redirects=False,
    )
    assert response.status_code == 303
    row = session.get(Setting, 1)
    assert row.default_hysteresis_k == Decimal("0.4")
    assert row.shadow_interval_seconds == 90


def test_a_rejected_default_leaves_nothing_half_written(
    client_als: ClientBuilder, session: Session
) -> None:
    """The bug that once left setup half-created: writing before everything is checked."""
    create_settings(session)
    before = session.get(Setting, 1).shadow_interval_seconds
    client = client_als(ALL_PERMISSIONS)
    response = client.post(
        "/settings",
        data=_defaults(shadow_interval_seconds="90", default_min_on_seconds="0"),
        headers=_csrf(client),
    )
    assert response.status_code == 200
    session.expire_all()
    assert session.get(Setting, 1).shadow_interval_seconds == before


def test_without_setting_manage_read_only(client_als: ClientBuilder, session: Session) -> None:
    create_settings(session)
    read_only = client_als([("zone.read", None)])
    assert read_only.get("/control").status_code == 200
    assert read_only.get("/settings").status_code == 200
    assert (
        read_only.post(
            "/settings", data=_defaults(), headers=_csrf(read_only)
        ).status_code
        == 403
    )


def test_the_operations_page_names_the_second_bolt(
    client_als: ClientBuilder, session: Session
) -> None:
    """Armed but nothing sent: anyone unaware of this state searches for
    hours in the wrong place."""
    create_settings(session)
    source(session, "web")
    arm(session, True, reason="Test", user_id=None)

    page = client_als(ALL_PERMISSIONS).get("/control")
    assert page.status_code == 200
    assert "noch nichts geschaltet" in page.text


def test_the_hint_does_not_appear_in_dry_run(
    client_als: ClientBuilder, session: Session
) -> None:
    """Counter-check: without it, the test above would also be satisfied by
    a version that always shows the hint -- and then every page would carry
    a warning that nobody reads any more."""
    create_settings(session)
    page = client_als(ALL_PERMISSIONS).get("/control")
    assert "noch nichts geschaltet" not in page.text
