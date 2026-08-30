"""Befehle, die von aussen auf den eigenen Topics ankommen.

Home Assistant bekommt je Zone einen Thermostat, und ein Thermostat, den man drehen kann,
muss auch etwas bewirken. Die Discovery-Nutzlast nennt dafuer zwei Befehls-Topics --
Sollwert und Betriebsart. Ohne einen Empfaenger waeren sie ein Regler, der sich dreht und
nichts tut.

Dieses Modul enthaelt **nur die Zerlegung**: aus einem Topic und einer Nutzlast wird ein
geprueftes Anliegen. Was damit geschieht, entscheidet die Domaene -- dieselben Funktionen,
die auch die Oberflaeche benutzt, mit denselben Grenzen.
"""

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

# Die Betriebsarten, die Home Assistant kennt, und ihre Entsprechung hier. `heat` heisst
# in der Discovery-Nutzlast `manual` -- HA kennt keinen Modus dieses Namens, und die
# Nutzlast rechnet ihn deshalb schon in beide Richtungen um.
BETRIEBSARTEN = {"auto", "manual", "off"}

_MUSTER = re.compile(r"^(?P<praefix>[^/]+)/zonen/(?P<zone>\d+)/befehl/(?P<art>[a-z]+)$")


@dataclass(frozen=True)
class Befehl:
    zone_id: int
    art: str  # "sollwert" oder "betriebsart"
    temperatur: Decimal | None = None
    betriebsart: str | None = None


class Befehlsfehler(ValueError):
    """Das Topic gehoert uns, aber die Nachricht ist unbrauchbar."""


def ist_befehl(topic: str, praefix: str) -> bool:
    """Ob dieses Topic ueberhaupt an uns gerichtet ist.

    Getrennt von `zerlegen`, damit der Nachrichtenverteiler ohne Ausnahmen entscheiden
    kann, ob eine Nachricht fuer Zigbee2MQTT oder fuer uns ist.
    """
    treffer = _MUSTER.match(topic)
    return (
        treffer is not None
        and treffer.group("praefix") == praefix.strip("/")
        and int(treffer.group("zone")) >= 1
    )


def zerlegen(topic: str, nutzlast: bytes, praefix: str) -> Befehl:
    """Macht aus Topic und Nutzlast ein geprueftes Anliegen.

    Prueft **nicht** die fachlichen Grenzen -- sie stehen in der Domaene und
    gilt fuer alle Adapter gleich. Hier faellt nur durch, was gar keine Zahl ist.
    """
    treffer = _MUSTER.match(topic)
    if treffer is None or treffer.group("praefix") != praefix.strip("/"):
        raise Befehlsfehler(f"Kein Befehls-Topic dieses Dienstes: {topic}")

    zone_id = int(treffer.group("zone"))
    if zone_id < 1:
        # Dieselbe Grenze wie beim Senden (`_zonenbasis`). Eine Zone 0 gibt es nicht,
        # und was auf der einen Seite abgewiesen wird, soll auf der anderen nicht
        # angenommen werden.
        raise Befehlsfehler(f"Keine gueltige Zonenkennung: {zone_id}")
    art = treffer.group("art")
    text = nutzlast.decode("utf-8", errors="replace").strip()

    if art == "sollwert":
        try:
            return Befehl(zone_id, art, temperatur=Decimal(text.replace(",", ".")))
        except InvalidOperation as exc:
            raise Befehlsfehler(f"Keine Temperatur: {text!r}") from exc

    if art == "betriebsart":
        if text not in BETRIEBSARTEN:
            raise Befehlsfehler(f"Unbekannte Betriebsart: {text!r}")
        return Befehl(zone_id, art, betriebsart=text)

    raise Befehlsfehler(f"Unbekannte Befehlsart: {art!r}")


def befehls_abonnement(praefix: str) -> str:
    """Das eine Abonnement, das alle Befehle abdeckt."""
    return f"{praefix.strip('/')}/zonen/+/befehl/+"
