"""Pure derivation of fault notices from state transitions."""

from dataclasses import dataclass


@dataclass(frozen=True)
class FaultNotice:
    key: str
    severity: str
    title: str
    text: str


def sensor_notice(
    key: str,
    zone_name: str,
    before: str | None,
    after: str,
) -> FaultNotice | None:
    """Reports only entry into a sensor fault and its all-clear."""
    if (
        before is not None
        and after in {"veraltet", "keine_quelle"}
        and before != after
    ):
        reason = (
            "Der Temperaturwert ist veraltet."
            if after == "veraltet"
            else "Der Zone ist keine Temperaturquelle zugeordnet."
        )
        return FaultNotice(
            key=key,
            severity="stoerung",
            title=f"Sensorstoerung in {zone_name}",
            text=reason,
        )
    if after == "ok" and before in {"veraltet", "keine_quelle"}:
        return FaultNotice(
            key=key,
            severity="entwarnung",
            title=f"Sensor in {zone_name} wieder in Ordnung",
            text="Die Temperaturquelle liefert wieder aktuelle Werte.",
        )
    return None


def bridge_notice(
    reachable_before: bool | None, reachable_after: bool
) -> FaultNotice | None:
    """Reports failure and recovery of the Zigbee2MQTT bridge, each exactly once."""
    if not reachable_after and reachable_before is not False:
        return FaultNotice(
            key="zigbee2mqtt:bruecke",
            severity="stoerung",
            title="Zigbee2MQTT-Bruecke nicht erreichbar",
            text="Die Verbindung zur Zigbee2MQTT-Bruecke ist ausgefallen.",
        )
    if reachable_after and reachable_before is False:
        return FaultNotice(
            key="zigbee2mqtt:bruecke",
            severity="entwarnung",
            title="Zigbee2MQTT-Bruecke wieder erreichbar",
            text="Die Verbindung zur Zigbee2MQTT-Bruecke ist wiederhergestellt.",
        )
    return None
