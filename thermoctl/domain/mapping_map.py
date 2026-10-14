# ruff: noqa: E501
"""Zuordnungskarte (Entwurf): Knoten mit Anschlüssen und die Regeln, wer mit wem verbunden werden darf.

Ein Wert fließt immer von einem **Ausgang** (liefert einen Wert) zu einem **Eingang** (nimmt einen
Wert oder Befehl an). Die Karte kennt zwei Arten von Knoten:

* **Gerät** -- Ausgänge: was es misst oder meldet (Temperatur, Luftfeuchte, Kontakt, Anwesenheit);
  Eingänge: was es annimmt (Schaltbefehle, Thermostat-Sollwert, beschreibbare Eigenschaften).
* **Zone** -- Eingänge: Messquelle (Temperatur) und Fensterkontakt; Ausgang: der Heizbefehl.

Daraus folgt die Richtung der gespeicherten Zuordnungen: Messwerte laufen **Gerät → Zone**
(Messquelle, Fensterkontakt), der Heizbefehl läuft **Zone → Gerät** (Aktor). Ein Schaltaktor ist
darum nie eine Quelle, die man auf eine Zonenstelle „Aktor" zieht -- die Zone speist ihn.

* **Wertart** (`Port.kind`) -- was fließt: Temperatur, Luftfeuchte, Kontakt, Anwesenheit, Schaltbefehl.
* **Verbindungen** (`Connection`) -- was in der Datenbank tatsächlich steht: die Rollenzuordnungen
  (`ZoneDevice`) und die Messquelle einer Zone. Zwischen zwei Geräten ist nur ein Entwurf möglich.

`can_connect` ist die **einzige** Stelle, die entscheidet, ob zwei Anschlüsse zusammenpassen. Die
Seite bekommt das Ergebnis für jedes Paar fertig mitgeliefert und rechnet nichts nach -- eine zweite
Fassung der Regeln in JavaScript hat in `assignment.js` schon einmal veraltet. Der schreibende
Endpunkt prüft vor dem Speichern erneut mit derselben Funktion.

Was gespeichert werden kann, entscheidet weiterhin `domain/device_assignment.py`
(`assign_device`, `set_temperature_source`, `REQUIRED_CAPABILITY`); diese Datei ergänzt nur die
feinere, anschlussgenaue Vorprüfung. Dieses Modul schreibt selbst nichts.
"""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.db.models.device import Device, DeviceCapabilityLink, DeviceProperty, ZoneDevice
from thermoctl.db.models.lookup import DeviceCapability, DeviceRole
from thermoctl.db.models.zone import Zone
from thermoctl.domain.controller_channels import may_be_written
from thermoctl.domain.device_assignment import REQUIRED_CAPABILITY, TEMPERATURE_SOURCE

Direction = Literal["out", "in"]
NodeKind = Literal["device", "zone"]

# Fähigkeiten, die Befehle annehmen (Geräte-Eingänge). Alle anderen liefern Werte (Ausgänge).
WRITE_CAPABILITIES = frozenset({"switch", "thermostat"})

# Nur diagnostische Fähigkeiten: Sie liefern Werte, die nirgends zugeordnet werden. Sie erscheinen
# nur hinter dem Schalter „technische Werte zeigen" und sind nie verbindbar.
DIAGNOSTIC_CAPABILITIES = frozenset({"battery", "link_quality", "availability", "action"})

# Wertart je Fähigkeit. Zwei Anschlüsse passen nur zusammen, wenn die Wertart gleich ist.
# Fähigkeiten ohne Eintrag haben keine bekannte Wertart und sind nicht verbindbar.
KIND_BY_CAPABILITY = {
    "temperature": "temperature",
    "setpoint": "temperature",
    "thermostat": "command",
    "humidity": "humidity",
    "contact": "contact",
    "window_open": "contact",
    "occupancy": "presence",
    "switch": "command",
}

