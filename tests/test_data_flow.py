"""Datenfluss einer Zone: Graph, Mermaid-Quelltext und Seite.

Der Graph entscheidet je Betriebsart nur über `domain/thermostat_mode.py`; getestet wird
hier, dass daraus die richtigen Kanten werden -- insbesondere dass ein `reading_only`-Ventil
nie eine Sollwert-Kante bekommt, auch nicht bei vorhandenem Fensterkontakt.
"""

import re
from datetime import datetime
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.helpers import (
    capability,
    create_device,
    create_settings,
    create_zone,
    integration,
    role,
)
from thermoctl.db.models.device import DeviceCapabilityLink, DeviceProperty, ZoneDevice
from thermoctl.domain.data_flow import (
    EDGE_ACTUAL,
    EDGE_FROST,
    EDGE_MISSING,
    EDGE_SETPOINT,
    EDGE_SWITCH,
    NO_SETPOINT_NOTE,
    build_data_flow,
)
from thermoctl.domain.data_flow_mermaid import escape_label, render_mermaid

NOW = datetime(2026, 10, 14, 8, 0)


def _property(session: Session, device_id: int, name: str, *, writable: bool = True) -> None:
    session.add(
        DeviceProperty(
            device_id=device_id,
            name=name,
            value_type="numeric",
            unit="°C",
            min_value=Decimal("5"),
            max_value=Decimal("30"),
            is_readable=True,
            is_writable=writable,
        )
    )
    session.flush()


def _valve(
    session: Session,
    zone,
    name: str,
    mode: str,
    properties: tuple[str, ...] = ("occupied_heating_setpoint", "external_temperature_input"),
):
    device = create_device(session, name)
    session.add(
        DeviceCapabilityLink(
            device_id=device.id, capability_id=capability(session, "thermostat").id
        )
    )
    session.add(
        ZoneDevice(
            zone_id=zone.id,
            device_id=device.id,
            device_role_id=role(session, "actuator").id,
            thermostat_mode=mode,
        )
    )
    for property_name in properties:
        _property(session, device.id, property_name)
    session.flush()
    return device


def _sensor_and_window(session: Session, zone) -> None:
    sensor = create_device(session, "sensor-wohnen")
    zone.temperature_source_device_id = sensor.id
    window = create_device(session, "fenster-wohnen")
    session.add(
        ZoneDevice(
            zone_id=zone.id,
            device_id=window.id,
            device_role_id=role(session, "window_contact").id,
        )
    )
    session.flush()


def _edges_to(graph, target_label: str):
    node = next(n for n in graph.nodes if n.label == target_label)
    return [e for e in graph.edges if e.target == node.id]


def test_reading_only_valve_gets_only_the_actual_value_even_with_a_window_contact(
    session: Session,
) -> None:
    create_settings(session)
    zone = create_zone(session, "nurlesen")
    _sensor_and_window(session, zone)
    _valve(session, zone, "kopf-lesen", "reading_only")

    graph = build_data_flow(session, zone, NOW)

    kinds = {e.kind: e for e in _edges_to(graph, "kopf-lesen")}
    assert set(kinds) == {EDGE_ACTUAL}
    assert kinds[EDGE_ACTUAL].property_name == "external_temperature_input"
    node = next(n for n in graph.nodes if n.label == "kopf-lesen")
    assert NO_SETPOINT_NOTE in (node.detail or "")


def test_self_regulating_valve_gets_setpoint_actual_and_frost_with_a_window(
    session: Session,
) -> None:
    create_settings(session)
    zone = create_zone(session, "selbstregler")
    _sensor_and_window(session, zone)
    _valve(session, zone, "kopf-regelt", "self_regulating")

    graph = build_data_flow(session, zone, NOW)

    kinds = {e.kind: e for e in _edges_to(graph, "kopf-regelt")}
    assert set(kinds) == {EDGE_SETPOINT, EDGE_ACTUAL, EDGE_FROST}
    assert kinds[EDGE_SETPOINT].property_name == "occupied_heating_setpoint"
    assert kinds[EDGE_FROST].property_name == "occupied_heating_setpoint"
    assert graph.node(kinds[EDGE_ACTUAL].source).label == "sensor-wohnen"
    assert graph.node(kinds[EDGE_FROST].source).kind == "zone"


