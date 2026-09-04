"""The view over the actuator command log -- filters, rights, and local time.

Every "not in response.text" assertion below checks a `device_name`, never a
`zone_name`: the filter's own `<select>` lists every zone name that has ever been
recorded, on every page, regardless of the current filter -- so a `zone_name` used
by an excluded entry would still show up in that dropdown and make the assertion
meaningless. `device_name` appears nowhere but the result rows.
"""

import re
from datetime import datetime

from sqlalchemy.orm import Session

from tests.helpers import command_outcome, create_settings, create_zone, source
from thermoctl.db.models.state import DeviceCommand


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


def test_device_commands_requires_audit_read(client_als) -> None:
    assert client_als([("zone.read", None)]).get("/device-commands").status_code == 403
    assert client_als([("audit.read", None)]).get("/device-commands").status_code == 200


def test_from_actually_filters(client_als, session: Session) -> None:
    create_settings(session)
    _entry(session, device_name="zu-alt", at=datetime(2026, 8, 1, 23, 59))
    _entry(session, device_name="im-zeitraum", at=datetime(2026, 8, 2))
    response = client_als([("audit.read", None)]).get(
        "/device-commands?from_date=2026-08-02"
    )
    assert "im-zeitraum" in response.text
    assert "zu-alt" not in response.text


def test_to_filters_inclusive_of_the_whole_day(client_als, session: Session) -> None:
    create_settings(session)
    _entry(session, device_name="noch-enthalten", at=datetime(2026, 8, 2, 23, 59, 59))
    _entry(session, device_name="zu-neu", at=datetime(2026, 8, 3))
    response = client_als([("audit.read", None)]).get(
        "/device-commands?to_date=2026-08-02"
    )
    assert "noch-enthalten" in response.text
    assert "zu-neu" not in response.text


def test_zone_filter_matches_the_name_snapshot_of_a_deleted_zone(
    client_als, session: Session
) -> None:
    """The filter has to work on the snapshot, not a join to `zone` -- otherwise a
    deleted zone's entries could no longer be found by name at all."""
    create_settings(session)
    zone = create_zone(session, "überlebende-zone")
    _entry(session, device_name="am-leben", zone_name=zone.display_name, zone_id=zone.id)
    _entry(session, device_name="verwaist", zone_name="gelöschte-zone", zone_id=None)
    response = client_als([("audit.read", None)]).get(
        f"/device-commands?zone={zone.display_name}"
    )
    assert "am-leben" in response.text
    assert "verwaist" not in response.text


def test_outcome_filter_actually_filters(client_als, session: Session) -> None:
    create_settings(session)
    _entry(session, device_name="lief-durch", outcome_code="executed")
    _entry(session, device_name="wurde-unterdrückt", outcome_code="suppressed")
    response = client_als([("audit.read", None)]).get(
        "/device-commands?outcome=suppressed"
    )
    assert "wurde-unterdrückt" in response.text
    assert "lief-durch" not in response.text


def test_error_and_reason_are_shown(client_als, session: Session) -> None:
    create_settings(session)
    _entry(
        session,
        device_name="fehlerventil",
        outcome_code="failed",
        error="MQTT-Client hat die Veröffentlichung abgewiesen",
        reason="Ist 19,2 unter Soll 21,0 minus Hysterese",
    )
    response = client_als([("audit.read", None)]).get("/device-commands")
    assert "MQTT-Client hat die Veröffentlichung abgewiesen" in response.text
    assert "Ist 19,2 unter Soll 21,0 minus Hysterese" in response.text


def test_pagination_returns_the_second_page(client_als, session: Session) -> None:
    create_settings(session)
    for number in range(51):
        _entry(
            session,
            device_name=f"eintrag-{number:02}",
            at=datetime(2026, 8, 15, 12, number),
        )
    response = client_als([("audit.read", None)]).get("/device-commands?page=2")
    assert "eintrag-00" in response.text
    assert "eintrag-50" not in response.text


def test_to_before_from_shows_a_message_and_keeps_the_values(client_als) -> None:
    response = client_als([("audit.read", None)]).get(
        "/device-commands?from_date=2026-08-20&to_date=2026-08-10"
    )
    assert response.status_code == 200
    assert "darf nicht vor dem Von-Datum liegen" in response.text
    assert 'value="2026-08-20"' in response.text
    assert 'value="2026-08-10"' in response.text


def test_unreadable_filter_values_show_messages_instead_of_422(client_als) -> None:
    response = client_als([("audit.read", None)]).get(
        "/device-commands?from_date=gestern&page=zwei"
    )
    assert response.status_code == 200
    assert "Bitte ein gültiges Datum eingeben" in response.text
    assert "Seitennummer muss eine ganze Zahl sein" in response.text


def test_the_time_shown_is_local_not_utc(client_als, session: Session) -> None:
    """`timezone` defaults to Europe/Berlin in `create_settings`; in August that is
    UTC+2, so 12:00 UTC must read 14:00 in both the cell and its tooltip."""
    create_settings(session)
    _entry(session, device_name="zeitzonenventil", at=datetime(2026, 8, 15, 12, 0, 0))
    response = client_als([("audit.read", None)]).get("/device-commands")
    assert "15.08.2026 14:00:00" in response.text
    assert 'title="15.08.2026 14:00:00"' in response.text
    assert "2026-08-15 12:00:00 UTC" not in response.text


def test_the_rendered_filter_form_actually_filters(client_als, session: Session) -> None:
    """Submits the filter form as rendered, instead of hand-building the query
    string -- so a renamed `name=` attribute in the template would be caught here."""
    create_settings(session)
    _entry(session, device_name="renderformventil", outcome_code="suppressed")
    _entry(session, device_name="anderes-ventil", outcome_code="executed")
    client = client_als([("audit.read", None)])
    page = client.get("/device-commands")

    form = re.search(
        r'<form method="get" action="/device-commands"[^>]*>(.*?)</form>',
        page.text,
        re.S,
    )
    assert form is not None
    field_names = set(re.findall(r'name="([^"]+)"', form.group(1)))
    assert field_names == {"from_date", "to_date", "zone", "outcome"}

    response = client.get("/device-commands", params={"outcome": "suppressed"})
    assert "renderformventil" in response.text
    assert "anderes-ventil" not in response.text
