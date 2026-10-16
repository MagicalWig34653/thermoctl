"""Schließt Testlücken der abgelösten Alt-Oberfläche für Geräteliste und Bediengeräte-Kanäle.

Gegen `/ui-api/devices` und `/ui-api/controllers`: die Felder der Geräteliste, die
Meross-Befundlogik in `build_device_survey`, die Batterie-Chip-Unterdrückung sowie die
Kanalkonfiguration (Nachkommastellen, Felder je Art, unveränderter Round-Trip).
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.helpers import (
    capability,
    create_device,
    create_settings,
    create_zone,
    role,
    source,
)
from thermoctl.auth.csrf import CSRF_HEADER, csrf_token
from thermoctl.auth.sessions import COOKIE_NAME
from thermoctl.config import get_settings
from thermoctl.db.base import utcnow
from thermoctl.db.models.device import (
    ControllerChannel,
    Device,
    DeviceCapabilityLink,
    DeviceProperty,
    ZoneDevice,
)
from thermoctl.db.models.lookup import (
    CHANNEL_KINDS,
    CONTROLLER_COMMANDS,
    ChannelKind,
    ControllerCommand,
)
from thermoctl.db.models.measurement import DeviceHealth
from thermoctl.db.models.zone import Zone

Als = Callable[[list[tuple[str, int | None]]], TestClient]


def _call(client: TestClient, method: str, url: str, body: dict[str, Any] | None = None) -> Any:
    cookie = client.cookies.get(COOKIE_NAME)
    assert cookie is not None
    headers = {CSRF_HEADER: csrf_token(cookie, get_settings().secret_key.get_secret_value())}
    return client.request(method, url, json=body, headers=headers)


def _prop(
    session: Session,
    device: Device,
    name: str,
    *,
    writable: bool = True,
    readable: bool = True,
    value_type: str = "numeric",
) -> DeviceProperty:
    prop = DeviceProperty(
        device_id=device.id,
        name=name,
        value_type=value_type,
        unit="°C",
        min_value=Decimal("0"),
        max_value=Decimal("35"),
        is_readable=readable,
        is_writable=writable,
    )
    session.add(prop)
    session.flush()
    return prop


def _with(session: Session, device: Device, *codes: str) -> Device:
    for code in codes:
        session.add(
            DeviceCapabilityLink(device_id=device.id, capability_id=capability(session, code).id)
        )
    session.flush()
    return device


def _seed_lookups(session: Session) -> None:
    create_settings(session)
    source(session, "web")
    for code in ("actuator", "controller"):
        role(session, code)
    for code, label in CHANNEL_KINDS:
        session.add(ChannelKind(code=code, label=label))
    for code, label in CONTROLLER_COMMANDS:
        session.add(ControllerCommand(code=code, label=label))
    session.flush()


@dataclass
class World:
    zone: Zone
    ctrl: Device
    sensor: Device


@pytest.fixture
def world(session: Session) -> World:
    _seed_lookups(session)
    zone = create_zone(session, "Wohnen")
    sensor = _with(session, create_device(session, "fuehler"), "temperature")
    ctrl = _with(session, create_device(session, "drehknopf"), "action")
    for name in ("p_fixed", "p_sensor", "p_zone"):
        _prop(session, ctrl, name)
    _prop(session, ctrl, "p_mode", writable=False, value_type="enum")
    _prop(session, ctrl, "p_setpoint", writable=False)
    session.add(
        ZoneDevice(
            zone_id=zone.id, device_id=ctrl.id, device_role_id=role(session, "controller").id
        )
    )
    zone.temperature_source_device_id = sensor.id
    session.flush()
    return World(zone, ctrl, sensor)


def _manage(client_als: Als, zone: Zone) -> TestClient:
    return client_als(
        [
            ("zone.read", zone.id),
            ("device.read", zone.id),
            ("device.manage", zone.id),
            ("zone.manage", zone.id),
        ]
    )


def _channel(world: World, name: str, direction: str, kind: str, **extra: Any) -> dict[str, Any]:
    return {
        "device_id": world.ctrl.id,
        "property_name": name,
        "direction": direction,
        "kind": kind,
        **extra,
    }


def _shown(client: TestClient) -> dict[str, dict[str, Any]]:
    (ctrl,) = client.get("/ui-api/controllers").json()["controllers"]
    return {c["property_name"]: c for c in ctrl["channels"]}


# -- Bediengeräte: Kanäle -------------------------------------------------------------------


def test_a_fixed_number_with_four_decimals_survives_an_unchanged_resubmit(
    client_als: Als, world: World, session: Session
) -> None:
    client = _manage(client_als, world.zone)
    url = "/ui-api/controllers/channel"

    saved = _call(
        client,
        "POST",
        url,
        _channel(world, "p_fixed", "write", "fixed", fixed_number="3,125"),
    )
    assert saved.status_code == 200, saved.text
    session.expire_all()
    stored = session.scalars(select(ControllerChannel)).one()
    assert stored.fixed_number == Decimal("3.125")

    # Ausgeliefert unverrundet: 3,125 wird weder auf 3,1 noch auf 3,13 gekürzt.
    shown = _shown(client)["p_fixed"]["fixed_number"]
    assert Decimal(shown) == Decimal("3.125")

    # Das ausgelieferte Feld unverändert zurückgesendet, ändert den gespeicherten Wert nicht.
    again = _call(
        client, "POST", url, _channel(world, "p_fixed", "write", "fixed", fixed_number=shown)
    )
    assert again.status_code == 200, again.text
    session.expire_all()
    assert session.scalars(select(ControllerChannel)).one().fixed_number == Decimal("3.125")
    assert Decimal(_shown(client)["p_fixed"]["fixed_number"]) == Decimal("3.125")

    # Die vierte Nachkommastelle bleibt ebenfalls erhalten.
    _call(client, "POST", url, _channel(world, "p_fixed", "write", "fixed", fixed_number="3,1257"))
    session.expire_all()
    assert session.scalars(select(ControllerChannel)).one().fixed_number == Decimal("3.1257")


def test_a_saved_channel_shows_every_field_of_its_kind_after_reload(
    client_als: Als, world: World, session: Session
) -> None:
    client = _manage(client_als, world.zone)
    url = "/ui-api/controllers/channel"
    for body in (
        _channel(world, "p_fixed", "write", "fixed", fixed_number="21,5", fixed_text=None),
        _channel(
            world, "p_sensor", "write", "sensor_temperature", source_device_id=world.sensor.id
        ),
        _channel(world, "p_zone", "write", "zone_temperature", zone_id=world.zone.id),
        _channel(world, "p_mode", "read", "operating_mode", zone_id=world.zone.id),
        _channel(world, "p_setpoint", "read", "zone_setpoint", zone_id=world.zone.id),
    ):
        assert _call(client, "POST", url, body).status_code == 200, body

    shown = _shown(client)  # frisch geladen, nicht die Antwort des POST

    fixed = shown["p_fixed"]
    assert (fixed["direction"], fixed["kind"]) == ("write", "fixed")
    assert Decimal(fixed["fixed_number"]) == Decimal("21.5")
    assert fixed["zone_id"] is None and fixed["source_device_id"] is None
    sensor = shown["p_sensor"]
    assert sensor["kind"] == "sensor_temperature"
    assert sensor["source_device_id"] == world.sensor.id
    assert sensor["zone_id"] is None and sensor["fixed_number"] is None
    zone = shown["p_zone"]
    assert zone["kind"] == "zone_temperature" and zone["zone_id"] == world.zone.id
    assert zone["source_device_id"] is None and zone["fixed_number"] is None
    mode = shown["p_mode"]
    assert (mode["direction"], mode["kind"], mode["zone_id"]) == (
        "read",
        "operating_mode",
        world.zone.id,
    )
    setpoint = shown["p_setpoint"]
    assert (setpoint["direction"], setpoint["kind"]) == ("read", "zone_setpoint")
    for channel in shown.values():
        assert channel["fixed_text"] is None


def test_resubmitting_the_shown_channels_unchanged_keeps_the_configuration(
    client_als: Als, world: World, session: Session
) -> None:
    client = _manage(client_als, world.zone)
    url = "/ui-api/controllers/channel"
    for body in (
        _channel(world, "p_mode", "read", "operating_mode", zone_id=world.zone.id),
        _channel(world, "p_zone", "write", "zone_temperature", zone_id=world.zone.id),
        _channel(world, "p_fixed", "write", "fixed", fixed_number="3,125"),
    ):
        assert _call(client, "POST", url, body).status_code == 200

    before = _shown(client)
    rows_before = {
        c.property_name: (c.direction, c.kind_id, c.zone_id, c.source_device_id, c.fixed_number)
        for c in session.scalars(select(ControllerChannel))
    }
    for channel in before.values():
        form = {"device_id": world.ctrl.id, **channel}
        assert _call(client, "POST", url, form).status_code == 200, channel

    assert _shown(client) == before
    session.expire_all()
    rows_after = {
        c.property_name: (c.direction, c.kind_id, c.zone_id, c.source_device_id, c.fixed_number)
        for c in session.scalars(select(ControllerChannel))
    }
    assert rows_after == rows_before
    mode = before["p_mode"]
    assert mode["kind"] == "operating_mode" and mode["zone_id"] == world.zone.id


def test_the_controllers_page_without_any_zone_shows_nothing(
    client_als: Als, session: Session
) -> None:
    _seed_lookups(session)
    # Ein Bediengerät existiert, hängt aber in keiner Zone: nichts davon ist sichtbar.
    ctrl = _with(session, create_device(session, "waise"), "action")
    _prop(session, ctrl, "p_fixed")
    client = client_als([("device.read", None), ("device.manage", None)])

    response = client.get("/ui-api/controllers")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["controllers"] == []
    assert body["zones"] == []
    assert body["source_devices"] == []
    assert "waise" not in response.text


# -- Geräteliste: Felder ---------------------------------------------------------------------


def _device_by_name(client: TestClient) -> dict[str, dict[str, Any]]:
    return {d["name"]: d for d in client.get("/ui-api/devices").json()["devices"]}


def test_the_device_list_carries_model_group_capabilities_battery_and_radio(
    client_als: Als, world: World, session: Session
) -> None:
    thermo = _with(session, create_device(session, "thermostat-1"), "thermostat", "battery")
    thermo.model = "TRV-X1"
    group = create_device(session, "gruppe-1")
    group.is_group = True
    session.add(
        DeviceHealth(
            device_id=thermo.id,
            last_payload_at=utcnow(),
            link_quality=187,
            battery_percent=Decimal("87.50"),
            availability="online",
            payload_count=3,
        )
    )
    session.flush()
    client = client_als([("device.read", None)])

    devices = _device_by_name(client)

    shown = devices["thermostat-1"]
    assert shown["model"] == "TRV-X1" and shown["is_group"] is False
    assert shown["capabilities"] == ["thermostat"]
    assert Decimal(shown["battery_percent"]) == Decimal("87.5")
    assert shown["radio_quality"] == 187
    assert shown["last_heard"] is not None and shown["last_heard"].endswith("+00:00")
    assert devices["gruppe-1"]["is_group"] is True
    assert devices["gruppe-1"]["model"] is None
    assert devices["gruppe-1"]["battery_percent"] is None
    assert devices["gruppe-1"]["radio_quality"] is None
    assert devices["gruppe-1"]["capabilities"] == []


def test_battery_and_radio_are_numbers_not_chips(
    client_als: Als, world: World, session: Session
) -> None:
    only_numbers = _with(session, create_device(session, "nur-zahlen"), "battery", "link_quality")
    none_at_all = create_device(session, "ohne-alles")
    mixed = _with(session, create_device(session, "gemischt"), "temperature", "battery")
    session.flush()
    client = client_als([("device.read", None)])

    devices = _device_by_name(client)

    # Batterie und Funkqualität sind keine Fähigkeit mit Chip, aber auch kein "ohne Fähigkeiten":
    # die Anzahl der unterdrückten steht getrennt daneben.
    assert devices[only_numbers.display_name]["capabilities"] == []
    assert devices[only_numbers.display_name]["quiet_capabilities"] == 2
    assert devices[none_at_all.display_name]["capabilities"] == []
    assert devices[none_at_all.display_name]["quiet_capabilities"] == 0
    assert devices[mixed.display_name]["capabilities"] == ["temperature"]
    assert devices[mixed.display_name]["quiet_capabilities"] == 1


# -- Geräteliste: Meross-Befund -----------------------------------------------------------------


def _findings(device: dict[str, Any]) -> list[str]:
    return [f["text"] for f in device["findings"] if f["kind"] == "silent"]


def test_a_meross_socket_recently_confirmed_online_is_not_flagged(
    client_als: Als, world: World, session: Session
) -> None:
    # Eine Stunde: weit über dem Fühler-Timeout (Standard 15 min), unter der Meross-Schwelle.
    socket = create_device(session, "steckdose-meross", code="meross")
    socket.last_seen_at = utcnow() - timedelta(hours=1)
    # Zum Vergleich: dieselbe Stille bei Zigbee gilt als still.
    zigbee = create_device(session, "steckdose-zigbee")
    session.add(
        DeviceHealth(
            device_id=zigbee.id,
            last_payload_at=utcnow() - timedelta(hours=1),
            availability="online",
            payload_count=1,
        )
    )
    session.flush()
    client = client_als([("device.read", None)])

    devices = _device_by_name(client)

    assert _findings(devices["steckdose-meross"]) == []
    assert devices["steckdose-meross"]["last_heard"] is not None
    assert len(_findings(devices["steckdose-zigbee"])) == 1
    assert "still" in _findings(devices["steckdose-zigbee"])[0]


def test_a_meross_socket_never_reconciled_says_so_without_claiming_a_report(
    client_als: Als, world: World, session: Session
) -> None:
    never = create_device(session, "steckdose-nie", code="meross")
    stale = create_device(session, "steckdose-alt", code="meross")
    stale.last_seen_at = utcnow() - timedelta(hours=5)
    silent_zigbee = create_device(session, "zigbee-nie")
    session.flush()
    client = client_als([("device.read", None)])

    devices = _device_by_name(client)

    assert _findings(devices[never.display_name]) == ["wurde noch nie als online abgeglichen"]
    (old,) = _findings(devices[stale.display_name])
    assert "nicht mehr als online bestätigt" in old and "gemeldet" not in old
    # Gegenprobe: Zigbee ohne jede Meldung heißt weiterhin "hat sich noch nie gemeldet".
    assert _findings(devices[silent_zigbee.display_name]) == ["hat sich noch nie gemeldet"]