# Wertart einer beschreibbaren Geräteeigenschaft nach ihrer Einheit. Eigenschaften mit anderer oder
# ohne Einheit bekommen keine Wertart und sind deshalb kein Anschluss der Karte -- eine Regel zu
# raten wäre schlechter, als sie wegzulassen.
KIND_BY_UNIT = {"°C": "temperature", "%": "humidity"}

KIND_LABELS = {
    "temperature": "Temperatur",
    "humidity": "Luftfeuchte",
    "contact": "Kontakt",
    "presence": "Anwesenheit",
    "command": "Schalten",
}
# Satzteile für die Hinweise an den Anschlüssen: „liefert …" / „nimmt … an".
_OUT_PHRASE = {
    "temperature": "Temperatur",
    "humidity": "Luftfeuchte",
    "contact": "Kontaktmeldungen",
    "presence": "Anwesenheitsmeldungen",
    "command": "Heizbefehle",
}
_IN_PHRASE = {
    "temperature": "Temperatur",
    "humidity": "Luftfeuchte",
    "contact": "Kontaktmeldungen",
    "presence": "Anwesenheitsmeldungen",
    "command": "Schaltbefehle",
}

SLOT_ORDER = ("temperature_source", "window_contact", "actuator")
# Der Aktor-Slot ist der einzige Zonen-Ausgang: Die Zone befiehlt dem Gerät.
OUTPUT_SLOTS = frozenset({"actuator"})
SLOT_KIND = {"temperature_source": "temperature", "window_contact": "contact", "actuator": "command"}
# Nur die Messquelle *einer Zone* nimmt genau eine Verbindung an (`Zone.temperature_source_device_id`
# ist eine Spalte). Das Gerät dagegen darf Messquelle beliebig vieler Zonen sein (offener Grundriss),
# ein Kontakt Fensterkontakt beliebig vieler Zonen; mehrere Fensterkontakte und mehrere Aktoren
# je Zone sind ebenfalls vorgesehen. Die Gegenrichtung gilt beim Heizbefehl NICHT: siehe `can_connect`.
SINGLE_SLOTS = frozenset({"temperature_source"})

SAME_SIDE = {
    "out": "Beides sind Ausgänge. Ein Ausgang liefert einen Wert und gehört an einen Eingang.",
    "in": "Beides sind Eingänge. Ein Eingang nimmt einen Wert an und gehört an einen Ausgang.",
}
TECHNICAL_REASON = "Technische Werte (Batterie, Verbindungsqualität, Erreichbarkeit, Tastendruck, Werte ohne bekannte Wertart) sind nicht verbindbar."


@dataclass(frozen=True)
class Port:
    """Ein Anschluss eines Knotens: Ausgang (liefert) oder Eingang (nimmt an)."""

    id: str
    node_id: str
    node_kind: NodeKind
    node_name: str
    direction: Direction
    kind: str | None  # Wertart; None: unbekannt
    label: str
    tooltip: str
    capability: str | None = None  # nur Geräteanschlüsse aus Fähigkeiten
    accepts: frozenset[str] = frozenset()  # nur Zonenanschlüsse: Fähigkeiten des Gegenübers
    accepts_text: str = ""
    zone_id: int | None = None
    slot: str | None = None
    device_id: int | None = None
    property_name: str | None = None
    technical: bool = False
    single: bool = False
    blocked_reason: str | None = None
    editable: bool = False
    # Nur Geräte-Eingänge mit Schalt-/Thermostat-Fähigkeit: die Zonen, in denen das Gerät schon
    # Aktor ist, als (Zonen-Kennung, Satzteil für die Begründung) -- unabhängig davon, welche Zonen
    # der Betrachter sehen darf (eine nicht sichtbare Zone heißt „einer anderen Zone").
    actuator_zones: tuple[tuple[int, str], ...] = ()


@dataclass(frozen=True)
class Node:
    id: str
    kind: NodeKind
    name: str
    ports: tuple[Port, ...]


@dataclass(frozen=True)
class Connection:
    """Eine gespeicherte Verbindung zwischen einer Zone und einem Gerät."""

    id: str
    out_port_id: str | None  # None: das Gerät hat keinen passenden Anschluss (siehe Tabelle)
    in_port_id: str | None
    zone_port_id: str
    device_id: int
    device_name: str
    text: str
    zone_id: int
    slot: str
    editable: bool


