"""Strukturiertes Logging mit Maskierung sensibler Zusatzfelder.

Projektregel: Geheimnisse gehoeren ausschliesslich in strukturierte Zusatzfelder
(``extra={...}``), niemals in den Meldungstext. Die Maskierung hier wirkt nur auf
diese Zusatzfelder — sowohl ueber ``MaskierungsFilter`` (wirkt in jedem Ausgabeformat)
als auch zusaetzlich ueber ``JsonFormatter`` (wirkt nur bei JSON-Ausgabe, redundant
zum Filter). Der Meldungstext selbst (``record.getMessage()``) ist zum Zeitpunkt der
Ausgabe bereits fertig formatierter Text und kann nicht mehr rueckwirkend maskiert
werden — ``log.info("passwort=%s", geheim)`` bleibt also sichtbar. Wer ein Geheimnis
loggen will, muss es als Zusatzfeld uebergeben, nie in die Meldung interpolieren.
"""

import json
import logging
import re
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from thermoctl.config import Settings

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

# Kernbegriffe statt exakter Schluessel: ein Schluessel gilt als sensibel, wenn er
# nach Normalisierung (Kleinschreibung, Trennzeichen entfernt) einen dieser
# Kernbegriffe als Teilzeichenkette enthaelt. Das erfasst auch zusammengesetzte
# Namen wie "mqtt_password", "client_secret" oder "refresh_token", ohne "username"
# faelschlich zu treffen — ein Nutzername ist kein Geheimnis.
KERNBEGRIFFE = frozenset(
    {
        "password",
        "passwort",
        "secret",
        "token",
        "credential",
        "cookie",
        "authorization",
        "apikey",
    }
)

_TRENNZEICHEN = re.compile(r"[_\-.]")

_STANDARDFELDER = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__)


def _normalisiere_schluessel(schluessel: object) -> str:
    return _TRENNZEICHEN.sub("", str(schluessel).lower())


def _ist_sensibel(schluessel: object) -> bool:
    normalisiert = _normalisiere_schluessel(schluessel)
    return any(begriff in normalisiert for begriff in KERNBEGRIFFE)


def mask(value: object) -> object:
    """Ersetzt Werte unter als sensibel erkannten Schluesseln durch '***'.

    Rekursiv ueber Abbildungen und Sequenzen. Ein Schluessel gilt als sensibel,
    wenn er (normalisiert: Kleinschreibung, ohne '_', '-', '.') einen Kernbegriff
    wie "password", "secret" oder "token" enthaelt — siehe ``KERNBEGRIFFE``.

    Wirkt ausschliesslich auf strukturierte Werte (Abbildungen, Listen, Tupel),
    also auf das, was als ``extra=...`` an einen Log-Aufruf uebergeben wird.
    Der bereits fertig formatierte Meldungstext eines Log-Aufrufs wird davon
    nicht erfasst und kann es prinzipbedingt nicht werden: Ein Geheimnis, das
    per ``log.info("passwort=%s", geheim)`` in die Meldung interpoliert wurde,
    bleibt sichtbar. Deshalb die Projektregel: Geheimnisse immer als Zusatzfeld
    uebergeben, nie in die Meldung schreiben.
    """
    if isinstance(value, dict):
        return {
            k: "***" if _ist_sensibel(k) else mask(v) for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [mask(v) for v in value]
    return value


class MaskierungsFilter(logging.Filter):
    """Maskiert sensible strukturierte Zusatzfelder eines Log-Datensatzes.

    Wirkt unabhaengig vom gewaehlten Ausgabeformat (JSON oder Text), weil sie
    als ``logging.Filter`` vor der Formatierung auf dem Datensatz selbst
    ansetzt. Erfasst nur Felder, die zusaetzlich zu den Standardattributen
    von ``logging.LogRecord`` per ``extra=...`` gesetzt wurden — nicht den
    Meldungstext, siehe Modul-Docstring.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        for schluessel, wert in list(record.__dict__.items()):
            if schluessel not in _STANDARDFELDER and not schluessel.startswith("_"):
                record.__dict__[schluessel] = mask(wert)
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        daten: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        anfrage_id = request_id_var.get()
        if anfrage_id is not None:
            daten["request_id"] = anfrage_id
        for schluessel, wert in record.__dict__.items():
            if schluessel not in _STANDARDFELDER and not schluessel.startswith("_"):
                daten[schluessel] = wert
        if record.exc_info:
            daten["exception"] = self.formatException(record.exc_info)
        return json.dumps(mask(daten), ensure_ascii=False, default=str)


class TextFormatter(logging.Formatter):
    """Textzeile mit angehaengten strukturierten Zusatzfeldern.

    Ohne diese Ergaenzung wuerden ueber ``extra=`` uebergebene Felder in der
    Textausgabe schlicht verschwinden — im Standardformat ``%(message)s`` kommen
    sie nicht vor. Das widerspraeche Grundsatz 5 (Debuggbarkeit ist ein Ziel).
    Die Felder kommen hier bereits durch ``MaskierungsFilter`` maskiert an, weil
    Filter vor Formatter laufen; die Maskierung hier ist eine zusaetzliche
    Absicherung, keine alleinige.
    """

    def format(self, record: logging.LogRecord) -> str:
        basis = super().format(record)
        zusatz = {
            schluessel: wert
            for schluessel, wert in record.__dict__.items()
            if schluessel not in _STANDARDFELDER and not schluessel.startswith("_")
        }
        if not zusatz:
            return basis
        gemaskt = mask(zusatz)
        assert isinstance(gemaskt, dict)
        teile = " ".join(f"{k}={v}" for k, v in gemaskt.items())
        return f"{basis} | {teile}"


def configure_logging(settings: Settings) -> None:
    handler = logging.StreamHandler(sys.stdout)
    if settings.log_format == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            TextFormatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")
        )
    # Maskiert Zusatzfelder unabhaengig vom Ausgabeformat. Bei JSON-Ausgabe
    # ueberschneidet sich das mit der Maskierung in JsonFormatter — das ist
    # gewollt: der Filter ist die Absicherung, falls das Format wechselt.
    handler.addFilter(MaskierungsFilter())
    wurzel = logging.getLogger()
    wurzel.handlers.clear()
    wurzel.addHandler(handler)
    wurzel.setLevel(settings.log_level.upper())
