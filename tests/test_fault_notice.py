from thermoctl.domain.fault_notice import bridge_notice, sensornotice
from thermoctl.integrations.mqtt.zigbee2mqtt import bridge_reachable


def test_ten_identical_fault_states_produce_one_notice() -> None:
    previous: str | None = "ok"
    notices = []
    for _ in range(10):
        notice = sensornotice("sensor:1", "Testzone", previous, "veraltet")
        if notice is not None:
            notices.append(notice)
        previous = "veraltet"

    assert len(notices) == 1
    assert notices[0].schwere == "stoerung"
    assert notices[0].schluessel == "sensor:1"


def test_all_clear_only_on_transition_back_to_ok() -> None:
    assert sensornotice("sensor:1", "Testzone", "veraltet", "keine_quelle") is not None
    all_clear = sensornotice("sensor:1", "Testzone", "veraltet", "ok")
    assert all_clear is not None
    assert all_clear.schwere == "entwarnung"
    assert sensornotice("sensor:1", "Testzone", "ok", "ok") is None
    assert sensornotice("sensor:1", "Testzone", None, "ok") is None


def test_first_sensor_state_is_not_yet_a_state_change() -> None:
    assert sensornotice("sensor:1", "Testzone", None, "veraltet") is None
    assert sensornotice("sensor:1", "Testzone", None, "keine_quelle") is None


def test_bridge_reports_a_failure_and_exactly_one_recovery() -> None:
    fault = bridge_notice(True, False)
    assert fault is not None
    assert fault.schwere == "stoerung"
    assert bridge_notice(False, False) is None

    all_clear = bridge_notice(False, True)
    assert all_clear is not None
    assert all_clear.schwere == "entwarnung"
    assert bridge_notice(True, True) is None


def test_first_observed_bridge_failure_is_not_suppressed() -> None:
    assert bridge_notice(None, False) is not None
    assert bridge_notice(None, True) is None


def test_bridge_state_accepts_text_and_object_but_no_guessing() -> None:
    assert bridge_reachable(b'"online"') is True
    assert bridge_reachable(b'{"state":"offline"}') is False
    assert bridge_reachable(b'{"state":"unbekannt"}') is None
    assert bridge_reachable(b"kein-json") is None
