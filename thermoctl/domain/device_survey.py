"""The state of the devices — what is fine and what is not.

Up to this point, the device page showed a table with nine equally weighted columns:
display name, integration, model, capabilities, last message, battery, radio quality,
availability, zone. Everything was there, nothing stood out, and most cells held nothing
but an em dash.

The question someone comes to this page with, though, is almost always the same: **is
something wrong with my hardware?** A sensor that has been silent for two days, a
battery at seven percent, a device the bridge lists as offline. This module answers
exactly that — and it does so in the domain, so the thresholds are stated once instead
of living in a template.

What is connected *where*, on the other hand, is answered by the plant diagram. The two
pages share the devices, but not the question.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Literal

# From here down, a battery counts as weak. Not as empty: Zigbee devices report 100
# percent for a long time and then drop quickly; twenty percent still leaves days to
# get hold of a replacement cell.
BATTERY_LOW_PERCENT = Decimal(20)

# How often the Meross cloud is reconciled (`app.py::MEROSS_REFRESH_S`, defined here
# instead so both places share one number instead of two that can drift apart --
# `app.py` imports this constant rather than declaring its own).
MEROSS_RECONCILE_INTERVAL_SECONDS = 3600

# The threshold after which a Meross device counts as silent. Not the sensor timeout
# used for Zigbee2MQTT: that number answers "how long may a sensor stay quiet before
# control logic gives up on it", a question about the device itself. A Meross socket
# is never heard from directly -- its freshness is entirely a property of the hourly
# reconciliation (see the module docstring's note on `last_heard_kind`), so one missed
# pass must not immediately read as a defective socket. Two reconciliation intervals
# tolerate exactly one missed pass -- transient cloud errors, a restart during the
# hour -- before flagging it.
MEROSS_SILENT_AFTER_SECONDS = 2 * MEROSS_RECONCILE_INTERVAL_SECONDS

# Below this radio quality, the connection becomes unreliable. Zigbee2MQTT reports it
# as an LQI from 0 to 255; the threshold is experience, not a standard, which is why
# it is named here instead of hidden inside a condition.
RADIO_WEAK_LQI = 30

# These capabilities get no chip of their own: their value already appears in the same
# row, either as a number or as a finding. A "battery level" chip next to "58 %" says
# nothing the number does not already say -- it only spends attention that belongs to
# the two devices that actually stand out.
WITHOUT_CHIP = frozenset({"battery", "link_quality", "availability"})


@dataclass(frozen=True)
class Finding:
    """A sentence about what is wrong with this device."""

    kind: str  # "disabled", "offline", "silent", "battery", "radio"
    text: str


@dataclass(frozen=True)
class DeviceSurvey:
    device_id: int
    name: str
    model: str | None
    integration: str
    ist_group: bool
    capabilities: list[str]
    zones: list[str]
    last_heard: datetime | None
    battery: Decimal | None
    radio_quality: int | None
    findings: list[Finding] = field(default_factory=list)
    # How many capabilities were suppressed because their value already appears as a
    # number. Without this count, "reports nothing" could not be told apart from
    # "reports only battery and radio" -- and the page would claim, for every remote
    # control button, that it could not do anything at all.
    quiet_capabilities: int = 0

    @property
    def is_fine(self) -> bool:
        return not self.findings

    @property
    def severity(self) -> int:
        """For sorting: the smaller, the more urgent.

        A silent device ranks ahead of a weak battery -- one is a failure, the other
        just a warning.
        """
        rang = {"offline": 0, "silent": 1, "disabled": 2, "battery": 3, "radio": 4}
        return min((rang.get(b.kind, 9) for b in self.findings), default=9)


def _age_in_words(seconds: float) -> str:
    if seconds < 3600:
        return f"{int(seconds // 60)} Minuten"
    if seconds < 86400:
        return f"{int(seconds // 3600)} Stunden"
    days = int(seconds // 86400)
    return f"{days} {'Tag' if days == 1 else 'Tagen'}"


def findings(
    *,
    active: bool,
    last_heard: datetime | None,
    availability: str | None,
    battery: Decimal | None,
    radio_quality: int | None,
    silent_after_seconds: int,
    now: datetime,
    last_heard_kind: Literal["gemeldet", "abgeglichen"] = "gemeldet",
) -> list[Finding]:
    """What stands out about a device. Empty means: nothing.

    `stumm_nach_sekunden` comes from the global defaults — the same threshold by which
    control logic considers a sensor failed. A second number just for this page would
    mean the device list considers a device healthy that control logic has already
    given up on. That reasoning holds for a Zigbee2MQTT device, which reports in on
    its own; a caller passes a Meross-appropriate threshold instead, see
    `MEROSS_SILENT_AFTER_SECONDS`.

    `last_heard_kind` says what `last_heard` actually records, because the two
    integrations mean different things by it and the wording must not blur them:

    * `"gemeldet"` (the default): `last_heard` is the last message the device itself
      sent -- Zigbee2MQTT. Silence here is a statement about the device: it has gone
      quiet.
    * `"abgeglichen"`: `last_heard` is the last time our own hourly reconciliation
      confirmed the Meross cloud still calls the device online -- the device never
      sends us anything directly. Silence here is a statement about the reconciliation
      or the cloud's bookkeeping, not about the socket itself, and saying "hat sich
      nie gemeldet" would claim a communication channel that does not exist.
    """
    found: list[Finding] = []
    if not active:
        found.append(Finding("disabled", "in der Brücke abgeschaltet"))
    if availability is not None and availability.lower() == "offline":
        found.append(Finding("offline", "die Brücke führt es als offline"))
    if last_heard_kind == "abgeglichen":
        never_text = "wurde noch nie als online abgeglichen"
        stale_template = "seit {age} nicht mehr als online bestätigt"
    else:
        never_text = "hat sich noch nie gemeldet"
        stale_template = "seit {age} still"
    if last_heard is None:
        found.append(Finding("silent", never_text))
    elif (now - last_heard).total_seconds() > silent_after_seconds:
        age = _age_in_words((now - last_heard).total_seconds())
        found.append(Finding("silent", stale_template.format(age=age)))
    if battery is not None and battery <= BATTERY_LOW_PERCENT:
        found.append(Finding("battery", f"Batterie bei {battery:.0f} %"))
    if radio_quality is not None and radio_quality < RADIO_WEAK_LQI:
        found.append(Finding("radio", f"schwacher Funk (LQI {radio_quality})"))
    return found
