"""Structured logging with masking of sensitive extra fields.

Project rule: secrets belong exclusively in structured extra fields (``extra={...}``),
never in the message text. The masking here only acts on these extra fields -- both
via ``MaskierungsFilter`` (works in every output format) and additionally via
``JsonFormatter`` (works only for JSON output, redundant with the filter). The message
text itself (``record.getMessage()``) is already fully formatted text by the time of
output and can no longer be masked retroactively -- so ``log.info("password=%s",
secret)`` stays visible. Whoever wants to log a secret must pass it as an extra field,
never interpolate it into the message.

The single deliberate exception to this rule in the whole project: ``create_app()`` in
``thermoctl/app.py`` interpolates the freshly generated setup token directly into the
message text instead of passing it as an extra field. That is the only channel through
which the operator gets this one-time token on first startup. Whoever later turns this
into ``extra={"setup_token": ...}`` because it looks "cleaner" disables the token's
output: the segment "token" immediately carries it into ``KERNBEGRIFFE``. That is not a
bug, it is intentional -- do not "fix" it.
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

# Core terms instead of exact keys: a key counts as sensitive if one of its
# segments (see _segmentiere_schluessel) matches one of these core terms
# exactly. This catches compound names like "mqtt_password", "client_secret",
# or "refresh_token", without falsely matching "username" -- a username is
# not a secret.
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
# Detects CamelCase transitions (lowercase letter/digit -> uppercase letter), so
# that e.g. "mqttPassword" is also split into the segments "mqtt" and "password"
# instead of being treated as one contiguous word.
_CAMELCASE_UEBERGANG = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

_DEFAULT_FIELDS = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__)


def _segmentiere_schluessel(schluessel: object) -> list[str]:
    mit_trennern = _CAMELCASE_UEBERGANG.sub("_", str(schluessel))
    return [teil.lower() for teil in _TRENNZEICHEN.split(mit_trennern) if teil]


def _ist_sensibel(schluessel: object) -> bool:
    # Exact per-segment check instead of substring search: a key only counts as
    # sensitive if a whole segment matches a core term exactly -- not already
    # when the core term occurs somewhere as a substring. This avoids false
    # positives like "tokenizer", "passwordless_supported", or "secretary_name",
    # where the core term is only part of a longer word within a segment.
    #
    # Trade-off: names like "token_count" or "cookie_policy" have their own
    # segment that matches a core term exactly, and are therefore still (also)
    # masked, even though they are not themselves a secret. This is deliberately
    # accepted: when in doubt, better to redact one harmless field too many than
    # one secret too few.
    return any(
        segment in KERNBEGRIFFE for segment in _segmentiere_schluessel(schluessel)
    )


def mask(value: object) -> object:
    """Replaces values under keys recognized as sensitive with '***'.

    Recursive over mappings and sequences. A key counts as sensitive if one of
    its segments (separators '_', '-', '.' as well as CamelCase transitions,
    lowercased) matches a core term such as "password", "secret", or "token"
    exactly -- see ``KERNBEGRIFFE``.

    Acts exclusively on structured values (mappings, lists, tuples), i.e. on
    what is passed as ``extra=...`` to a log call. The already fully formatted
    message text of a log call is not covered by this and cannot be in
    principle: a secret interpolated into the message via
    ``log.info("password=%s", secret)`` stays visible. Hence the project rule:
    always pass secrets as an extra field, never write them into the message.
    """
    if isinstance(value, dict):
        return {
            k: "***" if _ist_sensibel(k) else mask(v) for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [mask(v) for v in value]
    return value


class MaskierungsFilter(logging.Filter):
    """Masks sensitive structured extra fields of a log record.

    Works independently of the chosen output format (JSON or text), because as
    a ``logging.Filter`` it acts on the record itself before formatting.
    Covers only fields set in addition to the standard attributes of
    ``logging.LogRecord`` via ``extra=...`` -- not the message text, see the
    module docstring.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        for schluessel, value in list(record.__dict__.items()):
            if schluessel not in _DEFAULT_FIELDS and not schluessel.startswith("_"):
                # The field name itself decides whether it gets masked -- mask()
                # cannot judge a bare value, it only recognizes sensitive spots
                # via keys within mappings. For a top-level extra field such as
                # "mqtt_password", the attribute name "mqtt_password" is exactly
                # that key.
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
    """Text line with structured extra fields appended.

    Without this addition, fields passed via ``extra=`` would simply vanish in
    the text output -- the standard format ``%(message)s`` does not include
    them. That would violate principle 5 (debuggability is a goal). The fields
    arrive here already masked by ``MaskierungsFilter``, because filters run
    before formatters; the masking here is an additional safeguard, not the
    only one.
    """

    def format(self, record: logging.LogRecord) -> str:
        # The extra fields are collected BEFORE `super().format()`. Afterwards the
        # formatter has added `message` and (with an `asctime` pattern) `asctime` to
        # `record.__dict__` -- neither was in `_STANDARDFELDER`, which is built from a
        # fresh LogRecord at import time. The consequence was that every text line
        # repeated its own message once more at the end:
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
    # Masks extra fields independently of the output format. With JSON output
    # this overlaps with the masking in JsonFormatter -- that is intentional:
    # the filter is the safeguard in case the format changes.
    handler.addFilter(MaskierungsFilter())
    wurzel = logging.getLogger()
    wurzel.handlers.clear()
    wurzel.addHandler(handler)
    wurzel.setLevel(settings.log_level.upper())
