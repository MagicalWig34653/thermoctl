
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.helpers import (
    create_zone,
    operating_mode,
    source,
)
from thermoctl.auth.csrf import CSRF_HEADER, csrf_token
from thermoctl.auth.sessions import COOKIE_NAME
from thermoctl.config import get_settings
from thermoctl.db.models.operations import AuditEvent


def _csrf(client: TestClient) -> dict[str, str]:
    http_session = client.cookies.get(COOKIE_NAME)
    assert http_session is not None
    secret = get_settings().secret_key.get_secret_value()
    return {CSRF_HEADER: csrf_token(http_session, secret)}


def _daten(session: Session, name: str = "wohnzimmer") -> dict[str, str]:
    return {
        "name": name,
        "display_name": "Wohnzimmer",
        "operating_mode": str(operating_mode(session).id),
        "sort_order": "4",
        "temperature_source_device_id": "",
    }






















# --- Remote operating mode --------------------------------------------------


def test_setting_the_operating_mode_writes_and_logs(session: Session) -> None:
    """A dedicated function next to `zone_aendern`: a command from outside knows only
    the operating mode, and using `zone_aendern` would overwrite everything else
    with whatever the caller happens to have on hand."""

    from thermoctl.db.models.lookup import OperatingMode
    from thermoctl.domain.zones import set_operating_mode

    zone = create_zone(session, "betriebsartzone")
    source(session, "system")
    aus = OperatingMode(code="off", label="Aus")
    session.add(aus)
    session.flush()

    assert set_operating_mode(session, zone, "off", actor_id=None, source="system") is True
    assert zone.operating_mode_id == aus.id
    entry = session.scalars(
        select(AuditEvent).where(AuditEvent.object_type == "zone")
    ).one()
    assert "Aus" in (entry.detail or "")


def test_the_same_operating_mode_writes_no_entry(session: Session) -> None:
    """Home Assistant likes to resend its state. A log that records every repetition
    as a change would be unreadable after a week."""

    from thermoctl.domain.zones import set_operating_mode

    zone = create_zone(session, "wiederholungszone")
    source(session, "system")
    code = zone.operating_mode.code

    assert set_operating_mode(session, zone, code, actor_id=None, source="system") is False
    assert not session.scalars(
        select(AuditEvent).where(AuditEvent.object_type == "zone")
    ).all()


def test_an_unknown_operating_mode_is_refused(session: Session) -> None:
    from thermoctl.domain.zones import UnknownOperatingMode, set_operating_mode

    zone = create_zone(session, "unbekanntbetrieb")
    with pytest.raises(UnknownOperatingMode):
        set_operating_mode(session, zone, "gemütlich", actor_id=None, source="system")
