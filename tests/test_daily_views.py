from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.helpers import (
    capability,
    create_mode,
    create_settings,
    create_zone,
    integration,
    role,
    source,
)
from thermoctl.auth.csrf import CSRF_HEADER, csrf_token
from thermoctl.auth.sessions import COOKIE_NAME
from thermoctl.config import get_settings
from thermoctl.db.models.device import Device, DeviceCapabilityLink, ZoneDevice
from thermoctl.db.models.zone import Zone


def _grundlage(session: Session):
    source(session, "web")
    source(session, "api")
    create_settings(session)
    return create_zone(session, "wohnzimmer")


def _assign_switch_actuator(session: Session, zone: Zone) -> None:
    """The minimal assignment `pi_eligible()` accepts."""
    device = Device(
        integration_id=integration(session).id,
        external_id=f"{zone.name}-relais",
        display_name=f"{zone.name}-relais",
    )
    session.add(device)
    session.flush()
    session.add(
        DeviceCapabilityLink(device_id=device.id, capability_id=capability(session, "switch").id)
    )
    session.add(
        ZoneDevice(
            zone_id=zone.id,
            device_id=device.id,
            device_role_id=role(session, "actuator").id,
            thermostat_mode="controlled",
        )
    )
    session.flush()


def _csrf(client: TestClient) -> dict[str, str]:
    http_session = client.cookies.get(COOKIE_NAME)
    assert http_session is not None
    return {CSRF_HEADER: csrf_token(http_session, get_settings().secret_key.get_secret_value())}






































def test_the_window_temp_drop_switch_is_not_part_of_control_parameters(
    session: Session, client_als
) -> None:
    """The switch is now saved *through* `/zones/{id}/parameters` (see above), but
    it must still not become one of the `ControlParameters` fields REST exposes
    verbatim via `ControlParametersResponse` -- it is set via its own domain call
    (`set_window_temp_drop_detection`), not through `save_control_parameters`."""
    from thermoctl.domain.zone_settings import ControlParameters

    assert "window_temp_drop_detection_enabled" not in ControlParameters.__dataclass_fields__
































# --- Thermostat on the home page --------------------------------------------


def _zone_with_mode(session: Session, temperature: str = "21.0"):
    """A zone whose effective setpoint comes from a schedule mode."""
    from thermoctl.db.models.schedule import SchedulePoint
    from thermoctl.db.models.zone import ZoneSetpoint

    zone = _grundlage(session)
    mode = create_mode(session, "thermostat-tag", "Tag")
    session.add(
        ZoneSetpoint(
            zone_id=zone.id, setpoint_mode_id=mode.id, temperature_c=Decimal(temperature)
        )
    )
    session.add(
        SchedulePoint(
            zone_id=zone.id, weekday=1, minute_of_day=0, setpoint_mode_id=mode.id
        )
    )
    session.flush()
    return zone, mode






















# --- Außentemperatur und Fenster-Alarm auf der Startseite -------------------






