"""Schaltadapter mit einem datenbankgestuetzten Trockenlauf-Riegel."""

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol
from urllib import parse, request

from sqlalchemy.orm import Session

from thermoctl.config import Settings
from thermoctl.db.models.operations import Setting


@dataclass(frozen=True)
class SwitchResult:
    ausgefuehrt: bool
    beschreibung: str
    errors: str | None = None


class Actuator(Protocol):
    def beschreibung(self) -> str: ...

    async def switching(self, ein: bool) -> SwitchResult: ...


class HttpTransport(Protocol):
    async def post(
        self, url: str, daten: Mapping[str, str], kopfzeilen: Mapping[str, str]
    ) -> Mapping[str, object]: ...


class MqttPublisher(Protocol):
    async def publishing(
        self, topic: str, payload: str, *, switches: bool, behalten: bool = False
    ) -> bool: ...


def switching_allowed(session: Session) -> bool:
    """Liest setting.control_armed. Einzige Stelle, die darueber entscheidet."""
    setting = session.get(Setting, 1)
    return setting is not None and setting.control_armed


class Zigbee2MqttVentil:
    def __init__(
        self, session: Session, client: MqttPublisher, basis: str, device_name: str
    ) -> None:
        self._session = session
        self._client = client
        self._topic = f"{basis.rstrip('/')}/{device_name}/set"
        self._device_name = device_name

    def beschreibung(self) -> str:
        return f"Zigbee2MQTT-Ventil {self._device_name}"

    async def switching(self, ein: bool) -> SwitchResult:
        payload = json.dumps({"state": "ON" if ein else "OFF"})
        message = f"{self._topic} mit Nutzlast {payload}"
        if not switching_allowed(self._session):
            return SwitchResult(False, f"Trockenlauf, haette gesendet: {message}")

        try:
            ausgefuehrt = await self._client.publishing(
                self._topic, payload, switches=True
            )
        except Exception as exc:
            return SwitchResult(False, message, str(exc))
        if not ausgefuehrt:
            return SwitchResult(
                False, message, "MQTT-Client hat die Veroeffentlichung abgewiesen"
            )
        return SwitchResult(True, f"Gesendet: {message}")


class UrllibHttpTransport:
    """Kleine HTTP-Huelle, damit der Adapter keine weitere Abhaengigkeit braucht."""

    async def post(
        self, url: str, daten: Mapping[str, str], kopfzeilen: Mapping[str, str]
    ) -> Mapping[str, object]:
        return await asyncio.to_thread(self._post_synchron, url, daten, kopfzeilen)

    @staticmethod
    def _post_synchron(
        url: str, daten: Mapping[str, str], kopfzeilen: Mapping[str, str]
    ) -> Mapping[str, object]:
        anfrage = request.Request(  # noqa: S310 -- URL kommt aus der Adapterkonfiguration
            url,
            data=parse.urlencode(daten).encode(),
            headers=dict(kopfzeilen),
            method="POST",
        )
        with request.urlopen(anfrage, timeout=10) as response:  # noqa: S310
            result = json.loads(response.read())
        if not isinstance(result, dict):
            raise ValueError("Meross-Antwort ist kein Objekt")
        return result


class MerossSwitch:
    """Schaltet eine Meross-Steckdose, die in der Anlage als Ventil dient.

    **Ungeprueft gegen die echte Cloud.** Der Aufbau der beiden Aufrufe ist aus der
    oeffentlich dokumentierten Schnittstelle abgeleitet, aber nie gegen ein echtes Konto
    ausgefuehrt worden — in dieser Phase liegen keine Zugangsdaten vor, und der
    Trockenlauf verbietet den Versuch. Meross verlangt je nach Firmwarestand zusaetzlich
    eine signierte Nutzlast (Zeitstempel, Nonce, Pruefsumme); sollte das hier fehlen,
    faellt es beim ersten echten Aufruf auf.

    Das ist bewusst so stehengelassen und nicht als fertig ausgegeben: Der Adapter ist
    vollstaendig verdrahtet und im Trockenlauf pruefbar, seine Nutzlast aber ist eine
    begruendete Annahme. **Vor dem Scharfschalten in Phase 4 gehoert genau dieser Aufruf
    einmal gegen die echte Cloud geprueft.** Vermerkt in docs/offene-entscheidungen.md.
    """

    def __init__(
        self,
        session: Session,
        settings: Settings,
        devices_id: str,
        *,
        kanal: int = 0,
        transport: HttpTransport | None = None,
        api_basis: str | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._devices_id = devices_id
        self._kanal = kanal
        self._transport = transport or UrllibHttpTransport()
        self._api_basis = (api_basis or settings.meross_api_base).rstrip("/")

    def beschreibung(self) -> str:
        return f"Meross-Schalter {self._devices_id}"

    async def switching(self, ein: bool) -> SwitchResult:
        command = "ON" if ein else "OFF"
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
    daten = response.get("data")
    if not isinstance(daten, dict):
        raise ValueError("Meross-Anmeldung lieferte kein Token")
    token = daten.get("token")
    if not isinstance(token, str):
        raise ValueError("Meross-Anmeldung lieferte kein Token")
    return token
