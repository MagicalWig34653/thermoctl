"""The zone parameter page's solar fields (`solar_gain_factor`, `solar_setback_max_k`).

Sits next to `test_daily_views.py`, which already covers the six existing control
parameters -- these two fields were added to the same page and the same route rather
than a new one (per the task: "sieh dir an, wo die uebrigen Zonen-Parameter gepflegt
werden ... und fuege dich dort ein").
"""


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


















