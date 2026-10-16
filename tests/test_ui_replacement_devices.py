"""Ersatztests fuer Sicherheitsaussagen der entfernten Alt-Oberflaeche (Geraete/Karte).

Siehe `docs/ui-migration/SICHERHEITSTESTS.md`.
"""

# ruff: noqa: E501, F811

from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.test_ui_api_devices import (  # noqa: F401  (`world` ist eine Fixture)
    Als,
    World,
    _assign,
    _call,
    _fehler,
    _manage,
    _rows,
    _valve,
    world,
)
from thermoctl.db.models.operations import AuditEvent
from thermoctl.domain.mapping_map import zone_port_id
from thermoctl.domain.thermostat_mode import MODE_CONTROLLED, MODE_SELF_REGULATING


def test_the_regulation_mode_cannot_reach_an_assignment_of_another_zone(
    client_als: Als, world: World, session: Session
) -> None:
    own_valve = _valve(session, "ventil-eigen")
    foreign_valve = _valve(session, "ventil-fremd")
    _assign(session, world.a, own_valve, "actuator")
    foreign = _assign(session, world.b, foreign_valve, "actuator")
    client = _manage(client_als, world.a)
    url = f"/ui-api/zones/{world.a.id}/devices/regulation"
    # Die Kennung stammt aus dem Browser und kann alles benennen, auch das Ventil einer fremden Zone.
    _fehler(
        _call(client, "POST", url, {"assignment_id": foreign.id, "thermostat_mode": MODE_SELF_REGULATING}),
        404,
        "not_found",
    )
    _fehler(
        _call(client, "POST", url, {"assignment_id": "keine-zahl", "thermostat_mode": MODE_SELF_REGULATING}),
        422,
        "validation",
    )
    assert _rows(session, world.b, foreign_valve)[0].thermostat_mode == MODE_CONTROLLED
    assert session.scalars(
        select(AuditEvent.id).where(AuditEvent.object_type == "zone_device")
    ).all() == []


def test_a_connect_conflict_does_not_name_a_zone_the_viewer_cannot_see(
    client_als: Als, world: World, session: Session
) -> None:
    valve = _valve(session, "ventil-versteckt")
    _assign(session, world.b, valve, "actuator")  # Bad: fuer den Betrachter unsichtbar
    client = _manage(client_als, world.a)
    refused = _fehler(
        _call(
            client,
            "POST",
            "/ui-api/devices/map/connect",
            {"from_id": zone_port_id(world.a.id, "actuator"), "to_id": f"dev-{valve.id}-thermostat"},
        ),
        422,
        "connection_rejected",
    )
    assert "ist bereits Aktor einer anderen Zone" in refused["detail"]
    assert "Bad" not in refused["detail"]
    assert _rows(session, world.a, valve) == []


def test_disconnect_is_scoped_to_the_managed_zone_is_audited_and_refuses_unknown_lines(
    client_als: Als, world: World, session: Session
) -> None:
    world.b.temperature_source_device_id = world.sensor.id  # derselbe Fuehler in beiden Zonen
    session.flush()
    _assign(session, world.a, world.plug, "actuator")
    url = "/ui-api/devices/map/disconnect"
    mine = f"conn-{world.a.id}-temperature_source-{world.sensor.id}"
    foreign = f"conn-{world.b.id}-temperature_source-{world.sensor.id}"
    manager = _manage(client_als, world.a)

    # Fremde Zone und unbekannte Verbindung: gleiche Auskunft, nichts geschieht.
    _fehler(_call(manager, "POST", url, {"connection_id": foreign}), 404, "not_found")
    unknown = _fehler(_call(manager, "POST", url, {"connection_id": "conn-0-temperature_source-0"}), 404, "not_found")
    assert unknown["detail"] == "Verbindung nicht gefunden."
    session.expire_all()
    assert world.b.temperature_source_device_id == world.sensor.id
    assert world.a.temperature_source_device_id == world.sensor.id

    # Nur lesen genuegt nicht.
    reader = client_als([("device.read", world.a.id), ("zone.read", world.a.id)])
    _fehler(_call(reader, "POST", url, {"connection_id": mine}), 403, "forbidden")
    session.expire_all()
    assert world.a.temperature_source_device_id == world.sensor.id

    # Die eigene Linie loest nur diese Zone; die andere behaelt den Fuehler.
    # (`client_als` liefert denselben Client mit neuem Benutzer: neu anfordern.)
    manager = _manage(client_als, world.a)
    assert _call(manager, "POST", url, {"connection_id": mine}).status_code == 200
    session.expire_all()
    assert world.a.temperature_source_device_id is None
    assert world.b.temperature_source_device_id == world.sensor.id

    # Ein Heizbefehl wird protokolliert geloest.
    assert _call(manager, "POST", url, {"connection_id": f"conn-{world.a.id}-actuator-{world.plug.id}"}).json()["ok"] is True
    assert _rows(session, world.a, world.plug) == []
    assert session.scalar(
        select(AuditEvent.id).where(AuditEvent.action == "unassign", AuditEvent.object_type == "zone_device")
    )


def test_a_heating_command_on_a_plain_switch_is_stored_as_controlled(
    client_als: Als, world: World, session: Session
) -> None:
    client = _manage(client_als, world.a)
    connected = _call(
        client,
        "POST",
        "/ui-api/devices/map/connect",
        {"from_id": zone_port_id(world.a.id, "actuator"), "to_id": f"dev-{world.plug.id}-switch"},
    )
    assert connected.status_code == 200 and connected.json()["mode"] is None
    assert [row.thermostat_mode for row in _rows(session, world.a, world.plug)] == [MODE_CONTROLLED]
    gone = _call(client, "POST", "/ui-api/devices/map/disconnect", {"connection_id": f"conn-{world.a.id}-actuator-{world.plug.id}"})
    assert gone.status_code == 200
    assert _rows(session, world.a, world.plug) == []


def test_connect_needs_the_manage_right_of_that_zone_and_unchanged_otherwise(
    client_als: Als, world: World, session: Session
) -> None:
    world.a.temperature_source_device_id = None
    session.flush()
    out = f"dev-{world.sensor2.id}-temperature"
    reader = client_als([("device.read", world.a.id), ("zone.read", world.a.id)])
    response = _call(reader, "POST", "/ui-api/devices/map/connect", {"from_id": out, "to_id": zone_port_id(world.a.id, "temperature_source")})
    assert response.status_code in (403, 404), response.text
    session.expire_all()
    assert world.a.temperature_source_device_id is None
    other = client_als([("device.read", world.b.id), ("device.manage", world.b.id), ("zone.read", world.b.id)])
    _fehler(_call(other, "POST", "/ui-api/devices/map/connect", {"from_id": out, "to_id": zone_port_id(world.a.id, "temperature_source")}), 404, "not_found")
    session.expire_all()
    assert world.a.temperature_source_device_id is None
