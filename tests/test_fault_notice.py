from decimal import Decimal

import pytest

from thermoctl.db.models.operations import Setting
from thermoctl.domain.fault_notice import (
    AUDIT_ACTION_NOTIFICATION_SENT,
    AUDIT_ACTION_NOTIFICATION_SUPPRESSED,
    NOTICE_KIND_BRIDGE_FAULT,
    NOTICE_KIND_COMMAND_FAILURE,
    NOTICE_KIND_SENSOR_FAULT,
    bridge_notice,
    command_failure_notice,
    notice_enabled,
    notification_audit_action,
    sensor_notice,
)
from thermoctl.integrations.mqtt.zigbee2mqtt import bridge_reachable


def test_ten_identical_fault_states_produce_one_notice() -> None:
    previous: str | None = "ok"
    notices = []
    for _ in range(10):
        notice = sensor_notice(
            "sensor:1", "Testzone", previous, "veraltet", Decimal("16.0")
        )
        if notice is not None:
            notices.append(notice)
        previous = "veraltet"

    assert len(notices) == 1
    assert notices[0].severity == "stoerung"
    assert notices[0].key == "sensor:1"
    assert notices[0].text == (
        "Der Temperaturwert ist veraltet. Die Zone regelt die Heizung bis auf Weiteres gegen "
        "den Frostschutz-Sollwert von 16.0 °C."
    )


def test_all_clear_only_on_transition_back_to_ok() -> None:
    assert sensor_notice(
        "sensor:1", "Testzone", "veraltet", "keine_quelle", Decimal("16.0")
    ) is not None
    all_clear = sensor_notice(
        "sensor:1", "Testzone", "veraltet", "ok", Decimal("16.0")
    )
    assert all_clear is not None
    assert all_clear.severity == "entwarnung"
    assert all_clear.text == (
        "Die Temperaturquelle liefert wieder aktuelle Werte. "
        "Die Zone regelt die Heizung wieder normal."
    )
    assert sensor_notice("sensor:1", "Testzone", "ok", "ok", Decimal("16.0")) is None
    assert sensor_notice("sensor:1", "Testzone", None, "ok", Decimal("16.0")) is None


def test_first_sensor_state_is_not_yet_a_state_change() -> None:
    assert sensor_notice(
        "sensor:1", "Testzone", None, "veraltet", Decimal("16.0")
    ) is None
    assert sensor_notice(
        "sensor:1", "Testzone", None, "keine_quelle", Decimal("16.0")
    ) is None


def test_bridge_reports_a_failure_and_exactly_one_recovery() -> None:
    fault = bridge_notice(True, False)
    assert fault is not None
    assert fault.severity == "stoerung"
    assert bridge_notice(False, False) is None

    all_clear = bridge_notice(False, True)
    assert all_clear is not None
    assert all_clear.severity == "entwarnung"
    assert bridge_notice(True, True) is None


def test_first_observed_bridge_failure_is_not_suppressed() -> None:
    assert bridge_notice(None, False) is not None
    assert bridge_notice(None, True) is None


def test_bridge_state_accepts_text_and_object_but_no_guessing() -> None:
    assert bridge_reachable(b'"online"') is True
    assert bridge_reachable(b'{"state":"offline"}') is False
    assert bridge_reachable(b'{"state":"unbekannt"}') is None
    assert bridge_reachable(b"kein-json") is None
    # Valid JSON of a shape that carries no state at all. `None` and not `False`:
    # "we do not know" must not look like "the bridge is down", or an unreadable
    # message would raise a fault notice all by itself.
    assert bridge_reachable(b"[1, 2, 3]") is None
    assert bridge_reachable(b'{"zustand":"online"}') is None
    assert bridge_reachable(b"42") is None


