"""Was welches Gerät wo tut -- als Bild statt als Liste.

Die Zuordnung von Geräten zu Zonen steht in drei Tabellen: `zone.temperature_source_device_id`
für die Messquelle, `zone_device` mit einer Rolle für Aktoren und Fensterkontakte, und
`device_capability_link` für das, was ein Gerät überhaupt kann. Wer wissen will, warum ein
Raum kalt bleibt, muss diese drei im Kopf zusammensetzen.

Der Weg durch die Anlage ist immer derselbe und immer in einer Richtung:

    Brücke → Messquelle ─┐
                         ├→ Zone (Ist, Soll, Entscheidung) → Aktoren
       Fensterkontakte ──┘

Diese Funktion bildet genau das ab. Sie rechnet nichts aus, was es nicht schon gibt -- sie
stellt zusammen, was in fünf Abfragen verstreut liegt, und benennt die Lücken: eine Zone
ohne Messquelle kann nichts regeln, eine ohne Aktor nichts bewirken, und ein Gerät ohne
Zone tut überhaupt nichts.
"""

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.db.models.device import Device, DeviceCapabilityLink, ZoneDevice
from thermoctl.db.models.lookup import DeviceCapability, DeviceRole
from thermoctl.db.models.zone import Zone
from thermoctl.domain.device_assignment import REQUIRED_CAPABILITY, TEMPERATURE_SOURCE


@dataclass(frozen=True)
class DevicePicture:
    id: int
    name: str
    modell: str | None
    capabilities: list[str]
    active: bool
    # Nur gesetzt, wenn dieses Geraet an dieser Stelle nachweislich nicht kann, was von
    # ihm verlangt wird. Die Pruefung bei der Zuordnung verhindert neue solche Faelle;
    # die alten stehen schon in der Datenbank und wuerden sonst nie auffallen.
    ungeeignet: str | None = None
    # Die Kennung der `zone_device`-Zeile, ueber die dieses Geraet an dieser Stelle
    # haengt -- None bei der Messquelle (die ist eine Spalte an der Zone, keine Zeile)
    # und bei Geraeten ohne Zone. Die Oberflaeche braucht sie, um ein Geraet wieder
    # herausziehen zu koennen.
    assignment_id: int | None = None


@dataclass(frozen=True)
class ZonePicture:
    zone_id: int
    name: str
    temperature_source: DevicePicture | None
    window_contacts: list[DevicePicture] = field(default_factory=list)
    actuators: list[DevicePicture] = field(default_factory=list)
    controllere: list[DevicePicture] = field(default_factory=list)

    @property
    def maengel(self) -> list[str]:
        """Was diese Zone am Regeln hindert. Leer heisst: vollstaendig verdrahtet."""
        fehlt = []
        if self.temperature_source is None:
            fehlt.append("keine Messquelle — ohne Ist-Wert entscheidet die Regelung nichts")
        if not self.actuators:
            fehlt.append("kein Aktor — die Entscheidung erreicht kein Ventil")
        for device in [self.temperature_source, *self.window_contacts, *self.actuators]:
            if device is not None and device.ungeeignet:
                fehlt.append(device.ungeeignet)
        return fehlt


@dataclass(frozen=True)
class PlantDiagram:
    zones: list[ZonePicture]
    without_zone: list[DevicePicture]


def _picture(
    device: Device,
    capabilities: dict[int, list[str]],
    codes: dict[int, set[str]],
    stelle: str | None = None,
    assignment_id: int | None = None,
) -> DevicePicture:
    # `None` heisst "keine Anforderung" -- so stehen die Geraete ohne Zone da, an die
    # niemand etwas verlangt. Ohne die Unterscheidung waere ein herrenloses Ventil als
    # "misst keine Temperatur" markiert worden.
    verlangt = REQUIRED_CAPABILITY.get(stelle or "")
    ungeeignet = None
    kann = codes.get(device.id, set())
    if verlangt is not None and kann and verlangt[0] not in kann:
        ungeeignet = (
            f"'{device.display_name}' {verlangt[1]} — diese Zuordnung wirkt nicht"
        )
    return DevicePicture(
        id=device.id,
        name=device.display_name,
        modell=device.model,
        capabilities=sorted(capabilities.get(device.id, [])),
        active=device.is_enabled,
        ungeeignet=ungeeignet,
        assignment_id=assignment_id,
    )


def plant_diagram(session: Session, zones: list[Zone]) -> PlantDiagram:
    """Der Weg durch die Anlage, je Zone -- und was ausserhalb jeder Zone liegt."""
    devices = {g.id: g for g in session.scalars(select(Device))}
    capabilities: dict[int, list[str]] = {}
    for device_id, bezeichnung in session.execute(
        select(DeviceCapabilityLink.device_id, DeviceCapability.label).join(
            DeviceCapability, DeviceCapability.id == DeviceCapabilityLink.capability_id
        )
    ):
        capabilities.setdefault(device_id, []).append(bezeichnung)

    # Die Codes getrennt von den Bezeichnungen: Die einen sind fuer den Leser da, die
    # anderen fuer den Vergleich mit ERFORDERLICHE_FAEHIGKEIT.
    codes: dict[int, set[str]] = {}
    for device_id, code in session.execute(
        select(DeviceCapabilityLink.device_id, DeviceCapability.code).join(
            DeviceCapability, DeviceCapability.id == DeviceCapabilityLink.capability_id
        )
    ):
        codes.setdefault(device_id, set()).add(code)

    rollen = {r.id: r.code for r in session.scalars(select(DeviceRole))}
    by_zone: dict[int, dict[str, list[DevicePicture]]] = {
        zone.id: {"actuator": [], "window_contact": [], "controller": []} for zone in zones
    }
    zugeordnet: set[int] = set()
    for assignment in session.scalars(
        select(ZoneDevice)
        .where(ZoneDevice.zone_id.in_([zone.id for zone in zones]))
        .order_by(ZoneDevice.sort_order, ZoneDevice.id)
    ):
        device = devices.get(assignment.device_id)
        rolle = rollen.get(assignment.device_role_id)
        if device is None or rolle not in by_zone[assignment.zone_id]:
            continue
        by_zone[assignment.zone_id][rolle].append(
            _picture(device, capabilities, codes, rolle, assignment.id)
        )
        zugeordnet.add(device.id)

    pictures = []
    for zone in zones:
        source = devices.get(zone.temperature_source_device_id or 0)
        if source is not None:
            zugeordnet.add(source.id)
        pictures.append(
            ZonePicture(
                zone_id=zone.id,
                name=zone.display_name,
                temperature_source=(
                    _picture(source, capabilities, codes, TEMPERATURE_SOURCE) if source else None
                ),
                window_contacts=by_zone[zone.id]["window_contact"],
                actuators=by_zone[zone.id]["actuator"],
                controllere=by_zone[zone.id]["controller"],
            )
        )

    # Geraete, die keiner Zone zugeordnet sind. Sie melden zwar Werte, aber die
    # Regelung sieht sie nicht -- und das ist der haeufigste Grund, warum ein neu
    # eingebundener Sensor "nicht ankommt".
    without_zone = [
        _picture(device, capabilities, codes)
        for device in sorted(devices.values(), key=lambda g: g.display_name)
        if device.id not in zugeordnet
    ]
    return PlantDiagram(zones=pictures, without_zone=without_zone)
