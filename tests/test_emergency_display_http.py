"""The Notbetrieb banner on start/tenant/kiosk (Auftrag 8b item 1) -- HTTP
regression tests over the actual rendered text, per stage.
"""


from fastapi import status
from sqlalchemy.orm import Session

from tests.helpers import create_settings, create_zone
from thermoctl.db.models.sensor_failure import ZoneSensorFailureState
from thermoctl.domain import emergency_operation


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




def test_start_page_shows_no_banner_for_a_normal_zone(session: Session, client_als) -> None:
    create_settings(session)
    zone = create_zone(session, "normalzimmer")
    client = client_als([("zone.read", zone.id)])

    response = client.get("/")

    assert response.status_code == status.HTTP_200_OK
    assert "Ersatzquelle aktiv" not in response.text
    assert "Notbetrieb aktiv" not in response.text






