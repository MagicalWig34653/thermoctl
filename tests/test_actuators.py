import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session

from tests.helpers import create_mode
from thermoctl.config import Settings
from thermoctl.db.models.operations import Setting
from thermoctl.integrations import actuators as actuators_module
from thermoctl.integrations.actuators import MerossSwitch, Zigbee2MqttValve


class MqttStub:
    def __init__(self, *, errors: Exception | None = None) -> None:
        self.calls: list[tuple[str, str, bool]] = []
        self.errors = errors

    async def publishing(self, topic: str, payload: str, *, switches: bool) -> bool:
        self.calls.append((topic, payload, switches))
        if self.errors:
            raise self.errors
        return True


class HttpStub:
    def __init__(
        self, *, errors: Exception | None = None, response: dict[str, Any] | None = None
    ) -> None:
        self.calls: list[tuple[str, dict[str, str], dict[str, str]]] = []
        self.errors = errors
        self.response = response or {"data": {"token": "ersatz-token"}}

    async def post(
        self, url: str, data: dict[str, str], headers: dict[str, str]
    ) -> dict[str, Any]:
        self.calls.append((url, dict(data), dict(headers)))
        if self.errors:
            raise self.errors
        return self.response


class HttpResponse:
    def __enter__(self) -> HttpResponse:
        return self

    def __exit__(self, *_arguments: object) -> None:
        return None

    def read(self) -> bytes:
        return b'{"data": {"token": "ersatz-token"}}'


def _settings(**values: object) -> Settings:
    return Settings(
        _env_file=None,
        database_url="sqlite://",
        secret_key="s" * 32,
        **values,
    )


@pytest.mark.anyio
async def test_without_control_armed_nothing_is_sent(session: Session) -> None:
    mqtt = MqttStub()
    http = HttpStub()
    installation_data = json.loads(Path("tests/daten/anlage-beispiele.json").read_text())
    name = installation_data["geraete"][-1]
    base_topic = _settings().mqtt_base_topic
    devices_id = installation_data["geraete"][0]

    mqtt_result = await Zigbee2MqttValve(
        session, mqtt, base_topic, name
    ).switching(True)
    meross_result = await MerossSwitch(
        session, _settings(meross_email="konto@example.invalid", meross_password="geheim"),
        devices_id, transport=http,
    ).switching(False)

    assert mqtt.calls == []
    assert http.calls == []
    assert f"{base_topic}/{name}/set" in mqtt_result.description
    assert '{"state": "ON"}' in mqtt_result.description
    assert "Zustand OFF" in meross_result.description
    assert name in Zigbee2MqttValve(session, mqtt, base_topic, name).description()
    assert devices_id in MerossSwitch(
        session, _settings(), devices_id, transport=http
    ).description()


@pytest.mark.anyio
async def test_control_armed_builds_the_meross_login_and_switch_call(session: Session) -> None:
    frost_protection = create_mode(session, "frostschutz")
    session.add(Setting(id=1, control_armed=True, frost_protection_mode_id=frost_protection.id))
    session.flush()
    installation_data = json.loads(Path("tests/daten/anlage-beispiele.json").read_text())
    devices_id = installation_data["geraete"][0]
    base_topic = _settings().mqtt_base_topic
    http = HttpStub()
    result = await MerossSwitch(
        session,
        _settings(meross_email="konto@example.invalid", meross_password="geheim"),
        devices_id,
        channel=2,
        transport=http,
        api_base="https://meross.example.invalid",
    ).switching(True)
    assert result.executed is True
    assert [call[0] for call in http.calls] == [
        "https://meross.example.invalid/v1/Auth/signIn",
        "https://meross.example.invalid/v1/Device/devControl",
    ]
    assert http.calls[1][1] == {
        "uuid": devices_id, "channel": "2", "action": "ON"
    }

    without_http = HttpStub()
    not_configured = await MerossSwitch(
        session, _settings(), devices_id, transport=without_http
    ).switching(True)
    assert not_configured.executed is False
    assert "Nicht konfiguriert" in not_configured.description
    assert without_http.calls == []

    mqtt = MqttStub(errors=ConnectionError("Gegenstelle nicht erreichbar"))
    errors = await Zigbee2MqttValve(
        session, mqtt, base_topic, devices_id
    ).switching(True)
    assert errors.executed is False
    assert errors.errors == "Gegenstelle nicht erreichbar"

    rejected = MqttStub()
    rejected.publishing = _reject_publication  # type: ignore[method-assign]
    mqtt_result = await Zigbee2MqttValve(
        session, rejected, base_topic, devices_id
    ).switching(False)
    assert mqtt_result.errors == "MQTT-Client hat die Veroeffentlichung abgewiesen"

    meross_error = await MerossSwitch(
        session,
        _settings(meross_email="konto@example.invalid", meross_password="geheim"),
        devices_id,
        transport=HttpStub(errors=ConnectionError("Cloud nicht erreichbar")),
    ).switching(False)
    assert meross_error.errors == "Cloud nicht erreichbar"

    missing_token = await MerossSwitch(
        session,
        _settings(meross_email="konto@example.invalid", meross_password="geheim"),
        devices_id,
        transport=HttpStub(response={"data": {}}),
    ).switching(False)
    assert missing_token.errors == "Meross-Anmeldung lieferte kein Token"


async def _reject_publication(
    _topic: str, _payload: str, *, switches: bool
) -> bool:
    return False


@pytest.mark.anyio
async def test_http_transport_encodes_the_form_and_returns_an_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[object] = []

    def open_url(request: object, *, timeout: int) -> HttpResponse:
        requests.append(request)
        assert timeout == 10
        return HttpResponse()

    monkeypatch.setattr(actuators_module.request, "urlopen", open_url)
    result = await actuators_module.UrllibHttpTransport().post(
        "https://meross.example.invalid/v1/test",
        {"zustand": "AN"},
        {"Authorization": "Basic token"},
    )
    assert result == {"data": {"token": "ersatz-token"}}
    assert len(requests) == 1


