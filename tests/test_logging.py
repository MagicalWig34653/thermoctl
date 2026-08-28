import json
import logging

from thermoctl.config import Settings
from thermoctl.logging import (
    JsonFormatter,
    MaskierungsFilter,
    configure_logging,
    mask,
    request_id_var,
)


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


def test_maskierung_erkennt_zusammengesetzte_schluessel() -> None:
    roh = {
        "mqtt_password": "geheim1",
        "client_secret": "geheim2",
        "refresh_token": "geheim3",
        "broker_password": "geheim4",
        "access_token": "geheim5",
    }
    ergebnis = mask(roh)
    assert ergebnis == {
        "mqtt_password": "***",
        "client_secret": "***",
        "refresh_token": "***",
        "broker_password": "***",
        "access_token": "***",
    }


def test_username_wird_nicht_maskiert() -> None:
    # "username" enthaelt keinen Kernbegriff (password/secret/token/...) und
    # ist selbst kein Geheimnis — er darf lesbar bleiben.
    assert mask({"username": "lino", "mqtt_username": "lino"}) == {
        "username": "lino",
        "mqtt_username": "lino",
    }


def test_maskierung_wirkt_auch_bei_textformat(capsys: object) -> None:
    settings = Settings(
        _env_file=None,
        database_url="sqlite://",
        secret_key="a" * 32,
        log_format="text",
    )
    configure_logging(settings)
    logger = logging.getLogger("test.textformat")
    logger.info("verbindung", extra={"mqtt_password": "streng-geheim"})

    ausgabe = capsys.readouterr().out  # type: ignore[attr-defined]
    assert "streng-geheim" not in ausgabe
    assert "***" in ausgabe


def test_filter_maskiert_oberstes_zusatzfeld_isoliert() -> None:
    # Prueft den Filter fuer sich, ohne Formatter: Der Filter muss anhand des
    # Feldnamens entscheiden, nicht anhand des (nackten) Werts. Ein oberstes
    # Zusatzfeld wie "mqtt_password" hat als Attributname genau die Information,
    # die mask() braucht -- die uebergibt der Filter bislang nicht mit.
    satz = logging.LogRecord("t", logging.INFO, "p", 1, "verbindung", None, None)
    satz.mqtt_password = "streng-geheim"  # type: ignore[attr-defined]
    MaskierungsFilter().filter(satz)
    assert satz.mqtt_password == "***"  # type: ignore[attr-defined]


def test_filter_maskiert_verschachtelte_zusatzfelder_isoliert() -> None:
    satz = logging.LogRecord("t", logging.INFO, "p", 1, "verbindung", None, None)
    satz.config = {"secret_key": "streng-geheim"}  # type: ignore[attr-defined]
    MaskierungsFilter().filter(satz)
    assert satz.config == {"secret_key": "***"}  # type: ignore[attr-defined]


def test_maskierung_erkennt_camelcase_zusammengesetzte_schluessel() -> None:
    roh = {"mqttPassword": "geheim1", "refreshToken": "geheim2"}
    assert mask(roh) == {"mqttPassword": "***", "refreshToken": "***"}


def test_maskierung_lehnt_teilzeichenketten_treffer_ab() -> None:
    # Segmentweise exakte Pruefung statt Teilzeichenketten-Suche: diese Namen
    # enthalten "token"/"password"/"secret" nur als Teil eines laengeren Worts
    # in einem Segment, nicht als eigenes Segment, und sind keine Geheimnisse.
    roh = {
        "tokenizer": "x",
        "passwordless_supported": True,
        "secretary_name": "Kim",
        "username": "lino",
    }
    assert mask(roh) == roh


def test_maskierung_behaelt_absichtliche_ueberdeckung_bei() -> None:
    # Abwaegung: Im Zweifel lieber ein harmloses Feld zu viel schwaerzen als
    # ein Geheimnis zu wenig. "token_count" und "cookie_policy" haben ein
    # Segment ("token"/"cookie"), das exakt einem Kernbegriff entspricht --
    # sie werden bewusst mitmaskiert, obwohl sie selbst kein Geheimnis sind.
    roh = {"token_count": 3, "cookie_policy": "strict"}
    assert mask(roh) == {"token_count": "***", "cookie_policy": "***"}


def test_geheimnis_in_meldungstext_bleibt_sichtbar() -> None:
    # Bewusste, dokumentierte Grenze: Die Maskierung wirkt nur auf strukturierte
    # Zusatzfelder (extra=...), nicht auf den fertig formatierten Meldungstext.
    # `log.info("passwort=%s", geheim)` erzeugt zur Ausgabezeit bereits Klartext,
    # der nicht mehr rueckwirkend maskiert werden kann. Dieser Test haelt das
    # bewusst fest — er ist kein Fehler, der "repariert" werden soll.
    satz = logging.LogRecord(
        "t", logging.INFO, "p", 1, "passwort=streng-geheim", None, None
    )
    text = JsonFormatter().format(satz)
    assert "streng-geheim" in text
