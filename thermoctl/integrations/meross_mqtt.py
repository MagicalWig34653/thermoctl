"""The path over which Meross actually switches.

The previous adapter posted to `/v1/Device/devControl`. That path does not exist: the
real cloud answers HTTP 404. It had never been run against an account, and the
docstring said so -- it was an educated guess, and the guess was wrong.

Meross switches over MQTT. The cloud's sign-in hands out everything needed for it
(`mqttDomain`, `key`, `userid`), and the broker takes TLS on port 443:

* **Client identifier** `app:<hex>` with a fresh random half. Two connections sharing an
  identifier evict each other in a loop -- that exact fault already cost this project a
  night with Zigbee2MQTT, so nothing here is derived from a device or an account.
* **Credentials** are the account id as user and `md5(user_id + key)` as password.
* **Every message is signed** with `md5(messageId + key + timestamp)`, and the answer
  comes back on a reply topic that carries the same `messageId`. Without matching on it
  an answer of a *different* command could be read as the answer to this one.

Checked against the real account: a `GET Appliance.System.All` for a socket answered
`GETACK` with its channel state, and since then a `SET Appliance.Control.ToggleX`
against four sockets -- all standing at `onoff=0`, set to `onoff=0`, chosen on purpose
so nothing about the plant could actually move either way. All four answered `SETACK`
with an empty payload, and a subsequent `GET` showed each socket's `lmTime` advanced.
Broker, credentials, signature and the `SET` round trip are therefore measured against
real hardware, not assumed. The payload shape is the same one the reply reports back
for `togglex`.
"""

import asyncio
import hashlib
import json
import random
import ssl
import string
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, cast

import aiomqtt

from thermoctl.integrations.meross import MerossError, MerossSession

# Meross puts its broker on 443 rather than 8883 -- the port a restrictive network is
# least likely to block.
BROKER_PORT = 443
ANSWER_TIMEOUT_S = 20.0


def _md5(text: str) -> str:
    # Prescribed by the protocol, not a safeguard: the transport's security is TLS.
    return hashlib.md5(text.encode()).hexdigest()  # noqa: S324


@dataclass(frozen=True)
class MerossConnection:
    """Everything a broker connection needs, derived once per connection.

    `app_id` is the random half of the client identifier and also appears in the reply
    topic -- that is what keeps two clients of the same account apart.
    """

    domain: str
    user_id: str
    key: str
    app_id: str

    @classmethod
    def build(cls, account: MerossSession) -> MerossConnection:
        if not account.mqtt_domain:
            raise MerossError("Anmeldung nannte keinen MQTT-Broker")
        return cls(
            domain=account.mqtt_domain,
            user_id=account.user_id,
            key=account.key,
            app_id=_md5(f"API{uuid.uuid4()}"),
        )

    @property
    def client_id(self) -> str:
        return f"app:{self.app_id}"

    @property
    def password(self) -> str:
        return _md5(f"{self.user_id}{self.key}")

    @property
    def answer_topic(self) -> str:
        return f"/app/{self.user_id}-{self.app_id}/subscribe"


def command_topic(device_uuid: str) -> str:
    return f"/appliance/{device_uuid}/subscribe"


def build_message(
    connection: MerossConnection,
    namespace: str,
    method: str,
    payload: Mapping[str, object],
    *,
    now: float | None = None,
) -> tuple[str, str]:
    """The signed message and its `messageId`, which the answer has to carry back."""
    message_id = _md5("".join(random.choices(string.hexdigits, k=16)))  # noqa: S311
    timestamp = int(time.time() if now is None else now)
    message = {
        "header": {
            "from": connection.answer_topic,
            "messageId": message_id,
            "method": method,
            "namespace": namespace,
            "payloadVersion": 1,
            "sign": _md5(f"{message_id}{connection.key}{timestamp}"),
            "timestamp": timestamp,
            "triggerSrc": "Android",
        },
        "payload": dict(payload),
    }
    return json.dumps(message), message_id


def toggle_payload(channel: int, on: bool) -> dict[str, object]:
    return {"togglex": {"channel": channel, "onoff": 1 if on else 0}}


