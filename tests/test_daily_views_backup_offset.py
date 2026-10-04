"""Ausgleichswerte der Ersatzquelle im Regelparameter-Formular.

Hintergrund: Das Formular las den Wert unter einem nicht existierenden Namen und
zeigte deshalb nie etwas an; jedes Speichern leerte dann still alle Werte. Die Tests
fahren deshalb den echten Weg -- gerenderte Seite, Formulardaten wie ein Browser sie
aus dem HTML bildet, Speichern, Datenbank.
"""

from decimal import Decimal
from html.parser import HTMLParser

from sqlalchemy.orm import Session

from tests.helpers import capability, create_device, create_settings, create_zone, role, source
from thermoctl.auth.csrf import CSRF_HEADER, csrf_token
from thermoctl.auth.sessions import COOKIE_NAME
from thermoctl.config import get_settings
from thermoctl.db.models.device import DeviceCapabilityLink, ZoneDevice


class _Form(HTMLParser):
    """Sammelt, was ein Browser beim Absenden der Seite mitschickt."""

    def __init__(self) -> None:
        super().__init__()
        self.data: dict[str, str] = {}
        self._select: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: v or "" for k, v in attrs}
        name = a.get("name")
        if tag == "select" and name:
            self._select = name
            self.data.setdefault(name, "")
        elif tag == "option" and self._select and "selected" in a:
            self.data[self._select] = a.get("value", "")
        elif tag == "input" and name:
            kind = a.get("type", "text")
            if kind in ("checkbox", "radio"):
                if "checked" in a:
                    self.data[name] = a.get("value", "on")
            elif kind != "submit":
                self.data[name] = a.get("value", "")

    def handle_endtag(self, tag: str) -> None:
        if tag == "select":
            self._select = None


def _browser_form(html: str) -> dict[str, str]:
    parser = _Form()
    parser.feed(html)
    return parser.data


def _thermostat(session: Session, zone_id: int, name: str, offset: str | None) -> int:
    device = create_device(session, name)
    thermostat_id = capability(session, "thermostat").id
    session.add(DeviceCapabilityLink(device_id=device.id, capability_id=thermostat_id))
    row = ZoneDevice(
        zone_id=zone_id,
        device_id=device.id,
        device_role_id=role(session, "actuator").id,
        self_regulating=True,
        temperature_backup_offset_k=Decimal(offset) if offset is not None else None,
    )
    session.add(row)
    session.flush()
    return row.id


def _csrf(client) -> dict[str, str]:
    http_session = client.cookies.get(COOKIE_NAME)
    assert http_session is not None
    return {CSRF_HEADER: csrf_token(http_session, get_settings().secret_key.get_secret_value())}


def _setup(session: Session):
    source(session, "web")
    create_settings(session)
    zone = create_zone(session, "bad-ausgleich")
    assignment_id = _thermostat(session, zone.id, "bad-thermostat", "-1.50")
    return zone, assignment_id


def _stored(session: Session, assignment_id: int) -> Decimal | None:
    row = session.get(ZoneDevice, assignment_id)
    assert row is not None
    session.refresh(row)
    return row.temperature_backup_offset_k


def test_a_stored_backup_offset_is_shown_in_its_input(session: Session, client_als) -> None:
    zone, assignment_id = _setup(session)
    client = client_als([("zone.manage", zone.id), ("device.manage", zone.id)])

    page = client.get(f"/zones/{zone.id}/parameters")

    assert page.status_code == 200
    assert _browser_form(page.text)[f"backup_offset_{assignment_id}"] == "-1.50"


def test_saving_without_touching_the_backup_offset_keeps_it(session: Session, client_als) -> None:
    zone, assignment_id = _setup(session)
    client = client_als([("zone.manage", zone.id), ("device.manage", zone.id)])
    form = _browser_form(client.get(f"/zones/{zone.id}/parameters").text)

    response = client.post(
        f"/zones/{zone.id}/parameters", data=form, headers=_csrf(client), follow_redirects=True
    )

    assert response.status_code == 200
    assert _stored(session, assignment_id) == Decimal("-1.50")


def test_without_device_manage_the_offsets_are_hidden_and_untouched(
    session: Session, client_als
) -> None:
    zone, assignment_id = _setup(session)
    client = client_als([("zone.manage", zone.id)])
    form = _browser_form(client.get(f"/zones/{zone.id}/parameters").text)
    assert f"backup_offset_{assignment_id}" not in form

    # Auch ein von Hand eingeschleustes Feld darf ohne das Recht nichts bewirken.
    form[f"backup_offset_{assignment_id}"] = "7"
    client.post(
        f"/zones/{zone.id}/parameters", data=form, headers=_csrf(client), follow_redirects=True
    )

    assert _stored(session, assignment_id) == Decimal("-1.50")


def test_a_rejected_save_redisplays_the_typed_backup_offset(session: Session, client_als) -> None:
    zone, assignment_id = _setup(session)
    client = client_als([("zone.manage", zone.id), ("device.manage", zone.id)])
    form = _browser_form(client.get(f"/zones/{zone.id}/parameters").text)
    form[f"backup_offset_{assignment_id}"] = "2.25"
    form["hysteresis_k"] = "-1"  # wird abgelehnt, die Seite wird neu gerendert

    response = client.post(f"/zones/{zone.id}/parameters", data=form, headers=_csrf(client))

    assert _browser_form(response.text)[f"backup_offset_{assignment_id}"] == "2.25"
    assert _stored(session, assignment_id) == Decimal("-1.50")


def test_an_explicitly_changed_backup_offset_is_saved(session: Session, client_als) -> None:
    zone, assignment_id = _setup(session)
    client = client_als([("zone.manage", zone.id), ("device.manage", zone.id)])
    form = _browser_form(client.get(f"/zones/{zone.id}/parameters").text)
    form[f"backup_offset_{assignment_id}"] = "0,75"

    client.post(
        f"/zones/{zone.id}/parameters", data=form, headers=_csrf(client), follow_redirects=True
    )

    assert _stored(session, assignment_id) == Decimal("0.75")
