# ruff: noqa: E501
"""Zuordnungskarte (Entwurf): Geräte mit ihren Endpunkten und die Ziele, mit denen sie verbunden werden können.

Die Karte hat drei Teile, alle ohne Darstellung:

* **Endpunkte** -- was ein Gerät kann (`Endpoint`): je bekannter Fähigkeit einer, mit Richtung
  (`read` liefert Werte, `write` nimmt Befehle an) und Wertart (`quantity`).
* **Ziele** (`Target`) -- die Zonen-Stellen (Messquelle, Aktor, Fensterkontakt; aus
  `REQUIRED_CAPABILITY`, nicht noch einmal aufgeschrieben) und beschreibbare Eigenschaften
  anderer Geräte (die Eingänge eines Geräts).
* **Verbindungen** (`Connection`) -- was in der Datenbank tatsächlich steht: die
  Rollenzuordnungen (`ZoneDevice`) und die Messquelle einer Zone.

`can_connect` ist die **einzige** Stelle, die entscheidet, ob ein Endpunkt auf ein Ziel
passt. Die Seite bekommt das Ergebnis für jedes Paar fertig mitgeliefert und rechnet nichts
nach -- eine zweite Fassung der Regeln in JavaScript hat in `assignment.js` schon einmal
veraltet. Der schreibende Endpunkt prüft vor dem Speichern erneut mit derselben Funktion.

Was gespeichert werden kann, entscheidet weiterhin `domain/device_assignment.py`
(`assign_device`, `set_temperature_source`); diese Datei ergänzt nur die feinere,
endpunktgenaue Vorprüfung. Dieses Modul schreibt selbst nichts.
"""

from collections import defaultdict
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.db.models.device import Device, DeviceCapabilityLink, DeviceProperty, ZoneDevice
from thermoctl.db.models.lookup import DeviceCapability, DeviceRole
from thermoctl.db.models.zone import Zone
from thermoctl.domain.controller_channels import may_be_written
from thermoctl.domain.device_assignment import REQUIRED_CAPABILITY, TEMPERATURE_SOURCE

Direction = Literal["read", "write"]

# Fähigkeiten, die Befehle annehmen. Alle anderen liefern Werte. Gleiche Unterscheidung wie
# in `REQUIRED_CAPABILITY`: Aktor-Stelle = schreibend, Messquelle und Fensterkontakt = lesend.
WRITE_CAPABILITIES = frozenset({"switch", "thermostat"})

# Nur diagnostische Fähigkeiten: Sie liefern Werte, die nirgends zugeordnet werden, und
# würden die Karte nur füllen.
DIAGNOSTIC_CAPABILITIES = frozenset({"battery", "link_quality", "availability", "action"})

# Wertart je Fähigkeit. Zwei Endpunkte passen nur zusammen, wenn die Wertart gleich ist
# (eine Temperatur gehört nicht auf eine Luftfeuchte-Eigenschaft).
QUANTITY_BY_CAPABILITY = {
    "temperature": "temperature",
    "setpoint": "temperature",
    "thermostat": "temperature",
    "humidity": "humidity",
    "contact": "binary",
    "window_open": "binary",
    "occupancy": "binary",
    "switch": "binary",
}

# Wertart einer beschreibbaren Geräteeigenschaft nach ihrer Einheit. Eigenschaften mit
# anderer oder ohne Einheit bekommen keine Wertart und sind deshalb kein Ziel der Karte --
# eine Regel zu raten wäre schlechter, als sie wegzulassen.
QUANTITY_BY_UNIT = {"°C": "temperature", "%": "humidity"}

SLOT_ORDER = ("temperature_source", "actuator", "window_contact")


@dataclass(frozen=True)
class Endpoint:
    id: str
    device_id: int
    device_name: str
    capability: str
    label: str
    direction: Direction
    quantity: str | None


@dataclass(frozen=True)
class Target:
    """Eine Stelle, mit der ein Endpunkt verbunden werden kann."""

    id: str
    kind: Literal["zone_slot", "device_input"]
    group: str
    label: str
    direction: Direction  # Welche Richtung der Endpunkt haben muss, der hier ankommt.
    accepts: frozenset[str]  # Fähigkeiten, die die Stelle annimmt (leer: ohne Fähigkeitsvorgabe)
    accepts_text: str
    quantity: str | None
    zone_id: int | None = None
    slot: str | None = None
    device_id: int | None = None
    property_name: str | None = None
    blocked_reason: str | None = None
    editable: bool = False


@dataclass(frozen=True)
class Connection:
    id: str
    endpoint_id: str | None  # None: das Gerät kennt keinen passenden Endpunkt (siehe unten)
    target_id: str
    device_id: int
    device_name: str
    target_text: str
    zone_id: int
    slot: str
    editable: bool


@dataclass(frozen=True)
class Verdict:
    ok: bool
    reason: str | None = None


