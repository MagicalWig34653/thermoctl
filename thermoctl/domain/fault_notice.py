"""Pure derivation of fault notices from state transitions."""

from dataclasses import dataclass


@dataclass(frozen=True)
class FaultNotice:
    schluessel: str
    schwere: str
    titel: str
    text: str


def sensornotice(
    schluessel: str,
    zone_name: str,
    vorher: str | None,
    nachher: str,
) -> FaultNotice | None:
    """Reports only entry into a sensor fault and its all-clear."""
    if (
        vorher is not None
        and nachher in {"veraltet", "keine_quelle"}
        and vorher != nachher
    ):
        grund = (
            "Der Temperaturwert ist veraltet."
            if nachher == "veraltet"
            else "Der Zone ist keine Temperaturquelle zugeordnet."
        )
        return FaultNotice(
            schluessel=schluessel,
            schwere="stoerung",
            titel=f"Sensorstoerung in {zone_name}",
            text=grund,
        )
    if nachher == "ok" and vorher in {"veraltet", "keine_quelle"}:
        return FaultNotice(
            schluessel=schluessel,
            schwere="entwarnung",
            titel=f"Sensor in {zone_name} wieder in Ordnung",
            text="Die Temperaturquelle liefert wieder aktuelle Werte.",
        )
    return None


def bridge_notice(
    reachable_before: bool | None, reachable_after: bool
) -> FaultNotice | None:
    """Reports failure and recovery of the Zigbee2MQTT bridge, each exactly once."""
    if not reachable_after and reachable_before is not False:
        return FaultNotice(
            schluessel="zigbee2mqtt:bruecke",
            schwere="stoerung",
            titel="Zigbee2MQTT-Bruecke nicht erreichbar",
            text="Die Verbindung zur Zigbee2MQTT-Bruecke ist ausgefallen.",
        )
    if reachable_after and reachable_before is False:
        return FaultNotice(
            schluessel="zigbee2mqtt:bruecke",
            schwere="entwarnung",
            titel="Zigbee2MQTT-Bruecke wieder erreichbar",
            text="Die Verbindung zur Zigbee2MQTT-Bruecke ist wiederhergestellt.",
        )
    return None
