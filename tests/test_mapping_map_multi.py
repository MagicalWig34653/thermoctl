"""Zuordnungskarte: Mehrfachzuordnung (Matrix a. bis h.) und Entfernen von Verbindungen.

Jede Zeile der Kombinationsmatrix hat hier ihren Test mit dem Ergebnis (erlaubt / abgelehnt samt
Begründung). Wo behauptet wird, die Regelkette vertrage eine Mehrfachzuordnung, läuft der Test durch
`advance_zone_state`, nicht nur durch die Karte.
"""
# ruff: noqa: E501

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.helpers import (
    capability,
    create_device,
    create_settings,
    create_zone,
    role,
    sensor_status_of,
    source,
)
from thermoctl.auth.csrf import CSRF_HEADER, csrf_token
from thermoctl.auth.sessions import COOKIE_NAME
from thermoctl.config import get_settings
from thermoctl.db.models.device import Device, DeviceCapabilityLink, DeviceProperty, ZoneDevice
from thermoctl.db.models.measurement import Measurement
from thermoctl.db.models.operations import AuditEvent
from thermoctl.db.models.state import ZoneState
from thermoctl.db.models.zone import Zone
from thermoctl.domain.mapping_map import (
    MappingMap,
    Port,
    build_mapping_map,
    can_connect,
    zone_port_id,
)
from thermoctl.services.ingest import advance_zone_state
from thermoctl.web.mapping_map_views import _payload

ClientAls = Callable[[list[tuple[str, int | None]]], TestClient]
JETZT = datetime(2026, 8, 29, 7, 0)


def _caps(session: Session, device: Device, *codes: str) -> Device:
    for code in codes:
        session.add(DeviceCapabilityLink(device_id=device.id, capability_id=capability(session, code).id))
    session.flush()
    return device


def _sensor(session: Session, name: str) -> Device:
    return _caps(session, create_device(session, name), "temperature", "humidity", "battery")


def _valve(session: Session, name: str) -> Device:
    return _caps(session, create_device(session, name), "thermostat")


def _csrf(client: TestClient) -> dict[str, str]:
    cookie = client.cookies.get(COOKIE_NAME)
    assert cookie is not None
    return {CSRF_HEADER: csrf_token(cookie, get_settings().secret_key.get_secret_value())}


def _connect(client: TestClient, first: str, second: str) -> Any:
    return client.post("/devices/map/connect", headers=_csrf(client), data={"from_id": first, "to_id": second})


def _disconnect(client: TestClient, connection_id: str) -> Any:
    return client.post("/devices/map/disconnect", headers=_csrf(client), data={"connection_id": connection_id})


def _graph(session: Session, *zones: Zone) -> MappingMap:
    return build_mapping_map(session, list(zones), {z.id for z in zones})


def _ports(graph: MappingMap) -> dict[str, Port]:
    return {p.id: p for p in graph.ports()}


# ---- a. Ein Fühler, mehrere Zonen -----------------------------------------------------------------


def test_a_one_sensor_is_the_temperature_source_of_two_zones_and_both_zones_read_it(
    client_als: ClientAls, session: Session
) -> None:
    source(session)
    create_settings(session)
    for code in ("ok", "veraltet", "keine_quelle"):
        sensor_status_of(session, code)
    wohnen, essen = create_zone(session, "wohnen"), create_zone(session, "essen")
    sensor = _sensor(session, "grundriss-fuehler")
    client = client_als([("device.manage", wohnen.id), ("device.manage", essen.id), ("device.read", wohnen.id), ("device.read", essen.id)])
    out = f"dev-{sensor.id}-temperature"
    assert _connect(client, out, zone_port_id(wohnen.id, "temperature_source")).status_code == 200
    second = _connect(client, out, zone_port_id(essen.id, "temperature_source"))
    assert second.status_code == 200 and second.json() == {"ok": True}
    session.refresh(wohnen)
    session.refresh(essen)
    assert wohnen.temperature_source_device_id == essen.temperature_source_device_id == sensor.id

    # Beide Linien beginnen am selben Ausgang, die Tabelle führt jede einzeln.
    graph = _graph(session, wohnen, essen)
    assert sorted(c.zone_id for c in graph.connections if c.out_port_id == out) == sorted([wohnen.id, essen.id])
    assert len({c.id for c in graph.connections}) == 2
    page = client.get("/devices/map").text
    assert page.count("data-connection-row=") == 2

    # Die Regelkette liest den Messwert für beide Zonen (`advance_zone_state` hängt je Zone am Gerät).
    session.add(Measurement(device_id=sensor.id, capability_id=capability(session, "temperature").id,
                            value_numeric=Decimal("21.50"), measured_at=JETZT, received_at=JETZT))
    session.flush()
    advance_zone_state(session, JETZT)
    for zone in (wohnen, essen):
        state = session.get(ZoneState, zone.id)
        assert state is not None and state.temperature_c == Decimal("21.50")