def test_sensor_and_bridge_notices_carry_their_own_kind() -> None:
    """The gate needs the kind to decide anything -- both existing producers must
    set it, not just the new one."""
    fault = sensor_notice("sensor:1", "Testzone", "ok", "veraltet", Decimal("16.0"))
    assert fault is not None
    assert fault.kind == NOTICE_KIND_SENSOR_FAULT

    bridge_fault = bridge_notice(True, False)
    assert bridge_fault is not None
    assert bridge_fault.kind == NOTICE_KIND_BRIDGE_FAULT


def test_command_failure_reports_a_failure_and_exactly_one_recovery() -> None:
    fault = command_failure_notice("schaltbefehl:1", "Heizkoerper Flur", False, True)
    assert fault is not None
    assert fault.severity == "stoerung"
    assert fault.kind == NOTICE_KIND_COMMAND_FAILURE
    assert "Heizkoerper Flur" in fault.title
    # A repeated failure -- the device is still failing -- reports nothing again.
    assert command_failure_notice("schaltbefehl:1", "Heizkoerper Flur", True, True) is None

    all_clear = command_failure_notice("schaltbefehl:1", "Heizkoerper Flur", True, False)
    assert all_clear is not None
    assert all_clear.severity == "entwarnung"
    assert all_clear.kind == NOTICE_KIND_COMMAND_FAILURE
    # Two attempts in a row that both work report nothing.
    assert command_failure_notice("schaltbefehl:1", "Heizkoerper Flur", False, False) is None


def test_first_observed_command_failure_is_not_suppressed() -> None:
    """Mirrors `test_first_observed_bridge_failure_is_not_suppressed` above: a
    device already failing the first time this process ever attempts it must still
    raise the alert, it does not get to wait for a second attempt."""
    assert command_failure_notice("schaltbefehl:1", "Geraet", None, True) is not None
    assert command_failure_notice("schaltbefehl:1", "Geraet", None, False) is None


def _settings(**overrides: bool) -> Setting:
    values: dict[str, bool] = {
        "notify_sensor_faults": True,
        "notify_bridge_faults": True,
        "notify_command_failures": True,
    }
    values.update(overrides)
    return Setting(**values)  # type: ignore[arg-type]


def test_the_gate_answers_per_kind_from_its_own_column() -> None:
    settings = _settings(notify_bridge_faults=False)
    assert notice_enabled(NOTICE_KIND_SENSOR_FAULT, settings) is True
    assert notice_enabled(NOTICE_KIND_BRIDGE_FAULT, settings) is False
    assert notice_enabled(NOTICE_KIND_COMMAND_FAILURE, settings) is True


def test_the_gate_rejects_an_unknown_kind_instead_of_guessing() -> None:
    """A `FaultNotice` without a real kind must not silently slip past the gate --
    see the mandatory `kind` field's docstring."""
    with pytest.raises(ValueError, match="Meldungsart"):
        notice_enabled("irgendwas", _settings())


def test_the_audit_action_matches_the_gate_verdict() -> None:
    """The audit trail must not claim a notice was sent when `notice_enabled` says
    it was not -- this is the pure decision behind that claim, used identically
    by `app.py` (sensor and bridge notices) and `services/publishing.py` (command
    failures)."""
    on = _settings(notify_command_failures=True)
    off = _settings(notify_command_failures=False)
    assert (
        notification_audit_action(NOTICE_KIND_COMMAND_FAILURE, on)
        == AUDIT_ACTION_NOTIFICATION_SENT
    )
    assert (
        notification_audit_action(NOTICE_KIND_COMMAND_FAILURE, off)
        == AUDIT_ACTION_NOTIFICATION_SUPPRESSED
    )


def test_the_audit_action_fails_open_before_setup_is_complete() -> None:
    """No `setting` row yet (`None`) must not be mistaken for "off" -- the same
    fail-open default every `notice_enabled` caller already uses."""
    assert (
        notification_audit_action(NOTICE_KIND_SENSOR_FAULT, None)
        == AUDIT_ACTION_NOTIFICATION_SENT
    )
