"""Die MQTT-Topics auf Englisch.

Getrennt von den Web-Pfaden, weil es ein anderer Vertrag mit anderen Woertern ist -- und
weil der erste Anlauf beides in einen Topf warf und dabei `befehl/parameter` zu
`befehl/parameters` machte.
"""

SEGMENT = {
    "zonen": "zones", "zustand": "state", "befehl": "command",
    "verfuegbarkeit": "availability", "scharf": "armed",
    "ist_temperatur": "current_temperature", "sollwert": "setpoint",
    "betriebsart": "operating_mode", "sensorzustand": "sensor_state",
    "wuerde_heizen": "would_heat", "letzte_schaltung": "last_switch",
    "naechste_schaltung": "next_switch", "modus": "mode",
}
# Zigbee2MQTT und Home Assistant sind fremde Vertraege und bleiben Zeichen fuer Zeichen.
UNBERUEHRT = ("zigbee2mqtt", "homeassistant", "bridge")


def topic_uebersetzen(topic: str) -> str:
    teile = topic.split("/")
    if any(t in UNBERUEHRT for t in teile):
        return topic
    return "/".join(SEGMENT.get(t, t) for t in teile)
