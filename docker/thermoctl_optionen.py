#!/usr/bin/env python3
"""Translates Home Assistant add-on options (``/data/options.json``) into the
``THERMOCTL_*`` environment variables ``thermoctl.config.Settings`` reads.

Standalone, standard library only. The image has no ``jq``; pulling one in for
a single JSON file at start-up is a bigger change than reusing the interpreter
that is already there for the application itself.

Called from ``docker/entrypoint.sh``, which is the only caller that matters:
it evaluates this script's stdout as shell. The output is therefore exactly
``export NAME=value`` lines -- one per option that should become an
environment variable -- and nothing else. In particular: no logging of values,
no echo, no debug output. A Meross or MQTT password must never appear in the
container's log, and evaluated shell text is the last place that should leak
one.

An environment variable the operator set explicitly always wins. This script
only ever fills in what is *not already set* -- see ``exports_for``.

The add-on's option schema is deliberately **flat** -- no nested groups. The
Home Assistant Supervisor validates the *submitted* configuration, and every
group named in the schema must appear in it; the add-on UI omits a group
nobody filled anything into when saving, which produced
``Missing option 'notify' in root`` even with an empty group as the default.
A flat schema has no groups to omit.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path
from typing import Any

#: Overridable so tests do not need to write to the real /data. In the
#: container this is always the path the Supervisor writes options to.
OPTIONS_FILE = Path(os.environ.get("THERMOCTL_ADDON_OPTIONS_FILE", "/data/options.json"))

#: A valid POSIX-style environment variable name -- what `_parse_env_field` accepts.
_BEZEICHNER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _database_url(options: dict[str, Any]) -> str | None:
    """Builds ``THERMOCTL_DATABASE_URL`` from the flat ``database_*`` options.

    SQLite under ``/data`` is the default: that is the add-on's persistent
    storage, the Supervisor mounts it as a volume, and it survives updates.
    MariaDB is the documented alternative and needs host/user/password/name;
    with any of those missing, this returns ``None`` and leaves
    ``THERMOCTL_DATABASE_URL`` unset so the application's own, clearer
    "Feld erforderlich" error fires instead of a broken connection string.
    """
    db_type = options.get("database_type") or "sqlite"
    if db_type == "sqlite":
        return "sqlite:////data/thermoctl.db"
    if db_type == "mariadb":
        host = options.get("database_host")
        port = options.get("database_port") or 3306
        user = options.get("database_user")
        password = options.get("database_password")
        name = options.get("database_name")
        if not (host and user and password and name):
            return None
        return f"mysql+pymysql://{user}:{password}@{host}:{port}/{name}"
    return None


def _bool(value: Any) -> str | None:
    if value is None:
        return None
    return "true" if value else "false"


#: Maps every ``thermoctl.config.Settings`` field this script actually translates to
#: the flat add-on option that fills it. ``database_url`` is here too even though it
#: comes from five separate ``database_*`` options via ``_database_url`` rather than a
#: single lookup -- the guard test below cares about the Settings field, not the shape
#: of the option(s) that fill it.
#:
#: Kept as data, not just inline calls in ``translate``, so a test can compare it
#: against ``Settings.model_fields`` and catch a field neither mapped here nor listed
#: in ``BEWUSST_AUSGELASSEN`` -- which is exactly how the MQTT client id was missed
#: once before: the setting existed in ``config.py`` and in ``.env.example``, just not
#: here.
ABGEBILDETE_FELDER: dict[str, str] = {
    "database_url": "database_type/database_host/database_port/database_user/"
    "database_password/database_name",
    "secret_key": "secret_key",
    "log_level": "log_level",
    "log_format": "log_format",
    "mqtt_enabled": "mqtt_enabled",
    "mqtt_host": "mqtt_host",
    "mqtt_port": "mqtt_port",
    "mqtt_tls": "mqtt_tls",
    "mqtt_username": "mqtt_username",
    "mqtt_password": "mqtt_password",
    "mqtt_client_id": "mqtt_client_id",
    "mqtt_base_topic": "mqtt_base_topic",
    "mqtt_prefix": "mqtt_prefix",
    "mqtt_ca_cert": "mqtt_ca_cert",
    "meross_email": "meross_email",
    "meross_password": "meross_password",
    "notify_webhook": "notify_webhook",
    "notify_webhook_token": "notify_webhook_token",
}

#: Settings fields this script deliberately does *not* offer as a dedicated add-on
#: option, with why -- read by the guard test so a field can be excluded on purpose
#: instead of by oversight. Not every setting an operator could set belongs in the
#: add-on UI; anything missing here can still be reached through the free-form ``env``
#: option below, or through a real environment variable, both of which win over
#: whatever this script would otherwise fill in.
BEWUSST_AUSGELASSEN: dict[str, str] = {
    "bind_host": "Container-intern; die Supervisor-Ingress-Anbindung legt das fest.",
    "bind_port": "Container-intern; die Supervisor-Ingress-Anbindung legt das fest.",
    "secure_cookies": "Container-intern; das Add-on terminiert TLS nicht selbst, das "
    "übernimmt Ingress.",
    "meross_api_base": "Regionswahl (iotx-eu/us/ap), die so gut wie nie vom "
    "Standardwert abweicht -- wer sie braucht, setzt sie ueber das freie `env`-Feld.",
    "mcp_token": "Der MCP-Server ist eine optionale, separat betriebene Erweiterung "
    "(docs/mcp.md), keine Add-on-UI-Einstellung.",
    "passkey_rp_id": "Passkeys sind eine optionale Funktion, deren Relying-Party-Id an "
    "einen konkreten Hostnamen gebunden ist -- ausserhalb dessen, was das Add-on-Setup "
    "sinnvoll vorbelegen kann.",
    "passkey_rp_name": "Folgt aus passkey_rp_id: ohne Relying-Party-Id ohnehin ohne "
    "Wirkung.",
    "passkey_origin": "Folgt aus passkey_rp_id: ohne Relying-Party-Id ohnehin ohne "
    "Wirkung.",
    "root_path": "Keine vom Betreiber auszufuellende Option: unter Ingress vergibt der "
    "Supervisor den Pfad selbst und teilt ihn nur zur Laufzeit ueber seine eigene API "
    "mit (docker/thermoctl_ingress.py, aus docker/entrypoint.sh aufgerufen) -- ein "
    "Betreiber kennt den Wert vorab gar nicht und koennte ihn hier nicht sinnvoll "
    "eintragen.",
}


def translate(options: dict[str, Any]) -> dict[str, str]:
    """Maps the flat add-on options to ``THERMOCTL_*`` values. Pure, no I/O.

    Only options with an actual value are returned -- an option left empty
    or absent must not overwrite the application's own default. The free-form
    ``env`` option is deliberately not handled here: see ``_parse_env_field``.
    """
    values: dict[str, Any] = {
        "THERMOCTL_DATABASE_URL": _database_url(options),
        "THERMOCTL_SECRET_KEY": options.get("secret_key"),
        "THERMOCTL_LOG_LEVEL": options.get("log_level"),
        "THERMOCTL_LOG_FORMAT": options.get("log_format"),
        "THERMOCTL_MQTT_ENABLED": _bool(options.get("mqtt_enabled")),
        "THERMOCTL_MQTT_HOST": options.get("mqtt_host"),
        "THERMOCTL_MQTT_PORT": options.get("mqtt_port"),
        "THERMOCTL_MQTT_TLS": _bool(options.get("mqtt_tls")),
        "THERMOCTL_MQTT_USERNAME": options.get("mqtt_username"),
        "THERMOCTL_MQTT_PASSWORD": options.get("mqtt_password"),
        "THERMOCTL_MQTT_CLIENT_ID": options.get("mqtt_client_id"),
        "THERMOCTL_MQTT_BASE_TOPIC": options.get("mqtt_base_topic"),
        "THERMOCTL_MQTT_PREFIX": options.get("mqtt_prefix"),
        "THERMOCTL_MQTT_CA_CERT": options.get("mqtt_ca_cert"),
        "THERMOCTL_MEROSS_EMAIL": options.get("meross_email"),
        "THERMOCTL_MEROSS_PASSWORD": options.get("meross_password"),
        "THERMOCTL_NOTIFY_WEBHOOK": options.get("notify_webhook"),
        "THERMOCTL_NOTIFY_WEBHOOK_TOKEN": options.get("notify_webhook_token"),
    }
    return {name: str(value) for name, value in values.items() if value not in (None, "")}


def _parse_env_field(text: str) -> dict[str, str]:
    """Parses the free-form ``env`` option: the body of a ``.env`` file.

    One ``NAME=WERT`` assignment per line. Added so the operator is not stuck
    waiting for a new add-on release every time a field is missing from the
    dedicated options above -- this reaches any ``THERMOCTL_*`` variable at all.

    - Blank lines and lines starting with ``#`` (after stripping surrounding
      whitespace) are skipped.
    - A leading ``export `` is tolerated and stripped.
    - Whitespace around the name and the value is stripped.
    - Quotes (single or double) that wrap the *whole* value are removed; quote
      characters anywhere inside the value are left alone.
    - A name that is not a valid identifier (``[A-Za-z_][A-Za-z0-9_]*``) is
      discarded, and so is a line with no ``=`` at all.
    - The value may itself contain ``=``; splitting happens on the first one.
    - A name repeated across lines keeps the last assignment, same as a shell
      sourcing the same lines would.

    Deliberately does not log anything, valid or discarded: a discarded line
    can itself be a mistyped secret, and the only way to guarantee no value
    ever reaches stderr is to never print any part of a line here.
    """
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip()
        value = value.strip()
        if not _BEZEICHNER.fullmatch(name):
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        values[name] = value
    return values


def exports_for(options: dict[str, Any], environ: dict[str, str]) -> list[str]:
    """The ``export`` lines to emit -- skips anything already in ``environ``.

    Combines the dedicated options (``translate``) with the free-form ``env``
    field, which is allowed to override them -- exactly the precedence order
    from the task: dedicated option, then ``env``, then (winning over both) a
    real, externally set environment variable.
    """
    values = dict(translate(options))
    env_field = options.get("env")
    if isinstance(env_field, str):
        values.update(_parse_env_field(env_field))
    return [
        f"export {name}={shlex.quote(value)}"
        for name, value in sorted(values.items())
        if name not in environ
    ]


def main() -> int:
    if not OPTIONS_FILE.is_file():
        # No options.json: ordinary `docker compose` operation. Nothing changes.
        return 0
    try:
        options = json.loads(OPTIONS_FILE.read_text())
    except (OSError, json.JSONDecodeError) as error:
        print(f"# thermoctl-optionen: {OPTIONS_FILE} nicht lesbar: {error}", file=sys.stderr)
        return 1
    if not isinstance(options, dict):
        print(f"# thermoctl-optionen: {OPTIONS_FILE} ist kein JSON-Objekt", file=sys.stderr)
        return 1
    for line in exports_for(options, dict(os.environ)):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