@dataclass(frozen=True)
class Verdict:
    ok: bool
    reason: str | None = None
    output: Port | None = None
    input: Port | None = None


@dataclass(frozen=True)
class MappingMap:
    nodes: tuple[Node, ...]
    connections: tuple[Connection, ...]
    # Begründungen als Tabelle, die Ablehnungen verweisen darauf: `rejections[von][nach] = Index`.
    # Nicht Aufgeführtes (mit anderer Richtung) passt.
    reasons: tuple[str, ...]
    rejections: dict[str, dict[str, int]]

    def ports(self) -> list[Port]:
        return [p for node in self.nodes for p in node.ports]


def can_connect(a: Port, b: Port, existing: Sequence[Connection] = ()) -> Verdict:
    """Dürfen diese beiden Anschlüsse verbunden werden? Reine Funktion, keine Datenbank.

    Die Reihenfolge der Argumente spielt keine Rolle (Ziehen geht von jedem Ende aus); bei
    Erfolg liefert das Urteil `output` und `input` richtig herum. Geprüft in dieser Reihenfolge,
    die erste Verletzung gibt die Begründung:

    1. **Technischer Wert**: Batterie, Verbindungsqualität & Co. sind nie verbindbar.
    2. **Derselbe Knoten**: ein Gerät nicht mit sich selbst, eine Zone nicht mit einer Zone.
    3. **Richtung**: Ausgang↔Ausgang und Eingang↔Eingang sind nie erlaubt.
    4. **Wertart**: gleiche Wertart, und sie muss bekannt sein.
    5. **Fähigkeit** (Zonenanschlüsse): das Gerät muss die gebrauchte Fähigkeit haben --
       ein Sollwert ist keine Messquelle, obwohl beides Temperatur ist.
    6. **Sperre des Eingangs** (zum Beispiel ein Gerät, das irgendwo Aktor ist, als Schreibkanal).
    7. **Doppelzuordnung**: dieselbe Zone-Gerät-Verbindung gibt es schon.
    8. **Einzelverbindung**: die Messquelle einer Zone nimmt genau ein Gerät.
    9. **Ein Aktor, eine Zone**: Ein Gerät, das schon Aktor einer *anderen* Zone ist, nimmt keinen
       zweiten Heizbefehl an. Die Regelkette (`services/publishing.py`, `services/shadow_run.py`)
       entscheidet je Zone und schreibt je Zone an das Gerät; zwei Zonen mit gegensätzlichem Bedarf
       würden dasselbe Gerät abwechselnd ein- und ausschalten, jede an der Hysterese und
       Mindestschaltdauer der anderen vorbei. Eine Zusammenführung (zum Beispiel ODER) gibt es dort
       nicht -- bevor es sie gibt, wird nicht doppelt zugeordnet. Der umgekehrte Fall ist
       unproblematisch und erlaubt: ein *Fühler* oder *Kontakt* in mehreren Zonen.
    """
    for port in (a, b):
        if port.technical:
            return Verdict(False, TECHNICAL_REASON)
    if a.node_id == b.node_id:
        return Verdict(False, "Ein Gerät kann nicht mit sich selbst verbunden werden." if a.node_kind == "device" else "Eine Zone kann nicht mit sich selbst verbunden werden.")
    if a.node_kind == "zone" and b.node_kind == "zone":
        return Verdict(False, "Zonen werden nicht untereinander verbunden.")
    if a.direction == b.direction:
        return Verdict(False, SAME_SIDE[a.direction])
    out, inp = (a, b) if a.direction == "out" else (b, a)
    if out.kind is None or inp.kind is None:
        unknown = out if out.kind is None else inp
        return Verdict(False, f"Die Wertart von '{unknown.label}' ist unbekannt.")
    if out.kind != inp.kind:
        return Verdict(
            False,
            f"'{out.label}' liefert {_OUT_PHRASE[out.kind]}, '{inp.label}' nimmt "
            f"{_IN_PHRASE[inp.kind]} an.",
        )
    zone_port, device_port = (out, inp) if out.node_kind == "zone" else (inp, out)
    if zone_port.node_kind == "zone" and zone_port.accepts and device_port.capability not in zone_port.accepts:
        return Verdict(
            False,
            f"'{device_port.label}' passt nicht zu '{zone_port.label}' -- gebraucht wird "
            f"{zone_port.accepts_text}.",
        )
    if inp.blocked_reason is not None:
        return Verdict(False, inp.blocked_reason)
    if zone_port.node_kind == "zone":
        same = [c for c in existing if c.zone_port_id == zone_port.id]
        if any(c.device_id == device_port.device_id for c in same):
            return Verdict(False, f"'{device_port.node_name}' ist mit '{zone_port.label}' der Zone '{zone_port.node_name}' bereits verbunden.")
        if zone_port.single and same:
            return Verdict(
                False,
                f"'{zone_port.label}' der Zone '{zone_port.node_name}' nimmt nur eine Verbindung an und "
                f"ist schon mit '{same[0].device_name}' verbunden. Lösen Sie diese zuerst.",
            )
        if zone_port.slot == "actuator":
            elsewhere = [label for zone_id, label in device_port.actuator_zones if zone_id != zone_port.zone_id]
            if elsewhere:
                return Verdict(
                    False,
                    f"'{device_port.node_name}' ist bereits Aktor {' und '.join(elsewhere)}; "
                    "ein Gerät kann nur eine Zone heizen.",
                )
    return Verdict(True, None, out, inp)