def test_self_regulating_without_a_window_contact_has_no_frost_edge(session: Session) -> None:
    create_settings(session)
    zone = create_zone(session, "ohnefenster")
    _valve(session, zone, "kopf-regelt", "self_regulating")

    graph = build_data_flow(session, zone, NOW)

    assert EDGE_FROST not in {e.kind for e in graph.edges}


def test_controlled_valve_gets_an_on_off_edge_from_the_zone_and_nothing_else(
    session: Session,
) -> None:
    create_settings(session)
    zone = create_zone(session, "geschaltet")
    _sensor_and_window(session, zone)
    _valve(session, zone, "kopf-schaltet", "controlled", properties=("system_mode",))

    graph = build_data_flow(session, zone, NOW)

    (edge,) = _edges_to(graph, "kopf-schaltet")
    assert edge.kind == EDGE_SWITCH
    # Ein: Sollwert der Zone + Modus heat; Aus: Modus off -- wie `thermostat_payload` sendet.
    assert edge.label == "Ein: Sollwert der Zone + Modus heat; Aus: Modus off"
    assert edge.property_name == "occupied_heating_setpoint + system_mode"
    assert graph.node(edge.source).kind == "zone"


def test_controlled_thermostat_without_system_mode_switches_off_via_the_minimum_setpoint(
    session: Session,
) -> None:
    create_settings(session)
    zone = create_zone(session, "ohnemodus")
    _valve(session, zone, "kopf-ohne-modus", "controlled", properties=())

    (edge,) = _edges_to(build_data_flow(session, zone, NOW), "kopf-ohne-modus")

    assert edge.label == "Ein: Sollwert der Zone; Aus: Mindestsollwert 5,0 °C"
    assert edge.property_name == "occupied_heating_setpoint"


def test_controlled_thermostat_on_a_non_zigbee_binding_is_shown_as_missing(
    session: Session,
) -> None:
    create_settings(session)
    zone = create_zone(session, "fremd")
    device = _valve(session, zone, "kopf-meross", "controlled")
    device.integration_id = integration(session, "meross").id
    session.flush()

    (edge,) = _edges_to(build_data_flow(session, zone, NOW), "kopf-meross")

    assert edge.kind == EDGE_MISSING and edge.dashed


def _switch_actuator(session: Session, zone, name: str, integration_code: str):
    device = create_device(session, name, integration_code)
    session.add(
        DeviceCapabilityLink(device_id=device.id, capability_id=capability(session, "switch").id)
    )
    session.add(
        ZoneDevice(
            zone_id=zone.id, device_id=device.id, device_role_id=role(session, "actuator").id
        )
    )
    session.flush()
    return device


def test_a_zigbee_switch_is_written_through_the_state_property(session: Session) -> None:
    create_settings(session)
    zone = create_zone(session, "z2mschalter")
    _switch_actuator(session, zone, "steckdose-z2m", "zigbee2mqtt")

    (edge,) = _edges_to(build_data_flow(session, zone, NOW), "steckdose-z2m")

    assert edge.kind == EDGE_SWITCH and edge.label == "Ein/Aus"
    assert edge.property_name == "state"


def test_a_meross_switch_is_labelled_as_a_meross_command_not_as_a_state_property(
    session: Session,
) -> None:
    create_settings(session)
    zone = create_zone(session, "merossschalter")
    _switch_actuator(session, zone, "steckdose-meross", "meross")

    (edge,) = _edges_to(build_data_flow(session, zone, NOW), "steckdose-meross")

    assert edge.kind == EDGE_SWITCH and "Meross-Schaltbefehl" in edge.label
    assert edge.property_name == "togglex"
    assert not edge.dashed