def _armed(session: Session) -> None:
    """Sets control_armed — only in tests that check the bolt itself."""
    frost_protection = create_mode(session, "frostschutz")
    session.add(Setting(id=1, control_armed=True, frost_protection_mode_id=frost_protection.id))
    session.flush()


@pytest.mark.anyio
async def test_meross_without_credentials_reports_itself_as_unconfigured(
    session: Session,
) -> None:
    """The normal case at this stage: no account configured, so no call.

    This is explicitly not an error — the adapter should then quietly do
    nothing, instead of attempting a login with empty fields.
    """
    _armed(session)
    http = HttpStub()
    result = await MerossSwitch(session, _settings(), "geraet-1", transport=http).switching(
        True
    )
    assert result.executed is False
    assert "Nicht konfiguriert" in result.description
    assert http.calls == []


@pytest.mark.anyio
async def test_a_device_name_with_an_umlaut_and_a_space_yields_the_correct_topic(
    session: Session,
) -> None:
    """The installation carries names like 'Über Küche'. An adapter that
    mangles them later switches a different device, or none at all."""
    installation_data = json.loads(Path("tests/daten/anlage-beispiele.json").read_text())
    name = next(
        n for n in installation_data["geraete"]
        if " " in n and any(c in n for c in "äöüÄÖÜ")
    )
    valve = Zigbee2MqttValve(session, MqttStub(), "zigbee2mqtt", name)
    result = await valve.switching(True)
    assert f"zigbee2mqtt/{name}/set" in result.description


@pytest.mark.anyio
async def test_a_peer_error_becomes_a_result_not_an_exception(
    session: Session,
) -> None:
    """An actuator error must not abort the control cycle for every other zone."""
    _armed(session)
    mqtt = MqttStub(errors=ConnectionError("Broker weg"))
    valve = await Zigbee2MqttValve(session, mqtt, "zigbee2mqtt", "Ventil").switching(True)
    assert valve.executed is False
    assert valve.errors is not None and "Broker weg" in valve.errors

    http = HttpStub(errors=TimeoutError("Cloud antwortet nicht"))
    meross = await MerossSwitch(
        session, _settings(meross_email="k@example.invalid", meross_password="geheim"),
        "geraet-1", transport=http,
    ).switching(True)
    assert meross.executed is False
    assert meross.errors is not None and "antwortet nicht" in meross.errors


@pytest.mark.anyio
async def test_a_rejected_publication_is_reported_as_an_error(
    session: Session,
) -> None:
    """The second bolt in the MQTT client kicks in — the actuator must not
    count that as success, or the log would say 'switched' where nothing
    switched."""
    _armed(session)
    result = await Zigbee2MqttValve(
        session, _RejectingClient(), "zigbee2mqtt", "Ventil"
    ).switching(True)
    assert result.executed is False
    assert result.errors is not None and "abgewiesen" in result.errors


class _RejectingClient:
    async def publishing(self, topic: str, payload: str, *, switches: bool) -> bool:
        return False


@pytest.mark.anyio
async def test_a_login_without_a_token_becomes_an_error(session: Session) -> None:
    """If the cloud responds without a token, the switch call is
    meaningless — and the adapter must not send it anyway."""
    _armed(session)
    http = HttpStub(response={"data": {}})
    result = await MerossSwitch(
        session, _settings(meross_email="k@example.invalid", meross_password="geheim"),
        "geraet-1", transport=http,
    ).switching(True)
    assert result.executed is False
    assert result.errors is not None
    assert len(http.calls) == 1, "Nothing may follow a failed login"


@pytest.mark.anyio
async def test_an_armed_valve_actually_sends(session: Session) -> None:
    """The counter-proof to the dry run: the path works, it is merely locked.

    Without this test, the suite would only prove that nothing is sent —
    even if sending had never been built at all. Phase 4 depends on this.
    """
    _armed(session)
    mqtt = MqttStub()
    result = await Zigbee2MqttValve(session, mqtt, "zigbee2mqtt", "Ventil").switching(True)
    assert result.executed is True
    assert mqtt.calls == [("zigbee2mqtt/Ventil/set", '{"state": "ON"}', True)]


@pytest.mark.anyio
async def test_an_unexpected_meross_response_becomes_an_error(session: Session) -> None:
    """If the cloud responds with anything other than an object containing a
    token, that is a bug in the adapter and no reason to send the switch
    call blindly."""
    _armed(session)
    http = HttpStub(response={"data": "unerwartet"})
    result = await MerossSwitch(
        session, _settings(meross_email="k@example.invalid", meross_password="geheim"),
        "geraet-1", transport=http,
    ).switching(True)
    assert result.executed is False
    assert result.errors is not None and "Token" in result.errors


def test_http_transport_rejects_non_object_responses() -> None:
    """The HTTP wrapper only passes objects through — a list would surprise
    every caller, and only show up further down the line."""
    import pytest as _pytest

    class _List:
        def __enter__(self) -> _List:
            return self

        def __exit__(self, *_a: object) -> None:
            return None

        def read(self) -> bytes:
            return b"[1, 2]"

    transport = actuators_module.UrllibHttpTransport()
    with _pytest.MonkeyPatch.context() as mp:
        mp.setattr(actuators_module.request, "urlopen", lambda *_a, **_k: _List())
        with _pytest.raises(ValueError, match="kein Objekt"):
            transport._post_sync("https://example.invalid", {}, {})


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
