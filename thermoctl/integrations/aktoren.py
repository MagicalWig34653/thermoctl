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
class Schaltergebnis:
    ausgefuehrt: bool
    beschreibung: str
    fehler: str | None = None


class Aktor(Protocol):
    def beschreibung(self) -> str: ...

    async def schalten(self, ein: bool) -> Schaltergebnis: ...


class HttpTransport(Protocol):
    async def post(
        self, url: str, daten: Mapping[str, str], kopfzeilen: Mapping[str, str]
    ) -> Mapping[str, object]: ...


class MqttVeroeffentlicher(Protocol):
    async def veroeffentlichen(
        self, topic: str, nutzlast: str, *, scharf: bool
    ) -> bool: ...


def schalten_erlaubt(session: Session) -> bool:
    """Liest setting.control_armed. Einzige Stelle, die darueber entscheidet."""
    einstellung = session.get(Setting, 1)
    return einstellung is not None and einstellung.control_armed


class Zigbee2MqttVentil:
    def __init__(
        self, session: Session, client: MqttVeroeffentlicher, basis: str, geraetename: str
    ) -> None:
        self._session = session
        self._client = client
        self._topic = f"{basis.rstrip('/')}/{geraetename}/set"
        self._geraetename = geraetename

    def beschreibung(self) -> str:
        return f"Zigbee2MQTT-Ventil {self._geraetename}"

    async def schalten(self, ein: bool) -> Schaltergebnis:
        nutzlast = json.dumps({"state": "ON" if ein else "OFF"})
        nachricht = f"{self._topic} mit Nutzlast {nutzlast}"
        if not schalten_erlaubt(self._session):
            return Schaltergebnis(False, f"Trockenlauf, haette gesendet: {nachricht}")

        try:
            ausgefuehrt = await self._client.veroeffentlichen(
                self._topic, nutzlast, scharf=True
            )
        except Exception as exc:
            return Schaltergebnis(False, nachricht, str(exc))
        if not ausgefuehrt:
            return Schaltergebnis(
                False, nachricht, "MQTT-Client hat die Veroeffentlichung abgewiesen"
            )
        return Schaltergebnis(True, f"Gesendet: {nachricht}")


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
        with request.urlopen(anfrage, timeout=10) as antwort:  # noqa: S310
            ergebnis = json.loads(antwort.read())
        if not isinstance(ergebnis, dict):
            raise ValueError("Meross-Antwort ist kein Objekt")
        return ergebnis


class MerossSchalter:
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
        geraete_id: str,
        *,
        kanal: int = 0,
        transport: HttpTransport | None = None,
        api_basis: str | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._geraete_id = geraete_id
        self._kanal = kanal
        self._transport = transport or UrllibHttpTransport()
        self._api_basis = (api_basis or settings.meross_api_base).rstrip("/")

    def beschreibung(self) -> str:
        return f"Meross-Schalter {self._geraete_id}"

    async def schalten(self, ein: bool) -> Schaltergebnis:
        befehl = "ON" if ein else "OFF"
        nachricht = (
            f"{self._api_basis}/v1/Device/devControl: Geraet {self._geraete_id}, "
            f"Kanal {self._kanal}, Zustand {befehl}"
        )
        if not schalten_erlaubt(self._session):
            return Schaltergebnis(False, f"Trockenlauf, haette gesendet: {nachricht}")

        if self._settings.meross_email is None or self._settings.meross_password is None:
            return Schaltergebnis(False, f"Nicht konfiguriert: {nachricht}")

        try:
            anmeldung = await self._transport.post(
                f"{self._api_basis}/v1/Auth/signIn",
                {
                    "email": self._settings.meross_email,
                    "password": self._settings.meross_password.get_secret_value(),
                    "encryption": "1",
                },
                {},
            )
            token = _meross_token(anmeldung)
            await self._transport.post(
                f"{self._api_basis}/v1/Device/devControl",
                {
                    "uuid": self._geraete_id,
                    "channel": str(self._kanal),
                    "action": befehl,
                },
                {"Authorization": f"Basic {token}"},
            )
        except Exception as exc:
            return Schaltergebnis(False, nachricht, str(exc))
        return Schaltergebnis(True, f"Gesendet: {nachricht}")


def _meross_token(antwort: Mapping[str, object]) -> str:
    daten = antwort.get("data")
    if not isinstance(daten, dict):
        raise ValueError("Meross-Anmeldung lieferte kein Token")
    token = daten.get("token")
    if not isinstance(token, str):
        raise ValueError("Meross-Anmeldung lieferte kein Token")
    return token
