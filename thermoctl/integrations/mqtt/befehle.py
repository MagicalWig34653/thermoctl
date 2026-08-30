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

# Der Unterschluessel traegt, worauf sich ein Befehl bezieht: die Kennung des Modus bei
# `modus`, der Name des Regelparameters bei `parameter`. Sollwert, Betriebsart und Boost
# beziehen sich auf die Zone als Ganzes und haben keinen.
_MUSTER = re.compile(
    r"^(?P<praefix>[^/]+)/zonen/(?P<zone>\d+)/befehl/(?P<art>[a-z]+)"
    r"(?:/(?P<schluessel>[A-Za-z0-9_]+))?$"
)


@dataclass(frozen=True)
class Befehl:
    zone_id: int
    # "sollwert", "betriebsart", "boost", "modus" oder "parameter"
    art: str
    temperatur: Decimal | None = None
    betriebsart: str | None = None
    # Worauf sich der Befehl bezieht: die Moduskennung bei "modus", der Parametername
    # bei "parameter". Sonst None.
    modus_id: int | None = None
    parameter: str | None = None
    zahl: Decimal | None = None


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
    schluessel = treffer.group("schluessel")
    text = nutzlast.decode("utf-8", errors="replace").strip()

    if art in ("sollwert", "betriebsart", "boost") and schluessel is not None:
        raise Befehlsfehler(f"Die Befehlsart {art!r} kennt keinen Unterschluessel")

    if art == "sollwert":
        return Befehl(zone_id, art, temperatur=_zahl(text, "Temperatur"))

    if art == "betriebsart":
        if text not in BETRIEBSARTEN:
            raise Befehlsfehler(f"Unbekannte Betriebsart: {text!r}")
        return Befehl(zone_id, art, betriebsart=text)

    if art == "boost":
        # Die Nutzlast ist gleichgueltig: Ein Knopf hat keinen Wert, nur ein Ereignis.
        # Home Assistant sendet `payload_press`; alles andere waere ebenso gemeint.
        return Befehl(zone_id, art)

    if art == "modus":
        if schluessel is None or not schluessel.isdigit() or int(schluessel) < 1:
            raise Befehlsfehler(f"Keine gueltige Moduskennung: {schluessel!r}")
        return Befehl(
            zone_id, art, modus_id=int(schluessel), temperatur=_zahl(text, "Temperatur")
        )

    if art == "parameter":
        if schluessel is None or not re.fullmatch(r"[a-z][a-z0-9_]*", schluessel):
            raise Befehlsfehler(f"Kein gueltiger Parametername: {schluessel!r}")
        return Befehl(zone_id, art, parameter=schluessel, zahl=_zahl(text, "Zahl"))

    raise Befehlsfehler(f"Unbekannte Befehlsart: {art!r}")


def _zahl(text: str, was: str) -> Decimal:
    """Home Assistant sendet Punkt, ein Mensch auf der Kommandozeile auch mal Komma."""
    try:
        return Decimal(text.replace(",", "."))
    except InvalidOperation as exc:
        raise Befehlsfehler(f"Keine {was}: {text!r}") from exc


def befehls_abonnements(praefix: str) -> list[str]:
    """Die Abonnements, die alle Befehle abdecken.

    Zwei statt eines: `+` in MQTT trifft **genau eine** Ebene, nie null und nie zwei.
    Ein einzelnes `.../befehl/+` liesse deshalb jeden Befehl mit Unterschluessel
    (`befehl/modus/3`) liegen -- die Drehregler je Modus haetten stumm nichts getan.
    `#` statt der zweiten Zeile waere kuerzer und faenge auch alles darunter, beliebig
    tief; die zwei Ebenen sind der ganze Vertrag, und mehr soll auch nicht ankommen.
    """
    basis = praefix.strip("/")
    return [f"{basis}/zonen/+/befehl/+", f"{basis}/zonen/+/befehl/+/+"]
