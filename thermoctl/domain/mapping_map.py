# ruff: noqa: E501
"""Zuordnungskarte (Entwurf): Knoten mit Anschlüssen und die Regeln, wer mit wem verbunden werden darf.

Ein Wert fließt immer von einem **Ausgang** (liefert einen Wert) zu einem **Eingang** (nimmt einen
Wert oder Befehl an). Die Karte kennt zwei Arten von Knoten:

* **Gerät** -- Ausgänge: was es misst oder meldet (Temperatur, Luftfeuchte, Kontakt, Anwesenheit);
  Eingänge: was es annimmt (Schaltbefehle, Thermostat-Sollwert, beschreibbare Eigenschaften).
* **Zone** -- Eingänge: Messquelle (Temperatur) und Fensterkontakt; Ausgang: der Heizbefehl.

Die **Betriebsart** eines Thermostatventils (`domain/thermostat_mode.py`) hat in der Karte kein
eigenes Feld, sie **folgt aus den Linien** zwischen Zone und Ventil. Die Zone hat dafür drei
Ausgänge: „Heizbefehl" (Ein/Aus, `controlled`), „Sollwert" und „Raumtemperatur" (der Messwert der
Messquelle, als Istwert ans Ventil geschrieben). Welche Linien eine Betriebsart hat, sagen
`switches`, `writes_setpoint` und `writes_actual` -- dieselben Fragen, die die Regelkette stellt
(`domain/self_regulating.py::valve_commands`, `services/publishing.py`) und die Datenfluss-Seite
(`domain/data_flow.py`); die Karte formuliert sie nicht neu (`lines_for_mode`, `mode_for_lines`).

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

Was gespeichert werden kann, entscheidet weiterhin `domain/device_assignment.py` -- auch die Regel
„ein Aktor, eine Zone" (`check_actuator_exclusive`); `can_connect` bezieht nur deren Wortlaut
(`actuator_elsewhere_notice`) und zeigt die Ablehnung vorab an.
Gespeichert wird über `assign_device`, `set_temperature_source`, `REQUIRED_CAPABILITY`; diese Datei ergänzt nur die
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
from thermoctl.domain.device_assignment import (
    REQUIRED_CAPABILITY,
    TEMPERATURE_SOURCE,
    actuator_elsewhere_notice,
)
from thermoctl.domain.self_regulating import SETPOINT_PROPERTY, temperature_property_name
from thermoctl.domain.thermostat_mode import (
    MODE_LABELS,
    MODES,
    switches,
    writes_actual,
    writes_setpoint,
)

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

# Die drei Zonen-Ausgänge zum Thermostatventil: Heizbefehl (Ein/Aus), Sollwert, Raumtemperatur.
# Alle drei gehören zur Rolle `actuator`; welche Linien ein Ventil hat, bestimmt seine Betriebsart.
VALVE_SLOTS = ("actuator", "setpoint", "room_temperature")
SLOT_ORDER = ("temperature_source", "window_contact", *VALVE_SLOTS)
# Zonen-Ausgänge: Die Zone befiehlt dem Gerät beziehungsweise schreibt ihm Werte.
OUTPUT_SLOTS = frozenset(VALVE_SLOTS)
SLOT_KIND = {
    "temperature_source": "temperature",
    "window_contact": "contact",
    "actuator": "command",
    "setpoint": "temperature",
    "room_temperature": "temperature",
}
SLOT_LABEL = {"actuator": "Heizbefehl", "setpoint": "Sollwert", "room_temperature": "Raumtemperatur"}
# Welcher Geräte-Eingang (`Port.valve_channel`) zu welchem Zonen-Ausgang gehört.
CHANNEL_BY_SLOT = {"setpoint": "setpoint", "room_temperature": "actual"}
SLOT_TOOLTIP = {
    "setpoint": "Ausgang: der Sollwert der Zone (Zeitplan, Übersteuerung, Boost, Frostschutz), wird als Sollwert ans Ventil geschrieben.",
    "room_temperature": "Messwert der Messquelle dieser Zone, wird als Istwert ans Ventil geschrieben.",
}
NO_SOURCE_REASON = "Diese Zone hat keine Messquelle -- ohne Messwert gibt es keine Raumtemperatur, die ans Ventil geschrieben werden könnte. Verbinden Sie zuerst eine Messquelle."
VIA_ZONE_REASON = "Diesen Eingang beschreibt thermoctl über die Zone: Verbinden Sie den Sensor mit der Messquelle der Zone und danach den Zonen-Ausgang 'Raumtemperatur' mit diesem Eingang."


def role_for_slot(slot: str) -> str:
    """Die Geräterolle hinter einer Zonenstelle (die drei Ventil-Ausgänge sind alle `actuator`)."""
    return "actuator" if slot in VALVE_SLOTS else slot


def lines_for_mode(mode: str, *, has_setpoint_port: bool = True, has_actual_port: bool = True) -> frozenset[str]:
    """Welche Linien eine Betriebsart zwischen Zone und Ventil hat -- nach denselben Fragen wie die Regelkette.

    `switches` -> Heizbefehl, `writes_setpoint` -> Sollwert, `writes_actual` -> Raumtemperatur.
    Eine Linie gibt es nur, wenn das Gerät den Eingang hat, den die Regelkette beschreibt; ohne ihn
    schreibt `valve_commands` nichts, und die Karte zeichnet nichts.
    """
    lines: set[str] = set()
    if switches(mode):
        lines.add("actuator")
    if writes_setpoint(mode) and has_setpoint_port:
        lines.add("setpoint")
    if writes_actual(mode) and has_actual_port:
        lines.add("room_temperature")
    return frozenset(lines)


def mode_for_lines(lines: frozenset[str] | set[str], *, has_actual_port: bool) -> str | None:
    """Die Betriebsart, die genau diese Linien hat -- oder `None`, wenn keine Betriebsart sie trägt.

    Die Umkehrung von `lines_for_mode`. Ein Sollwert ohne Raumtemperatur ist nur dort eine
    Betriebsart, wo das Gerät keinen Istwert-Eingang hat; sonst schreibt die Regelkette den Istwert
    mit, und die Linie wäre unvollständig dargestellt.
    """
    wanted = frozenset(lines)
    if not wanted:
        return None
    for mode in MODES:
        if lines_for_mode(mode, has_actual_port=has_actual_port) == wanted:
            return mode
    return None
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
    # Nur Geräte-Eingänge, die thermoctl über die Zone beschreibt (Eigenschaften eines
    # Thermostatventils): `setpoint` (Sollwert) oder `actual` (Istwert, Raumtemperatur).
    valve_channel: str | None = None
    # Nur beim Sollwert-Eingang: das Ventil hat auch einen Istwert-Eingang, die Regelkette schreibt
    # den Istwert dann immer mit -- ein Sollwert ohne Raumtemperatur gibt es dort nicht.
    requires_actual: bool = False
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
    # Läuft von der Zone zum Gerät (Heizbefehl, Sollwert, Raumtemperatur) statt vom Gerät zur Zone.
    from_zone: bool = False


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
    valve_channel = CHANNEL_BY_SLOT.get(zone_port.slot or "") if zone_port.node_kind == "zone" else None
    if valve_channel is not None and device_port.valve_channel != valve_channel:
        return Verdict(
            False,
            f"'{device_port.label}' ist kein Eingang für '{zone_port.label}': thermoctl beschreibt "
            f"am Thermostatventil nur die Eigenschaft, die an seiner Karte als "
            f"{zone_port.label}-Eingang erscheint.",
        )
    # Die Zone beschreibt ihren Ventil-Eingang selbst; jede andere Quelle trifft die Sperre.
    if inp.blocked_reason is not None and valve_channel is None:
        return Verdict(False, inp.blocked_reason)
    if zone_port.node_kind == "zone":
        if zone_port.blocked_reason is not None:
            return Verdict(False, zone_port.blocked_reason)
        same = [c for c in existing if c.zone_port_id == zone_port.id]
        if any(c.device_id == device_port.device_id for c in same):
            return Verdict(False, f"'{device_port.node_name}' ist mit '{zone_port.label}' der Zone '{zone_port.node_name}' bereits verbunden.")
        if zone_port.single and same:
            return Verdict(
                False,
                f"'{zone_port.label}' der Zone '{zone_port.node_name}' nimmt nur eine Verbindung an und "
                f"ist schon mit '{same[0].device_name}' verbunden. Lösen Sie diese zuerst.",
            )
        if zone_port.slot in VALVE_SLOTS:
            lines = {
                c.slot
                for c in existing
                if c.zone_id == zone_port.zone_id and c.device_id == device_port.device_id and c.slot in VALVE_SLOTS
            }
            has_actual = device_port.requires_actual or zone_port.slot == "room_temperature"
            if mode_for_lines(lines | {zone_port.slot}, has_actual_port=has_actual) is None:
                name = f"'{device_port.node_name}'"
                if not lines:
                    return Verdict(
                        False,
                        f"{name} bekommt den Sollwert nur zusammen mit der Raumtemperatur: thermoctl "
                        f"schreibt beides. Verbinden Sie zuerst '{SLOT_LABEL['room_temperature']}'.",
                    )
                have = " und ".join(f"'{SLOT_LABEL[x]}'" for x in VALVE_SLOTS if x in lines)
                return Verdict(
                    False,
                    f"{name} ist in Zone '{zone_port.node_name}' schon mit {have} verbunden. "
                    f"'Heizbefehl' (thermoctl entscheidet Ein/Aus) schließt 'Sollwert' und "
                    f"'Raumtemperatur' (das Ventil regelt selbst) aus. Lösen Sie die vorhandene Linie zuerst.",
                )
            elsewhere = [label for zone_id, label in device_port.actuator_zones if zone_id != zone_port.zone_id]
            if elsewhere:
                return Verdict(False, actuator_elsewhere_notice(device_port.node_name, elsewhere))
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
    # Dazu bei einem Thermostatventil die Eigenschaften, in die die Regelkette Sollwert und Istwert
    # schreibt (`SETPOINT_PROPERTY`, `temperature_property_name`) -- unabhängig von der Einheit.
    writable: dict[int, list[DeviceProperty]] = defaultdict(list)
    for prop in session.scalars(
        select(DeviceProperty)
        .where(DeviceProperty.is_writable.is_(True))
        .order_by(DeviceProperty.device_id, DeviceProperty.name)
    ):
        writable[prop.device_id].append(prop)
    valve_properties: dict[int, dict[str, str]] = {}
    properties: dict[int, list[DeviceProperty]] = defaultdict(list)
    for device_id, props in writable.items():
        names = [p.name for p in props]
        channels: dict[str, str] = {}
        if "thermostat" in codes_by_device.get(device_id, ()):
            if SETPOINT_PROPERTY in names:
                channels[SETPOINT_PROPERTY] = "setpoint"
            actual_name = temperature_property_name(names)
            if actual_name is not None:
                channels[actual_name] = "actual"
        valve_properties[device_id] = channels
        for prop in props:
            if prop.name in channels or KIND_BY_UNIT.get(prop.unit or ""):
                properties[device_id].append(prop)
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
        channels = valve_properties.get(device.id, {})
        for prop in properties.get(device.id, []):
            channel = channels.get(prop.name)
            kind = "temperature" if channel is not None else KIND_BY_UNIT[prop.unit or ""]
            blocked = blocked_by_device.get(device.id)
            tooltip = _tooltip("in", kind, prop.name)
            if channel is not None:
                tooltip = (
                    f"Eingang: {'Sollwert' if channel == 'setpoint' else 'Istwert (Raumtemperatur)'} "
                    "des Ventils. thermoctl beschreibt ihn über die Zone."
                )
                if blocked is not None:
                    blocked = VIA_ZONE_REASON
            ports.append(
                Port(
                    id=property_port_id(device.id, prop.name),
                    node_id=f"dev-{device.id}",
                    node_kind="device",
                    node_name=device.display_name,
                    direction="in",
                    kind=kind,
                    label=prop.name,
                    tooltip=tooltip,
                    capability=None,
                    device_id=device.id,
                    property_name=prop.name,
                    single=True,
                    blocked_reason=blocked,
                    valve_channel=channel,
                    requires_actual=channel == "setpoint" and "actual" in channels.values(),
                    actuator_zones=tuple(actuator_zones.get(device.id, ())) if channel is not None else (),
                )
            )
        # Eingänge links zuerst, dann Ausgänge; innerhalb stabil nach Beschriftung.
        ports.sort(key=lambda p: (p.direction != "in", p.technical, p.label))
        nodes.append(Node(id=f"dev-{device.id}", kind="device", name=device.display_name, ports=tuple(ports)))

    slot_labels = _slot_labels(session)
    for zone in zones:
        zone_ports: list[Port] = []
        for slot in SLOT_ORDER:
            # Sollwert und Raumtemperatur haben keine Fähigkeitsvorgabe: Ihr Ziel ist eine bestimmte
            # Eigenschaft des Ventils (`CHANNEL_BY_SLOT`), keine Fähigkeit.
            codes = REQUIRED_CAPABILITY[slot][0] if slot in REQUIRED_CAPABILITY else frozenset()
            direction = "out" if slot in OUTPUT_SLOTS else "in"
            label = SLOT_LABEL.get(slot) or slot_labels.get(slot, slot)
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
                    tooltip=SLOT_TOOLTIP.get(slot) or _tooltip(direction, kind, label),
                    accepts=codes,
                    accepts_text=" oder ".join(sorted(capability_labels.get(c, c) for c in codes)),
                    zone_id=zone.id,
                    slot=slot,
                    single=slot in SINGLE_SLOTS,
                    editable=zone.id in editable_zone_ids,
                    blocked_reason=(
                        NO_SOURCE_REASON
                        if slot == "room_temperature" and zone.temperature_source_device_id is None
                        else None
                    ),
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
        """Der Geräte-Anschluss, an dem die Linie dieser Zonenstelle endet (oder `None`)."""
        ports = [p for p in by_device.get(device_id, []) if not p.technical]
        if slot in CHANNEL_BY_SLOT:
            return next((p for p in ports if p.valve_channel == CHANNEL_BY_SLOT[slot]), None)
        required = REQUIRED_CAPABILITY[slot][0]
        return next((p for p in ports if p.capability in required), None)

    def make(zone: Zone, slot: str, device_id: int, *, text: str | None = None, drawn: bool = True) -> Connection:
        found = device_port(device_id, slot) if drawn else None
        zone_port = zone_port_id(zone.id, slot)
        out_id, in_id = (zone_port, found.id if found else None) if slot in OUTPUT_SLOTS else (found.id if found else None, zone_port)
        label = SLOT_LABEL.get(slot) or slot_labels.get(slot, slot)
        return Connection(
            id=f"conn-{zone.id}-{slot}-{device_id}",
            out_port_id=out_id,
            in_port_id=in_id,
            zone_port_id=zone_port,
            device_id=device_id,
            device_name=names.get(device_id, str(device_id)),
            text=text or f"{zone.display_name}: {label}",
            zone_id=zone.id,
            slot=slot,
            editable=zone.id in editable_zone_ids,
            from_zone=slot in OUTPUT_SLOTS,
        )

    result: list[Connection] = []
    for zone in zones:
        if zone.temperature_source_device_id is not None:
            result.append(make(zone, TEMPERATURE_SOURCE, zone.temperature_source_device_id))
    for assignment, role_code, mode in session.execute(
        select(ZoneDevice, DeviceRole.code, ZoneDevice.thermostat_mode)
        .join(DeviceRole, DeviceRole.id == ZoneDevice.device_role_id)
        .where(ZoneDevice.zone_id.in_([zone.id for zone in zones]))
        .order_by(ZoneDevice.zone_id, ZoneDevice.id)
    ):
        if role_code not in SLOT_ORDER:
            continue  # zum Beispiel `controller`: ohne Fähigkeitsvorgabe, hier nicht dargestellt
        zone = zone_by_id[assignment.zone_id]
        if role_code != "actuator":
            result.append(make(zone, role_code, assignment.device_id))
            continue
        # Die Linien eines Aktors folgen aus seiner Betriebsart (`lines_for_mode`), nicht aus einer
        # eigenen Tabelle: Gezeichnet wird, was die Regelkette ausführt -- und nur dort, wo es den
        # Eingang am Gerät gibt und (beim Istwert) die Zone etwas zu schreiben hat.
        ports = by_device.get(assignment.device_id, [])
        lines = lines_for_mode(
            mode,
            has_setpoint_port=any(p.valve_channel == "setpoint" for p in ports),
            has_actual_port=any(p.valve_channel == "actual" for p in ports),
        )
        if not lines:
            # Keine Linie, die die Regelkette ausführen würde (zum Beispiel „nur Raumtemperatur" an
            # einem Gerät ohne Istwert-Eingang): ungezeichnet, aber gelistet, damit sie lösbar bleibt.
            label = MODE_LABELS.get(mode, mode)
            result.append(
                make(
                    zone,
                    "actuator",
                    assignment.device_id,
                    text=f"{zone.display_name}: Betriebsart '{label}' (ohne Linie, das Gerät hat keinen passenden Eingang)",
                    drawn=False,
                )
            )
            continue
        for slot in VALVE_SLOTS:
            if slot not in lines:
                continue
            if slot == "room_temperature" and zone.temperature_source_device_id is None:
                result.append(
                    make(
                        zone,
                        slot,
                        assignment.device_id,
                        text=f"{zone.display_name}: Raumtemperatur (ohne Messquelle wird nichts geschrieben)",
                        drawn=False,
                    )
                )
            else:
                result.append(make(zone, slot, assignment.device_id))
    return result