class MerossCommandTransport(Protocol):
    """One command to one device, answer included. Narrow, so tests stay offline."""

    async def send(
        self,
        device_uuid: str,
        namespace: str,
        method: str,
        payload: Mapping[str, object],
    ) -> Mapping[str, object]: ...


class AiomqttCommandTransport:
    """Opens a connection per command and closes it again.

    A permanent connection would be cheaper, but it would also be a second long-lived
    MQTT client next to the one for the plant's own broker -- with its own reconnect,
    its own backoff and its own failure modes. Switching a socket happens rarely enough
    that a connection per command is the simpler answer, and the simpler one is the one
    that can be reasoned about at three in the morning.
    """

    def __init__(self, connection: MerossConnection) -> None:
        self._connection = connection

    async def send(
        self,
        device_uuid: str,
        namespace: str,
        method: str,
        payload: Mapping[str, object],
    ) -> Mapping[str, object]:
        conn = self._connection
        message, message_id = build_message(conn, namespace, method, payload)
        async with aiomqtt.Client(
            conn.domain,
            port=BROKER_PORT,
            username=conn.user_id,
            password=conn.password,
            identifier=conn.client_id,
            tls_context=ssl.create_default_context(),
        ) as client:
            await client.subscribe(conn.answer_topic)
            await client.publish(command_topic(device_uuid), message)
            try:
                return await asyncio.wait_for(
                    self._answer(client, conn, namespace, message_id),
                    timeout=ANSWER_TIMEOUT_S,
                )
            except TimeoutError as exc:
                raise MerossError(
                    f"Geraet {device_uuid} hat auf {namespace} nicht geantwortet"
                ) from exc

    @staticmethod
    async def _answer(
        client: aiomqtt.Client,
        connection: MerossConnection,
        namespace: str,
        message_id: str,
    ) -> Mapping[str, object]:
        """Waits for the answer to *this* message, ignoring anything else.

        A socket also reports state on its own; matching on the `messageId` is what
        keeps such a report from being read as a confirmation of the command. A
        matching `messageId` alone is not authenticity, though -- it is the one part
        of the outgoing message that was published in the clear (on the command
        topic every subscriber to that topic can read). Whoever answers still has to
        prove they hold the account `key`, the same way every outgoing message does,
        and has to actually answer the namespace that was asked. Both are checked
        before the answer is handed back; a mismatch here is treated as no answer at
        all rather than a confirmation of anything.
        """
        async for incoming in client.messages:
            try:
                answer = json.loads(incoming.payload)
            except (ValueError, TypeError):  # pragma: no cover - broker sends JSON
                continue
            if not isinstance(answer, dict):  # pragma: no cover - broker sends objects
                continue
            header = answer.get("header")
            if not isinstance(header, dict) or header.get("messageId") != message_id:
                continue
            _verify_answer_authenticity(connection, namespace, header)
            return cast(Mapping[str, object], answer)
        # The stream ends when the connection drops. Without this the coroutine
        # would return `None` and the caller would read a dropped connection as a
        # confirmation.
        raise MerossError("Verbindung endete vor der Antwort")


def _verify_answer_authenticity(
    connection: MerossConnection, namespace: str, header: Mapping[str, object]
) -> None:
    """Rejects an answer that carries the right `messageId` but nothing else.

    A `messageId` travels in the clear on the command topic, so it alone does not
    prove the reply came from someone who actually holds the account `key`, nor
    that it actually answers the namespace that was asked rather than some other
    one addressed to the same reply topic. Both are re-derived the same way
    `build_message` derives them for outgoing messages, and a mismatch in either
    raises -- callers must not treat it as a confirmation.
    """
    if header.get("namespace") != namespace:
        raise MerossError(
            f"Antwort auf {namespace} nannte einen anderen Namespace"
        )
    expected_sign = _md5(f"{header.get('messageId')}{connection.key}{header.get('timestamp')}")
    if header.get("sign") != expected_sign:
        raise MerossError(f"Antwort auf {namespace} war nicht mit dem Kontoschluessel signiert")
