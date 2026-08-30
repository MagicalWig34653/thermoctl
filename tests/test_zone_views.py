from datetime import datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.helpers import (
    create_device,
    create_zone,
    operating_mode,
    role,
    source,
)
from thermoctl.auth.csrf import CSRF_HEADER, csrf_token
from thermoctl.auth.sessions import COOKIE_NAME
from thermoctl.config import get_settings
from thermoctl.db.models.device import ZoneDevice
from thermoctl.db.models.operations import AuditEvent
from thermoctl.db.models.override import ZoneOverride
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.state import ShadowDecision
from thermoctl.db.models.zone import SetpointMode, Zone, ZoneSetpoint


def _csrf(client: TestClient) -> dict[str, str]:
    http_session = client.cookies.get(COOKIE_NAME)
    assert http_session is not None
    geheimnis = get_settings().secret_key.get_secret_value()
    return {CSRF_HEADER: csrf_token(http_session, geheimnis)}


def _daten(session: Session, name: str = "wohnzimmer") -> dict[str, str]:
    return {
        "name": name,
        "display_name": "Wohnzimmer",
        "operating_mode": str(operating_mode(session).id),
        "sort_order": "4",
        "temperature_source_device_id": "",
    }


def test_the_zone_list_shows_only_visible_zones(client_als, session: Session) -> None:
    sichtbar = create_zone(session, "sichtbar")
    create_zone(session, "verborgen")

    response = client_als([("zone.read", sichtbar.id)]).get("/zones")

    assert response.status_code == 200
    assert sichtbar.display_name in response.text
    assert "Verborgen" not in response.text


def test_the_empty_zone_form_needs_an_installation_wide_permission(
    client_als, session: Session
) -> None:
    zone = create_zone(session, "bestehend")
    assert client_als([("zone.manage", zone.id)]).get("/zones/new").status_code == 403
    assert client_als([("zone.manage", None)]).get("/zones/new").status_code == 200


def test_creating_a_zone_writes_an_audit_entry(client_als, session: Session) -> None:
    source(session)
    client = client_als([("zone.manage", None)])

    response = client.post(
        "/zones", data=_daten(session), headers=_csrf(client), follow_redirects=False
    )

    assert response.status_code == 303
    zone = session.scalar(select(Zone).where(Zone.name == "wohnzimmer"))
    assert zone is not None
    audit = session.scalar(select(AuditEvent).where(AuditEvent.object_id == str(zone.id)))
    assert audit is not None
    assert audit.action == "create"
    assert audit.actor_user_id is not None


def test_a_duplicate_name_stays_in_the_form_with_a_field_message(
    client_als, session: Session
) -> None:
    create_zone(session, "wohnzimmer")
    client = client_als([("zone.manage", None)])

    response = client.post("/zones", data=_daten(session), headers=_csrf(client))

    assert response.status_code == 200
    assert "Dieser Name ist bereits vergeben." in response.text
    assert 'name="display_name" value="Wohnzimmer"' in response.text