def zone_port_id(zone_id: int, slot: str) -> str:
    return f"zone-{zone_id}-{slot}"


def _device_port_id(device_id: int, capability: str) -> str:
    return f"dev-{device_id}-{capability}"


def property_port_id(device_id: int, name: str) -> str:
    return f"dev-{device_id}-prop-{name}"


def _tooltip(direction: Direction, kind: str | None, label: str) -> str:
    if kind is None:
        return f"{'Ausgang' if direction == 'out' else 'Eingang'}: {label} (Wertart unbekannt, nicht verbindbar)"
    if direction == "out":
        return f"Ausgang: liefert {_OUT_PHRASE[kind]}"
    return f"Eingang: nimmt {_IN_PHRASE[kind]} an"


def _slot_labels(session: Session) -> dict[str, str]:
    labels = {"window_contact": "Fensterkontakt"}  # Rückfall, falls die Rolle in der Anlage fehlt
    labels.update({code: label for code, label in session.execute(select(DeviceRole.code, DeviceRole.label))})
    labels[TEMPERATURE_SOURCE] = "Messquelle"
    return labels


def build_mapping_map(session: Session, zones: list[Zone], editable_zone_ids: set[int]) -> MappingMap:
    """Baut Knoten, vorhandene Verbindungen und die Ablehnungstabelle.

    `zones` sind die Zonen, die der Betrachter sehen darf; `editable_zone_ids` die, in denen er
    auch ändern darf (die Seite blendet die übrigen Anschlüsse als nur lesend ein).
    """
    devices = list(session.scalars(select(Device).order_by(Device.display_name, Device.id)))
    capability_labels = {code: label for code, label in session.execute(select(DeviceCapability.code, DeviceCapability.label))}
    codes_by_device: dict[int, list[str]] = defaultdict(list)
    for device_id, code in session.execute(
        select(DeviceCapabilityLink.device_id, DeviceCapability.code)
        .join(DeviceCapability, DeviceCapability.id == DeviceCapabilityLink.capability_id)
        .order_by(DeviceCapability.code)
    ):
        codes_by_device[device_id].append(code)

    # Beschreibbare Eigenschaften mit bekannter Einheit: weitere Eingänge des Geräts.
    properties: dict[int, list[DeviceProperty]] = defaultdict(list)
    for prop in session.scalars(
        select(DeviceProperty)
        .where(DeviceProperty.is_writable.is_(True))
        .order_by(DeviceProperty.device_id, DeviceProperty.name)
    ):
        if KIND_BY_UNIT.get(prop.unit or ""):
            properties[prop.device_id].append(prop)
    blocked_by_device = {
        device.id: (
            None
            if may_be_written(session, device)
            else "Schreibkanäle sind nur auf Bediengeräten erlaubt, die nirgends Aktor sind."
        )
        for device in devices
        if device.id in properties
    }

    # Aktor-Zuordnungen **aller** Zonen (nicht nur der sichtbaren): Die Sperre „ein Aktor, eine Zone"
    # darf nicht daran hängen, was der Betrachter sehen darf. Namen unsichtbarer Zonen bleiben verborgen.
    visible_zone_ids = {zone.id for zone in zones}
    actuator_zones: dict[int, list[tuple[int, str]]] = defaultdict(list)
    for device_id, zone_id, zone_name in session.execute(
        select(ZoneDevice.device_id, Zone.id, Zone.display_name)
        .join(Zone, Zone.id == ZoneDevice.zone_id)
        .join(DeviceRole, DeviceRole.id == ZoneDevice.device_role_id)
        .where(DeviceRole.code == "actuator")
        .order_by(Zone.id)
    ):
        actuator_zones[device_id].append(
            (zone_id, f"von Zone '{zone_name}'" if zone_id in visible_zone_ids else "einer anderen Zone")
        )
    actuator_codes = REQUIRED_CAPABILITY["actuator"][0]

    nodes: list[Node] = []
    for device in devices:
        ports: list[Port] = []
        for code in codes_by_device.get(device.id, []):
            label = capability_labels.get(code, code)
            kind = KIND_BY_CAPABILITY.get(code)
            direction: Direction = "in" if code in WRITE_CAPABILITIES else "out"
            technical = code in DIAGNOSTIC_CAPABILITIES or kind is None
            ports.append(
                Port(
                    id=_device_port_id(device.id, code),
                    node_id=f"dev-{device.id}",
                    node_kind="device",
                    node_name=device.display_name,
                    direction=direction,
                    kind=None if technical else kind,
                    label=label,
                    tooltip=TECHNICAL_REASON if code in DIAGNOSTIC_CAPABILITIES else _tooltip(direction, kind, label),
                    capability=code,
                    device_id=device.id,
                    technical=technical,
                    actuator_zones=tuple(actuator_zones.get(device.id, ())) if code in actuator_codes else (),
                )
            )
        for prop in properties.get(device.id, []):
            kind = KIND_BY_UNIT[prop.unit or ""]
            ports.append(
                Port(
                    id=property_port_id(device.id, prop.name),
                    node_id=f"dev-{device.id}",
                    node_kind="device",
                    node_name=device.display_name,
                    direction="in",
                    kind=kind,
                    label=prop.name,
                    tooltip=_tooltip("in", kind, prop.name),
                    capability=None,
                    device_id=device.id,
                    property_name=prop.name,
                    single=True,
                    blocked_reason=blocked_by_device.get(device.id),
                )
            )
        # Eingänge links zuerst, dann Ausgänge; innerhalb stabil nach Beschriftung.
        ports.sort(key=lambda p: (p.direction != "in", p.technical, p.label))
        nodes.append(Node(id=f"dev-{device.id}", kind="device", name=device.display_name, ports=tuple(ports)))

    slot_labels = _slot_labels(session)
    for zone in zones:
        zone_ports: list[Port] = []
        for slot in SLOT_ORDER:
            required = REQUIRED_CAPABILITY.get(slot)
            if required is None:  # pragma: no cover - SLOT_ORDER und Tabelle gehören zusammen
                continue
            codes, _defect = required
            direction = "out" if slot in OUTPUT_SLOTS else "in"
            label = "Heizbefehl" if slot == "actuator" else slot_labels.get(slot, slot)
            kind = SLOT_KIND[slot]
            zone_ports.append(
                Port(
                    id=zone_port_id(zone.id, slot),
                    node_id=f"zone-{zone.id}",
                    node_kind="zone",
                    node_name=zone.display_name,
                    direction=direction,
                    kind=kind,
                    label=label,
                    tooltip=_tooltip(direction, kind, label),
                    accepts=codes,
                    accepts_text=" oder ".join(sorted(capability_labels.get(c, c) for c in codes)),
                    zone_id=zone.id,
                    slot=slot,
                    single=slot in SINGLE_SLOTS,
                    editable=zone.id in editable_zone_ids,
                )
            )
        nodes.append(Node(id=f"zone-{zone.id}", kind="zone", name=zone.display_name, ports=tuple(zone_ports)))

    all_ports = [p for node in nodes for p in node.ports]
    by_device: dict[int, list[Port]] = defaultdict(list)
    for port in all_ports:
        if port.node_kind == "device" and port.device_id is not None:
            by_device[port.device_id].append(port)
    connections = tuple(_connections(session, zones, editable_zone_ids, by_device, slot_labels))

    reason_index: dict[str, int] = {}
    rejections: dict[str, dict[str, int]] = {}
    for first in all_ports:
        if first.technical:
            continue
        rejected: dict[str, int] = {}
        for second in all_ports:
            # Gleiche Seite: eine feste Begründung je Richtung, siehe `SAME_SIDE`.
            if second.technical or second.direction == first.direction:
                continue
            verdict = can_connect(first, second, connections)
            if not verdict.ok and verdict.reason is not None:
                rejected[second.id] = reason_index.setdefault(verdict.reason, len(reason_index))
        rejections[first.id] = rejected
    reasons = sorted(reason_index, key=lambda text: reason_index[text])
    return MappingMap(nodes=tuple(nodes), connections=connections, reasons=tuple(reasons), rejections=rejections)


