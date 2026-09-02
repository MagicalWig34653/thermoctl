import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session

from tests.helpers import create_mode
from thermoctl.config import Settings
from thermoctl.db.models.operations import Setting
from thermoctl.integrations.actuators import (
    MerossSwitch,
    Zigbee2MqttThermostat,
    Zigbee2MqttValve,
)


class MqttStub:
    def __init__(self, *, errors: Exception | None = None) -> None:
        self.calls: list[tuple[str, str, bool]] = []
        self.errors = errors

    async def publishing(self, topic: str, payload: str, *, switches: bool) -> bool:
        self.calls.append((topic, payload, switches))
        if self.errors:
            raise self.errors
        return True


class MerossStub:
    """Stands in for the MQTT command path, and answers `SETACK` like a real socket."""

    def __init__(
        self,
        *,
        errors: Exception | None = None,
        method: str = "SETACK",
        confirmed_payload: dict[str, Any] | None = None,
    ) -> None:
        self.calls: list[tuple[str, str, str, dict[str, Any]]] = []
        self.errors = errors
        self.method = method
        self.confirmed_payload = confirmed_payload

    async def send(
        self, device_uuid: str, namespace: str, method: str, payload: Any
    ) -> dict[str, Any]:
        self.calls.append((device_uuid, namespace, method, dict(payload)))
        if self.errors:
            raise self.errors
        return {
            "header": {"method": self.method},
            "payload": self.confirmed_payload if self.confirmed_payload is not None else {},
        }


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
    meross = MerossStub()
    installation_data = json.loads(Path("tests/daten/anlage-beispiele.json").read_text())
    name = installation_data["geraete"][-1]
    base_topic = _settings().mqtt_base_topic
    devices_id = installation_data["geraete"][0]

    mqtt_result = await Zigbee2MqttValve(
        session, mqtt, base_topic, name
    ).switching(True)
    meross_result = await MerossSwitch(session, meross, devices_id).switching(False)

    assert mqtt.calls == []
    assert meross.calls == []
    assert f"{base_topic}/{name}/set" in mqtt_result.description
    assert '{"state": "ON"}' in mqtt_result.description
    assert "Zustand OFF" in meross_result.description
    assert name in Zigbee2MqttValve(session, mqtt, base_topic, name).description()
    assert devices_id in MerossSwitch(session, meross, devices_id).description()


@pytest.mark.anyio
async def test_control_armed_sends_the_signed_toggle_command(session: Session) -> None:
    frost_protection = create_mode(session, "frostschutz")
    session.add(Setting(id=1, control_armed=True, frost_protection_mode_id=frost_protection.id))
    session.flush()
    installation_data = json.loads(Path("tests/daten/anlage-beispiele.json").read_text())
    devices_id = installation_data["geraete"][0]
    base_topic = _settings().mqtt_base_topic
    meross = MerossStub()

    result = await MerossSwitch(
        session, meross, devices_id, channel=2, frozen_switching_allowed=True
    ).switching(True)

    assert result.executed is True
    assert meross.calls == [
        (devices_id, "Appliance.Control.ToggleX", "SET",
         {"togglex": {"channel": 2, "onoff": 1}}),
    ]

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
        MerossStub(errors=ConnectionError("Cloud nicht erreichbar")),
        devices_id,
        frozen_switching_allowed=True,
    ).switching(False)
    assert meross_error.errors == "Cloud nicht erreichbar"


@pytest.mark.anyio
async def test_actuator_io_never_sees_an_open_database_transaction(session: Session) -> None:
    """Guard both externally visible actuator transports, not their callers."""
    _armed(session)

    class GuardedMqtt(MqttStub):
        async def publishing(
            self, topic: str, payload: str, *, switches: bool, retained: bool = False
        ) -> bool:
            assert not session.in_transaction()
            return await super().publishing(topic, payload, switches=switches)

    class GuardedMeross(MerossStub):
        async def send(
            self, device_uuid: str, namespace: str, method: str, payload: Any
        ) -> dict[str, Any]:
            assert not session.in_transaction()
            return await super().send(device_uuid, namespace, method, payload)

    mqtt_result = await Zigbee2MqttValve(
        session, GuardedMqtt(), "zigbee2mqtt", "valve"
    ).switching(True)
    meross_result = await MerossSwitch(
        session, GuardedMeross(), "socket", frozen_switching_allowed=True
    ).switching(True)

    assert mqtt_result.executed is True
    assert meross_result.executed is True