@dataclass(frozen=True)
class MappingMap:
    devices: tuple[tuple[int, str, tuple[Endpoint, ...]], ...]
    targets: tuple[Target, ...]
    connections: tuple[Connection, ...]
    # Für jeden Endpunkt: abgelehnte Ziele mit Begründung. Nicht Aufgeführtes passt.
    rejections: dict[str, dict[str, str]]


def can_connect(source: Endpoint, target: Target) -> Verdict:
    """Darf dieser Endpunkt mit diesem Ziel verbunden werden? Reine Funktion, keine Datenbank.

    Geprüft in dieser Reihenfolge, die erste Verletzung gibt die Begründung:

    1. **Eigenes Gerät**: ein Geräte-Eingang nimmt nichts vom eigenen Gerät.
    2. **Richtung**: die Stelle verlangt einen lesenden Endpunkt (liefert Werte) oder einen
       schreibenden (nimmt Befehle an).
    3. **Fähigkeit** (nur Zonen-Stellen): die Fähigkeit steht in der Liste der Stelle.
    4. **Wertart** (nur Geräte-Eingänge): Temperatur auf Temperatur, Luftfeuchte auf
       Luftfeuchte -- und nie ein Wert ohne bekannte Wertart.
    5. **Sperre des Ziels**: das Ziel selbst ist nicht beschreibbar (zum Beispiel ein Aktor,
       dessen Sollwert thermoctl führt).
    """
    if target.kind == "device_input" and target.device_id == source.device_id:
        return Verdict(False, "Ein Gerät kann nicht mit seinem eigenen Eingang verbunden werden.")
    if source.direction != target.direction:
        if target.direction == "read":
            return Verdict(
                False,
                f"'{source.label}' nimmt Befehle an, hier wird aber ein Endpunkt gebraucht, "
                "der Werte liefert.",
            )
        return Verdict(
            False,
            f"'{source.label}' liefert Werte, hier wird aber ein Endpunkt gebraucht, "
            "der Befehle annimmt.",
        )
    if target.accepts and source.capability not in target.accepts:
        return Verdict(
            False,
            f"'{source.label}' passt nicht zu '{target.label}' -- gebraucht wird "
            f"{target.accepts_text}.",
        )
    if target.kind == "device_input":
        if source.quantity is None or target.quantity is None:
            return Verdict(False, f"Die Wertart von '{source.label}' ist unbekannt.")
        if source.quantity != target.quantity:
            return Verdict(
                False,
                f"'{source.label}' ({_quantity_text(source.quantity)}) passt nicht auf "
                f"'{target.label}' ({_quantity_text(target.quantity)}).",
            )
    if target.blocked_reason is not None:
        return Verdict(False, target.blocked_reason)
    return Verdict(True)


def _quantity_text(quantity: str) -> str:
    return {"temperature": "Temperatur", "humidity": "Luftfeuchte", "binary": "Ein/Aus"}.get(
        quantity, quantity
    )


def slot_target_id(zone_id: int, slot: str) -> str:
    return f"zone-{zone_id}-{slot}"


def _endpoint_id(device_id: int, capability: str) -> str:
    return f"dev-{device_id}-{capability}"


def _slot_labels(session: Session) -> dict[str, str]:
    labels = {
        code: label
        for code, label in session.execute(select(DeviceRole.code, DeviceRole.label))
    }
    labels[TEMPERATURE_SOURCE] = "Messquelle"
    return labels


