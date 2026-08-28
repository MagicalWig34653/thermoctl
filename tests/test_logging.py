import json
import logging

from thermoctl.logging import JsonFormatter, mask, request_id_var


def test_ausgabe_ist_gueltiges_json() -> None:
    satz = logging.LogRecord("t", logging.INFO, "p", 1, "hallo", None, None)
    daten = json.loads(JsonFormatter().format(satz))
    assert daten["message"] == "hallo"
    assert daten["level"] == "INFO"
    assert "timestamp" in daten


def test_anfrage_id_landet_im_datensatz() -> None:
    marke = request_id_var.set("abc123")
    try:
        satz = logging.LogRecord("t", logging.INFO, "p", 1, "hallo", None, None)
        daten = json.loads(JsonFormatter().format(satz))
        assert daten["request_id"] == "abc123"
    finally:
        request_id_var.reset(marke)


def test_zusatzfelder_werden_uebernommen() -> None:
    satz = logging.LogRecord("t", logging.INFO, "p", 1, "hallo", None, None)
    satz.zone = "Wohnzimmer"  # type: ignore[attr-defined]
    daten = json.loads(JsonFormatter().format(satz))
    assert daten["zone"] == "Wohnzimmer"


def test_maskierung_greift_bei_bekannten_schluesseln() -> None:
    roh = {"username": "lino", "password": "geheim", "token": "tctl_x_y"}
    assert mask(roh) == {"username": "lino", "password": "***", "token": "***"}


def test_maskierung_wirkt_verschachtelt_und_in_listen() -> None:
    roh = {"aussen": {"secret_key": "s"}, "liste": [{"cookie": "c"}]}
    ergebnis = mask(roh)
    assert ergebnis == {"aussen": {"secret_key": "***"}, "liste": [{"cookie": "***"}]}


def test_maskierung_ist_unabhaengig_von_gross_kleinschreibung() -> None:
    assert mask({"Authorization": "Bearer x"}) == {"Authorization": "***"}


def test_maskierte_felder_erscheinen_nicht_in_der_ausgabe() -> None:
    satz = logging.LogRecord("t", logging.INFO, "p", 1, "start", None, None)
    satz.config = {"secret_key": "streng-geheim"}  # type: ignore[attr-defined]
    text = JsonFormatter().format(satz)
    assert "streng-geheim" not in text
    assert "***" in text
