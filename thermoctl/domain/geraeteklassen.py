import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast


@dataclass(frozen=True)
class Geraetebeschreibung:
    name: str
    adresse: str | None
    modell: str | None
    hersteller: str | None
    ist_gruppe: bool
    faehigkeiten: frozenset[str]


_FAEHIGKEIT_NACH_MERKMAL = {
    "temperature": "temperature",
    "local_temperature": "temperature",
    "humidity": "humidity",
    "battery": "battery",
    "linkquality": "link_quality",
    "illuminance": "illuminance",
    "illuminance_lux": "illuminance",
    "occupancy": "occupancy",
    "contact": "contact",
    "current_heating_setpoint": "setpoint",
    "occupied_heating_setpoint": "setpoint",
    "position": "valve_position",
    "valve_position": "valve_position",
    "pi_heating_demand": "valve_position",
    "power": "power",
    "energy": "energy",
}


def _text(wert: object) -> str | None:
    return wert if isinstance(wert, str) else None


def _faehigkeiten_sammeln(
    eintraege: list[object], *, in_schalter: bool = False
) -> set[str]:
    ergebnis: set[str] = set()
    for roh in eintraege:
        if not isinstance(roh, Mapping):
            continue
        eintrag = cast(Mapping[str, object], roh)
        typ = _text(eintrag.get("type"))
        merkmal = _text(eintrag.get("property")) or typ
        faehigkeit = _FAEHIGKEIT_NACH_MERKMAL.get(merkmal or "")
        if faehigkeit is not None:
            ergebnis.add(faehigkeit)
        if in_schalter and typ == "binary" and merkmal == "state":
            ergebnis.add("switch")

        features = eintrag.get("features")
        if isinstance(features, list):
            ergebnis.update(
                _faehigkeiten_sammeln(
                    cast(list[object], features), in_schalter=in_schalter or typ == "switch"
                )
            )
    return ergebnis


def faehigkeiten_aus_exposes(
    exposes: list[dict[str, object]],
) -> frozenset[str]:
    """Leitet nur ausdruecklich vereinbarte Faehigkeiten aus Zigbee2MQTT ab."""
    return frozenset(_faehigkeiten_sammeln(cast(list[object], exposes)))


def beschreibungen_aus_bridge_liste(
    nutzlast: str | bytes,
) -> list[Geraetebeschreibung]:
    """Liest die Geraetebeschreibungen aus der Zigbee2MQTT-Brueckenliste."""
    try:
        roh = json.loads(nutzlast)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("Geraeteliste ist kein gueltiges JSON") from exc
    if not isinstance(roh, list):
        raise ValueError("Geraeteliste muss eine JSON-Liste sein")

    beschreibungen: list[Geraetebeschreibung] = []
    for element in roh:
        if not isinstance(element, Mapping):
            continue
        eintrag = cast(Mapping[str, object], element)
        name = _text(eintrag.get("friendly_name"))
        if name is None:
            continue
        if eintrag.get("type") == "Coordinator" or name in {"Coordinator", "bridge"}:
            continue

        adresse = _text(eintrag.get("ieee_address"))
        definition_roh = eintrag.get("definition")
        definition = (
            cast(Mapping[str, object], definition_roh)
            if isinstance(definition_roh, Mapping)
            else {}
        )
        exposes_roh = definition.get("exposes")
        exposes = (
            cast(list[dict[str, object]], exposes_roh)
            if isinstance(exposes_roh, list)
            else []
        )
        beschreibungen.append(
            Geraetebeschreibung(
                name=name,
                adresse=adresse,
                modell=_text(definition.get("model")),
                hersteller=_text(definition.get("vendor")),
                ist_gruppe=adresse is None,
                faehigkeiten=faehigkeiten_aus_exposes(exposes),
            )
        )
    return beschreibungen
