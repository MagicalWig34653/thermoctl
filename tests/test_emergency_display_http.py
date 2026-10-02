"""The Notbetrieb banner on start/tenant/kiosk (Auftrag 8b item 1) -- HTTP
regression tests over the actual rendered text, per stage.
"""


from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.helpers import create_settings, create_zone
from thermoctl.auth.csrf import csrf_token
from thermoctl.auth.kiosk import KIOSK_COOKIE_NAME, KIOSK_CSRF_COOKIE_NAME
from thermoctl.config import get_settings
from thermoctl.db.models.sensor_failure import ZoneSensorFailureState
from thermoctl.domain import emergency_operation
from thermoctl.domain.kiosk import issue_kiosk_token


def _ersatzquelle_state(
    session: Session, zone_id: int, device_name: str = "Thermostat Küche"
) -> None:
    from tests.helpers import create_device

    device = create_device(session, "ersatzquelle-geraet")
    device.display_name = device_name
    session.add(
        ZoneSensorFailureState(
            zone_id=zone_id,
            stage=emergency_operation.STAGE_ERSATZQUELLE,
            active_source_device_id=device.id,
        )
    )
    session.flush()


def test_start_page_shows_the_ersatzquelle_banner(
    session: Session, client_als
) -> None:
    create_settings(session)
    zone = create_zone(session, "wohnzimmer")
    _ersatzquelle_state(session, zone.id)
    client = client_als([("zone.read", zone.id)])

    response = client.get("/")

    assert response.status_code == status.HTTP_200_OK
    assert "Ersatzquelle aktiv: Thermostat Küche" in response.text


def test_start_page_shows_no_banner_for_a_normal_zone(session: Session, client_als) -> None:
    create_settings(session)
    zone = create_zone(session, "normalzimmer")
    client = client_als([("zone.read", zone.id)])

    response = client.get("/")

    assert response.status_code == status.HTTP_200_OK
    assert "Ersatzquelle aktiv" not in response.text
    assert "Notbetrieb aktiv" not in response.text


def test_tenant_page_shows_the_ersatzquelle_banner(
    session: Session, tenant_client
) -> None:
    create_settings(session)
    zone = create_zone(session, "schlafzimmer")
    _ersatzquelle_state(session, zone.id, "Thermostat Flur")
    client = tenant_client([("zone.read", zone.id)])

    response = client.get("/")

    assert response.status_code == status.HTTP_200_OK
    assert "Ersatzquelle aktiv: Thermostat Flur" in response.text


def test_kiosk_dashboard_shows_the_ersatzquelle_banner(
    session: Session, client: TestClient
) -> None:
    from tests.helpers import source, user_with_permissions

    source(session, "kiosk")
    source(session, "web")
    create_settings(session)
    zone = create_zone(session, "kueche")
    admin = user_with_permissions(
        session, "kiosk-admin", [("token.manage", None), ("zone.read", zone.id)]
    )
    _ersatzquelle_state(session, zone.id, "Thermostat Bad")
    _token, plaintext = issue_kiosk_token(
        session, admin, "Küche", [zone.id], control_allowed=False, expires_at=None
    )
    client.cookies.set(KIOSK_COOKIE_NAME, plaintext)
    client.cookies.set(
        KIOSK_CSRF_COOKIE_NAME,
        csrf_token(plaintext, get_settings().secret_key.get_secret_value()),
    )

    response = client.get("/kiosk")

    assert response.status_code == status.HTTP_200_OK
    assert "Ersatzquelle aktiv: Thermostat Bad" in response.text


def test_tenant_top_banner_shows_notbetrieb_instead_of_the_calm_message(
    session: Session, tenant_client
) -> None:
    """Auftrag 8b, correctness: the tenant home page must not claim "Heizung
    läuft normal" while a visible zone is actually in Notbetrieb -- the exact
    kind of literally-wrong user-visible claim `tests/test_user_visible_
    effect_texts.py` exists to catch (two earlier real cases: a missing start
    page, a missing stylesheet)."""
    create_settings(session)
    zone = create_zone(session, "arbeitszimmer")
    _ersatzquelle_state(session, zone.id, "Thermostat Diele")
    client = tenant_client([("zone.read", zone.id)])

    response = client.get("/")

    assert response.status_code == status.HTTP_200_OK
    assert "Heizung läuft normal" not in response.text
    # "<Zone> – <Stufe>", nicht "<Zone>: <Headline>" -- Letzteres hätte hier
    # "Arbeitszimmer: Ersatzquelle aktiv: ..." ergeben, einen doppelten
    # Doppelpunkt (Kreuzreview von c1ae1c5).
    assert "Arbeitszimmer – Ersatzquelle" in response.text
    assert "Arbeitszimmer:" not in response.text