def test_a_the_sensor_stays_with_the_other_zone_when_one_line_is_removed(client_als: ClientAls, session: Session) -> None:
    source(session)
    wohnen, essen = create_zone(session, "wohnen2"), create_zone(session, "essen2")
    sensor = _sensor(session, "grundriss-fuehler2")
    wohnen.temperature_source_device_id = essen.temperature_source_device_id = sensor.id
    session.flush()
    client = client_als([("device.manage", wohnen.id), ("device.manage", essen.id)])
    done = _disconnect(client, f"conn-{wohnen.id}-temperature_source-{sensor.id}")
    assert done.status_code == 200
    session.refresh(wohnen)
    session.refresh(essen)
    assert wohnen.temperature_source_device_id is None and essen.temperature_source_device_id == sensor.id


# ---- b./c. Kontakt in mehreren Zonen, mehrere Kontakte und Aktoren je Zone ---------------------


def test_b_one_contact_is_the_window_contact_of_two_zones_and_both_zones_see_it_open(
    client_als: ClientAls, session: Session
) -> None:
    source(session)
    create_settings(session)
    sensor_status_of(session, "keine_quelle")
    role(session, "window_contact")
    a, b = create_zone(session, "tuer-a"), create_zone(session, "tuer-b")
    door = _caps(session, create_device(session, "durchgang"), "contact")
    client = client_als([("device.manage", a.id), ("device.manage", b.id)])
    for zone in (a, b):
        assert _connect(client, f"dev-{door.id}-contact", zone_port_id(zone.id, "window_contact")).status_code == 200
    rows = session.scalars(select(ZoneDevice).where(ZoneDevice.device_id == door.id)).all()
    assert sorted(r.zone_id for r in rows) == sorted([a.id, b.id])
    # Zigbee-Kontakt `false` heißt offen.
    session.add(Measurement(device_id=door.id, capability_id=capability(session, "contact").id,
                            value_text="false", measured_at=JETZT, received_at=JETZT))
    session.flush()
    advance_zone_state(session, JETZT)
    for zone in (a, b):
        state = session.get(ZoneState, zone.id)
        assert state is not None and state.window_open is True


def test_c_one_zone_takes_several_window_contacts_and_several_actuators(client_als: ClientAls, session: Session) -> None:
    source(session)
    role(session, "window_contact")
    role(session, "actuator")
    zone = create_zone(session, "viele")
    contacts = [_caps(session, create_device(session, f"kontakt-{i}"), "contact") for i in range(2)]
    valves = [_valve(session, f"ventil-{i}") for i in range(2)]
    client = client_als([("device.manage", zone.id)])
    for contact in contacts:
        assert _connect(client, f"dev-{contact.id}-contact", zone_port_id(zone.id, "window_contact")).status_code == 200
    for valve in valves:
        assert _connect(client, zone_port_id(zone.id, "actuator"), f"dev-{valve.id}-thermostat").status_code == 200
    assert len(session.scalars(select(ZoneDevice).where(ZoneDevice.zone_id == zone.id)).all()) == 4


# ---- d. Ein Aktor, eine Zone ----------------------------------------------------------------------


def test_d_a_device_that_is_an_actuator_elsewhere_takes_no_second_heating_command(
    client_als: ClientAls, session: Session
) -> None:
    source(session)
    role(session, "actuator")
    a, b = create_zone(session, "bad"), create_zone(session, "flur")
    valve = _valve(session, "gemeinsames-ventil")
    client = client_als([("device.manage", a.id), ("device.manage", b.id), ("device.read", a.id), ("device.read", b.id)])
    assert _connect(client, zone_port_id(a.id, "actuator"), f"dev-{valve.id}-thermostat").status_code == 200
    refused = _connect(client, zone_port_id(b.id, "actuator"), f"dev-{valve.id}-thermostat")
    assert refused.status_code == 422
    assert refused.json()["reason"] == "'gemeinsames-ventil' ist bereits Aktor von Zone 'Bad'; ein Gerät kann nur eine Zone heizen."
    # Auch von der anderen Ziehrichtung aus, und nichts wurde gespeichert.
    again = _connect(client, f"dev-{valve.id}-thermostat", zone_port_id(b.id, "actuator"))
    assert again.status_code == 422 and "nur eine Zone heizen" in again.json()["reason"]
    assert [r.zone_id for r in session.scalars(select(ZoneDevice).where(ZoneDevice.device_id == valve.id))] == [a.id]
    # Die Seite kündigt es schon vor dem Ziehen an: die Ablehnung steht in der Begründungstabelle.
    graph = _graph(session, a, b)
    reason = graph.reasons[graph.rejections[zone_port_id(b.id, "actuator")][f"dev-{valve.id}-thermostat"]]
    assert "bereits Aktor" in reason
    # In der ersten Zone selbst bleibt es bei der bekannten Meldung für die Doppelzuordnung.
    same = _connect(client, zone_port_id(a.id, "actuator"), f"dev-{valve.id}-thermostat")
    assert same.status_code == 422 and "bereits verbunden" in same.json()["reason"]


