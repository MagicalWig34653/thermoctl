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
OPERATING_MODES = {"auto", "manual", "off"}

# Der Unterschluessel traegt, worauf sich ein Befehl bezieht: die Kennung des Modus bei
# `modus`, der Name des Regelparameters bei `parameter`. Sollwert, Betriebsart und Boost
# beziehen sich auf die Zone als Ganzes und haben keinen.
_PATTERN = re.compile(
    r"^(?P<praefix>[^/]+)/zones/(?P<zone>\d+)/command/(?P<art>[a-z_]+)"
    r"(?:/(?P<schluessel>[A-Za-z0-9_]+))?$"
)


@dataclass(frozen=True)
class Command:
    zone_id: int
    # "sollwert", "betriebsart", "boost", "modus" oder "parameter"
    kind: str
    temperature: Decimal | None = None
    operating_mode: str | None = None
    # Worauf sich der Befehl bezieht: die Moduskennung bei "modus", der Parametername
    # bei "parameter". Sonst None.
    mode_id: int | None = None
    parameter: str | None = None
    zahl: Decimal | None = None


class CommandError(ValueError):
    """Das Topic gehoert uns, aber die Nachricht ist unbrauchbar."""


def ist_command(topic: str, praefix: str) -> bool:
    """Ob dieses Topic ueberhaupt an uns gerichtet ist.

    Getrennt von `zerlegen`, damit der Nachrichtenverteiler ohne Ausnahmen entscheiden
    kann, ob eine Nachricht fuer Zigbee2MQTT oder fuer uns ist.
    """
    match = _PATTERN.match(topic)
    return (
        match is not None
        and match.group("praefix") == praefix.strip("/")
        and int(match.group("zone")) >= 1
    )


def zerlegen(topic: str, payload: bytes, praefix: str) -> Command:
    """Macht aus Topic und Nutzlast ein geprueftes Anliegen.

    Prueft **nicht** die fachlichen Grenzen -- sie stehen in der Domaene und
    gilt fuer alle Adapter gleich. Hier faellt nur durch, was gar keine Zahl ist.
    """
    match = _PATTERN.match(topic)
    if match is None or match.group("praefix") != praefix.strip("/"):
        raise CommandError(f"Kein Befehls-Topic dieses Dienstes: {topic}")

    zone_id = int(match.group("zone"))
    if zone_id < 1:
        # Dieselbe Grenze wie beim Senden (`_zonenbasis`). Eine Zone 0 gibt es nicht,
        # und was auf der einen Seite abgewiesen wird, soll auf der anderen nicht
        # angenommen werden.
        raise CommandError(f"Keine gueltige Zonenkennung: {zone_id}")
    kind = match.group("art")
    schluessel = match.group("schluessel")
    text = payload.decode("utf-8", errors="replace").strip()

    if kind in ("setpoint", "operating_mode", "boost") and schluessel is not None:
        raise CommandError(f"Die Befehlsart {kind!r} kennt keinen Unterschluessel")

    if kind == "setpoint":
        return Command(zone_id, kind, temperature=_number(text, "Temperatur"))

    if kind == "operating_mode":
        if text not in OPERATING_MODES:
            raise CommandError(f"Unbekannte Betriebsart: {text!r}")
        return Command(zone_id, kind, operating_mode=text)

    if kind == "boost":
        # Die Nutzlast ist gleichgueltig: Ein Knopf hat keinen Wert, nur ein Ereignis.
        # Home Assistant sendet `payload_press`; alles andere waere ebenso gemeint.
        return Command(zone_id, kind)

    if kind == "mode":
        if schluessel is None or not schluessel.isdigit() or int(schluessel) < 1:
            raise CommandError(f"Keine gueltige Moduskennung: {schluessel!r}")
        return Command(
            zone_id, kind, mode_id=int(schluessel), temperature=_number(text, "Temperatur")
        )

    if kind == "parameter":
        if schluessel is None or not re.fullmatch(r"[a-z][a-z0-9_]*", schluessel):
            raise CommandError(f"Kein gueltiger Parametername: {schluessel!r}")
        return Command(zone_id, kind, parameter=schluessel, zahl=_number(text, "Zahl"))

    raise CommandError(f"Unbekannte Befehlsart: {kind!r}")


def _number(text: str, was: str) -> Decimal:
    """Home Assistant sendet Punkt, ein Mensch auf der Kommandozeile auch mal Komma."""
    try:
        return Decimal(text.replace(",", "."))
    except InvalidOperation as exc:
        raise CommandError(f"Keine {was}: {text!r}") from exc


def commands_abonnements(praefix: str) -> list[str]:
    """Die Abonnements, die alle Befehle abdecken.

    Zwei statt eines: `+` in MQTT trifft **genau eine** Ebene, nie null und nie zwei.
    Ein einzelnes `.../befehl/+` liesse deshalb jeden Befehl mit Unterschluessel
    (`befehl/modus/3`) liegen -- die Drehregler je Modus haetten stumm nichts getan.
    `#` statt der zweiten Zeile waere kuerzer und faenge auch alles darunter, beliebig
    tief; die zwei Ebenen sind der ganze Vertrag, und mehr soll auch nicht ankommen.
    """
    basis = praefix.strip("/")
    return [f"{basis}/zones/+/command/+", f"{basis}/zones/+/command/+/+"]