def _connections(
    session: Session,
    zones: list[Zone],
    editable_zone_ids: set[int],
    by_device: dict[int, list[Port]],
    slot_labels: dict[str, str],
) -> list[Connection]:
    zone_by_id = {zone.id: zone for zone in zones}
    names = {i: n for i, n in session.execute(select(Device.id, Device.display_name))}

    def device_port(device_id: int, slot: str) -> Port | None:
        required = REQUIRED_CAPABILITY.get(slot)
        if required is None:  # pragma: no cover - nur bekannte Stellen kommen hier an
            return None
        return next(
            (p for p in by_device.get(device_id, []) if p.capability in required[0] and not p.technical),
            None,
        )

    def make(zone: Zone, slot: str, device_id: int) -> Connection:
        found = device_port(device_id, slot)
        zone_port = zone_port_id(zone.id, slot)
        out_id, in_id = (zone_port, found.id if found else None) if slot in OUTPUT_SLOTS else (found.id if found else None, zone_port)
        label = "Heizbefehl" if slot == "actuator" else slot_labels.get(slot, slot)
        return Connection(
            id=f"conn-{zone.id}-{slot}-{device_id}",
            out_port_id=out_id,
            in_port_id=in_id,
            zone_port_id=zone_port,
            device_id=device_id,
            device_name=names.get(device_id, str(device_id)),
            text=f"{zone.display_name}: {label}",
            zone_id=zone.id,
            slot=slot,
            editable=zone.id in editable_zone_ids,
        )

    result: list[Connection] = []
    for zone in zones:
        if zone.temperature_source_device_id is not None:
            result.append(make(zone, TEMPERATURE_SOURCE, zone.temperature_source_device_id))
    for assignment, role_code in session.execute(
        select(ZoneDevice, DeviceRole.code)
        .join(DeviceRole, DeviceRole.id == ZoneDevice.device_role_id)
        .where(ZoneDevice.zone_id.in_([zone.id for zone in zones]))
        .order_by(ZoneDevice.zone_id, ZoneDevice.id)
    ):
        if role_code not in SLOT_ORDER:
            continue  # zum Beispiel `controller`: ohne Fähigkeitsvorgabe, hier nicht dargestellt
        result.append(make(zone_by_id[assignment.zone_id], role_code, assignment.device_id))
    return result
