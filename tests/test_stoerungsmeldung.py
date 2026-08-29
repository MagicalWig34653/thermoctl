from thermoctl.domain.stoerungsmeldung import brueckenmeldung, sensormeldung
from thermoctl.integrations.mqtt.zigbee2mqtt import bruecke_erreichbar


def test_zehn_gleiche_stoerungszustaende_ergeben_eine_meldung() -> None:
    vorher: str | None = "ok"
    meldungen = []
    for _ in range(10):
        meldung = sensormeldung("sensor:1", "Testzone", vorher, "veraltet")
        if meldung is not None:
            meldungen.append(meldung)
        vorher = "veraltet"

    assert len(meldungen) == 1
    assert meldungen[0].schwere == "stoerung"
    assert meldungen[0].schluessel == "sensor:1"


def test_entwarnung_nur_beim_wechsel_zurueck_auf_ok() -> None:
    assert sensormeldung("sensor:1", "Testzone", "veraltet", "keine_quelle") is not None
    entwarnung = sensormeldung("sensor:1", "Testzone", "veraltet", "ok")
    assert entwarnung is not None
    assert entwarnung.schwere == "entwarnung"
    assert sensormeldung("sensor:1", "Testzone", "ok", "ok") is None
    assert sensormeldung("sensor:1", "Testzone", None, "ok") is None


def test_erster_sensorzustand_ist_noch_keine_zustandsaenderung() -> None:
    assert sensormeldung("sensor:1", "Testzone", None, "veraltet") is None
    assert sensormeldung("sensor:1", "Testzone", None, "keine_quelle") is None


def test_bruecke_meldet_ausfall_und_genau_eine_wiederkehr() -> None:
    stoerung = brueckenmeldung(True, False)
    assert stoerung is not None
    assert stoerung.schwere == "stoerung"
    assert brueckenmeldung(False, False) is None

    entwarnung = brueckenmeldung(False, True)
    assert entwarnung is not None
    assert entwarnung.schwere == "entwarnung"
    assert brueckenmeldung(True, True) is None


def test_erster_beobachteter_brueckenausfall_wird_nicht_verschwiegen() -> None:
    assert brueckenmeldung(None, False) is not None
    assert brueckenmeldung(None, True) is None


def test_brueckenzustand_akzeptiert_text_und_objekt_aber_keine_vermutung() -> None:
    assert bruecke_erreichbar(b'"online"') is True
    assert bruecke_erreichbar(b'{"state":"offline"}') is False
    assert bruecke_erreichbar(b'{"state":"unbekannt"}') is None
    assert bruecke_erreichbar(b"kein-json") is None
