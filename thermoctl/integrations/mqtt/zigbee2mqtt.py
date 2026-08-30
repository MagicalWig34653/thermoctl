"""Pure topic parsing for Zigbee2MQTT."""

import json
from dataclasses import dataclass
from enum import StrEnum


class MessageKind(StrEnum):
    DEVICE_LIST = "geraeteliste"
    BRIDGE_STATE = "brueckenzustand"
    DEVICE_STATE = "geraetezustand"
    AVAILABILITY = "erreichbarkeit"
    UNBEKANNT = "unbekannt"


@dataclass(frozen=True)
class TopicCut:
    kind: MessageKind
    device_name: str | None


def zuschneiden(topic: str, basis: str) -> TopicCut:
    """Maps exactly the subscribed read topics to a message kind."""
    basis_teile = basis.strip("/").split("/")
    topic_teile = topic.split("/")
    unbekannt = TopicCut(MessageKind.UNBEKANNT, None)
    if not basis_teile or topic_teile[: len(basis_teile)] != basis_teile:
        return unbekannt

    rest = topic_teile[len(basis_teile) :]
    if rest == ["bridge", "devices"]:
        return TopicCut(MessageKind.DEVICE_LIST, None)
    if rest == ["bridge", "state"]:
        return TopicCut(MessageKind.BRIDGE_STATE, None)
    if not rest or rest[0] == "bridge":
        return unbekannt
    if len(rest) == 1 and rest[0]:
        return TopicCut(MessageKind.DEVICE_STATE, rest[0])
    if len(rest) == 2 and rest[0] and rest[1] == "availability":
        return TopicCut(MessageKind.AVAILABILITY, rest[0])
    return unbekannt


def abonnements(basis: str) -> list[str]:
    """Returns the four deliberately narrow Zigbee2MQTT subscriptions."""
    basis = basis.strip("/")
    return [
        f"{basis}/bridge/devices",
        f"{basis}/bridge/state",
        f"{basis}/+",
        f"{basis}/+/availability",
    ]


def bridge_reachable(payload: bytes) -> bool | None:
    """Tolerantly reads the known text and object forms of `bridge/state`."""
    try:
        daten = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if isinstance(daten, str):
        state = daten
    elif isinstance(daten, dict) and isinstance(daten.get("state"), str):
        state = daten["state"]
    else:
        return None
    if state == "online":
        return True
    if state == "offline":
        return False
    return None