def test_a_switch_on_an_unwired_binding_is_shown_as_missing(session: Session) -> None:
    create_settings(session)
    zone = create_zone(session, "unverdrahtet")
    _switch_actuator(session, zone, "steckdose-hue", "homekit")

    (edge,) = _edges_to(build_data_flow(session, zone, NOW), "steckdose-hue")

    assert edge.kind == EDGE_MISSING and edge.dashed
    assert "homekit" in edge.label


def test_frost_edge_names_the_window_contact_as_its_source(session: Session) -> None:
    create_settings(session)
    zone = create_zone(session, "kontakt")
    _sensor_and_window(session, zone)
    _valve(session, zone, "kopf-regelt", "self_regulating")

    edges = {e.kind: e for e in _edges_to(build_data_flow(session, zone, NOW), "kopf-regelt")}

    assert "Fensterkontakt" in edges[EDGE_FROST].label


def test_frost_edge_follows_the_temperature_drop_detection_when_there_is_no_contact(
    session: Session,
) -> None:
    create_settings(session)
    zone = create_zone(session, "abfall")
    zone.window_temp_drop_detection_enabled = True
    _valve(session, zone, "kopf-regelt", "self_regulating")
    _valve(session, zone, "kopf-lesen", "reading_only")

    graph = build_data_flow(session, zone, NOW)

    (frost,) = [e for e in _edges_to(graph, "kopf-regelt") if e.kind == EDGE_FROST]
    assert "Temperaturabfall-Erkennung" in frost.label
    assert EDGE_FROST not in {e.kind for e in _edges_to(graph, "kopf-lesen")}


def test_a_contact_wins_over_the_temperature_drop_detection(session: Session) -> None:
    create_settings(session)
    zone = create_zone(session, "beides")
    zone.window_temp_drop_detection_enabled = True
    _sensor_and_window(session, zone)
    _valve(session, zone, "kopf-regelt", "self_regulating")

    (frost,) = [
        e
        for e in _edges_to(build_data_flow(session, zone, NOW), "kopf-regelt")
        if e.kind == EDGE_FROST
    ]

    assert "Fensterkontakt" in frost.label and "Temperaturabfall" not in frost.label


def test_long_edge_labels_are_wrapped_with_the_property_on_its_own_line(
    session: Session,
) -> None:
    create_settings(session)
    zone = create_zone(session, "umbruch")
    _sensor_and_window(session, zone)
    _valve(session, zone, "kopf-lesen", "reading_only")

    source = render_mermaid(build_data_flow(session, zone, NOW))

    assert '|"Istwert<br/>external_temperature_input"|' in source
    _valve(session, zone, "kopf-regelt", "self_regulating")
    wrapped = render_mermaid(build_data_flow(session, zone, NOW))
    frost_line = next(line for line in wrapped.split("\n") if "==>" in line)
    assert "<br/>Frostschutz-Sollwert<br/>occupied_heating_setpoint" in frost_line
    assert all(len(part) <= 31 for part in frost_line.split('|"')[1].split('"|')[0].split("<br/>"))


def test_controlled_actuator_without_any_switching_capability_is_shown_as_missing(
    session: Session,
) -> None:
    create_settings(session)
    zone = create_zone(session, "keinausgang")
    device = create_device(session, "stummer-aktor")
    session.add(
        ZoneDevice(
            zone_id=zone.id, device_id=device.id, device_role_id=role(session, "actuator").id
        )
    )
    session.flush()

    graph = build_data_flow(session, zone, NOW)

    (edge,) = _edges_to(graph, "stummer-aktor")
    assert edge.kind == EDGE_MISSING and edge.dashed


def test_missing_writable_properties_are_shown_dashed_with_the_reason(session: Session) -> None:
    create_settings(session)
    zone = create_zone(session, "ohneeigenschaft")
    _sensor_and_window(session, zone)
    device = _valve(session, zone, "kopf-leer", "self_regulating", properties=())
    # vorhanden, aber nicht beschreibbar
    _property(session, device.id, "external_temperature_input", writable=False)

    graph = build_data_flow(session, zone, NOW)

    edges = _edges_to(graph, "kopf-leer")
    assert {e.kind for e in edges} == {EDGE_MISSING}
    assert all(e.dashed for e in edges)
    labels = " | ".join(e.label for e in edges)
    assert "keine beschreibbare Temperatur-Eigenschaft" in labels
    assert "occupied_heating_setpoint" in labels
    assert "-.->" in render_mermaid(graph)


