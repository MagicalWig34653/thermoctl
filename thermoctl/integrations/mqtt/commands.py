"""Commands that arrive from outside on our own topics.

Home Assistant gets a thermostat per zone, and a thermostat you can turn also has to do
something. The discovery payload names two command topics for that -- setpoint and
operating mode. Without a receiver they would be a dial that turns and does nothing.

This module contains **only the parsing**: a topic and a payload become a validated
request. What happens with it is decided by the domain -- the same functions the
interface also uses, with the same limits.
"""

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

# The operating modes Home Assistant knows, and their counterpart here. `heat` is
# called `manual` in the discovery payload -- HA doesn't know a mode by that name, so
# the payload already converts it in both directions.
OPERATING_MODES = {"auto", "manual", "off"}

# The sub-key carries what a command refers to: the mode's id for `modus`, the control
# parameter's name for `parameter`. Setpoint, operating mode, and boost refer to the
# zone as a whole and have none.
_PATTERN = re.compile(
    r"^(?P<prefix>[^/]+)/zones/(?P<zone>\d+)/command/(?P<kind>[a-z_]+)"
    r"(?:/(?P<schluessel>[A-Za-z0-9_]+))?$"
)


@dataclass(frozen=True)
class Command:
    zone_id: int
    # "setpoint", "operating_mode", "boost", "cancel_override", "mode", or "parameter"
    kind: str
    temperature: Decimal | None = None
    operating_mode: str | None = None
    # What the command refers to: the mode id for "mode", the parameter name for
    # "parameter". Otherwise None.
    mode_id: int | None = None
    parameter: str | None = None
    number: Decimal | None = None


class CommandError(ValueError):
    """The topic is ours, but the message is unusable."""


def ist_command(topic: str, prefix: str) -> bool:
    """Whether this topic is addressed to us at all.

    Kept separate from `zerlegen`, so the message dispatcher can decide without
    exceptions whether a message is for Zigbee2MQTT or for us.
    """
    match = _PATTERN.match(topic)
    return (
        match is not None
        and match.group("prefix") == prefix.strip("/")
        and int(match.group("zone")) >= 1
    )


def split_topic(topic: str, payload: bytes, prefix: str) -> Command:
    """Turns a topic and a payload into a validated request.

    Does **not** check the business limits -- they live in the domain and apply
    equally to every adapter. Only what isn't even a number gets rejected here.
    """
    match = _PATTERN.match(topic)
    if match is None or match.group("prefix") != prefix.strip("/"):
        raise CommandError(f"Kein Befehls-Topic dieses Dienstes: {topic}")

    zone_id = int(match.group("zone"))
    if zone_id < 1:
        # The same limit as on sending (`_zonenbasis`). There is no zone 0, and what
        # is rejected on one side should not be accepted on the other.
        raise CommandError(f"Keine gueltige Zonenkennung: {zone_id}")
    kind = match.group("kind")
    key = match.group("schluessel")
    text = payload.decode("utf-8", errors="replace").strip()

    if kind in ("setpoint", "operating_mode", "boost", "cancel_override") and key is not None:
        raise CommandError(f"Die Befehlsart {kind!r} kennt keinen Unterschluessel")

    if kind == "setpoint":
        return Command(zone_id, kind, temperature=_number(text, "Temperatur"))

    if kind == "operating_mode":
        if text not in OPERATING_MODES:
            raise CommandError(f"Unbekannte Betriebsart: {text!r}")
        return Command(zone_id, kind, operating_mode=text)

    if kind == "boost":
        # The payload doesn't matter: a button has no value, only an event. Home
        # Assistant sends `payload_press`; anything else would mean the same thing.
        return Command(zone_id, kind)

    if kind == "cancel_override":
        # Same reasoning as `boost` above: a button, no value to carry. Ends
        # whatever override is currently running -- a boost included, since a boost
        # *is* an override (`domain/remote_control.py::boost`).
        return Command(zone_id, kind)

    if kind == "mode":
        if key is None or not key.isdigit() or int(key) < 1:
            raise CommandError(f"Keine gueltige Moduskennung: {key!r}")
        return Command(
            zone_id, kind, mode_id=int(key), temperature=_number(text, "Temperatur")
        )

    if kind == "parameter":
        if key is None or not re.fullmatch(r"[a-z][a-z0-9_]*", key):
            raise CommandError(f"Kein gueltiger Parametername: {key!r}")
        return Command(zone_id, kind, parameter=key, number=_number(text, "Zahl"))

    raise CommandError(f"Unbekannte Befehlsart: {kind!r}")


def _number(text: str, was: str) -> Decimal:
    """Home Assistant sends a period, a human on the command line sometimes a comma."""
    try:
        return Decimal(text.replace(",", "."))
    except InvalidOperation as exc:
        raise CommandError(f"Keine {was}: {text!r}") from exc


def command_subscriptions(prefix: str) -> list[str]:
    """The subscriptions that cover all commands.

    Two instead of one: `+` in MQTT matches **exactly one** level, never zero and
    never two. A single `.../command/+` would therefore leave every command with a
    sub-key (`command/mode/3`) unhandled -- the dial per mode would have silently done
    nothing. `#` instead of the second line would be shorter and would also catch
    everything below it, arbitrarily deep; the two levels are the whole contract, and
    nothing more should arrive either.
    """
    base = prefix.strip("/")
    return [f"{base}/zones/+/command/+", f"{base}/zones/+/command/+/+"]
