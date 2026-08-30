from thermoctl.domain.fault_notice import bridge_notice, sensornotice
from thermoctl.integrations.mqtt.zigbee2mqtt import bridge_reachable


def test_zehn_gleiche_stoerungszustaende_ergeben_eine_meldung() -> None:
    vorher: str | None = "ok"
    notices = []
    for _ in range(10):
        notice = sensornotice("sensor:1", "Testzone", vorher, "veraltet")
        if notice is not None:
            notices.append(notice)
        vorher = "veraltet"

    assert len(notices) == 1
    assert notices[0].schwere == "stoerung"
    assert notices[0].schluessel == "sensor:1"


def test_entwarnung_nur_beim_wechsel_zurueck_auf_ok() -> None:
    assert sensornotice("sensor:1", "Testzone", "veraltet", "keine_quelle") is not None
    entwarnung = sensornotice("sensor:1", "Testzone", "veraltet", "ok")
    assert entwarnung is not None
    assert entwarnung.schwere == "entwarnung"
    assert sensornotice("sensor:1", "Testzone", "ok", "ok") is None
    assert sensornotice("sensor:1", "Testzone", None, "ok") is None


def test_erster_sensorzustand_ist_noch_keine_zustandsaenderung() -> None:
    assert sensornotice("sensor:1", "Testzone", None, "veraltet") is None
    assert sensornotice("sensor:1", "Testzone", None, "keine_quelle") is None


def test_bruecke_meldet_ausfall_und_genau_eine_wiederkehr() -> None:
    fault = bridge_notice(True, False)
    assert fault is not None
    assert fault.schwere == "stoerung"
    assert bridge_notice(False, False) is None

    entwarnung = bridge_notice(False, True)
    assert entwarnung is not None
    assert entwarnung.schwere == "entwarnung"
    assert bridge_notice(True, True) is None


def test_erster_beobachteter_brueckenausfall_wird_nicht_verschwiegen() -> None:
    assert bridge_notice(None, False) is not None
    assert bridge_notice(None, True) is None


def test_brueckenzustand_akzeptiert_text_und_objekt_aber_keine_vermutung() -> None:
    assert bridge_reachable(b'"online"') is True
    assert bridge_reachable(b'{"state":"offline"}') is False
    assert bridge_reachable(b'{"state":"unbekannt"}') is None
    assert bridge_reachable(b"kein-json") is None
