from decimal import Decimal

from thermoctl.domain.fault_notice import bridge_notice, sensor_notice
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
