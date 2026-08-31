"""Tests for `thermoctl.integrations.meross_mqtt` -- the path Meross really switches on.

Nothing here connects to a broker. The pure helpers (identifier, password, signature,
payload) are checked directly, and `AiomqttCommandTransport` against a fake
`aiomqtt.Client` -- the interesting part of it is not the connecting but what it does
with the messages that come back.

The signature assertions restate the protocol rather than the code: the previous Meross
adapter passed every test it had and still could never have worked, because its tests
only checked its own habits.
"""

import asyncio
import hashlib
import json
import types
from typing import Any

import pytest

from thermoctl.integrations import meross_mqtt as mqtt_module
from thermoctl.integrations.meross import MerossError, MerossSession
from thermoctl.integrations.meross_mqtt import (
    AiomqttCommandTransport,
    MerossConnection,
    build_message,
    command_topic,
    toggle_payload,
)

ACCOUNT = MerossSession(
    token="a-token", key="a-key", user_id="4711", mqtt_domain="mqtt-eu-5.meross.com"
)


def _md5(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()  # noqa: S324


def test_the_connection_takes_its_broker_and_credentials_from_the_sign_in() -> None:
    connection = MerossConnection.build(ACCOUNT)

    assert connection.domain == "mqtt-eu-5.meross.com"
    assert connection.user_id == "4711"
    assert connection.password == _md5("4711a-key")
    assert connection.client_id.startswith("app:")
    assert connection.answer_topic == f"/app/4711-{connection.app_id}/subscribe"


def test_two_connections_of_one_account_never_share_a_client_identifier() -> None:
    """Two clients on one identifier evict each other in a loop -- that fault has
    already cost this project a night on its own broker."""
    first = MerossConnection.build(ACCOUNT)
    second = MerossConnection.build(ACCOUNT)

    assert first.client_id != second.client_id
    assert first.answer_topic != second.answer_topic


def test_a_sign_in_without_a_broker_is_refused_instead_of_guessed() -> None:
    without = MerossSession(token="t", key="k", user_id="1", mqtt_domain="")

    with pytest.raises(MerossError, match="MQTT-Broker"):
        MerossConnection.build(without)


def test_the_message_is_signed_the_way_the_device_verifies_it() -> None:
    connection = MerossConnection.build(ACCOUNT)

    raw, message_id = build_message(
        connection, "Appliance.Control.ToggleX", "SET", toggle_payload(0, True), now=1000
    )

    message = json.loads(raw)
    header = message["header"]
    assert header["messageId"] == message_id
    assert header["timestamp"] == 1000
    assert header["sign"] == _md5(f"{message_id}a-key1000")
    assert header["from"] == connection.answer_topic
    assert header["method"] == "SET"
    assert message["payload"] == {"togglex": {"channel": 0, "onoff": 1}}


def test_two_messages_never_repeat_their_identifier() -> None:
    """The identifier is what pairs an answer with its command."""
    connection = MerossConnection.build(ACCOUNT)

    _first, one = build_message(connection, "N", "GET", {})
    _second, two = build_message(connection, "N", "GET", {})

    assert one != two


def test_the_command_topic_addresses_the_device_not_the_account() -> None:
    assert command_topic("1111") == "/appliance/1111/subscribe"


def test_switching_off_is_its_own_payload() -> None:
    assert toggle_payload(2, False) == {"togglex": {"channel": 2, "onoff": 0}}


class _FakeMessage:
    def __init__(self, payload: object) -> None:
        self.payload = (
            payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        )


class _FakeClient:
    """A broker that answers with a canned list of messages."""

    last: _FakeClient

    def __init__(self, *_args: object, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.subscribed: list[str] = []
        self.published: list[tuple[str, str]] = []
        self.answers: list[object] = []
        type(self).last = self

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def subscribe(self, topic: str) -> None:
        self.subscribed.append(topic)

    async def publish(self, topic: str, payload: str) -> None:
        self.published.append((topic, payload))

    @property
    def messages(self) -> Any:
        answers = self.answers

        async def stream() -> Any:
            for answer in answers:
                yield _FakeMessage(answer)
                await asyncio.sleep(0)

        return stream()


def _with_fake_client(
    monkeypatch: pytest.MonkeyPatch, answers: list[object]
) -> type[_FakeClient]:
    def build(*args: object, **kwargs: object) -> _FakeClient:
        client = _FakeClient(*args, **kwargs)
        client.answers = answers
        return client

    monkeypatch.setattr(mqtt_module, "aiomqtt", types.SimpleNamespace(Client=build))
    return _FakeClient


@pytest.mark.anyio
async def test_a_command_connects_signs_publishes_and_returns_the_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = MerossConnection.build(ACCOUNT)
    # The answer has to carry the identifier of *this* command -- it is only known
    # once the message has been built, so the fake fills it in as it streams.
    answers: list[object] = []
    _with_fake_client(monkeypatch, answers)
    transport = AiomqttCommandTransport(connection)

    async def answer_once() -> None:
        while not _FakeClient.last.published:  # pragma: no cover - immediate in practice
            await asyncio.sleep(0)

    original = mqtt_module.build_message
    captured: dict[str, str] = {}

    def remember(*args: object, **kwargs: object) -> tuple[str, str]:
        raw, message_id = original(*args, **kwargs)  # type: ignore[arg-type]
        captured["id"] = message_id
        answers.append({"header": {"method": "SETACK", "messageId": message_id}, "payload": {}})
        return raw, message_id

    monkeypatch.setattr(mqtt_module, "build_message", remember)

    answer = await transport.send(
        "1111", "Appliance.Control.ToggleX", "SET", toggle_payload(0, True)
    )

    client = _FakeClient.last
    assert client.kwargs["port"] == 443
    assert client.kwargs["username"] == "4711"
    assert client.kwargs["password"] == _md5("4711a-key")
    assert client.kwargs["identifier"] == connection.client_id
    assert client.kwargs["tls_context"] is not None
    assert client.subscribed == [connection.answer_topic]
    assert client.published[0][0] == "/appliance/1111/subscribe"
    assert answer["header"]["messageId"] == captured["id"]  # type: ignore[index]


@pytest.mark.anyio
async def test_an_answer_to_another_command_is_not_read_as_this_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A socket also reports on its own. Matching on the identifier is what keeps such
    a report from confirming a command that was never carried out."""
    connection = MerossConnection.build(ACCOUNT)
    answers: list[object] = []
    _with_fake_client(monkeypatch, answers)
    monkeypatch.setattr(mqtt_module, "ANSWER_TIMEOUT_S", 0.2)
    transport = AiomqttCommandTransport(connection)

    original = mqtt_module.build_message

    def remember(*args: object, **kwargs: object) -> tuple[str, str]:
        raw, message_id = original(*args, **kwargs)  # type: ignore[arg-type]
        answers.append(b"kein json")
        answers.append([1, 2])
        answers.append({"header": {"method": "PUSH", "messageId": "fremde-kennung"}})
        answers.append({"header": {"method": "SETACK", "messageId": message_id}})
        return raw, message_id

    monkeypatch.setattr(mqtt_module, "build_message", remember)

    answer = await transport.send("1111", "Appliance.Control.ToggleX", "SET", {})

    assert answer["header"]["method"] == "SETACK"  # type: ignore[index]


@pytest.mark.anyio
async def test_a_connection_that_drops_is_not_read_as_a_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The message stream ends when the connection goes. Falling out of the loop would
    hand the caller `None`, and `None` has no header to fail on."""
    _with_fake_client(monkeypatch, [])
    transport = AiomqttCommandTransport(MerossConnection.build(ACCOUNT))

    with pytest.raises(MerossError, match="Verbindung endete"):
        await transport.send("1111", "Appliance.Control.ToggleX", "SET", {})


@pytest.mark.anyio
async def test_a_device_that_stays_silent_becomes_an_error_not_a_hang(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A socket that is unplugged answers nothing. The control cycle must not wait on
    it -- every other zone would stand still with it."""

    class _SilentClient(_FakeClient):
        @property
        def messages(self) -> Any:
            async def stream() -> Any:
                await asyncio.Event().wait()
                yield  # pragma: no cover - never reached

            return stream()

    monkeypatch.setattr(
        mqtt_module, "aiomqtt", types.SimpleNamespace(Client=_SilentClient)
    )
    monkeypatch.setattr(mqtt_module, "ANSWER_TIMEOUT_S", 0.05)
    transport = AiomqttCommandTransport(MerossConnection.build(ACCOUNT))

    with pytest.raises(MerossError, match="1111.*nicht geantwortet"):
        await transport.send("1111", "Appliance.Control.ToggleX", "SET", {})