def test_zone_without_a_measurement_source_shows_the_gap(session: Session) -> None:
    create_settings(session)
    zone = create_zone(session, "blind")
    _valve(session, zone, "kopf-regelt", "self_regulating")

    graph = build_data_flow(session, zone, NOW)

    assert graph.node("sensor").kind == "missing"
    assert not graph.without_devices
    assert all(e.dashed for e in graph.edges if e.source == "sensor"), (
        "ohne Messquelle darf keine Istwert-Kante durchgezogen sein"
    )


def test_zone_without_devices_has_a_graph_with_only_the_fixed_nodes(session: Session) -> None:
    create_settings(session)
    zone = create_zone(session, "leer")

    graph = build_data_flow(session, zone, NOW)

    assert graph.without_devices
    assert {n.id for n in graph.nodes} == {"sensor", "setpoint", "zone"}


def test_the_graph_works_without_the_settings_row(session: Session) -> None:
    zone = create_zone(session, "ohneeinstellung")
    graph = build_data_flow(session, zone, NOW)
    assert graph.node("setpoint").detail is None


def test_mermaid_source_has_one_line_per_node_and_edge(session: Session) -> None:
    create_settings(session)
    zone = create_zone(session, "quelltext")
    _sensor_and_window(session, zone)
    _valve(session, zone, "kopf-regelt", "self_regulating")

    graph = build_data_flow(session, zone, NOW)
    source = render_mermaid(graph)

    assert source.startswith("flowchart LR\n")
    assert len(re.findall(r"^    n\d+[\(\[]", source, re.M)) == len(graph.nodes)
    assert len(re.findall(r"^    n\d+ (?:-->|==>|-\.->)\|", source, re.M)) == len(graph.edges)
    assert "occupied_heating_setpoint" in source


def test_a_hostile_device_name_cannot_leave_its_label(session: Session) -> None:
    create_settings(session)
    zone = create_zone(session, "boese")
    hostile = 'x"]\n    n9["<script>alert(1)</script><img src=x onerror=alert(2)>] %% ; ` {{ }}'
    device = _valve(session, zone, "harmlos", "reading_only")
    device.display_name = hostile
    session.flush()

    source = render_mermaid(build_data_flow(session, zone, NOW))

    assert "<script" not in source and "onerror=alert" not in source.replace("#", "")
    # Nur unsere eigenen festen Zeilenumbrüche im Etikett bleiben.
    assert source.replace("<br/>", "").count("<") == 0
    # Es gibt keine zusätzliche Zeile, die der Name hätte einschleusen können.
    assert "n9[" not in source
    assert source.count("\n") == len(source.strip().split("\n"))
    # Eine Gerätezeile hat genau ein schließendes Anführungszeichen vor dem Zeilenende.
    for line in source.split("\n"):
        if line.strip().startswith("n") and "[" in line.split('"')[0]:
            assert line.count('"') == 2, line


def test_escape_label_reference_encodes_everything_unknown() -> None:
    assert escape_label('a"b') == "a#34;b"
    assert escape_label("a\nb\tc") == "a b c"
    assert escape_label("#") == "#35;"
    assert escape_label("[x]<y>&z") == "#91;x#93;#60;y#62;#38;z"
    assert escape_label("Wohnzimmer (Süd) 20,5 °C") == "Wohnzimmer (Süd) 20,5 °C"


# -- Seite -----------------------------------------------------------------------------