def test_d_the_lock_follows_the_assignment_not_a_stale_page(client_als: ClientAls, session: Session) -> None:
    source(session)
    role(session, "actuator")
    a, b = create_zone(session, "bad2"), create_zone(session, "flur2")
    valve = _valve(session, "wandertes-ventil")
    client = client_als([("device.manage", a.id), ("device.manage", b.id)])
    assert _connect(client, zone_port_id(a.id, "actuator"), f"dev-{valve.id}-thermostat").status_code == 200
    assert _disconnect(client, f"conn-{a.id}-actuator-{valve.id}").status_code == 200
    assert _connect(client, zone_port_id(b.id, "actuator"), f"dev-{valve.id}-thermostat").status_code == 200


def test_d_a_zone_the_viewer_cannot_see_still_locks_but_stays_unnamed(client_als: ClientAls, session: Session) -> None:
    source(session)
    role(session, "actuator")
    mine, hidden = create_zone(session, "sichtbar"), create_zone(session, "versteckt")
    valve = _valve(session, "ventil-versteckt")
    session.add(ZoneDevice(zone_id=hidden.id, device_id=valve.id, device_role_id=role(session, "actuator").id))
    session.flush()
    client = client_als([("device.manage", mine.id)])
    refused = _connect(client, zone_port_id(mine.id, "actuator"), f"dev-{valve.id}-thermostat")
    assert refused.status_code == 422
    assert refused.json()["reason"] == "'ventil-versteckt' ist bereits Aktor einer anderen Zone; ein Gerät kann nur eine Zone heizen."
    assert "Versteckt" not in refused.json()["reason"]


def test_d_a_switch_is_locked_like_a_thermostat(session: Session) -> None:
    role(session, "actuator")
    a, b = create_zone(session, "s-a"), create_zone(session, "s-b")
    plug = _caps(session, create_device(session, "steckdose"), "switch")
    session.add(ZoneDevice(zone_id=a.id, device_id=plug.id, device_role_id=role(session, "actuator").id))
    session.flush()
    ports = _ports(_graph(session, a, b))
    verdict = can_connect(ports[zone_port_id(b.id, "actuator")], ports[f"dev-{plug.id}-switch"], [])
    assert not verdict.ok and verdict.reason is not None and "nur eine Zone heizen" in verdict.reason


# ---- e. Mehrere Fühler, eine Messquelle -----------------------------------------------------------


def test_e_two_sensors_never_feed_one_temperature_source(client_als: ClientAls, session: Session) -> None:
    source(session)
    zone = create_zone(session, "einzelquelle")
    first, second = _sensor(session, "fuehler-eins"), _sensor(session, "fuehler-zwei")
    client = client_als([("device.manage", zone.id)])
    target = zone_port_id(zone.id, "temperature_source")
    assert _connect(client, f"dev-{first.id}-temperature", target).status_code == 200
    refused = _connect(client, f"dev-{second.id}-temperature", target)
    assert refused.status_code == 422 and "nimmt nur eine Verbindung an" in refused.json()["reason"]
    session.refresh(zone)
    assert zone.temperature_source_device_id == first.id


# ---- f. Gerät → Gerät: nur Entwurf; die Regeln gelten trotzdem -------------------------------------