@pytest.mark.anyio
async def test_an_armed_switch_without_a_signed_in_session_fails_without_touching_the_network(
    session: Session,
) -> None:
    """`transport=None` -- no account configured, or the cloud rejected the sign-in
    this cycle (`services/meross_session.py`). Armed does not mean a network call is
    attempted regardless: without a session there is nothing to send through, and
    the adapter reports that as a failure of its own rather than raising."""
    frost_protection = create_mode(session, "frostschutz")
    session.add(Setting(id=1, control_armed=True, frost_protection_mode_id=frost_protection.id))
    session.flush()

    result = await MerossSwitch(
        session, None, "irgendein-geraet", frozen_switching_allowed=True
    ).switching(True)

    assert result.executed is False
    assert result.errors == "Keine gueltige Meross-Sitzung vorhanden"


@pytest.mark.anyio
async def test_switching_off_carries_onoff_zero(session: Session) -> None:
    """Off is a command of its own, not the absence of one."""
    _armed(session)
    meross = MerossStub()

    result = await MerossSwitch(
        session, meross, "geraet-1", frozen_switching_allowed=True
    ).switching(False)

    assert result.executed is True
    assert meross.calls[0][3] == {"togglex": {"channel": 0, "onoff": 0}}


async def _reject_publication(
    _topic: str, _payload: str, *, switches: bool
) -> bool:
    return False


