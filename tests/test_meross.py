"""Tests for the Meross integration -- the cloud client and the device discovery.

No test here ever touches the network (CLAUDE.md: the suite must never go online).
`sign_in` and `device_list` run against a fake `JsonTransport` that answers with canned
payloads and records what it was sent; `UrllibJsonTransport` runs against a patched
`urlopen`.

The canned answers are shaped after the real ones -- the previous version of this
adapter was wrong twice (form POST instead of a signed envelope, plain-text instead of
an MD5 password) and passed every test that only checked itself. So the envelope
assertions below check the *protocol*, not the code's own habits: that the password
never leaves in plain text, and that the signature is the md5 the service verifies.
"""

import base64
import hashlib
import json
import types
from collections.abc import Mapping
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.helpers import capability, create_device, integration
from thermoctl import app as app_module
from thermoctl.config import Settings
from thermoctl.db.models.device import Device, DeviceCapabilityLink
from thermoctl.integrations import meross as meross_module
from thermoctl.integrations.meross import (
    APP_SECRET,
    MerossDevice,
    MerossError,
    MerossSession,
    UrllibJsonTransport,
    device_list,
    sign_in,
)
from thermoctl.services.meross_discovery import refresh, save_devices

NOW = datetime(2026, 8, 31, 18, 0)

ACCOUNT = MerossSession(
    token="a-token", key="a-key", user_id="4711", mqtt_domain="mqtt-eu.meross.com"
)

_SIGN_IN_ANSWER: dict[str, Any] = {
    "apiStatus": 0,
    "data": {
        "token": "a-token",
        "key": "a-key",
        "userid": "4711",
        "mqttDomain": "mqtt-eu.meross.com",
    },
}

_DEVICE_LIST_ANSWER: dict[str, Any] = {
    "apiStatus": 0,
    "data": [
        {
            "uuid": "1111",
            "devName": "Wohnzimmer Heizung",
            "deviceType": "mss710",
            "onlineStatus": 1,
            "channels": [{}],
        },
        {
            "uuid": "2222",
            "devName": "Bad Heizung",
            "deviceType": "mss710",
            "onlineStatus": 2,
            "channels": [{}, {}],
        },
    ],
}


class _FakeJsonTransport:
    """Answers from a queue and records every call.

    An entry may be a payload or an exception -- the latter stands for a network that
    is simply gone, which is a different failure from a cloud that refuses.
    """

    def __init__(self, *answers: Mapping[str, Any] | Exception) -> None:
        self.answers: list[Mapping[str, Any] | Exception] = list(answers)
        self.calls: list[tuple[str, dict[str, Any], dict[str, str]]] = []

    async def post_json(
        self, url: str, body: Mapping[str, object], headers: Mapping[str, str]
    ) -> Mapping[str, object]:
        self.calls.append((url, dict(body), dict(headers)))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def _payload_of(call: tuple[str, dict[str, Any], dict[str, str]]) -> dict[str, Any]:
    """The JSON the envelope of a recorded call actually carries."""
    decoded = base64.b64decode(str(call[1]["params"])).decode()
    return dict(json.loads(decoded))


@pytest.mark.anyio
async def test_the_password_never_leaves_in_plain_text() -> None:
    """The real service answers `apiStatus 1004, Wrong password` to a plain one."""
    transport = _FakeJsonTransport(_SIGN_IN_ANSWER)

    await sign_in(transport, "https://example.invalid/", "a@b.de", "geheim")

    sent = _payload_of(transport.calls[0])
    assert sent["password"] != "geheim"
    assert sent["password"] == hashlib.md5(b"geheim").hexdigest()  # noqa: S324
    assert sent["email"] == "a@b.de"


@pytest.mark.anyio
async def test_the_envelope_is_signed_the_way_the_service_verifies_it() -> None:
    transport = _FakeJsonTransport(_SIGN_IN_ANSWER)

    await sign_in(transport, "https://example.invalid/", "a@b.de", "geheim")

    _url, body, _headers = transport.calls[0]
    expected = hashlib.md5(  # noqa: S324
        f"{APP_SECRET}{body['timestamp']}{body['nonce']}{body['params']}".encode()
    ).hexdigest()
    assert body["sign"] == expected


