"""The zone parameter page's solar fields (`solar_gain_factor`, `solar_setback_max_k`).

Sits next to `test_daily_views.py`, which already covers the six existing control
parameters -- these two fields were added to the same page and the same route rather
than a new one (per the task: "sieh dir an, wo die uebrigen Zonen-Parameter gepflegt
werden ... und fuege dich dort ein").
"""

from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.helpers import create_settings, create_zone, source
from thermoctl.auth.csrf import CSRF_HEADER, csrf_token
from thermoctl.auth.sessions import COOKIE_NAME
from thermoctl.config import get_settings


def _grundlage(session: Session):
    source(session, "web")
    create_settings(session)
    return create_zone(session, "dachzimmer")


def _csrf(client: TestClient) -> dict[str, str]:
    http_session = client.cookies.get(COOKIE_NAME)
    assert http_session is not None
    return {CSRF_HEADER: csrf_token(http_session, get_settings().secret_key.get_secret_value())}


def test_the_page_shows_the_documented_default_of_zero(session: Session, client_als) -> None:
    zone = _grundlage(session)
    client = client_als([("zone.manage", zone.id)])

    response = client.get(f"/zones/{zone.id}/parameters")

    assert response.status_code == 200
    assert 'name="solar_gain_factor"' in response.text
    assert 'value="0"' in response.text


def test_setting_a_factor_within_bounds_is_saved(session: Session, client_als) -> None:
    zone = _grundlage(session)
    client = client_als([("zone.manage", zone.id)])

    response = client.post(
        f"/zones/{zone.id}/parameters",
        data={"solar_gain_factor": "0.8"},
        headers=_csrf(client),
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert zone.solar_gain_factor == Decimal("0.8")


def test_a_factor_above_one_is_refused(session: Session, client_als) -> None:
    zone = _grundlage(session)
    client = client_als([("zone.manage", zone.id)])

    response = client.post(
        f"/zones/{zone.id}/parameters",
        data={"solar_gain_factor": "1.5"},
        headers=_csrf(client),
    )

    assert response.status_code == 200
    assert "zwischen 0 und 1" in response.text
    assert zone.solar_gain_factor == Decimal("0")  # unchanged


def test_a_non_numeric_factor_is_refused(session: Session, client_als) -> None:
    zone = _grundlage(session)
    client = client_als([("zone.manage", zone.id)])

    response = client.post(
        f"/zones/{zone.id}/parameters",
        data={"solar_gain_factor": "viel"},
        headers=_csrf(client),
    )

    assert response.status_code == 200
    assert "gültige Zahl" in response.text
    assert zone.solar_gain_factor == Decimal("0")


def test_a_negative_factor_is_refused(session: Session, client_als) -> None:
    zone = _grundlage(session)
    client = client_als([("zone.manage", zone.id)])

    response = client.post(
        f"/zones/{zone.id}/parameters",
        data={"solar_gain_factor": "-0.1"},
        headers=_csrf(client),
    )

    assert response.status_code == 200
    assert zone.solar_gain_factor == Decimal("0")


def test_omitting_the_field_leaves_the_current_factor_unchanged(
    session: Session, client_als
) -> None:
    """A partial submission (as several other tests on this page also send) must not
    be misread as clearing a non-nullable value back to some default."""
    zone = _grundlage(session)
    zone.solar_gain_factor = Decimal("0.6")
    client = client_als([("zone.manage", zone.id)])

    response = client.post(
        f"/zones/{zone.id}/parameters",
        data={"temperature_offset_k": "0.5"},
        headers=_csrf(client),
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert zone.solar_gain_factor == Decimal("0.6")


def test_the_setback_maximum_inherits_the_global_default(
    session: Session, client_als
) -> None:
    settings = create_settings(session)
    source(session, "web")
    settings.default_solar_setback_max_k = Decimal("3.5")
    zone = create_zone(session, "erbend")
    client = client_als([("zone.manage", zone.id)])

    response = client.get(f"/zones/{zone.id}/parameters")

    assert response.status_code == 200
    assert "Derzeit 3.5 K aus dem globalen Standard" in response.text


def test_the_setback_maximum_can_be_overridden_per_zone(
    session: Session, client_als
) -> None:
    zone = _grundlage(session)
    client = client_als([("zone.manage", zone.id)])

    response = client.post(
        f"/zones/{zone.id}/parameters",
        data={"solar_setback_max_k": "4.0"},
        headers=_csrf(client),
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert zone.solar_setback_max_k == Decimal("4.0")


def test_an_empty_setback_maximum_restores_inheritance(
    session: Session, client_als
) -> None:
    zone = _grundlage(session)
    zone.solar_setback_max_k = Decimal("4.0")
    client = client_als([("zone.manage", zone.id)])

    response = client.post(
        f"/zones/{zone.id}/parameters",
        data={"solar_setback_max_k": ""},
        headers=_csrf(client),
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert zone.solar_setback_max_k is None