def _armed(session: Session) -> None:
    """Sets control_armed — only in tests that check the bolt itself."""
    frost_protection = create_mode(session, "frostschutz")
    session.add(Setting(id=1, control_armed=True, frost_protection_mode_id=frost_protection.id))
    session.flush()


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

    meross = await MerossSwitch(
        session,
        MerossStub(errors=TimeoutError("Cloud antwortet nicht")),
        "geraet-1",
        frozen_switching_allowed=True,
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
async def test_a_command_the_device_does_not_confirm_is_not_reported_as_switched(
    session: Session,
) -> None:
    """A socket answers `SETACK`. Anything else means the heater did not switch --
    counting it as success would put a lie in the log at the one place it matters."""
    _armed(session)

    result = await MerossSwitch(
        session, MerossStub(method="ERROR"), "geraet-1", frozen_switching_allowed=True
    ).switching(True)

    assert result.executed is False
    assert result.errors is not None and "ERROR" in result.errors


@pytest.mark.anyio
async def test_an_answer_without_a_header_is_not_a_confirmation(session: Session) -> None:
    _armed(session)

    class _Headerless:
        async def send(self, *_a: object, **_k: object) -> dict[str, Any]:
            return {"payload": {}}

    result = await MerossSwitch(
        session, _Headerless(), "geraet-1", frozen_switching_allowed=True
    ).switching(True)

    assert result.executed is False


@pytest.mark.anyio
async def test_a_setack_confirming_the_state_that_was_sent_is_accepted(
    session: Session,
) -> None:
    """The positive case: a `SETACK` that names the channel and state actually sent
    is exactly what the manufacturer's own apps treat as success, and so is this."""
    _armed(session)
    meross = MerossStub(confirmed_payload={"togglex": {"channel": 2, "onoff": 1}})

    result = await MerossSwitch(
        session, meross, "geraet-1", channel=2, frozen_switching_allowed=True
    ).switching(True)

    assert result.executed is True


@pytest.mark.anyio
async def test_a_setack_confirming_a_different_state_is_not_a_confirmation(
    session: Session,
) -> None:
    """A compromised or faulty peer could see the fresh `messageId` and answer
    `SETACK` right away without the relay ever having moved. Naming a state that
    was never asked for is how that shows up here, and it must not be logged as
    success -- the outcome stays `FAILED`, so the next cycle retries."""
    _armed(session)
    meross = MerossStub(confirmed_payload={"togglex": {"channel": 0, "onoff": 0}})

    result = await MerossSwitch(
        session, meross, "geraet-1", channel=0, frozen_switching_allowed=True
    ).switching(True)

    assert result.executed is False
    assert result.errors is not None and "anderen Zustand" in result.errors


@pytest.mark.anyio
async def test_a_setack_confirming_the_right_state_on_the_wrong_channel_is_not_a_confirmation(
    session: Session,
) -> None:
    """Channel and state are checked together -- the right `onoff` on a channel
    nobody asked to switch is not this command's confirmation either."""
    _armed(session)
    meross = MerossStub(confirmed_payload={"togglex": {"channel": 1, "onoff": 1}})

    result = await MerossSwitch(
        session, meross, "geraet-1", channel=2, frozen_switching_allowed=True
    ).switching(True)

    assert result.executed is False
    assert result.errors is not None and "anderen Zustand" in result.errors


@pytest.mark.anyio
async def test_a_setack_with_an_empty_payload_is_still_accepted(session: Session) -> None:
    """Measured against real hardware, every `SETACK` came back with an empty
    payload (see the module docstring) -- that alone must not be treated as
    suspicious, or this adapter would never report success against a real socket."""
    _armed(session)
    meross = MerossStub()

    result = await MerossSwitch(
        session, meross, "geraet-1", frozen_switching_allowed=True
    ).switching(True)

    assert result.executed is True


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
async def test_thermostat_without_control_armed_sends_nothing(session: Session) -> None:
    """The dry-run bolt covers the thermostat exactly like the plain valve.

    A thermostatic radiator valve moves a real valve motor just like the switch it
    replaces here -- it must not slip past the two dry-run bolts because it uses a
    different payload shape.
    """
    mqtt = MqttStub()
    result = await Zigbee2MqttThermostat(
        session, mqtt, "zigbee2mqtt", "TRV-Wohnzimmer", Decimal("21.5")
    ).switching(True)

    assert mqtt.calls == []
    assert result.executed is False
    assert "zigbee2mqtt/TRV-Wohnzimmer/set" in result.description
    assert "haette gesendet" in result.description


@pytest.mark.anyio
async def test_thermostat_switching_off_is_bolted_shut_too(session: Session) -> None:
    """Turning off moves the valve just as much as turning on.

    Pointed out in a cross-review: the dry-run test covered only `switching(True)`.
    `system_mode: off` closes a real valve motor, so it must not slip past the bolt
    either -- and a valve that closes in the dry run is exactly as wrong as one that
    opens.
    """
    mqtt = MqttStub()
    result = await Zigbee2MqttThermostat(
        session, mqtt, "zigbee2mqtt", "TRV-Wohnzimmer", Decimal("21.5")
    ).switching(False)

    assert mqtt.calls == []
    assert result.executed is False
    assert "haette gesendet" in result.description
    assert "system_mode" in result.description


@pytest.mark.anyio
async def test_thermostat_peer_error_becomes_a_result_not_an_exception(
    session: Session,
) -> None:
    """An MQTT-level failure must not abort the control cycle for every other
    zone -- same requirement as for the plain valve, same code path."""
    _armed(session)
    mqtt = MqttStub(errors=ConnectionError("Broker weg"))
    result = await Zigbee2MqttThermostat(
        session, mqtt, "zigbee2mqtt", "TRV-Wohnzimmer", Decimal("21.5")
    ).switching(True)
    assert result.executed is False
    assert result.errors is not None and "Broker weg" in result.errors


@pytest.mark.anyio
async def test_thermostat_reports_a_refused_publication(session: Session) -> None:
    _armed(session)
    rejected = MqttStub()
    rejected.publishing = _reject_publication  # type: ignore[method-assign]
    result = await Zigbee2MqttThermostat(
        session, rejected, "zigbee2mqtt", "TRV-Wohnzimmer", Decimal("21.5")
    ).switching(False)
    assert result.executed is False
    assert result.errors == "MQTT-Client hat die Veroeffentlichung abgewiesen"


@pytest.mark.anyio
async def test_armed_thermostat_switching_on_sends_system_mode_heat_and_setpoint(
    session: Session,
) -> None:
    """The counter-proof to the dry run, for the thermostat adapter."""
    _armed(session)
    mqtt = MqttStub()
    result = await Zigbee2MqttThermostat(
        session, mqtt, "zigbee2mqtt", "TRV-Wohnzimmer", Decimal("21.5")
    ).switching(True)

    assert result.executed is True
    # Compared as parsed JSON: the order of keys in an object is not a promise this
    # adapter makes, and pinning it turns a harmless reordering into a red test.
    assert len(mqtt.calls) == 1
    topic, payload, switches = mqtt.calls[0]
    assert topic == "zigbee2mqtt/TRV-Wohnzimmer/set"
    assert json.loads(payload) == {"system_mode": "heat", "occupied_heating_setpoint": 21.5}
    assert switches is True


@pytest.mark.anyio
async def test_armed_thermostat_switching_off_sends_system_mode_off(
    session: Session,
) -> None:
    """Switching off never needs a setpoint -- the valve just closes."""
    _armed(session)
    mqtt = MqttStub()
    result = await Zigbee2MqttThermostat(
        session, mqtt, "zigbee2mqtt", "TRV-Wohnzimmer", Decimal("21.5")
    ).switching(False)

    assert result.executed is True
    assert mqtt.calls == [
        (
            "zigbee2mqtt/TRV-Wohnzimmer/set",
            json.dumps({"system_mode": "off"}),
            True,
        )
    ]


@pytest.mark.anyio
async def test_armed_thermostat_rounds_the_setpoint_to_the_devices_half_degree_step(
    session: Session,
) -> None:
    """The device only accepts 0.5 degree steps -- a schedule value in between is
    rounded, not rejected, since it is still a perfectly valid target temperature."""
    _armed(session)
    mqtt = MqttStub()
    result = await Zigbee2MqttThermostat(
        session, mqtt, "zigbee2mqtt", "TRV-Buero", Decimal("21.3")
    ).switching(True)

    assert result.executed is True
    sent_payload = json.loads(mqtt.calls[0][1])
    assert sent_payload["occupied_heating_setpoint"] == 21.5


@pytest.mark.parametrize("out_of_range_setpoint", [Decimal("4.5"), Decimal("30.5")])
@pytest.mark.anyio
async def test_a_setpoint_outside_five_to_thirty_degrees_is_rejected(
    session: Session, out_of_range_setpoint: Decimal
) -> None:
    """The WT-A03E accepts 5-30 degrees C only -- anything else must not be sent,
    armed or not, since the device would reject it anyway."""
    _armed(session)
    mqtt = MqttStub()
    result = await Zigbee2MqttThermostat(
        session, mqtt, "zigbee2mqtt", "TRV-Bad", out_of_range_setpoint
    ).switching(True)

    assert result.executed is False
    assert mqtt.calls == []
    assert "ausserhalb" in result.description


@pytest.mark.anyio
async def test_thermostat_switching_on_at_exactly_the_boundary_is_accepted(
    session: Session,
) -> None:
    """5 and 30 degrees themselves are valid -- the check must be inclusive."""
    _armed(session)
    mqtt = MqttStub()
    result = await Zigbee2MqttThermostat(
        session, mqtt, "zigbee2mqtt", "TRV-Flur", Decimal("5")
    ).switching(True)

    assert result.executed is True
    sent_payload = json.loads(mqtt.calls[0][1])
    assert sent_payload["occupied_heating_setpoint"] == 5.0


def test_thermostat_description_names_the_device() -> None:
    assert "TRV-Kueche" in Zigbee2MqttThermostat(
        None, None, "zigbee2mqtt", "TRV-Kueche", Decimal("20")  # type: ignore[arg-type]
    ).description()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_a_valve_without_a_system_mode_is_switched_off_by_its_lowest_setpoint(
    session: Session,
) -> None:
    """Not every TRV has a `system_mode` -- a Bosch BTH-RA does not.

    Sending one anyway would put a key in the payload that Zigbee2MQTT rejects, and
    the command would be lost without an error. Such a valve is switched off the way a
    person switches it off: by turning it down to the lowest setpoint it accepts.
    """
    _armed(session)
    mqtt = MqttStub()
    adapter = Zigbee2MqttThermostat(
        session, mqtt, "zigbee2mqtt", "BTH-RA-Flur", Decimal("21.0"),
        has_system_mode=False,
    )

    result = await adapter.switching(False)
    assert result.executed is True
    topic, payload, switches = mqtt.calls[0]
    assert json.loads(payload) == {"occupied_heating_setpoint": 5.0}
    assert "system_mode" not in payload
    assert switches is True  # this moves a valve like any other switch-off


@pytest.mark.anyio
async def test_a_valve_without_a_system_mode_still_heats_at_its_setpoint(
    session: Session,
) -> None:
    """Switching on is the same either way -- only switching off differs."""
    _armed(session)
    mqtt = MqttStub()
    adapter = Zigbee2MqttThermostat(
        session, mqtt, "zigbee2mqtt", "BTH-RA-Flur", Decimal("21.5"),
        has_system_mode=False,
    )

    await adapter.switching(True)
    payload = json.loads(mqtt.calls[0][1])
    assert payload["occupied_heating_setpoint"] == 21.5
    assert "system_mode" not in payload
