"""Datenfluss einer Zone: welcher Wert von welchem Gerät kommt und wohin er geschrieben wird.

Gebaut wird eine **neutrale Graphstruktur** (`DataFlowGraph`: Knoten und Kanten mit Art und
Beschriftung), unabhängig von jeder Darstellung. `domain/data_flow_mermaid.py` macht daraus
Mermaid-Quelltext; die Seite zeigt außerdem eine Kantenliste als Text, die auch ohne
JavaScript lesbar und testbar ist.

Quellen, die der Code tatsächlich benutzt:

* die **Messquelle** der Zone (`zone.temperature_source_device_id`) -- der Istwert,
* die **Fensterkontakte** (Rolle `window_contact`),
* der **aufgelöste Sollwert** (`schedule.resolved_setpoint`: Zeitplan, Übersteuerung,
  Boost, Urlaub, Frostschutz).

Senken je Aktor entscheidet ausschließlich `domain/thermostat_mode.py` (Grundsatz 6:
keine zweite Wahrheit) -- `switches`, `writes_setpoint`, `writes_actual`. Die Eigenschaftsnamen
kommen aus denselben Konstanten und `device_property`-Zeilen wie beim echten Schreiben
(`domain/self_regulating.py`); ist keine passende beschreibbare Eigenschaft bekannt, steht
das als gestrichelte Kante da und nicht als stilles Fehlen.

Dieses Modul schreibt nichts und sendet nichts.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.db.models.device import Device, DeviceCapabilityLink, DeviceProperty, ZoneDevice
from thermoctl.db.models.lookup import DeviceCapability, DeviceRole, Integration
from thermoctl.db.models.operations import Setting
from thermoctl.db.models.zone import Zone
from thermoctl.domain.number_text import temperature_text
from thermoctl.domain.schedule import resolved_setpoint
from thermoctl.domain.self_regulating import SETPOINT_PROPERTY, TEMPERATURE_PROPERTIES
from thermoctl.domain.thermostat_mode import (
    MODE_LABELS,
    switches,
    writes_actual,
    writes_setpoint,
)
from thermoctl.integrations.actuators import THERMOSTAT_MIN_SETPOINT_C, thermostat_payload
from thermoctl.integrations.meross_mqtt import toggle_payload

# Knotenarten.
NODE_SENSOR = "sensor"
NODE_WINDOW = "window"
NODE_SETPOINT = "setpoint"
NODE_ZONE = "zone"
NODE_ACTUATOR = "actuator"
NODE_MISSING = "missing"

# Kantenarten.
EDGE_ACTUAL = "actual"  # Istwert (gemessene Raumtemperatur)
EDGE_SETPOINT = "setpoint"  # Sollwert
EDGE_SWITCH = "switch"  # Ein/Aus-Befehl
EDGE_WINDOW = "window"  # Fensterzustand
EDGE_FROST = "frost"  # Frostschutz-Sollwert bei offenem Fenster
EDGE_MISSING = "missing"  # fehlende oder nicht beschreibbare Eigenschaft

# Anbindungen, über die `services/publishing.py` Schaltbefehle tatsächlich sendet.
INTEGRATION_ZIGBEE2MQTT = "zigbee2mqtt"
INTEGRATION_MEROSS = "meross"
# Zigbee2MQTT-Schalter (`Zigbee2MqttValve`) schreiben die Eigenschaft `state`.
Z2M_SWITCH_PROPERTY = "state"
# Meross-Schaltbefehle sind ein ToggleX-Befehl, kein Zigbee2MQTT-Eigenschaftsschreiben;
# der Name kommt aus derselben Funktion, die die Nutzlast baut.
MEROSS_SWITCH_PROPERTY = next(iter(toggle_payload(0, True)))

# An einer Stelle gesammelt, damit Graph, Renderer und Test dieselben Wörter benutzen.
NO_SETPOINT_NOTE = "Sollwert bleibt am Gerät"


@dataclass(frozen=True)
class FlowNode:
    id: str
    kind: str
    label: str
    detail: str | None = None
    device_id: int | None = None


@dataclass(frozen=True)
class FlowEdge:
    source: str
    target: str
    kind: str
    label: str
    # Name der Geräteeigenschaft (z. B. `occupied_heating_setpoint`), soweit bekannt.
    property_name: str | None = None
    # Gestrichelt zu zeichnen: etwas fehlt oder ist nicht beschreibbar.
    dashed: bool = False


@dataclass
class DataFlowGraph:
    zone_id: int
    zone_name: str
    nodes: list[FlowNode] = field(default_factory=list)
    edges: list[FlowEdge] = field(default_factory=list)
    # True, wenn die Zone gar keine Geräte hat (weder Messquelle noch Fenster noch Aktor).
    without_devices: bool = False

    def node(self, node_id: str) -> FlowNode:
        return next(node for node in self.nodes if node.id == node_id)

    def edge_text(self, edge: FlowEdge) -> str:
        """Eine Kante als Satz -- die Textfassung für die Seite ohne JavaScript."""
        suffix = " (fehlt)" if edge.dashed else ""
        source, target = self.node(edge.source).label, self.node(edge.target).label
        return f"{source} → {target}: {edge.label}{suffix}"


def _writable_names(session: Session, device_id: int) -> set[str]:
    return set(
        session.scalars(
            select(DeviceProperty.name).where(
                DeviceProperty.device_id == device_id,
                DeviceProperty.is_writable.is_(True),
            )
        )
    )


def _capability_codes(session: Session, device_id: int) -> set[str]:
    return set(
        session.scalars(
            select(DeviceCapability.code)
            .join(DeviceCapabilityLink, DeviceCapabilityLink.capability_id == DeviceCapability.id)
            .where(DeviceCapabilityLink.device_id == device_id)
        )
    )


def _switch_edge(node_id: str, integration_code: str | None) -> FlowEdge:
    """Die Ein/Aus-Kante eines Schalters, je nach Anbindung (`services/publishing.py`)."""
    if integration_code == INTEGRATION_ZIGBEE2MQTT:
        return FlowEdge("zone", node_id, EDGE_SWITCH, "Ein/Aus", property_name=Z2M_SWITCH_PROPERTY)
    if integration_code == INTEGRATION_MEROSS:
        return FlowEdge(
            "zone",
            node_id,
            EDGE_SWITCH,
            "Ein/Aus: Meross-Schaltbefehl",
            property_name=MEROSS_SWITCH_PROPERTY,
        )
    return FlowEdge(
        "zone",
        node_id,
        EDGE_MISSING,
        f"Ein/Aus: Anbindung {integration_code} sendet keine Schaltbefehle",
        dashed=True,
    )


def _thermostat_edge(node_id: str, integration_code: str | None, writable: set[str]) -> FlowEdge:
    """Ein/Aus eines Thermostats im Modus `controlled`: dieselbe Nutzlast wie beim Senden.

    Die Namen kommen aus `thermostat_payload` (`integrations/actuators.py`), nicht aus einer
    zweiten Aufzählung hier.
    """
    if integration_code != INTEGRATION_ZIGBEE2MQTT:
        return FlowEdge(
            "zone",
            node_id,
            EDGE_MISSING,
            f"Ein/Aus: Anbindung {integration_code} sendet keine Thermostat-Befehle",
            dashed=True,
        )
    has_mode = "system_mode" in writable
    on = thermostat_payload(True, Decimal(0), has_system_mode=has_mode)
    off = thermostat_payload(False, Decimal(0), has_system_mode=has_mode)
    on_text = "Sollwert der Zone" + (f" + Modus {on['system_mode']}" if has_mode else "")
    if has_mode:
        off_text = f"Modus {off['system_mode']}"
    else:
        off_text = f"Mindestsollwert {temperature_text(THERMOSTAT_MIN_SETPOINT_C)}"
    names = list(dict.fromkeys([*on, *off]))
    return FlowEdge(
        "zone",
        node_id,
        EDGE_SWITCH,
        f"Ein: {on_text}; Aus: {off_text}",
        property_name=" + ".join(names),
    )


def _device_label(device: Device) -> str:
    return device.display_name if device.is_enabled else f"{device.display_name} (abgeschaltet)"


def build_data_flow(session: Session, zone: Zone, now: datetime) -> DataFlowGraph:
    """Der Datenfluss einer Zone als neutraler Graph."""
    graph = DataFlowGraph(zone_id=zone.id, zone_name=zone.display_name)
    nodes, edges = graph.nodes, graph.edges

    # -- Quellen --------------------------------------------------------------------
    source_device = (
        session.get(Device, zone.temperature_source_device_id)
        if zone.temperature_source_device_id is not None
        else None
    )
    if source_device is not None:
        nodes.append(
            FlowNode(
                "sensor",
                NODE_SENSOR,
                _device_label(source_device),
                "Messquelle (Raumtemperatur)",
                source_device.id,
            )
        )
    else:
        nodes.append(FlowNode("sensor", NODE_MISSING, "Keine Messquelle"))

    detail = None
    if session.get(Setting, 1) is not None:
        resolved = resolved_setpoint(session, zone, now)
        detail = f"{temperature_text(resolved.temperature_c)}: {resolved.reason}"
    nodes.append(
        FlowNode(
            "setpoint",
            NODE_SETPOINT,
            "Sollwert (Zeitplan, Übersteuerung, Boost, Urlaub)",
            detail,
        )
    )

    window_role = session.scalar(select(DeviceRole).where(DeviceRole.code == "window_contact"))
    actuator_role = session.scalar(select(DeviceRole).where(DeviceRole.code == "actuator"))
    windows: list[Device] = []
    if window_role is not None:
        windows = list(
            session.scalars(
                select(Device)
                .join(ZoneDevice, ZoneDevice.device_id == Device.id)
                .where(ZoneDevice.zone_id == zone.id, ZoneDevice.device_role_id == window_role.id)
                .order_by(ZoneDevice.sort_order, ZoneDevice.id)
            )
        )
    actuators: list[tuple[Device, str]] = []
    if actuator_role is not None:
        actuators = [
            (device, mode)
            for device, mode in session.execute(
                select(Device, ZoneDevice.thermostat_mode)
                .join(ZoneDevice, ZoneDevice.device_id == Device.id)
                .where(ZoneDevice.zone_id == zone.id, ZoneDevice.device_role_id == actuator_role.id)
                .order_by(ZoneDevice.sort_order, ZoneDevice.id)
            )
        ]

    graph.without_devices = source_device is None and not windows and not actuators

    nodes.append(FlowNode("zone", NODE_ZONE, f"Zone {zone.display_name}", "Regelentscheidung"))
    if source_device is not None:
        edges.append(FlowEdge("sensor", "zone", EDGE_ACTUAL, "Istwert (Raumtemperatur)"))
    else:
        edges.append(
            FlowEdge("sensor", "zone", EDGE_MISSING, "ohne Messquelle kein Istwert", dashed=True)
        )
    edges.append(FlowEdge("setpoint", "zone", EDGE_SETPOINT, "Sollwert"))
    for index, window in enumerate(windows):
        node_id = f"window{index}"
        nodes.append(
            FlowNode(node_id, NODE_WINDOW, _device_label(window), "Fensterkontakt", window.id)
        )
        edges.append(FlowEdge(node_id, "zone", EDGE_WINDOW, "Fenster offen/zu"))

    # Woher "Fenster offen" kommt, entscheidet `services/ingest.py::_window_open`: ein
    # zugeordneter Kontakt allein; nur ohne Kontakt und bei eingeschalteter Zonen-Option die
    # Temperaturabfall-Erkennung. `valve_commands` liest in beiden Fällen `ZoneState.window_open`.
    frost_source: str | None = None
    if windows:
        frost_source = "Fensterkontakt"
    elif zone.window_temp_drop_detection_enabled:
        frost_source = "Temperaturabfall-Erkennung"

    # -- Senken je Aktor, entschieden von `thermostat_mode` ---------------------------
    for index, (device, mode) in enumerate(actuators):
        node_id = f"actuator{index}"
        mode_label = MODE_LABELS.get(mode, mode)
        keeps_setpoint = not writes_setpoint(mode) and not switches(mode)
        detail_text = f"{mode_label} — {NO_SETPOINT_NOTE}" if keeps_setpoint else mode_label
        nodes.append(
            FlowNode(node_id, NODE_ACTUATOR, _device_label(device), detail_text, device.id)
        )
        writable = _writable_names(session, device.id)

        if switches(mode):
            codes = _capability_codes(session, device.id)
            integration = session.get(Integration, device.integration_id)
            integration_code = integration.code if integration is not None else None
            if "switch" in codes:
                edges.append(_switch_edge(node_id, integration_code))
            elif "thermostat" in codes:
                edges.append(_thermostat_edge(node_id, integration_code, writable))
            else:
                edges.append(
                    FlowEdge(
                        "zone",
                        node_id,
                        EDGE_MISSING,
                        "Ein/Aus: keine Schaltfähigkeit (switch/thermostat) erfasst",
                        dashed=True,
                    )
                )

        if writes_setpoint(mode):
            if SETPOINT_PROPERTY in writable:
                edges.append(
                    FlowEdge(
                        "setpoint",
                        node_id,
                        EDGE_SETPOINT,
                        "Sollwert",
                        property_name=SETPOINT_PROPERTY,
                    )
                )
                if frost_source is not None:
                    edges.append(
                        FlowEdge(
                            "zone",
                            node_id,
                            EDGE_FROST,
                            f"Fenster offen ({frost_source}): Frostschutz-Sollwert",
                            property_name=SETPOINT_PROPERTY,
                        )
                    )
            else:
                edges.append(
                    FlowEdge(
                        "setpoint",
                        node_id,
                        EDGE_MISSING,
                        f"keine beschreibbare Sollwert-Eigenschaft ({SETPOINT_PROPERTY})",
                        dashed=True,
                    )
                )

        if writes_actual(mode):
            temperature_property = next(
                (name for name in TEMPERATURE_PROPERTIES if name in writable), None
            )
            if source_device is None:
                edges.append(
                    FlowEdge(
                        "sensor",
                        node_id,
                        EDGE_MISSING,
                        "ohne Messquelle kein Istwert zum Schreiben",
                        dashed=True,
                    )
                )
            elif temperature_property is not None:
                edges.append(
                    FlowEdge(
                        "sensor",
                        node_id,
                        EDGE_ACTUAL,
                        "Istwert",
                        property_name=temperature_property,
                    )
                )
            else:
                edges.append(
                    FlowEdge(
                        "sensor",
                        node_id,
                        EDGE_MISSING,
                        "keine beschreibbare Temperatur-Eigenschaft",
                        dashed=True,
                    )
                )
    return graph