@pytest.mark.anyio
async def test_two_envelopes_never_repeat_their_nonce() -> None:
    """A replayed envelope must not be reusable -- so the nonce has to be fresh."""
    transport = _FakeJsonTransport(_SIGN_IN_ANSWER, _SIGN_IN_ANSWER)

    await sign_in(transport, "https://example.invalid", "a@b.de", "geheim")
    await sign_in(transport, "https://example.invalid", "a@b.de", "geheim")

    assert transport.calls[0][1]["nonce"] != transport.calls[1][1]["nonce"]


@pytest.mark.anyio
async def test_a_sign_in_returns_the_mqtt_credentials() -> None:
    """`key` and `user_id` are what the MQTT path needs -- the HTTP one answers 404."""
    transport = _FakeJsonTransport(_SIGN_IN_ANSWER)

    account = await sign_in(transport, "https://example.invalid", "a@b.de", "geheim")

    assert account == ACCOUNT
    assert transport.calls[0][0] == "https://example.invalid/v1/Auth/signIn"


@pytest.mark.anyio
async def test_a_refused_sign_in_names_the_reason_of_the_service() -> None:
    transport = _FakeJsonTransport({"apiStatus": 1004, "info": "Wrong password"})

    with pytest.raises(MerossError, match="1004.*Wrong password"):
        await sign_in(transport, "https://example.invalid", "a@b.de", "falsch")


@pytest.mark.anyio
async def test_a_refusal_without_a_reason_still_says_which_call_failed() -> None:
    transport = _FakeJsonTransport({"apiStatus": 1022})

    with pytest.raises(MerossError, match="Anmeldung.*ohne Begründung"):
        await sign_in(transport, "https://example.invalid", "a@b.de", "geheim")


@pytest.mark.anyio
async def test_a_sign_in_without_data_is_an_error_not_an_empty_account() -> None:
    transport = _FakeJsonTransport({"apiStatus": 0, "data": "nichts"})

    with pytest.raises(MerossError, match="keine Daten"):
        await sign_in(transport, "https://example.invalid", "a@b.de", "geheim")


@pytest.mark.anyio
async def test_a_sign_in_missing_a_field_names_the_missing_field() -> None:
    transport = _FakeJsonTransport({"apiStatus": 0, "data": {"token": "t"}})

    with pytest.raises(MerossError, match="ohne key, userid"):
        await sign_in(transport, "https://example.invalid", "a@b.de", "geheim")


@pytest.mark.anyio
async def test_the_device_list_carries_the_token_and_reads_the_entries() -> None:
    transport = _FakeJsonTransport(_DEVICE_LIST_ANSWER)

    devices = await device_list(transport, "https://example.invalid", ACCOUNT)

    url, _body, headers = transport.calls[0]
    assert url == "https://example.invalid/v1/Device/devList"
    assert headers["Authorization"] == "Basic a-token"
    assert devices == [
        MerossDevice(
            uuid="1111", name="Wohnzimmer Heizung", model="mss710", online=True, channels=1
        ),
        MerossDevice(
            uuid="2222", name="Bad Heizung", model="mss710", online=False, channels=2
        ),
    ]


@pytest.mark.anyio
async def test_an_entry_without_uuid_or_name_is_skipped_not_guessed() -> None:
    """Such a device could not be found again, nor told apart in the list."""
    transport = _FakeJsonTransport(
        {
            "apiStatus": 0,
            "data": [
                "kein Objekt",
                {"devName": "Ohne Kennung"},
                {"uuid": "3333"},
                {"uuid": "4444", "devName": "Vollstaendig"},
            ],
        }
    )

    devices = await device_list(transport, "https://example.invalid", ACCOUNT)

    assert [d.uuid for d in devices] == ["4444"]
    assert devices[0].model == ""
    assert devices[0].channels == 1


@pytest.mark.anyio
async def test_a_device_list_that_is_not_a_list_is_an_error() -> None:
    transport = _FakeJsonTransport({"apiStatus": 0, "data": {"nicht": "eine Liste"}})

    with pytest.raises(MerossError, match="keine Liste"):
        await device_list(transport, "https://example.invalid", ACCOUNT)