def test_f_device_to_device_fan_out_is_judged_fit_but_never_stored(client_als: ClientAls, session: Session) -> None:
    source(session)
    zone = create_zone(session, "entwurf")
    sensor = _sensor(session, "entwurfsfuehler")
    shows = []
    for index in range(2):
        display = create_device(session, f"anzeige-{index}")
        session.add(ZoneDevice(zone_id=zone.id, device_id=display.id, device_role_id=role(session, "controller").id))
        session.add(DeviceProperty(device_id=display.id, name="ext", value_type="numeric", unit="°C", is_readable=True, is_writable=True))
        shows.append(display)
    session.flush()
    graph = _graph(session, zone)
    ports = _ports(graph)
    out = ports[f"dev-{sensor.id}-temperature"]
    for display in shows:  # ein Ausgang, mehrere Eingänge gleicher Wertart: zulässig
        assert can_connect(out, ports[f"dev-{display.id}-prop-ext"], graph.connections).ok
        assert f"dev-{display.id}-prop-ext" not in graph.rejections[out.id]
    client = client_als([("device.manage", zone.id)])
    stored = _connect(client, out.id, f"dev-{shows[0].id}-prop-ext")
    assert stored.status_code == 422 and "nicht gespeichert" in stored.json()["reason"]
    # Mehrere Ausgänge auf einen Eingang: der Eingang ist als Einzelverbindung markiert, die Seite
    # lehnt die zweite Linie mit dieser Begründung ab (`payload.single`, Browsertest).
    assert ports[f"dev-{shows[0].id}-prop-ext"].single
    assert 'Dieser Eingang nimmt nur eine Verbindung an.' in client.get("/devices/map").text


# ---- g. Werte ohne Zonen-Eingang -----------------------------------------------------------------


def test_g_humidity_presence_and_unknown_values_have_no_zone_target(session: Session) -> None:
    zone = create_zone(session, "ohne-ziel")
    sensor = _caps(session, create_device(session, "vielfuehler"), "temperature", "humidity", "occupancy", "illuminance", "battery")
    graph = _graph(session, zone)
    ports = _ports(graph)
    zone_ports = [p for p in ports.values() if p.node_kind == "zone"]
    assert {p.slot for p in zone_ports} == {"temperature_source", "window_contact", "actuator"}
    for code in ("humidity", "occupancy", "illuminance"):
        port = ports[f"dev-{sensor.id}-{code}"]
        for target in zone_ports:
            assert not can_connect(port, target, []).ok, (code, target.slot)
    # Keine Zonenstelle nimmt Luftfeuchte oder Anwesenheit an: Nur Temperatur, Kontakt, Heizbefehl.
    assert {p.kind for p in zone_ports} == {"temperature", "contact", "command"}


def test_g_humidity_to_a_device_property_is_only_a_draft(client_als: ClientAls, session: Session) -> None:
    source(session)
    zone = create_zone(session, "feuchte")
    sensor = _sensor(session, "feuchtefuehler")
    display = create_device(session, "feuchteanzeige")
    session.add(ZoneDevice(zone_id=zone.id, device_id=display.id, device_role_id=role(session, "controller").id))
    session.add(DeviceProperty(device_id=display.id, name="rh", value_type="numeric", unit="%", is_readable=True, is_writable=True))
    session.flush()
    client = client_als([("device.manage", zone.id)])
    refused = _connect(client, f"dev-{sensor.id}-humidity", f"dev-{display.id}-prop-rh")
    assert refused.status_code == 422 and "nicht gespeichert" in refused.json()["reason"]


# ---- h. Gruppen und Bediengeräte ------------------------------------------------------------------


def test_h_a_group_is_locked_like_any_other_device_and_a_controller_may_sit_in_several_zones(
    client_als: ClientAls, session: Session
) -> None:
    source(session)
    role(session, "actuator")
    controller_role = role(session, "controller")
    a, b = create_zone(session, "gruppe-a"), create_zone(session, "gruppe-b")
    group = _valve(session, "ventilgruppe")
    group.is_group = True
    knob = create_device(session, "drehknopf")
    for zone in (a, b):
        session.add(ZoneDevice(zone_id=zone.id, device_id=knob.id, device_role_id=controller_role.id))
    session.flush()
    client = client_als([("device.manage", a.id), ("device.manage", b.id)])
    assert _connect(client, zone_port_id(a.id, "actuator"), f"dev-{group.id}-thermostat").status_code == 200
    refused = _connect(client, zone_port_id(b.id, "actuator"), f"dev-{group.id}-thermostat")
    assert refused.status_code == 422 and "nur eine Zone heizen" in refused.json()["reason"]
    # Das Bediengerät in zwei Zonen ist kein Karten-Thema: keine Verbindung, keine Linie.
    graph = _graph(session, a, b)
    assert all(c.device_id != knob.id for c in graph.connections)


# ---- Entfernen -----------------------------------------------------------------------------------


