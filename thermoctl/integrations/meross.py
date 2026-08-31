"""The Meross cloud: sign in and list the devices of an account.

Meross publishes no open interface. What stands here was checked against the real
account -- which was necessary, because the previous version was wrong on two counts:

* The API takes **no** form POST but a signed envelope of `params` (base64 of the
  actual JSON), `sign`, `timestamp` and `nonce`.
* The password goes out **MD5-hashed**. In plain text the service answers
  `apiStatus 1004, Wrong password` -- which looks like a typo of the user's and is
  none.

Either would only have shown up on the first real call. So this also records what is
**not** checked: see `MerossSwitch` in `actuators.py`.
"""

import asyncio
import base64
import hashlib
import json
import random
import string
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, cast
from urllib import request

from thermoctl.config import Settings

# The manufacturer's app secret. Not a credential of this installation, and not
# documented by the manufacturer either -- there is no official specification of this
# protocol at all. It has been public for years regardless, recovered by reverse
# engineering (the MerossIot project, among others) and reused unchanged there and
# here. Principle 2 is about credentials of this installation; this is neither -- but
# "publicly known" rests on other people's reverse engineering, not on a manufacturer
# document, and is worded that way here on purpose.
APP_SECRET = "23x17ahWarFH6w29"  # noqa: S105 -- protocol constant, not a credential

LOGIN_PATH = "/v1/Auth/signIn"
DEVICE_LIST_PATH = "/v1/Device/devList"


class MerossError(Exception):
    """The cloud refused, or answered something unexpected."""


class JsonTransport(Protocol):
    """A POST with a JSON body. Kept narrow so tests never reach the network."""

    async def post_json(
        self, url: str, body: Mapping[str, object], headers: Mapping[str, str]
    ) -> Mapping[str, object]: ...


@dataclass(frozen=True)
class MerossDevice:
    """A device as the account reports it."""

    uuid: str
    name: str
    model: str
    online: bool
    channels: int


@dataclass(frozen=True)
class MerossSession:
    """What a sign-in returns.

    `key` and `user_id` are the credentials for the MQTT path over which Meross
    actually switches -- the HTTP path for that does not exist (measured: 404).
    """

    token: str
    key: str
    user_id: str
    mqtt_domain: str


def credentials_configured(settings: Settings) -> bool:
    """Whether an account is set up at all -- email **and** password.

    The single place both `services/meross_discovery.py` (whether to attempt a sign-in)
    and `domain/interfaces.py` (whether the interfaces page may call the reconciliation
    "running") ask this question, so they cannot drift apart on what "configured"
    means. An email without a password, or the reverse, cannot sign in -- reporting
    such a half-entered account as configured would be wrong in both places.
    """
    return settings.meross_email is not None and settings.meross_password is not None


def _envelope(payload: Mapping[str, object]) -> dict[str, object]:
    """The signed envelope every request needs.

    `nonce` and `timestamp` enter the signature; a replayed envelope is invalid because
    of it. So it is built anew per request and never kept.
    """
    params = base64.b64encode(json.dumps(payload).encode()).decode()
    nonce = "".join(random.choices(string.ascii_uppercase + string.digits, k=16))  # noqa: S311
    timestamp = int(time.time() * 1000)
    signature = hashlib.md5(  # noqa: S324 -- prescribed by the protocol, not a safeguard
        f"{APP_SECRET}{timestamp}{nonce}{params}".encode()
    ).hexdigest()
    return {"params": params, "sign": signature, "timestamp": timestamp, "nonce": nonce}


def _headers(token: str | None = None) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "AppVersion": "1.3.0",
        "AppType": "MerossIOT",
        "AppLanguage": "DE",
        "User-Agent": "MerossIOT/1.3.0",
    }
    if token is not None:
        headers["Authorization"] = f"Basic {token}"
    return headers


