"""The control page -- operating state, arming, global defaults.

Arming is the only operation in the project that immediately moves a valve.
The tests here therefore check not only that it works, but also that it
**does not** work without the dedicated permission -- and that the way back
into dry run fails on nothing.
"""

import re
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
    save_settings,
    save_solar_location,
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


def test_operating_pages_describe_dry_run_truthfully(
    client_als: ClientBuilder, session: Session
) -> None:
    create_settings(session)
    client = client_als(ALL_PERMISSIONS)

    for path in ("/", "/control"):
        page = client.get(path)
        assert page.status_code == 200
        assert "Es werden keine Sollwerte an Ventile gesendet." in page.text
        assert "Ein/Aus-Entscheidungen erreichen keinen Aktor." in page.text
        assert "Sollwertausgabe freigegeben" not in page.text
        assert "Tatsächlich <em>geschaltet</em> wird" not in page.text


def test_operating_pages_describe_armed_before_restart_truthfully(
    client_als: ClientBuilder, session: Session
) -> None:
    row = create_settings(session)
    row.control_armed = True
    session.flush()
    client = client_als(ALL_PERMISSIONS)

    for path in ("/", "/control"):
        page = client.get(path)
        assert page.status_code == 200
        assert "Scharf, Neustart fehlt" in page.text
        assert "Der beim Start gebaute MQTT-Riegel ist noch zu." in page.text
        assert "Sollwertausgabe freigegeben" not in page.text


def test_operating_pages_describe_armed_after_restart_truthfully(
    client_als: ClientBuilder, session: Session
) -> None:
    row = create_settings(session)
    row.control_armed = True
    session.flush()
    client = client_als(ALL_PERMISSIONS)
    client.app.state.sending_allowed = True

    for path in ("/", "/control"):
        page = client.get(path)
        assert page.status_code == 200
        assert "Scharf und neu gestartet" in page.text
        assert "Sollwertausgabe freigegeben" in page.text
        assert "Sollwerte können an selbstregelnde Thermostatventile gesendet werden." in page.text
        assert "Ein/Aus-Entscheidungen erreichen weiterhin keinen Aktor." in page.text
        assert "Jede Entscheidung unten geht an die Ventile." not in page.text


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
    assert "Der beim Start gebaute MQTT-Riegel ist noch zu." in page.text


def test_the_hint_does_not_appear_in_dry_run(
    client_als: ClientBuilder, session: Session
) -> None:
    """Counter-check: without it, the test above would also be satisfied by
    a version that always shows the hint -- and then every page would carry
    a warning that nobody reads any more."""
    create_settings(session)
    page = client_als(ALL_PERMISSIONS).get("/control")
    assert "Der beim Start gebaute MQTT-Riegel ist noch zu." not in page.text


def test_an_empty_timezone_is_refused_with_its_field(session: Session) -> None:
    """The timezone has no sensible default -- an empty one would silently shift every
    schedule, because schedules are stored in local time."""
    create_settings(session)
    with pytest.raises(ControlError) as fehler:
        save_settings(session, {}, "   ", user_id=None)
    assert fehler.value.field == "timezone"


@pytest.mark.parametrize("eingabe", ["keine Zahl", "12,5,7", ""])
def test_a_coordinate_that_is_no_number_names_its_field(
    session: Session, eingabe: str
) -> None:
    """The coordinate arrives as text from a form, so anything can be in it.

    An empty one is the exception and means "no location" -- checked separately in the
    REST tests. Everything else has to come back naming the field, so the page can mark
    it, instead of raising a bare `InvalidOperation`.
    """
    create_settings(session)
    if eingabe == "":
        save_solar_location(
            session, enabled=False, latitude_text="", longitude_text="", user_id=None
        )
        return
    with pytest.raises(ControlError) as fehler:
        save_solar_location(
            session, enabled=True, latitude_text=eingabe, longitude_text="0", user_id=None
        )
    assert fehler.value.field == "solar_forecast_latitude"


def test_the_solar_setback_can_be_switched_on_through_its_own_form(
    angemeldeter_client: TestClient, session: Session
) -> None:
    """Abgeschickt wird, was die Seite wirklich rendert — Name **und** Wert.

    Der Schalter trug einmal keinen `value`, dann schickte der Browser `"on"`, und die
    View verglich damit. Seit das Makro `value="yes"` setzt, traf der Vergleich nie
    mehr zu: Die Sonnenabsenkung liess sich nicht mehr einschalten, ohne dass irgendwo
    ein Fehler erschien. Aus dem Betrieb gemeldet.

    Ein Test, der den Wert selbst hinschreibt, haette das nicht gesehen — er stimmt
    immer mit der Haelfte ueberein, gegen die er geschrieben wurde.
    """
    create_settings(session)
    source(session, "web")
    session.flush()

    page = angemeldeter_client.get("/settings")
    assert page.status_code == 200
    formular = re.search(
        r'<form[^>]*>(?:(?!</form>).)*name="solar_forecast_enabled".*?</form>',
        page.text,
        re.S,
    )
    assert formular is not None, "Kein Formular mit dem Sonnenschalter gefunden"
    rumpf = formular.group(0)

    daten = dict(re.findall(r'name="([^"]+)"[^>]*value="([^"]*)"', rumpf))
    schalter = re.search(
        r'name="solar_forecast_enabled"[^>]*value="([^"]*)"', rumpf
    ) or re.search(r'value="([^"]*)"[^>]*name="solar_forecast_enabled"', rumpf)
    assert schalter is not None, "Der Schalter rendert keinen Wert"
    daten["solar_forecast_enabled"] = schalter.group(1)
    daten["solar_forecast_latitude"] = "52.520"
    daten["solar_forecast_longitude"] = "13.405"

    antwort = angemeldeter_client.post(
        "/settings", data=daten, headers=_csrf(angemeldeter_client), follow_redirects=False
    )
    assert antwort.status_code in (200, 303), antwort.text[:400]

    row = session.get(Setting, 1)
    assert row is not None
    assert row.solar_forecast_enabled is True
    assert row.solar_forecast_latitude == Decimal("52.520")
