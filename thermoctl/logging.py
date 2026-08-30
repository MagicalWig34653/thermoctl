"""Strukturiertes Logging mit Maskierung sensibler Zusatzfelder.

Projektregel: Geheimnisse gehoeren ausschliesslich in strukturierte Zusatzfelder
(``extra={...}``), niemals in den Meldungstext. Die Maskierung hier wirkt nur auf
diese Zusatzfelder — sowohl ueber ``MaskierungsFilter`` (wirkt in jedem Ausgabeformat)
als auch zusaetzlich ueber ``JsonFormatter`` (wirkt nur bei JSON-Ausgabe, redundant
zum Filter). Der Meldungstext selbst (``record.getMessage()``) ist zum Zeitpunkt der
Ausgabe bereits fertig formatierter Text und kann nicht mehr rueckwirkend maskiert
werden — ``log.info("passwort=%s", geheim)`` bleibt also sichtbar. Wer ein Geheimnis
loggen will, muss es als Zusatzfeld uebergeben, nie in die Meldung interpolieren.

Einzige bewusste Ausnahme von dieser Regel im gesamten Projekt: ``create_app()`` in
``thermoctl/app.py`` interpoliert das frisch erzeugte Setup-Token direkt in den
Meldungstext, statt es als Zusatzfeld zu uebergeben. Das ist der einzige Kanal, ueber
den der Betreiber beim Erststart an dieses Einmal-Token kommt. Wer hier spaeter ein
``extra={"setup_token": ...}`` daraus macht, weil das "sauberer" aussieht, schaltet
die Ausgabe des Tokens ab: Das Segment "token" traegt es sofort in ``KERNBEGRIFFE``.
Das ist kein Fehler, sondern Absicht — nicht "reparieren".
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

# Kernbegriffe statt exakter Schluessel: ein Schluessel gilt als sensibel, wenn
# eines seiner Segmente (siehe _segmentiere_schluessel) exakt einem dieser
# Kernbegriffe entspricht. Das erfasst zusammengesetzte Namen wie
# "mqtt_password", "client_secret" oder "refresh_token", ohne "username"
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
# Erkennt CamelCase-Uebergaenge (Kleinbuchstabe/Ziffer -> Grossbuchstabe), damit
# z. B. "mqttPassword" ebenfalls in die Segmente "mqtt" und "password" zerlegt
# wird statt als ein zusammenhaengendes Wort behandelt zu werden.
_CAMELCASE_UEBERGANG = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

_DEFAULT_FIELDS = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__)


def _segmentiere_schluessel(schluessel: object) -> list[str]:
    mit_trennern = _CAMELCASE_UEBERGANG.sub("_", str(schluessel))
    return [teil.lower() for teil in _TRENNZEICHEN.split(mit_trennern) if teil]


def _ist_sensibel(schluessel: object) -> bool:
    # Segmentweise exakte Pruefung statt Teilzeichenketten-Suche: ein Schluessel
    # gilt nur als sensibel, wenn ein ganzes Segment einem Kernbegriff
    # entspricht — nicht schon, wenn der Kernbegriff irgendwo als Teilstring
    # vorkommt. Das vermeidet Fehlalarme wie "tokenizer", "passwordless_supported"
    # oder "secretary_name", bei denen der Kernbegriff nur Teil eines laengeren
    # Worts in einem Segment ist.
    #
    # Abwaegung: Namen wie "token_count" oder "cookie_policy" haben ein eigenes
    # Segment, das exakt einem Kernbegriff entspricht, und werden deshalb
    # weiterhin (mit-)maskiert, obwohl sie selbst kein Geheimnis sind. Das wird
    # bewusst in Kauf genommen: Im Zweifel lieber ein harmloses Feld zu viel
    # schwaerzen als ein Geheimnis zu wenig.
    return any(
        segment in KERNBEGRIFFE for segment in _segmentiere_schluessel(schluessel)
    )


def mask(value: object) -> object:
    """Ersetzt Werte unter als sensibel erkannten Schluesseln durch '***'.

    Rekursiv ueber Abbildungen und Sequenzen. Ein Schluessel gilt als sensibel,
    wenn eines seiner Segmente (Trennzeichen '_', '-', '.' sowie CamelCase-
    Uebergaenge, kleingeschrieben) exakt einem Kernbegriff wie "password",
    "secret" oder "token" entspricht — siehe ``KERNBEGRIFFE``.

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
        for schluessel, value in list(record.__dict__.items()):
            if schluessel not in _DEFAULT_FIELDS and not schluessel.startswith("_"):
                # Der Feldname selbst entscheidet, ob maskiert wird -- mask()
                # kann einen nackten Wert nicht beurteilen, es erkennt sensible
                # Stellen nur anhand von Schluesseln in Abbildungen. Fuer ein
                # oberstes Zusatzfeld wie "mqtt_password" ist der Attributname
                # "mqtt_password" genau dieser Schluessel.
                record.__dict__[schluessel] = (
                    "***" if _ist_sensibel(schluessel) else mask(value)
                )
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
        for schluessel, value in record.__dict__.items():
            if schluessel not in _DEFAULT_FIELDS and not schluessel.startswith("_"):
                daten[schluessel] = value
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
        # Die Zusatzfelder werden VOR `super().format()` eingesammelt. Danach hat der
        # Formatierer `message` und (bei einem `asctime`-Muster) `asctime` in
        # `record.__dict__` nachgetragen — beide standen nicht in `_STANDARDFELDER`, das
        # beim Import aus einem frischen LogRecord entsteht. Die Folge war, dass jede
        # Textzeile ihre eigene Meldung am Ende noch einmal wiederholte:
        # "thermoctl startet | database=... message=thermoctl startet asctime=...".
        zusatz = {
            schluessel: value
            for schluessel, value in record.__dict__.items()
            if schluessel not in _DEFAULT_FIELDS and not schluessel.startswith("_")
        }
        basis = super().format(record)
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
