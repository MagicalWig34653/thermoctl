import json
import logging
import sys

import pytest

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
    marker = request_id_var.set("abc123")
    try:
        satz = logging.LogRecord("t", logging.INFO, "p", 1, "hallo", None, None)
        daten = json.loads(JsonFormatter().format(satz))
        assert daten["request_id"] == "abc123"
    finally:
        request_id_var.reset(marker)


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
    result = mask(roh)
    assert result == {"aussen": {"secret_key": "***"}, "liste": [{"cookie": "***"}]}


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
    result = mask(roh)
    assert result == {
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


def test_ausnahme_landet_im_json_protokoll() -> None:
    """Ohne den Stapelauszug ist eine Ausnahme im Protokoll nur eine Meldung ohne Ort."""
    import json
    import logging as py_logging

    from thermoctl.logging import JsonFormatter

    try:
        raise ValueError("etwas ging schief")
    except ValueError:
        satz = py_logging.LogRecord(
            "test", py_logging.ERROR, __file__, 1, "gescheitert", None, sys.exc_info()
        )
    ausgabe = json.loads(JsonFormatter().format(satz))
    assert "exception" in ausgabe
    assert "ValueError" in ausgabe["exception"]


def test_textzeile_wiederholt_ihre_eigene_meldung_nicht() -> None:
    """Jede Textlogzeile endete mit einer Kopie ihrer selbst.

    `super().format()` traegt `message` und `asctime` nachtraeglich in den LogRecord ein.
    Beide standen nicht in `_STANDARDFELDER`, das beim Import aus einem frischen Record
    entsteht — also hielt der Formatierer sie fuer Zusatzfelder und haengte sie an:
    "thermoctl startet | database=… message=thermoctl startet asctime=…". Die Felder
    werden jetzt vor dem Formatieren eingesammelt.
    """
    import logging as py_logging

    from thermoctl.logging import TextFormatter

    satz = py_logging.LogRecord("test", py_logging.INFO, __file__, 1, "schlicht", None, None)
    assert TextFormatter().format(satz) == "schlicht"

    mit_zusatz = py_logging.LogRecord("test", py_logging.INFO, __file__, 1, "gemeldet", None, None)
    mit_zusatz.zone = "wohnzimmer"  # type: ignore[attr-defined]
    ausgabe = TextFormatter("%(asctime)s %(message)s").format(mit_zusatz)
    assert ausgabe.endswith("gemeldet | zone=wohnzimmer")
    assert "message=" not in ausgabe and "asctime=" not in ausgabe


def test_textformat_wird_gewaehlt(monkeypatch: pytest.MonkeyPatch) -> None:
    """`THERMOCTL_LOG_FORMAT=text` ist die Wahl fuer Menschen, json die fuer Maschinen."""
    import logging as py_logging

    from thermoctl.config import Settings
    from thermoctl.logging import JsonFormatter, configure_logging

    for format_wahl, expected_json in (("text", False), ("json", True)):
        settings = Settings(
            _env_file=None, database_url="sqlite://", secret_key="s" * 32,
            log_format=format_wahl,
        )
        configure_logging(settings)
        handler = py_logging.getLogger().handlers[0]
        assert isinstance(handler.formatter, JsonFormatter) is expected_json, format_wahl