def test_editing_a_zone_shows_the_values_and_saves_the_change(
    client_als, session: Session
) -> None:
    source(session)
    zone = create_zone(session, "alt")
    client = client_als([("zone.manage", zone.id)])
    form = client.get(f"/zones/{zone.id}")
    assert form.status_code == 200
    assert 'value="alt"' in form.text

    response = client.post(
        f"/zones/{zone.id}",
        data=_daten(session, "neu"),
        headers=_csrf(client),
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert zone.name == "neu"
    assert zone.sort_order == 4
    assert session.scalar(
        select(AuditEvent).where(
            AuditEvent.object_id == str(zone.id), AuditEvent.action == "update"
        )
    ) is not None


def test_a_zone_permission_for_another_zone_yields_404(client_als, session: Session) -> None:
    eigene = create_zone(session, "eigene")
    fremde = create_zone(session, "fremde")
    client = client_als([("zone.manage", eigene.id)])

    assert client.get(f"/zones/{fremde.id}").status_code == 404
    assert client.post(
        f"/zones/{fremde.id}", data=_daten(session), headers=_csrf(client)
    ).status_code == 404


def test_the_delete_confirmation_names_every_dependency(
    client_als, session: Session
) -> None:
    zone = create_zone(session, "voll")
    device = create_device(session, "sensor")
    session.add(
        ZoneDevice(
            zone_id=zone.id,
            device_id=device.id,
            device_role_id=role(session, "controller").id,
        )
    )
    mode = SetpointMode(code="tag", name="Tag")
    session.add(mode)
    session.flush()
    session.add_all(
        [
            SchedulePoint(
                zone_id=zone.id, weekday=1, minute_of_day=0, setpoint_mode_id=mode.id
            ),
            ZoneSetpoint(
                zone_id=zone.id, setpoint_mode_id=mode.id, temperature_c=Decimal("20.0")
            ),
            ZoneOverride(
                zone_id=zone.id,
                temperature_c=Decimal("21.0"),
                starts_at=datetime(2026, 8, 29),
                source_id=source(session).id,
            ),
            ShadowDecision(
                decided_at=datetime(2026, 8, 29),
                zone_id=zone.id,
                temperature_c=Decimal("19.0"),
                setpoint_c=Decimal("20.0"),
                setpoint_reason="Zeitplan",
                would_heat=True,
                outcome_code="ein",
                reason="Unter Sollwert",
            ),
        ]
    )
    session.flush()

    response = client_als([("zone.manage", zone.id)]).get(
        f"/zones/{zone.id}/delete"
    )

    assert response.status_code == 200
    for expected in (
        "1 Schaltpunkte",
        "1 zugeordnete Geräte",
        "1 Sollwerte",
        "1 Übersteuerungen",
        "1 Schattenentscheidungen",
    ):
        assert expected in response.text


def test_deleting_a_zone_removes_the_cascades_and_writes_an_audit_entry(
    client_als, session: Session
) -> None:
    source(session)
    zone = create_zone(session, "weg")
    mode = SetpointMode(code="nacht", name="Nacht")
    session.add(mode)
    session.flush()
    point = SchedulePoint(
        zone_id=zone.id, weekday=1, minute_of_day=0, setpoint_mode_id=mode.id
    )
    session.add(point)
    session.flush()
    zone_id = zone.id
    point_id = point.id
    client = client_als([("zone.manage", zone.id)])

    response = client.post(
        f"/zones/{zone.id}/delete", headers=_csrf(client), follow_redirects=False
    )

    assert response.status_code == 303
    session.flush()
    session.expire_all()
    assert session.get(Zone, zone_id) is None
    assert session.get(SchedulePoint, point_id) is None
    assert session.scalar(
        select(AuditEvent).where(
            AuditEvent.object_id == str(zone_id), AuditEvent.action == "delete"
        )
    ) is not None


def test_invalid_input_returns_to_the_form(
    client_als, session: Session
) -> None:
    """Every branch of the input validation individually -- on create and on update.

    The implementer's report does not count as long as nobody has verified it: the
    entire check was unproven until now, and this exact kind of gap has already let
    basic bugs through twice in this project.
    """
    source(session, "web")
    kind = operating_mode(session, "auto")
    zone = create_zone(session, "bestehende-zone")
    client = client_als([("zone.manage", None), ("zone.read", None)])

    gueltig = {
        "name": "neu", "display_name": "Neu", "operating_mode": str(kind.id),
        "sort_order": "0", "temperature_source_device_id": "",
    }
    faelle = [
        ({"name": ""}, "technischen Namen"),
        ({"display_name": ""}, "Anzeigenamen"),
        ({"operating_mode": ""}, "Betriebsart auswählen"),
        ({"operating_mode": "999999"}, "nicht bekannt"),
        ({"sort_order": "oben"}, "ganze Zahl"),
        ({"temperature_source_device_id": "kein Gerät"}, "bekanntes Gerät"),
        ({"temperature_source_device_id": "999999"}, "nicht bekannt"),
    ]
    for deviation, expected in faelle:
        data = {**gueltig, **deviation}
        create = client.post("/zones", data=data, headers=_csrf(client))
        assert create.status_code == 200, deviation
        assert expected in create.text, deviation

        update = client.post(f"/zones/{zone.id}", data=data, headers=_csrf(client))
        assert update.status_code == 200, deviation
        assert expected in update.text, deviation

    assert session.scalar(select(Zone).where(Zone.name == "neu")) is None
    assert zone.name == "bestehende-zone"


def test_renaming_to_a_taken_name_stays_in_the_form(
    client_als, session: Session
) -> None:
    """On create, this case was covered; on update, it was not -- it is the same conflict."""
    source(session, "web")
    kind = operating_mode(session, "auto")
    create_zone(session, "schon-da")
    andere = create_zone(session, "wird-umbenannt")
    client = client_als([("zone.manage", None), ("zone.read", None)])
    response = client.post(
        f"/zones/{andere.id}",
        data={
            "name": "schon-da", "display_name": "Andere", "operating_mode": str(kind.id),
            "sort_order": "0", "temperature_source_device_id": "",
        },
        headers=_csrf(client),
    )
    assert response.status_code == 200
    assert "bereits vergeben" in response.text
    assert andere.name == "wird-umbenannt"


# --- Remote operating mode --------------------------------------------------


def test_setting_the_operating_mode_writes_and_logs(session: Session) -> None:
    """A dedicated function next to `zone_aendern`: a command from outside knows only
    the operating mode, and using `zone_aendern` would overwrite everything else
    with whatever the caller happens to have on hand."""
    from sqlalchemy import select

    from tests.helpers import source
    from thermoctl.db.models.lookup import OperatingMode
    from thermoctl.db.models.operations import AuditEvent
    from thermoctl.domain.zones import set_operating_mode

    zone = create_zone(session, "betriebsartzone")
    source(session, "system")
    aus = OperatingMode(code="off", label="Aus")
    session.add(aus)
    session.flush()

    assert set_operating_mode(session, zone, "off", akteur_id=None, source="system") is True
    assert zone.operating_mode_id == aus.id
    entry = session.scalars(
        select(AuditEvent).where(AuditEvent.object_type == "zone")
    ).one()
    assert "Aus" in (entry.detail or "")


def test_the_same_operating_mode_writes_no_entry(session: Session) -> None:
    """Home Assistant likes to resend its state. A log that records every repetition
    as a change would be unreadable after a week."""
    from sqlalchemy import select

    from tests.helpers import source
    from thermoctl.db.models.operations import AuditEvent
    from thermoctl.domain.zones import set_operating_mode

    zone = create_zone(session, "wiederholungszone")
    source(session, "system")
    code = zone.operating_mode.code

    assert set_operating_mode(session, zone, code, akteur_id=None, source="system") is False
    assert not session.scalars(
        select(AuditEvent).where(AuditEvent.object_type == "zone")
    ).all()


def test_an_unknown_operating_mode_is_refused(session: Session) -> None:
    from thermoctl.domain.zones import UnknownOperatingMode, set_operating_mode

    zone = create_zone(session, "unbekanntbetrieb")
    with pytest.raises(UnknownOperatingMode):
        set_operating_mode(session, zone, "gemuetlich", akteur_id=None, source="system")
