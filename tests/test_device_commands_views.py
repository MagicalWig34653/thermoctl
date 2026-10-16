"""The view over the actuator command log -- filters, rights, and local time.

Every "not in response.text" assertion below checks a `device_name`, never a
`zone_name`: the filter's own `<select>` lists every zone name that has ever been
recorded, on every page, regardless of the current filter -- so a `zone_name` used
by an excluded entry would still show up in that dropdown and make the assertion
meaningless. `device_name` appears nowhere but the result rows.
"""

from datetime import datetime

from sqlalchemy.orm import Session

from tests.helpers import command_outcome, create_settings, create_zone, source
from thermoctl.db.models.state import DeviceCommand
from thermoctl.db.models.zone import Zone


def _entry(
    session: Session,
    *,
    device_name: str,
    zone_name: str = "wohnzimmer",
    zone_id: int | None = None,
    at: datetime = datetime(2026, 8, 15, 12, 0, 0),
    outcome_code: str = "executed",
    reason: str = "Zeitplan",
    error: str | None = None,
    source_code: str = "system",
) -> DeviceCommand:
    entry = DeviceCommand(
        sent_at=at,
        source_id=source(session, source_code).id,
        zone_id=zone_id,
        zone_name=zone_name,
        device_id=None,
        device_name=device_name,
        command="setpoint",
        payload='{"occupied_heating_setpoint": 21.0}',
        outcome_id=command_outcome(session, outcome_code).id,
        error=error,
        reason=reason,
    )
    session.add(entry)
    session.flush()
    return entry
























def _absenkung(session: Session, zone_name: str) -> Zone:
    """Beginn und Ende einer Sonnenabsenkung als echte Schattenzeilen."""
    from decimal import Decimal

    from tests.helpers import create_zone
    from thermoctl.db.models.state import ShadowDecision

    zone = create_zone(session, zone_name)
    for minute, k in enumerate([None, Decimal("2.0"), Decimal("2.0"), None]):
        session.add(
            ShadowDecision(
                decided_at=datetime(2026, 8, 15, 12, minute),
                zone_id=zone.id,
                setpoint_c=Decimal("20.5") - (k or 0),
                scheduled_setpoint_c=Decimal("20.5"),
                solar_setback_k=k,
                setpoint_reason="Zeitplan",
                would_heat=False,
                outcome_code="aus",
                reason="Sollwert ist erreicht.",
            )
        )
    session.flush()
    return zone














def test_a_zone_that_never_had_an_absenkung_is_not_offered_by_the_shadow_log(
    client_als, session: Session
) -> None:
    """Eine bloße Zone ohne Befehl und ohne Absenkung bleibt außen vor -- die Auswahl
    wird nicht zur Liste aller Zonen."""
    create_settings(session)
    create_zone(session, "ohne-alles")
    page = client_als([("audit.read", None)]).get("/device-commands")
    assert 'value="Ohne-alles"' not in page.text