def build_mapping_map(
    session: Session, zones: list[Zone], editable_zone_ids: set[int]
) -> MappingMap:
    """Baut Endpunkte, Ziele, vorhandene Verbindungen und die Ablehnungstabelle.

    `zones` sind die Zonen, die der Betrachter sehen darf; `editable_zone_ids` die, in denen er
    auch ändern darf (die Seite blendet die übrigen Stellen als nur lesend ein).
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

    device_nodes: list[tuple[int, str, tuple[Endpoint, ...]]] = []
    endpoints: list[Endpoint] = []
    for device in devices:
        own = tuple(
            Endpoint(
                id=_endpoint_id(device.id, code),
                device_id=device.id,
                device_name=device.display_name,
                capability=code,
                label=capability_labels.get(code, code),
                direction="write" if code in WRITE_CAPABILITIES else "read",
                quantity=QUANTITY_BY_CAPABILITY.get(code),
            )
            for code in codes_by_device.get(device.id, [])
            if code not in DIAGNOSTIC_CAPABILITIES
        )
        device_nodes.append((device.id, device.display_name, own))
        endpoints.extend(own)

    slot_labels = _slot_labels(session)
    targets: list[Target] = []
    for zone in zones:
        for slot in SLOT_ORDER:
            required = REQUIRED_CAPABILITY.get(slot)
            if required is None:  # pragma: no cover - SLOT_ORDER und Tabelle gehören zusammen
                continue
            codes, _defect = required
            targets.append(
                Target(
                    id=slot_target_id(zone.id, slot),
                    kind="zone_slot",
                    group=zone.display_name,
                    label=slot_labels.get(slot, slot),
                    direction="write" if codes & WRITE_CAPABILITIES else "read",
                    accepts=codes,
                    accepts_text=" oder ".join(
                        sorted(capability_labels.get(code, code) for code in codes)
                    ),
                    quantity=None,
                    zone_id=zone.id,
                    slot=slot,
                    editable=zone.id in editable_zone_ids,
                )
            )

    blocked: dict[int, str | None] = {}
    for device, prop in session.execute(
        select(Device, DeviceProperty)
        .join(DeviceProperty, DeviceProperty.device_id == Device.id)
        .where(DeviceProperty.is_writable.is_(True))
        .order_by(Device.display_name, Device.id, DeviceProperty.name)
    ):
        quantity = QUANTITY_BY_UNIT.get(prop.unit or "")
        if quantity is None:
            continue
        if device.id not in blocked:
            blocked[device.id] = (
                None
                if may_be_written(session, device)
                else "Schreibkanäle sind nur auf Bediengeräten erlaubt, die nirgends Aktor sind."
            )
        targets.append(
            Target(
                id=f"input-{device.id}-{prop.name}",
                kind="device_input",
                group=device.display_name,
                label=prop.name,
                direction="read",
                accepts=frozenset(),
                accepts_text="",
                quantity=quantity,
                device_id=device.id,
                property_name=prop.name,
                blocked_reason=blocked[device.id],
            )
        )

    rejections: dict[str, dict[str, str]] = {}
    for endpoint in endpoints:
        rejected = {}
        for target in targets:
            verdict = can_connect(endpoint, target)
            if not verdict.ok and verdict.reason is not None:
                rejected[target.id] = verdict.reason
        rejections[endpoint.id] = rejected

    return MappingMap(
        devices=tuple(device_nodes),
        targets=tuple(targets),
        connections=tuple(
            _connections(session, zones, editable_zone_ids, endpoints, slot_labels)
        ),
        rejections=rejections,
    )


def _connections(
    session: Session,
    zones: list[Zone],
    editable_zone_ids: set[int],
    endpoints: list[Endpoint],
    slot_labels: dict[str, str],
) -> list[Connection]:
    by_device: dict[int, list[Endpoint]] = defaultdict(list)
    for endpoint in endpoints:
        by_device[endpoint.device_id].append(endpoint)
    zone_by_id = {zone.id: zone for zone in zones}
    names = {i: n for i, n in session.execute(select(Device.id, Device.display_name))}

    def endpoint_for(device_id: int, slot: str) -> Endpoint | None:
        required = REQUIRED_CAPABILITY.get(slot)
        if required is None:  # pragma: no cover - nur bekannte Stellen kommen hier an
            return None
        return next((e for e in by_device.get(device_id, []) if e.capability in required[0]), None)

    result: list[Connection] = []
    for zone in zones:
        if zone.temperature_source_device_id is not None:
            device_id = zone.temperature_source_device_id
            found = endpoint_for(device_id, TEMPERATURE_SOURCE)
            result.append(
                Connection(
                    id=f"conn-{zone.id}-{TEMPERATURE_SOURCE}-{device_id}",
                    endpoint_id=found.id if found else None,
                    target_id=slot_target_id(zone.id, TEMPERATURE_SOURCE),
                    device_id=device_id,
                    device_name=names.get(device_id, str(device_id)),
                    target_text=f"{zone.display_name}: {slot_labels[TEMPERATURE_SOURCE]}",
                    zone_id=zone.id,
                    slot=TEMPERATURE_SOURCE,
                    editable=zone.id in editable_zone_ids,
                )
            )
    for assignment, role_code in session.execute(
        select(ZoneDevice, DeviceRole.code)
        .join(DeviceRole, DeviceRole.id == ZoneDevice.device_role_id)
        .where(ZoneDevice.zone_id.in_([zone.id for zone in zones]))
        .order_by(ZoneDevice.zone_id, ZoneDevice.id)
    ):
        if role_code not in SLOT_ORDER:
            continue  # zum Beispiel `controller`: ohne Fähigkeitsvorgabe, hier nicht dargestellt
        zone = zone_by_id[assignment.zone_id]
        found = endpoint_for(assignment.device_id, role_code)
        result.append(
            Connection(
                id=f"conn-{zone.id}-{role_code}-{assignment.device_id}",
                endpoint_id=found.id if found else None,
                target_id=slot_target_id(zone.id, role_code),
                device_id=assignment.device_id,
                device_name=names.get(assignment.device_id, str(assignment.device_id)),
                target_text=f"{zone.display_name}: {slot_labels.get(role_code, role_code)}",
                zone_id=zone.id,
                slot=role_code,
                editable=zone.id in editable_zone_ids,
            )
        )
    return result
