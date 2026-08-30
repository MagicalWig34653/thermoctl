"""The `/settings` page's solar section: on/off switch and location.

Sits next to `test_control.py`, which covers the rest of `/settings`. Location is
checked separately from the bounded numeric defaults (`domain.control.LIMITS`):
empty is a valid, meaningful value here (CLAUDE.md principle 1 -- no default
location), which the generic `check_number` path does not allow for any other field.
"""

from collections.abc import Callable
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.helpers import create_settings, source
from thermoctl.auth.csrf import CSRF_HEADER, csrf_token
from thermoctl.auth.sessions import COOKIE_NAME
from thermoctl.config import get_settings
from thermoctl.db.models.operations import Setting
from thermoctl.domain.control import LIMITS

ClientBuilder = Callable[[list[tuple[str, int | None]]], TestClient]

ALL_PERMISSIONS: list[tuple[str, int | None]] = [
    ("zone.read", None),
    ("setting.manage", None),
]


def _csrf(client: TestClient) -> dict[str, str]:
    http_session = client.cookies.get(COOKIE_NAME)
    assert http_session is not None
    return {CSRF_HEADER: csrf_token(http_session, get_settings().secret_key.get_secret_value())}


def _form(**overrides: str) -> dict[str, str]:
    values = {field: str(LIMITS[field][0]) for field in LIMITS}
    values["timezone"] = "Europe/Berlin"
    values["solar_forecast_latitude"] = ""
    values["solar_forecast_longitude"] = ""
    values.update(overrides)
    return values


def test_the_page_shows_the_off_state_by_default(
    client_als: ClientBuilder, session: Session
) -> None:
    create_settings(session)
    response = client_als(ALL_PERMISSIONS).get("/settings")
    assert response.status_code == 200
    assert 'name="solar_forecast_enabled"' in response.text


def test_enabling_with_a_location_is_saved(
    client_als: ClientBuilder, session: Session
) -> None:
    create_settings(session)
    source(session, "web")
    client = client_als(ALL_PERMISSIONS)

    response = client.post(
        "/settings",
        data=_form(
            solar_forecast_enabled="on",
            solar_forecast_latitude="52.520",
            solar_forecast_longitude="13.405",
        ),
        headers=_csrf(client),
        follow_redirects=False,
    )

    assert response.status_code == 303
    row = session.get(Setting, 1)
    assert row.solar_forecast_enabled is True
    assert row.solar_forecast_latitude == Decimal("52.520")
    assert row.solar_forecast_longitude == Decimal("13.405")


def test_leaving_the_location_empty_disables_it_regardless_of_the_switch(
    client_als: ClientBuilder, session: Session
) -> None:
    """CLAUDE.md principle 1: there is no sensible default location -- the switch
    alone must not be enough, and saving must not invent one."""
    create_settings(session)
    source(session, "web")
    client = client_als(ALL_PERMISSIONS)

    response = client.post(
        "/settings",
        data=_form(solar_forecast_enabled="on"),
        headers=_csrf(client),
        follow_redirects=False,
    )

    assert response.status_code == 303
    row = session.get(Setting, 1)
    assert row.solar_forecast_enabled is True
    assert row.solar_forecast_latitude is None
    assert row.solar_forecast_longitude is None


def test_an_out_of_range_latitude_is_refused_and_nothing_is_written(
    client_als: ClientBuilder, session: Session
) -> None:
    """Neither half of the form is written when the other half fails -- the same
    check-before-write discipline as `save_settings` on its own."""
    create_settings(session)
    before = session.get(Setting, 1).shadow_interval_seconds
    client = client_als(ALL_PERMISSIONS)

    response = client.post(
        "/settings",
        data=_form(
            solar_forecast_latitude="200", solar_forecast_longitude="13.405",
            shadow_interval_seconds="90",
        ),
        headers=_csrf(client),
    )

    assert response.status_code == 200
    assert "zwischen" in response.text
    session.expire_all()
    row = session.get(Setting, 1)
    assert row.solar_forecast_latitude is None
    assert row.shadow_interval_seconds == before  # the OTHER form's field, also unwritten


def test_a_rejected_default_also_leaves_the_location_unwritten(
    client_als: ClientBuilder, session: Session
) -> None:
    """The reverse direction: a bad value in the global-defaults half must not leave
    an already-checked coordinate committed on its own."""
    create_settings(session)
    client = client_als(ALL_PERMISSIONS)

    response = client.post(
        "/settings",
        data=_form(
            default_min_on_seconds="0",  # invalid
            solar_forecast_enabled="on",
            solar_forecast_latitude="52.520",
            solar_forecast_longitude="13.405",
        ),
        headers=_csrf(client),
    )

    assert response.status_code == 200
    session.expire_all()
    row = session.get(Setting, 1)
    assert row.solar_forecast_enabled is False
    assert row.solar_forecast_latitude is None


def test_without_setting_manage_the_solar_section_cannot_be_changed(
    client_als: ClientBuilder, session: Session
) -> None:
    create_settings(session)
    read_only = client_als([("zone.read", None)])
    response = read_only.post(
        "/settings",
        data=_form(solar_forecast_enabled="on"),
        headers=_csrf(read_only),
    )
    assert response.status_code == 403