def _data(answer: Mapping[str, object], what: str) -> object:
    status = answer.get("apiStatus")
    if status != 0:
        # `info` carries the service's reason and is the only trace an operator has --
        # without it the log says no more than "did not work".
        raise MerossError(
            f"{what} abgelehnt: apiStatus={status}, {answer.get('info') or 'ohne Begründung'}"
        )
    return answer.get("data")


async def sign_in(
    transport: JsonTransport, api_base: str, email: str, password: str
) -> MerossSession:
    """Signs the account in. The password goes out hashed, never in plain text."""
    hashed = hashlib.md5(password.encode()).hexdigest()  # noqa: S324 -- prescribed by the protocol
    answer = await transport.post_json(
        f"{api_base.rstrip('/')}{LOGIN_PATH}",
        _envelope(
            {
                "email": email,
                "password": hashed,
                "accountCountryCode": "DE",
                "encryption": 1,
                "agree": 0,
                "mobileInfo": {
                    "deviceModel": "",
                    "mobileOsVersion": "",
                    "mobileOs": "android",
                    "uuid": "",
                    "carrier": "",
                },
            }
        ),
        _headers(),
    )
    data = _data(answer, "Anmeldung")
    if not isinstance(data, Mapping):
        raise MerossError("Anmeldung lieferte keine Daten")
    fields = cast(Mapping[str, object], data)
    missing = [name for name in ("token", "key", "userid") if not fields.get(name)]
    if missing:
        raise MerossError(f"Anmeldung ohne {', '.join(missing)}")
    return MerossSession(
        token=str(fields["token"]),
        key=str(fields["key"]),
        user_id=str(fields["userid"]),
        mqtt_domain=str(fields.get("mqttDomain") or ""),
    )


async def device_list(
    transport: JsonTransport, api_base: str, session: MerossSession
) -> list[MerossDevice]:
    """The devices of the account.

    Only what has an identifier and a name comes back: a device without a `uuid` could
    not be found again later, and one without a name would be indistinguishable from
    the others in the list.
    """
    answer = await transport.post_json(
        f"{api_base.rstrip('/')}{DEVICE_LIST_PATH}", _envelope({}), _headers(session.token)
    )
    data = _data(answer, "Geräteliste")
    if not isinstance(data, list):
        raise MerossError("Geräteliste ist keine Liste")

    devices: list[MerossDevice] = []
    for raw in cast(list[object], data):
        if not isinstance(raw, Mapping):
            continue
        entry = cast(Mapping[str, object], raw)
        uuid = entry.get("uuid")
        name = entry.get("devName")
        if not isinstance(uuid, str) or not uuid or not isinstance(name, str) or not name:
            continue
        channels = entry.get("channels")
        devices.append(
            MerossDevice(
                uuid=uuid,
                name=name,
                model=str(entry.get("deviceType") or ""),
                # `onlineStatus` is 1 for reachable; anything else means "not right now".
                online=entry.get("onlineStatus") == 1,
                channels=len(channels) if isinstance(channels, list) else 1,
            )
        )
    return devices


class UrllibJsonTransport:
    """A JSON POST over the standard library.

    Its own transport rather than the form POST in `actuators.py`: Meross takes a JSON
    body, not `application/x-www-form-urlencoded`. The separation also keeps the tests
    dry -- nobody has to reach the network to check the parsing.
    """

    async def post_json(
        self, url: str, body: Mapping[str, object], headers: Mapping[str, str]
    ) -> Mapping[str, object]:
        return await asyncio.to_thread(self._post_sync, url, body, headers)

    @staticmethod
    def _post_sync(
        url: str, body: Mapping[str, object], headers: Mapping[str, str]
    ) -> Mapping[str, object]:
        call = request.Request(  # noqa: S310 -- address comes from the configuration
            url, data=json.dumps(body).encode(), headers=dict(headers), method="POST"
        )
        with request.urlopen(call, timeout=20) as answer:  # noqa: S310
            parsed = json.loads(answer.read())
        if not isinstance(parsed, dict):
            raise MerossError("Meross-Antwort ist kein Objekt")
        return cast(Mapping[str, object], parsed)
