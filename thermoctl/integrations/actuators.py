"""Switching adapters with a database-backed dry-run bolt."""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Protocol

from sqlalchemy.orm import Session

from thermoctl.db.models.operations import Setting
from thermoctl.integrations.meross_mqtt import MerossCommandTransport, toggle_payload


@dataclass(frozen=True)
class SwitchResult:
    executed: bool
    description: str
    errors: str | None = None


class Actuator(Protocol):
    def description(self) -> str: ...

    async def switching(self, on: bool) -> SwitchResult: ...


class MqttPublisher(Protocol):
    async def publishing(
        self, topic: str, payload: str, *, switches: bool, retained: bool = False
    ) -> bool: ...


# The namespace a socket switches under. Confirmed by the reply of a real device: its
# `digest` reports its channel state as `togglex`.
TOGGLE_NAMESPACE = "Appliance.Control.ToggleX"


def switching_allowed(session: Session) -> bool:
    """Reads setting.control_armed. The only place that decides this."""
    setting = session.get(Setting, 1)
    return setting is not None and setting.control_armed


class Zigbee2MqttValve:
    def __init__(
        self, session: Session, client: MqttPublisher, base: str, device_name: str
    ) -> None:
        self._session = session
        self._client = client
        self._topic = f"{base.rstrip('/')}/{device_name}/set"
        self._device_name = device_name

    def description(self) -> str:
        return f"Zigbee2MQTT-Ventil {self._device_name}"

    async def switching(self, on: bool) -> SwitchResult:
        payload = json.dumps({"state": "ON" if on else "OFF"})
        message = f"{self._topic} mit Nutzlast {payload}"
        if not switching_allowed(self._session):
            return SwitchResult(False, f"Trockenlauf, haette gesendet: {message}")

        try:
            executed = await self._client.publishing(
                self._topic, payload, switches=True
            )
        except Exception as exc:
            return SwitchResult(False, message, str(exc))
        if not executed:
            return SwitchResult(
                False, message, "MQTT-Client hat die Veroeffentlichung abgewiesen"
            )
        return SwitchResult(True, f"Gesendet: {message}")


# The WT-A03E and similar Zigbee2MQTT thermostatic radiator valves accept
# `occupied_heating_setpoint` in this range and this step size only. Anything else
# is rejected by the device -- rejecting it here as well means the caller sees the
# reason immediately instead of a silently ignored command or a device-side error
# that never reaches this service.
THERMOSTAT_MIN_SETPOINT_C = Decimal("5")
THERMOSTAT_MAX_SETPOINT_C = Decimal("30")
_THERMOSTAT_SETPOINT_STEP_C = Decimal("0.5")


def _quantized_setpoint(value: Decimal) -> Decimal:
    """Rounds to the nearest step the device actually accepts."""
    steps = (value / _THERMOSTAT_SETPOINT_STEP_C).to_integral_value(rounding=ROUND_HALF_UP)
    return steps * _THERMOSTAT_SETPOINT_STEP_C


def thermostat_payload(
    on: bool, setpoint_c: Decimal, *, has_system_mode: bool
) -> dict[str, object]:
    """The exact payload `Zigbee2MqttThermostat.switching()` sends for this state.

    Exposed as its own function so a caller that only wants to *log* what a decision
    would send -- `services/publishing.py`'s command log entry -- writes down the same
    JSON the adapter actually builds, not a separately maintained approximation that
    could drift from it. `setpoint_c` is not quantised or range-checked here; the
    adapter does that itself before calling this for the `on` case.
    """
    if not on:
        return (
            {"system_mode": "off"}
            if has_system_mode
            # No `system_mode`: the lowest setpoint the device accepts is the only
            # way to tell it to stop heating. Deliberately the same bound the range
            # check in `switching()` uses, so "off" cannot mean a value the device
            # would refuse.
            else {"occupied_heating_setpoint": float(THERMOSTAT_MIN_SETPOINT_C)}
        )
    command: dict[str, object] = {
        "occupied_heating_setpoint": float(_quantized_setpoint(setpoint_c))
    }
    if has_system_mode:
        # Only where the device knows it. Zigbee2MQTT rejects a payload with an
        # unknown key, and it rejects it silently -- the command would simply not
        # arrive, and the valve would stay wherever it was.
        command["system_mode"] = "heat"
    return command