@pytest.mark.anyio
async def test_the_standard_library_transport_posts_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeResponse:
        def __enter__(self) -> _FakeResponse:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps(_SIGN_IN_ANSWER).encode()

    captured: dict[str, Any] = {}

    def _fake_urlopen(call: Any, timeout: int) -> _FakeResponse:
        captured["url"] = call.full_url
        captured["body"] = json.loads(call.data)
        captured["headers"] = call.headers
        captured["method"] = call.get_method()
        return _FakeResponse()

    monkeypatch.setattr(meross_module.request, "urlopen", _fake_urlopen)

    answer = await UrllibJsonTransport().post_json(
        "https://example.invalid/v1/Auth/signIn", {"a": 1}, {"Content-Type": "application/json"}
    )

    assert answer == _SIGN_IN_ANSWER
    assert captured["method"] == "POST"
    assert captured["body"] == {"a": 1}


@pytest.mark.anyio
async def test_an_answer_that_is_not_an_object_is_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeResponse:
        def __enter__(self) -> _FakeResponse:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return b"[1, 2]"

    monkeypatch.setattr(
        meross_module.request, "urlopen", lambda call, timeout: _FakeResponse()
    )

    with pytest.raises(MerossError, match="kein Objekt"):
        await UrllibJsonTransport().post_json("https://example.invalid", {}, {})


def _meross_devices(session: Session) -> list[Device]:
    anbindung = integration(session, "meross")
    return list(
        session.scalars(
            select(Device)
            .where(Device.integration_id == anbindung.id)
            .order_by(Device.external_id)
        )
    )


def test_discovery_creates_a_device_per_uuid_with_the_switch_capability(
    session: Session,
) -> None:
    integration(session, "meross")
    switch = capability(session, "switch")

    new = save_devices(
        session,
        [
            MerossDevice(uuid="1111", name="Wohnzimmer", model="mss710", online=True, channels=1),
            MerossDevice(uuid="2222", name="Bad", model="mss710", online=False, channels=1),
        ],
        NOW,
    )
    session.flush()

    assert new == 2
    devices = _meross_devices(session)
    assert [d.display_name for d in devices] == ["Wohnzimmer", "Bad"]
    assert [d.model for d in devices] == ["mss710", "mss710"]
    # Only the reachable one has been seen -- `last_seen_at` must not claim otherwise.
    assert devices[0].last_seen_at == NOW
    assert devices[1].last_seen_at is None
    for device in devices:
        assert session.scalar(
            select(DeviceCapabilityLink).where(
                DeviceCapabilityLink.device_id == device.id,
                DeviceCapabilityLink.capability_id == switch.id,
            )
        )


def test_a_second_pass_renames_instead_of_duplicating(session: Session) -> None:
    """The assignment hangs on the uuid, so a rename in the app must survive it."""
    integration(session, "meross")
    capability(session, "switch")
    first = MerossDevice(uuid="1111", name="Wohnzimmer", model="mss710", online=True, channels=1)

    save_devices(session, [first], NOW)
    session.flush()
    before = _meross_devices(session)[0].id

    new = save_devices(
        session,
        [MerossDevice(uuid="1111", name="Wohnzimmer neu", model="mss710", online=True, channels=1)],
        NOW,
    )
    session.flush()

    assert new == 0
    devices = _meross_devices(session)
    assert len(devices) == 1
    assert devices[0].id == before
    assert devices[0].display_name == "Wohnzimmer neu"
    assert (
        len(
            list(
                session.scalars(
                    select(DeviceCapabilityLink).where(
                        DeviceCapabilityLink.device_id == before
                    )
                )
            )
        )
        == 1
    )


def test_a_device_the_cloud_does_not_name_is_kept_not_deleted(session: Session) -> None:
    """Offline, or a failed query -- a zone must not lose its actuator over it."""
    integration(session, "meross")
    capability(session, "switch")
    save_devices(
        session,
        [MerossDevice(uuid="1111", name="Wohnzimmer", model="mss710", online=True, channels=1)],
        NOW,
    )
    session.flush()

    save_devices(session, [], NOW)
    session.flush()

    assert [d.external_id for d in _meross_devices(session)] == ["1111"]


def test_a_device_without_a_model_gets_none_not_an_empty_string(session: Session) -> None:
    integration(session, "meross")
    capability(session, "switch")

    save_devices(
        session,
        [MerossDevice(uuid="1111", name="Wohnzimmer", model="", online=True, channels=1)],
        NOW,
    )
    session.flush()

    assert _meross_devices(session)[0].model is None


