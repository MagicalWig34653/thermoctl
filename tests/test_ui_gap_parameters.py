"""Schließt Testlücken der abgelösten Alt-Oberfläche für die Raumtechnik-Parameter.

Gegen `GET/PUT /ui-api/rooms/{id}/parameters`: Ventilschutz ausschalten, ein aktiver aber
inzwischen unzulässiger PI-Schalter, Vorgabe und Vererbung der Sonnenabsenkung.
"""

from collections.abc import Callable
from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.orm import Session

from tests.helpers import capability, create_device, create_settings, create_zone, role, source
from thermoctl.auth.csrf import CSRF_HEADER, csrf_token
from thermoctl.auth.sessions import COOKIE_NAME
from thermoctl.config import get_settings
from thermoctl.db.models.device import DeviceCapabilityLink, ZoneDevice
from thermoctl.db.models.operations import Setting
from thermoctl.db.models.zone import Zone

Als = Callable[[list[tuple[str, int | None]]], TestClient]

ALLES: list[tuple[str, int | None]] = [
    ("zone.read", None),
    ("zone.manage", None),
    ("device.manage", None),
]


def _call(client: TestClient, method: str, url: str, body: dict[str, Any] | None = None) -> Any:
    cookie = client.cookies.get(COOKIE_NAME)
    assert cookie is not None
    headers = {CSRF_HEADER: csrf_token(cookie, get_settings().secret_key.get_secret_value())}
    return client.request(method, url, json=body, headers=headers)


def _anlage(session: Session) -> tuple[Setting, Zone]:
    source(session, "web")
    row = create_settings(session)
    return row, create_zone(session, "bad")


def _schalter(session: Session, zone: Zone) -> None:
    """Ein gewöhnlicher Schaltaktor: macht die Zone für PI zulässig."""
    device = create_device(session, "schalter")
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


def test_valve_protection_can_be_switched_off_explicitly(client_als: Als, session: Session) -> None:
    _, zone = _anlage(session)
    client = client_als(ALLES)
    url = f"/ui-api/rooms/{zone.id}/parameters"

    on = _call(client, "PUT", url, {"valve_protection_enabled": True})
    assert on.status_code == 200, on.text
    assert on.json()["valve_protection"]["enabled"] is True
    session.refresh(zone)
    assert zone.valve_protection_enabled is True

    # Ein Teilaufruf ohne das Feld lässt es an ...
    unrelated = _call(client, "PUT", url, {"hysteresis_k": "0.5"})
    assert unrelated.json()["valve_protection"]["enabled"] is True

    # ... und nur ein ausdrückliches false schaltet es ab, ohne die übrigen Werte anzufassen.
    off = _call(client, "PUT", url, {"valve_protection_enabled": False})
    assert off.status_code == 200, off.text
    assert off.json()["valve_protection"]["enabled"] is False
    session.refresh(zone)
    assert zone.valve_protection_enabled is False
    assert zone.hysteresis_k == Decimal("0.50")
    assert client.get(url).json()["valve_protection"]["enabled"] is False


def test_an_enabled_but_now_ineligible_pi_switch_stays_enabled(
    client_als: Als, session: Session
) -> None:
    _, zone = _anlage(session)
    _schalter(session, zone)
    client = client_als(ALLES)
    url = f"/ui-api/rooms/{zone.id}/parameters"
    enabled = _call(client, "PUT", url, {"pi_enabled": True, "pi_confirm": True})
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["pi"]["enabled"] is True and enabled.json()["pi"]["eligible"] is True

    # Der Aktor wird entfernt: PI bleibt eingeschaltet, ist aber nicht mehr zulässig.
    session.execute(delete(ZoneDevice).where(ZoneDevice.zone_id == zone.id))
    session.flush()

    shown = client.get(url).json()["pi"]
    assert shown["enabled"] is True
    assert shown["eligible"] is False and shown["eligibility_reason"]
    # Ein Speichern, das PI gar nicht erwähnt, schaltet es nie nebenbei aus.
    session.refresh(zone)
    assert zone.pi_enabled is True
    _call(client, "PUT", url, {"hysteresis_k": "0.9"})
    session.refresh(zone)
    assert zone.pi_enabled is True
    assert client.get(url).json()["pi"]["enabled"] is True

    # Ausschalten bleibt möglich, auch wenn die Zone nicht mehr zulässig ist.
    off = _call(client, "PUT", url, {"pi_enabled": False})
    assert off.status_code == 200, off.text
    assert off.json()["pi"]["enabled"] is False
    session.refresh(zone)
    assert zone.pi_enabled is False
    # Und wieder einschalten geht nicht, solange sie unzulässig ist.
    again = _call(client, "PUT", url, {"pi_enabled": True, "pi_confirm": True})
    assert again.status_code == 422
    session.refresh(zone)
    assert zone.pi_enabled is False


def test_the_solar_gain_factor_documents_its_default_of_zero(
    client_als: Als, session: Session
) -> None:
    _, zone = _anlage(session)
    client = client_als(ALLES)
    url = f"/ui-api/rooms/{zone.id}/parameters"

    assert Decimal(client.get(url).json()["solar_gain_factor"]) == Decimal("0")

    saved = _call(client, "PUT", url, {"solar_gain_factor": "0.5"})
    assert Decimal(saved.json()["solar_gain_factor"]) == Decimal("0.5")
    back = _call(client, "PUT", url, {"solar_gain_factor": "0"})
    assert back.status_code == 200, back.text
    assert Decimal(back.json()["solar_gain_factor"]) == Decimal("0")


def test_the_setback_maximum_inherits_the_global_default(client_als: Als, session: Session) -> None:
    settings, zone = _anlage(session)
    settings.default_solar_setback_max_k = Decimal("3.5")
    session.flush()
    client = client_als(ALLES)
    url = f"/ui-api/rooms/{zone.id}/parameters"

    inherited = client.get(url).json()["control"]["solar_setback_max_k"]
    assert inherited["own"] is None and inherited["inherited"] is True
    assert Decimal(inherited["effective"]) == Decimal("3.5")
    assert inherited["source"] == "plant"

    own = _call(client, "PUT", url, {"solar_setback_max_k": "2"}).json()["control"][
        "solar_setback_max_k"
    ]
    assert own["inherited"] is False and own["source"] == "zone"
    assert Decimal(own["own"]) == Decimal("2") and Decimal(own["effective"]) == Decimal("2")

    # null kehrt zur Vererbung zurück; der wirksame Wert folgt wieder der Anlage.
    settings.default_solar_setback_max_k = Decimal("4.0")
    session.flush()
    back = _call(client, "PUT", url, {"solar_setback_max_k": None}).json()["control"][
        "solar_setback_max_k"
    ]
    assert back["inherited"] is True and back["own"] is None
    assert Decimal(back["effective"]) == Decimal("4.0")
