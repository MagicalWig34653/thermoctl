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


def trim(topic: str, base: str) -> TopicCut:
    """Maps exactly the subscribed read topics to a message kind."""
    base_parts = base.strip("/").split("/")
    topic_parts = topic.split("/")
    unknown = TopicCut(MessageKind.UNBEKANNT, None)
    if not base_parts or topic_parts[: len(base_parts)] != base_parts:
        return unknown

    rest = topic_parts[len(base_parts) :]
    if rest == ["bridge", "devices"]:
        return TopicCut(MessageKind.DEVICE_LIST, None)
    if rest == ["bridge", "state"]:
        return TopicCut(MessageKind.BRIDGE_STATE, None)
    if not rest or rest[0] == "bridge":
        return unknown
    if len(rest) == 1 and rest[0]:
        return TopicCut(MessageKind.DEVICE_STATE, rest[0])
    if len(rest) == 2 and rest[0] and rest[1] == "availability":
        return TopicCut(MessageKind.AVAILABILITY, rest[0])
    return unknown


def subscriptions(base: str) -> list[str]:
    """Returns the four deliberately narrow Zigbee2MQTT subscriptions."""
    base = base.strip("/")
    return [
        f"{base}/bridge/devices",
        f"{base}/bridge/state",
        f"{base}/+",
        f"{base}/+/availability",
    ]


def bridge_reachable(payload: bytes) -> bool | None:
    """Tolerantly reads the known text and object forms of `bridge/state`."""
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if isinstance(data, str):
        state = data
    elif isinstance(data, dict) and isinstance(data.get("state"), str):
        state = data["state"]
    else:
        return None
    if state == "online":
        return True
    if state == "offline":
        return False
    return None