def test_page_shows_the_zone_with_diagram_source_and_edge_list(
    client_als, session: Session
) -> None:
    create_settings(session)
    zone = create_zone(session, "wohnen")
    _sensor_and_window(session, zone)
    _valve(session, zone, "kopf-lesen", "reading_only")
    page = client_als([("zone.manage", zone.id)]).get("/zones/data-flow")

    assert page.status_code == 200
    assert "data-flow-diagram" in page.text and "flowchart LR" in page.text
    assert "kopf-lesen" in page.text and "external_temperature_input" in page.text
    assert "static/data_flow.js" not in page.text  # wird von page_scripts.js nachgeladen
    # Keine Anfrage an Dritte: kein externes Skript, kein fremdes Stylesheet.
    assert not re.findall(r'<(?:script|link)[^>]+(?:src|href)="https?://', page.text)
    assert "cdn." not in page.text


def test_page_escapes_a_hostile_name_in_html(client_als, session: Session) -> None:
    create_settings(session)
    zone = create_zone(session, "htmlboese")
    sensor = create_device(session, "s")
    sensor.display_name = '<script>alert("x")</script>'
    zone.temperature_source_device_id = sensor.id
    session.flush()
    page = client_als([("zone.manage", zone.id)]).get("/zones/data-flow")

    assert page.status_code == 200
    assert "<script>alert" not in page.text


def test_page_lists_only_visible_zones(client_als, session: Session) -> None:
    create_settings(session)
    mine = create_zone(session, "meinraum")
    create_zone(session, "fremderraum")
    page = client_als([("zone.manage", mine.id)]).get("/zones/data-flow")
    assert page.status_code == 200
    assert "Meinraum" in page.text and "Fremderraum" not in page.text


def test_single_zone_page_refuses_a_foreign_zone(client_als, session: Session) -> None:
    create_settings(session)
    mine = create_zone(session, "einzelmein")
    theirs = create_zone(session, "einzelfremd")
    user = client_als([("zone.manage", mine.id)])
    assert user.get(f"/zones/{mine.id}/data-flow").status_code == 200
    refused = user.get(f"/zones/{theirs.id}/data-flow")
    assert refused.status_code == 404
    assert "einzelfremd" not in refused.text.lower()


def test_page_without_the_right_is_empty_like_its_neighbours(client_als, session: Session) -> None:
    create_settings(session)
    zone = create_zone(session, "nurlesen")
    page = client_als([("zone.read", zone.id)]).get("/zones/data-flow")
    assert page.status_code == 200
    assert "Nurlesen" not in page.text and "Noch keine sichtbaren Zonen" in page.text
    single = client_als([("zone.read", zone.id)]).get(f"/zones/{zone.id}/data-flow")
    assert single.status_code == 404


def test_page_is_closed_to_tenants_and_anonymous_visitors(
    client_als, tenant_client, client: TestClient, session: Session
) -> None:
    create_settings(session)
    zone = create_zone(session, "mieterdaten")
    anonymous = client.get("/zones/data-flow", follow_redirects=False)
    assert anonymous.status_code in (303, 401) and "Mieterdaten" not in anonymous.text
    tenant = tenant_client([("zone.manage", zone.id)])
    assert tenant.get("/zones/data-flow").status_code == 403
    assert tenant.get(f"/zones/{zone.id}/data-flow").status_code == 403


def test_static_assets_are_served_locally(client: TestClient) -> None:
    script = client.get("/static/data_flow.js")
    assert script.status_code == 200 and "securityLevel" in script.text
    mermaid = client.get("/static/vendor/mermaid/mermaid.min.js")
    assert mermaid.status_code == 200 and len(mermaid.content) > 1_000_000
    assert "securityLevel" in script.text and 'securityLevel: "strict"' in script.text
    # Der Initialisierer lädt nirgends von einer fremden Adresse.
    assert "http://" not in script.text and "https://" not in script.text


def test_single_zone_page_has_one_heading_and_the_dry_run_note(
    client_als, session: Session
) -> None:
    create_settings(session)
    zone = create_zone(session, "einzeln")
    page = client_als([("zone.manage", zone.id)]).get(f"/zones/{zone.id}/data-flow")

    assert page.status_code == 200
    assert "Datenfluss dieser Zone" not in page.text
    assert "Im Trockenlauf wird nichts gesendet" in page.text
    assert "Notbetrieb-Übergabe ist hier nicht dargestellt" in page.text
    assert "data-flow-edges" in page.text
