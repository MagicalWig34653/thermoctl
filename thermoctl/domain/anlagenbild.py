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
from thermoctl.domain.geraetezuordnung import ERFORDERLICHE_FAEHIGKEIT, MESSQUELLE


@dataclass(frozen=True)
class Geraetebild:
    id: int
    name: str
    modell: str | None
    faehigkeiten: list[str]
    aktiv: bool
    # Nur gesetzt, wenn dieses Geraet an dieser Stelle nachweislich nicht kann, was von
    # ihm verlangt wird. Die Pruefung bei der Zuordnung verhindert neue solche Faelle;
    # die alten stehen schon in der Datenbank und wuerden sonst nie auffallen.
    ungeeignet: str | None = None


@dataclass(frozen=True)
class Zonenbild:
    zone_id: int
    name: str
    messquelle: Geraetebild | None
    fensterkontakte: list[Geraetebild] = field(default_factory=list)
    aktoren: list[Geraetebild] = field(default_factory=list)
    bediengeraete: list[Geraetebild] = field(default_factory=list)

    @property
    def maengel(self) -> list[str]:
        """Was diese Zone am Regeln hindert. Leer heisst: vollstaendig verdrahtet."""
        fehlt = []
        if self.messquelle is None:
            fehlt.append("keine Messquelle — ohne Ist-Wert entscheidet die Regelung nichts")
        if not self.aktoren:
            fehlt.append("kein Aktor — die Entscheidung erreicht kein Ventil")
        for geraet in [self.messquelle, *self.fensterkontakte, *self.aktoren]:
            if geraet is not None and geraet.ungeeignet:
                fehlt.append(geraet.ungeeignet)
        return fehlt


@dataclass(frozen=True)
class Anlagenbild:
    zonen: list[Zonenbild]
    ohne_zone: list[Geraetebild]


def _bild(
    geraet: Device,
    faehigkeiten: dict[int, list[str]],
    codes: dict[int, set[str]],
    stelle: str | None = None,
) -> Geraetebild:
    # `None` heisst "keine Anforderung" -- so stehen die Geraete ohne Zone da, an die
    # niemand etwas verlangt. Ohne die Unterscheidung waere ein herrenloses Ventil als
    # "misst keine Temperatur" markiert worden.
    verlangt = ERFORDERLICHE_FAEHIGKEIT.get(stelle or "")
    ungeeignet = None
    kann = codes.get(geraet.id, set())
    if verlangt is not None and kann and verlangt[0] not in kann:
        ungeeignet = (
            f"'{geraet.display_name}' {verlangt[1]} — diese Zuordnung wirkt nicht"
        )
    return Geraetebild(
        id=geraet.id,
        name=geraet.display_name,
        modell=geraet.model,
        faehigkeiten=sorted(faehigkeiten.get(geraet.id, [])),
        aktiv=geraet.is_enabled,
        ungeeignet=ungeeignet,
    )


def anlagenbild(session: Session, zonen: list[Zone]) -> Anlagenbild:
    """Der Weg durch die Anlage, je Zone -- und was ausserhalb jeder Zone liegt."""
    geraete = {g.id: g for g in session.scalars(select(Device))}
    faehigkeiten: dict[int, list[str]] = {}
    for geraet_id, bezeichnung in session.execute(
        select(DeviceCapabilityLink.device_id, DeviceCapability.label).join(
            DeviceCapability, DeviceCapability.id == DeviceCapabilityLink.capability_id
        )
    ):
        faehigkeiten.setdefault(geraet_id, []).append(bezeichnung)

    # Die Codes getrennt von den Bezeichnungen: Die einen sind fuer den Leser da, die
    # anderen fuer den Vergleich mit ERFORDERLICHE_FAEHIGKEIT.
    codes: dict[int, set[str]] = {}
    for geraet_id, code in session.execute(
        select(DeviceCapabilityLink.device_id, DeviceCapability.code).join(
            DeviceCapability, DeviceCapability.id == DeviceCapabilityLink.capability_id
        )
    ):
        codes.setdefault(geraet_id, set()).add(code)

    rollen = {r.id: r.code for r in session.scalars(select(DeviceRole))}
    nach_zone: dict[int, dict[str, list[Geraetebild]]] = {
        zone.id: {"actuator": [], "window_contact": [], "controller": []} for zone in zonen
    }
    zugeordnet: set[int] = set()
    for zuordnung in session.scalars(
        select(ZoneDevice)
        .where(ZoneDevice.zone_id.in_([zone.id for zone in zonen]))
        .order_by(ZoneDevice.sort_order, ZoneDevice.id)
    ):
        geraet = geraete.get(zuordnung.device_id)
        rolle = rollen.get(zuordnung.device_role_id)
        if geraet is None or rolle not in nach_zone[zuordnung.zone_id]:
            continue
        nach_zone[zuordnung.zone_id][rolle].append(
            _bild(geraet, faehigkeiten, codes, rolle)
        )
        zugeordnet.add(geraet.id)

    bilder = []
    for zone in zonen:
        quelle = geraete.get(zone.temperature_source_device_id or 0)
        if quelle is not None:
            zugeordnet.add(quelle.id)
        bilder.append(
            Zonenbild(
                zone_id=zone.id,
                name=zone.display_name,
                messquelle=_bild(quelle, faehigkeiten, codes, MESSQUELLE) if quelle else None,
                fensterkontakte=nach_zone[zone.id]["window_contact"],
                aktoren=nach_zone[zone.id]["actuator"],
                bediengeraete=nach_zone[zone.id]["controller"],
            )
        )

    # Geraete, die keiner Zone zugeordnet sind. Sie melden zwar Werte, aber die
    # Regelung sieht sie nicht -- und das ist der haeufigste Grund, warum ein neu
    # eingebundener Sensor "nicht ankommt".
    ohne_zone = [
        _bild(geraet, faehigkeiten, codes)
        for geraet in sorted(geraete.values(), key=lambda g: g.display_name)
        if geraet.id not in zugeordnet
    ]
    return Anlagenbild(zonen=bilder, ohne_zone=ohne_zone)