class Zigbee2MqttThermostat:
    """Drives a Zigbee2MQTT thermostatic radiator valve (e.g. WT-A03E).

    Unlike `Zigbee2MqttValve`, this device has no `state` topic to switch -- it has
    no on/off output at all. It is driven through `occupied_heating_setpoint`
    (5-30 degrees C in 0.5 degree steps) and, where the device has one, `system_mode`
    (`heat` / `off`). `switching(True)` arms heating at the given setpoint;
    `switching(False)` turns the valve off. Both move the actual valve, so both go
    through the same dry-run bolt as `Zigbee2MqttValve` -- `switches=True` here too.

    **`system_mode` is optional, and that is not a detail.** A Bosch BTH-RA has none;
    sending it anyway would put a key in the payload that Zigbee2MQTT rejects, and the
    command would be lost -- silently, because a rejected payload is not an error here.
    Such a valve is switched off the way it is switched off by hand: by setting its
    setpoint to the lowest value it accepts. That is a real difference in behaviour --
    off through `system_mode` closes the valve, a minimum setpoint leaves it regulating
    towards 5 degrees -- so the caller says which it is, and the description names it.

    The setpoint is passed in at construction, not hardcoded and not looked up by
    this adapter itself: a thermostat adapter without a setpoint would have nothing
    to arm heating at, and where that value comes from (a zone's current setpoint)
    is the caller's business, not this adapter's.
    """

    def __init__(
        self,
        session: Session,
        client: MqttPublisher,
        base: str,
        device_name: str,
        setpoint_c: Decimal,
        *,
        has_system_mode: bool = True,
    ) -> None:
        self._session = session
        self._client = client
        self._topic = f"{base.rstrip('/')}/{device_name}/set"
        self._device_name = device_name
        self._setpoint_c = setpoint_c
        self._has_system_mode = has_system_mode

    def description(self) -> str:
        return f"Zigbee2MQTT-Thermostat {self._device_name}"

    async def switching(self, on: bool) -> SwitchResult:
        if on and not (
            THERMOSTAT_MIN_SETPOINT_C <= self._setpoint_c <= THERMOSTAT_MAX_SETPOINT_C
        ):
            return SwitchResult(
                False,
                (
                    f"Sollwert {self._setpoint_c} liegt ausserhalb des am Geraet "
                    f"zulaessigen Bereichs {THERMOSTAT_MIN_SETPOINT_C}-"
                    f"{THERMOSTAT_MAX_SETPOINT_C} Grad C"
                ),
            )
        payload = json.dumps(
            thermostat_payload(on, self._setpoint_c, has_system_mode=self._has_system_mode)
        )
        message = f"{self._topic} mit Nutzlast {payload}"
        if not switching_allowed(self._session):
            return SwitchResult(False, f"Trockenlauf, haette gesendet: {message}")

        try:
            executed = await self._client.publishing(
                self._topic, payload, switches=True
            )
        except Exception as exc:
            return SwitchResult(False, message, str(exc))
        if not executed:
            return SwitchResult(
                False, message, "MQTT-Client hat die Veroeffentlichung abgewiesen"
            )
        return SwitchResult(True, f"Gesendet: {message}")


class MerossSwitch:
    """Switches a Meross socket that serves as a valve in the plant.

    The command goes over MQTT, not over HTTP. The earlier version posted to
    `/v1/Device/devControl` and called its own payload an educated guess -- the guess
    was wrong twice over: the path does not exist (the cloud answers 404), and the
    envelope of the calls that *do* exist is signed. `integrations/meross_mqtt.py`
    carries the path that was checked against a real account.

    Checked there against real hardware: a `GET`, and since then also a `SET
    Appliance.Control.ToggleX` against four sockets (all standing at `onoff=0`, set to
    `onoff=0` -- chosen deliberately so nothing about the plant could move). All four
    answered `SETACK` on the payload `toggle_payload` below builds, and the socket's
    `lmTime` had advanced on a subsequent read. Payload shape, signature and
    confirmation are therefore measured, not assumed.

    **`executed=True` means the device confirmed the command with a matching
    `SETACK` -- nothing more.** It is not a second read of the device's state after
    the fact; a socket that accepted the command but, for whatever reason, did not
    actually move its relay would still be reported as `executed=True` here. That is
    judged acceptable for this adapter (a `SETACK` is what the manufacturer's own
    apps treat as success too), but it is the honest boundary of the claim: this
    checks that the socket *accepted* the command, not that the room got warmer.

    **Two bolts, like `MqttClient`.** `frozen_switching_allowed` is read once, at
    process start, and handed in here unchanged for the adapter's whole lifetime --
    the counterpart of the flag `MqttClient.__init__` freezes for the Zigbee2MQTT
    path (see its docstring for why one caller relying on the runtime check alone
    was judged too thin a margin for something that moves a real heater). This path
    has no `MqttClient` to enforce that frozen bolt on its behalf -- the Meross
    command goes out through `MerossCommandTransport.send()`, not through
    `client.publishing()` -- so this adapter carries its own copy of the same
    check instead of relying on `switching_allowed(session)` (the *runtime* bolt)
    alone. Both must agree before anything reaches the transport.
    """

    def __init__(
        self,
        session: Session,
        transport: MerossCommandTransport | None,
        device_uuid: str,
        *,
        channel: int = 0,
        frozen_switching_allowed: bool = False,
    ) -> None:
        self._session = session
        self._transport = transport
        self._device_uuid = device_uuid
        self._channel = channel
        self._frozen_switching_allowed = frozen_switching_allowed

    def description(self) -> str:
        return f"Meross-Schalter {self._device_uuid}"

    async def switching(self, on: bool) -> SwitchResult:
        command = "ON" if on else "OFF"
        payload = toggle_payload(self._channel, on)
        message = (
            f"{TOGGLE_NAMESPACE} an {self._device_uuid}, "
            f"Kanal {self._channel}, Zustand {command}"
        )
        if not self._frozen_switching_allowed or not switching_allowed(self._session):
            return SwitchResult(False, f"Trockenlauf, haette gesendet: {message}")

        # No signed-in session to switch through -- the periodic sign-in
        # (`services/meross_session.py`) either has not run yet or was rejected by
        # the cloud. Reported the same way any other failed attempt is: `executed`
        # stays `False`, the reason names it, and nothing here retries or blocks --
        # the caller moves on to the next device.
        if self._transport is None:
            return SwitchResult(
                False, message, "Keine gueltige Meross-Sitzung vorhanden"
            )

        try:
            answer = await self._transport.send(
                self._device_uuid, TOGGLE_NAMESPACE, "SET", payload
            )
        except Exception as exc:
            return SwitchResult(False, message, str(exc))

        # The socket confirms with `SETACK`. Anything else is not a confirmation, and
        # treating it as one would report a heater as switched that never was.
        header = answer.get("header")
        method = header.get("method") if isinstance(header, Mapping) else None
        if method != "SETACK":
            return SwitchResult(
                False, message, f"Geraet bestaetigte nicht, sondern antwortete {method!r}"
            )
        return SwitchResult(True, f"Gesendet: {message}")