def test_discovery_works_before_the_switch_capability_exists(session: Session) -> None:
    """A fresh database might not know the capability yet -- that is no reason to fail."""
    integration(session, "meross")

    new = save_devices(
        session,
        [MerossDevice(uuid="1111", name="Wohnzimmer", model="mss710", online=True, channels=1)],
        NOW,
    )
    session.flush()

    assert new == 1


def _settings_with_credentials() -> Settings:
    return Settings(meross_email="a@b.de", meross_password="geheim")  # type: ignore[arg-type]


@pytest.mark.anyio
async def test_the_refresh_does_nothing_without_credentials(session: Session) -> None:
    """Meross is optional -- whoever does not use it must not see a warning per pass."""
    transport = _FakeJsonTransport()
    # Explicitly empty: a bare `Settings()` reads the operator's `.env` and would carry
    # real credentials here -- the test would then pass for the wrong reason.
    without = Settings(meross_email=None, meross_password=None)

    assert await refresh(session, without, transport, NOW) == 0
    assert transport.calls == []


@pytest.mark.anyio
async def test_the_refresh_signs_in_and_stores_what_it_finds(session: Session) -> None:
    integration(session, "meross")
    capability(session, "switch")
    transport = _FakeJsonTransport(_SIGN_IN_ANSWER, _DEVICE_LIST_ANSWER)

    new = await refresh(session, _settings_with_credentials(), transport, NOW)
    session.flush()

    assert new == 2
    assert [d.external_id for d in _meross_devices(session)] == ["1111", "2222"]


@pytest.mark.anyio
async def test_a_refused_sign_in_leaves_the_installation_running(
    session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    integration(session, "meross")
    transport = _FakeJsonTransport({"apiStatus": 1004, "info": "Wrong password"})

    with caplog.at_level("ERROR"):
        assert await refresh(session, _settings_with_credentials(), transport, NOW) == 0

    assert "Meross" in caplog.text
    assert _meross_devices(session) == []


@pytest.mark.anyio
async def test_a_broken_connection_leaves_the_installation_running(
    session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    """Not every failure is a `MerossError` -- a socket error must not stop the cycle."""
    integration(session, "meross")
    transport = _FakeJsonTransport(OSError("Netz weg"))

    with caplog.at_level("ERROR"):
        assert await refresh(session, _settings_with_credentials(), transport, NOW) == 0

    assert "Meross" in caplog.text


def test_a_meross_device_does_not_collide_with_a_zigbee_device_of_the_same_id(
    session: Session,
) -> None:
    """`external_id` is only unique per integration -- a uuid may repeat elsewhere."""
    integration(session, "meross")
    capability(session, "switch")
    create_device(session, "1111")  # zigbee2mqtt

    new = save_devices(
        session,
        [MerossDevice(uuid="1111", name="Wohnzimmer", model="mss710", online=True, channels=1)],
        NOW,
    )
    session.flush()

    assert new == 1
    assert [d.display_name for d in _meross_devices(session)] == ["Wohnzimmer"]


class _FakeTransportCounting:
    """Counts how often a pass reached the cloud, and answers a full round."""

    def __init__(self) -> None:
        self.rounds = 0

    async def post_json(
        self, url: str, body: Mapping[str, object], headers: Mapping[str, str]
    ) -> Mapping[str, object]:
        if url.endswith("signIn"):
            self.rounds += 1
            return _SIGN_IN_ANSWER
        return _DEVICE_LIST_ANSWER


@pytest.mark.anyio
async def test_the_loop_hook_reconciles_through_the_transport_on_app_state(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Pinned settings rather than `get_settings()`: whether the operator's `.env` holds
    # Meross credentials must not decide whether this test tests anything.
    monkeypatch.setattr(app_module, "get_settings", _settings_with_credentials)
    integration(session, "meross")
    capability(session, "switch")
    transport = _FakeTransportCounting()
    app = types.SimpleNamespace(state=types.SimpleNamespace(meross_transport=transport))

    await app_module._refresh_meross(app, session, NOW)  # type: ignore[arg-type]
    session.flush()

    assert transport.rounds == 1
    assert [d.external_id for d in _meross_devices(session)] == ["1111", "2222"]
