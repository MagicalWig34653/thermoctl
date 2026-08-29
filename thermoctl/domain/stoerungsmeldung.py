"""Reine Ableitung von Stoerungsmeldungen aus Zustandswechseln."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Stoerungsmeldung:
    schluessel: str
    schwere: str
    titel: str
    text: str


def sensormeldung(
    schluessel: str,
    zonenname: str,
    vorher: str | None,
    nachher: str,
) -> Stoerungsmeldung | None:
    """Meldet nur den Eintritt in einen Sensorausfall und dessen Entwarnung."""
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
        return Stoerungsmeldung(
            schluessel=schluessel,
            schwere="stoerung",
            titel=f"Sensorstoerung in {zonenname}",
            text=grund,
        )
    if nachher == "ok" and vorher in {"veraltet", "keine_quelle"}:
        return Stoerungsmeldung(
            schluessel=schluessel,
            schwere="entwarnung",
            titel=f"Sensor in {zonenname} wieder in Ordnung",
            text="Die Temperaturquelle liefert wieder aktuelle Werte.",
        )
    return None


def brueckenmeldung(
    vorher_erreichbar: bool | None, nachher_erreichbar: bool
) -> Stoerungsmeldung | None:
    """Meldet Ausfall und Wiederkehr der Zigbee2MQTT-Bruecke je einmal."""
    if not nachher_erreichbar and vorher_erreichbar is not False:
        return Stoerungsmeldung(
            schluessel="zigbee2mqtt:bruecke",
            schwere="stoerung",
            titel="Zigbee2MQTT-Bruecke nicht erreichbar",
            text="Die Verbindung zur Zigbee2MQTT-Bruecke ist ausgefallen.",
        )
    if nachher_erreichbar and vorher_erreichbar is False:
        return Stoerungsmeldung(
            schluessel="zigbee2mqtt:bruecke",
            schwere="entwarnung",
            titel="Zigbee2MQTT-Bruecke wieder erreichbar",
            text="Die Verbindung zur Zigbee2MQTT-Bruecke ist wiederhergestellt.",
        )
    return None
