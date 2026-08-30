"""Switching adapters with a database-backed dry-run bolt."""

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Protocol
from urllib import parse, request

from sqlalchemy.orm import Session

from thermoctl.config import Settings
from thermoctl.db.models.operations import Setting


@dataclass(frozen=True)
class SwitchResult:
    executed: bool
    description: str
    errors: str | None = None


class Actuator(Protocol):
    def description(self) -> str: ...

    async def switching(self, on: bool) -> SwitchResult: ...


class HttpTransport(Protocol):
    async def post(
        self, url: str, data: Mapping[str, str], headers: Mapping[str, str]
    ) -> Mapping[str, object]: ...


class MqttPublisher(Protocol):
    async def publishing(
        self, topic: str, payload: str, *, switches: bool, retained: bool = False
    ) -> bool: ...


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


class Zigbee2MqttThermostat:
    """Drives a Zigbee2MQTT thermostatic radiator valve (e.g. WT-A03E).

    Unlike `Zigbee2MqttValve`, this device has no `state` topic to switch -- it has
    no on/off output at all. It is driven through two features together:
    `system_mode` (`heat` / `off`) and `occupied_heating_setpoint` (5-30 degrees C
    in 0.5 degree steps). `switching(True)` arms heating at the given setpoint;
    `switching(False)` turns the valve off. Both move the actual valve, so both go
    through the same dry-run bolt as `Zigbee2MqttValve` -- `switches=True` here too.

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
    ) -> None:
        self._session = session
        self._client = client
        self._topic = f"{base.rstrip('/')}/{device_name}/set"
        self._device_name = device_name
        self._setpoint_c = setpoint_c

    def description(self) -> str:
        return f"Zigbee2MQTT-Thermostat {self._device_name}"

    async def switching(self, on: bool) -> SwitchResult:
        if not on:
            payload = json.dumps({"system_mode": "off"})
        else:
            if not (
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
                {
                    "system_mode": "heat",
                    "occupied_heating_setpoint": float(
                        _quantized_setpoint(self._setpoint_c)
                    ),
                }
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


class UrllibHttpTransport:
    """Small HTTP wrapper, so the adapter doesn't need another dependency."""

    async def post(
        self, url: str, data: Mapping[str, str], headers: Mapping[str, str]
    ) -> Mapping[str, object]:
        return await asyncio.to_thread(self._post_sync, url, data, headers)

    @staticmethod
    def _post_sync(
        url: str, data: Mapping[str, str], headers: Mapping[str, str]
    ) -> Mapping[str, object]:
        http_request = request.Request(  # noqa: S310 -- URL comes from the adapter configuration
            url,
            data=parse.urlencode(data).encode(),
            headers=dict(headers),
            method="POST",
        )
        with request.urlopen(http_request, timeout=10) as response:  # noqa: S310
            result = json.loads(response.read())
        if not isinstance(result, dict):
            raise ValueError("Meross-Antwort ist kein Objekt")
        return result


class MerossSwitch:
    """Switches a Meross socket that serves as a valve in the plant.

    **Untested against the real cloud.** The structure of the two calls is derived
    from the publicly documented interface, but has never been run against a real
    account — at this stage there are no credentials, and the dry run forbids the
    attempt. Depending on the firmware version, Meross additionally requires a signed
    payload (timestamp, nonce, checksum); if that's missing here, it will surface on
    the first real call.

    This is deliberately left as is and not presented as finished: the adapter is
    fully wired up and verifiable in the dry run, but its payload is an educated
    guess. **Before arming in phase 4, this exact call needs to be checked once
    against the real cloud.** Noted in docs/offene-entscheidungen.md.
    """

    def __init__(
        self,
        session: Session,
        settings: Settings,
        devices_id: str,
        *,
        channel: int = 0,
        transport: HttpTransport | None = None,
        api_base: str | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._devices_id = devices_id
        self._kanal = channel
        self._transport = transport or UrllibHttpTransport()
        self._api_basis = (api_base or settings.meross_api_base).rstrip("/")

    def description(self) -> str:
        return f"Meross-Schalter {self._devices_id}"

    async def switching(self, on: bool) -> SwitchResult:
        command = "ON" if on else "OFF"
        message = (
            f"{self._api_basis}/v1/Device/devControl: Geraet {self._devices_id}, "
            f"Kanal {self._kanal}, Zustand {command}"
        )
        if not switching_allowed(self._session):
            return SwitchResult(False, f"Trockenlauf, haette gesendet: {message}")

        if self._settings.meross_email is None or self._settings.meross_password is None:
            return SwitchResult(False, f"Nicht konfiguriert: {message}")

        try:
            login = await self._transport.post(
                f"{self._api_basis}/v1/Auth/signIn",
                {
                    "email": self._settings.meross_email,
                    "password": self._settings.meross_password.get_secret_value(),
                    "encryption": "1",
                },
                {},
            )
            token = _meross_token(login)
            await self._transport.post(
                f"{self._api_basis}/v1/Device/devControl",
                {
                    "uuid": self._devices_id,
                    "channel": str(self._kanal),
                    "action": command,
                },
                {"Authorization": f"Basic {token}"},
            )
        except Exception as exc:
            return SwitchResult(False, message, str(exc))
        return SwitchResult(True, f"Gesendet: {message}")


def _meross_token(response: Mapping[str, object]) -> str:
    data = response.get("data")
    if not isinstance(data, dict):
        raise ValueError("Meross-Anmeldung lieferte kein Token")
    token = data.get("token")
    if not isinstance(token, str):
        raise ValueError("Meross-Anmeldung lieferte kein Token")
    return token