def test_disconnect_of_an_actuator_is_audited_and_says_the_device_keeps_its_last_state(
    client_als: ClientAls, session: Session
) -> None:
    source(session)
    role(session, "actuator")
    zone = create_zone(session, "entfernen")
    valve = _valve(session, "ventil-weg")
    session.add(ZoneDevice(zone_id=zone.id, device_id=valve.id, device_role_id=role(session, "actuator").id))
    session.flush()
    client = client_als([("device.manage", zone.id)])
    done = _disconnect(client, f"conn-{zone.id}-actuator-{valve.id}")
    assert done.status_code == 200 and done.json()["ok"] is True
    assert "behält seinen letzten Zustand" in done.json()["message"] and "ventil-weg" in done.json()["message"]
    assert session.scalar(select(ZoneDevice.id).where(ZoneDevice.zone_id == zone.id)) is None
    assert session.scalar(select(AuditEvent.id).where(AuditEvent.action == "unassign", AuditEvent.object_type == "zone_device"))


def test_disconnect_of_a_temperature_source_needs_no_hint_and_clears_only_that_zone(client_als: ClientAls, session: Session) -> None:
    source(session)
    zone, other = create_zone(session, "quelle-weg"), create_zone(session, "quelle-bleibt")
    sensor = _sensor(session, "fuehler-weg")
    zone.temperature_source_device_id = other.temperature_source_device_id = sensor.id
    session.flush()
    client = client_als([("device.manage", zone.id)])
    done = _disconnect(client, f"conn-{zone.id}-temperature_source-{sensor.id}")
    assert done.status_code == 200 and "letzten Zustand" not in done.json()["message"]
    session.refresh(zone)
    session.refresh(other)
    assert zone.temperature_source_device_id is None and other.temperature_source_device_id == sensor.id


def test_disconnect_of_one_of_two_zones_for_a_window_contact_leaves_the_other(client_als: ClientAls, session: Session) -> None:
    source(session)
    window = role(session, "window_contact")
    a, b = create_zone(session, "fenster-a"), create_zone(session, "fenster-b")
    contact = _caps(session, create_device(session, "durchgang2"), "contact")
    for zone in (a, b):
        session.add(ZoneDevice(zone_id=zone.id, device_id=contact.id, device_role_id=window.id))
    session.flush()
    client = client_als([("device.manage", a.id)])
    assert _disconnect(client, f"conn-{a.id}-window_contact-{contact.id}").status_code == 200
    assert [r.zone_id for r in session.scalars(select(ZoneDevice).where(ZoneDevice.device_id == contact.id))] == [b.id]


def test_disconnect_needs_the_manage_right_of_that_zone_a_token_and_a_known_connection(client_als: ClientAls, session: Session) -> None:
    source(session)
    mine, foreign = create_zone(session, "recht-meine"), create_zone(session, "recht-fremde")
    sensor = _sensor(session, "fuehler-recht")
    mine.temperature_source_device_id = foreign.temperature_source_device_id = sensor.id
    session.flush()
    read_only: list[tuple[str, int | None]] = [("device.read", mine.id), ("device.read", foreign.id)]
    mine_id = f"conn-{mine.id}-temperature_source-{sensor.id}"
    foreign_id = f"conn-{foreign.id}-temperature_source-{sensor.id}"
    # Nur lesen genügt nicht; eine fremde Zone ist für den Verwalter einer anderen „nicht gefunden".
    # (`client_als` liefert denselben Client mit neuem Benutzer: jeweils unmittelbar vor dem Aufruf anfordern.)
    assert _disconnect(client_als(read_only), mine_id).status_code == 404
    manager = client_als([("device.manage", mine.id)])
    assert _disconnect(manager, foreign_id).status_code == 404
    unknown = _disconnect(manager, "conn-0-temperature_source-0")
    assert unknown.status_code == 404 and unknown.json() == {"ok": False, "reason": "Verbindung nicht gefunden."}
    # Ohne CSRF-Kopfzeile geschieht nichts.
    assert manager.post("/devices/map/disconnect", data={"connection_id": mine_id}).status_code in (400, 403)
    session.refresh(mine)
    session.refresh(foreign)
    assert mine.temperature_source_device_id == sensor.id and foreign.temperature_source_device_id == sensor.id
    assert _disconnect(manager, mine_id).status_code == 200


def test_the_payload_names_every_line_in_the_direction_of_the_data_flow(session: Session) -> None:
    role(session, "actuator")
    zone = create_zone(session, "titel")
    sensor, valve = _sensor(session, "titelfuehler"), _valve(session, "titelventil")
    zone.temperature_source_device_id = sensor.id
    session.add(ZoneDevice(zone_id=zone.id, device_id=valve.id, device_role_id=role(session, "actuator").id))
    session.flush()
    connections = _payload(_graph(session, zone))["connections"]
    assert isinstance(connections, list)
    assert sorted(c["title"] for c in connections) == ["Titel: Heizbefehl → titelventil", "titelfuehler → Titel: Messquelle"]
