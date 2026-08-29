"""Reiner Topic-Zuschnitt fuer Zigbee2MQTT."""

from dataclasses import dataclass
from enum import StrEnum


class Nachrichtenart(StrEnum):
    GERAETELISTE = "geraeteliste"
    BRUECKENZUSTAND = "brueckenzustand"
    GERAETEZUSTAND = "geraetezustand"
    ERREICHBARKEIT = "erreichbarkeit"
    UNBEKANNT = "unbekannt"


@dataclass(frozen=True)
class Zuschnitt:
    art: Nachrichtenart
    geraetename: str | None


def zuschneiden(topic: str, basis: str) -> Zuschnitt:
    """Ordnet genau die abonnierten Lese-Topics einer Nachrichtenart zu."""
    basis_teile = basis.strip("/").split("/")
    topic_teile = topic.split("/")
    unbekannt = Zuschnitt(Nachrichtenart.UNBEKANNT, None)
    if not basis_teile or topic_teile[: len(basis_teile)] != basis_teile:
        return unbekannt

    rest = topic_teile[len(basis_teile) :]
    if rest == ["bridge", "devices"]:
        return Zuschnitt(Nachrichtenart.GERAETELISTE, None)
    if rest == ["bridge", "state"]:
        return Zuschnitt(Nachrichtenart.BRUECKENZUSTAND, None)
    if not rest or rest[0] == "bridge":
        return unbekannt
    if len(rest) == 1 and rest[0]:
        return Zuschnitt(Nachrichtenart.GERAETEZUSTAND, rest[0])
    if len(rest) == 2 and rest[0] and rest[1] == "availability":
        return Zuschnitt(Nachrichtenart.ERREICHBARKEIT, rest[0])
    return unbekannt


def abonnements(basis: str) -> list[str]:
    """Liefert die vier absichtlich eng begrenzten Zigbee2MQTT-Abonnements."""
    basis = basis.strip("/")
    return [
        f"{basis}/bridge/devices",
        f"{basis}/bridge/state",
        f"{basis}/+",
        f"{basis}/+/availability",
    ]
