import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from thermoctl.config import Settings

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

SENSIBLE_SCHLUESSEL = frozenset(
    {
        "password",
        "passwort",
        "password_hash",
        "secret",
        "secret_key",
        "token",
        "token_hash",
        "api_token",
        "authorization",
        "cookie",
        "set-cookie",
        "session",
    }
)

_STANDARDFELDER = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__)


def mask(value: object) -> object:
    """Ersetzt Werte unter bekannten Schluesseln durch '***'.

    Rekursiv ueber Abbildungen und Sequenzen. Der Vergleich ist unabhaengig von
    Gross- und Kleinschreibung, weil HTTP-Kopfzeilen beliebig geschrieben ankommen.
    """
    if isinstance(value, dict):
        return {
            k: "***" if str(k).lower() in SENSIBLE_SCHLUESSEL else mask(v)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [mask(v) for v in value]
    return value


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


def configure_logging(settings: Settings) -> None:
    handler = logging.StreamHandler(sys.stdout)
    if settings.log_format == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")
        )
    wurzel = logging.getLogger()
    wurzel.handlers.clear()
    wurzel.addHandler(handler)
    wurzel.setLevel(settings.log_level.upper())
