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
"""

from __future__ import annotations

import json
import os
import shlex
import sys
from pathlib import Path
from typing import Any

#: Overridable so tests do not need to write to the real /data. In the
#: container this is always the path the Supervisor writes options to.
OPTIONS_FILE = Path(os.environ.get("THERMOCTL_ADDON_OPTIONS_FILE", "/data/options.json"))


def _get(options: dict[str, Any], *path: str) -> Any:
    """Reads a possibly-nested key, or ``None`` if any segment is missing."""
    node: Any = options
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node


def _database_url(options: dict[str, Any]) -> str | None:
    """Builds ``THERMOCTL_DATABASE_URL`` from the ``database`` option group.

    SQLite under ``/data`` is the default: that is the add-on's persistent
    storage, the Supervisor mounts it as a volume, and it survives updates.
    MariaDB is the documented alternative and needs host/user/password/name;
    with any of those missing, this returns ``None`` and leaves
    ``THERMOCTL_DATABASE_URL`` unset so the application's own, clearer
    "Feld erforderlich" error fires instead of a broken connection string.
    """
    db_type = _get(options, "database", "type") or "sqlite"
    if db_type == "sqlite":
        return "sqlite:////data/thermoctl.db"
    if db_type == "mariadb":
        host = _get(options, "database", "host")
        port = _get(options, "database", "port") or 3306
        user = _get(options, "database", "user")
        password = _get(options, "database", "password")
        name = _get(options, "database", "database")
        if not (host and user and password and name):
            return None
        return f"mysql+pymysql://{user}:{password}@{host}:{port}/{name}"
    return None


def _bool(value: Any) -> str | None:
    if value is None:
        return None
    return "true" if value else "false"


#: Maps every ``thermoctl.config.Settings`` field this script actually translates to
#: the add-on option path it reads. ``database_url`` is here too even though it comes
#: from the ``database`` option *group* via ``_database_url`` rather than a single
#: ``_get`` call -- the guard test below cares about the Settings field, not the shape
#: of the option that fills it.
#:
#: Kept as data, not just inline calls in ``translate``, so a test can compare it
#: against ``Settings.model_fields`` and catch a field neither mapped here nor listed
#: in ``BEWUSST_AUSGELASSEN`` -- which is exactly how the MQTT client id was missed:
#: the setting existed in ``config.py`` and in ``.env.example``, just not here.
ABGEBILDETE_FELDER: dict[str, str] = {
    "database_url": "database.*",
    "secret_key": "secret_key",
    "log_level": "log_level",
    "log_format": "log_format",
    "mqtt_enabled": "mqtt.enabled",
    "mqtt_host": "mqtt.host",
    "mqtt_port": "mqtt.port",
    "mqtt_tls": "mqtt.tls",
    "mqtt_username": "mqtt.username",
    "mqtt_password": "mqtt.password",
    "mqtt_client_id": "mqtt.client_id",
    "mqtt_base_topic": "mqtt.base_topic",
    "mqtt_prefix": "mqtt.prefix",
    "mqtt_ca_cert": "mqtt.ca_cert",
    "meross_api_base": "meross.api_base",
    "meross_email": "meross.email",
    "meross_password": "meross.password",
    "notify_webhook": "notify.webhook",
    "notify_webhook_token": "notify.webhook_token",
}

#: Settings fields this script deliberately does *not* offer as an add-on option, with
#: why -- read by the guard test so a field can be excluded on purpose instead of by
#: oversight. Not every setting an operator could set belongs in the add-on UI.
BEWUSST_AUSGELASSEN: dict[str, str] = {
    "bind_host": "Container-intern; die Supervisor-Ingress-Anbindung legt das fest.",
    "bind_port": "Container-intern; die Supervisor-Ingress-Anbindung legt das fest.",
    "secure_cookies": "Container-intern; das Add-on terminiert TLS nicht selbst, das "
    "übernimmt Ingress.",
    "mcp_token": "Der MCP-Server ist eine optionale, separat betriebene Erweiterung "
    "(docs/mcp.md), keine Add-on-UI-Einstellung.",
    "passkey_rp_id": "Passkeys sind eine optionale Funktion, deren Relying-Party-Id an "
    "einen konkreten Hostnamen gebunden ist -- ausserhalb dessen, was das Add-on-Setup "
    "sinnvoll vorbelegen kann.",
    "passkey_rp_name": "Folgt aus passkey_rp_id: ohne Relying-Party-Id ohnehin ohne "
    "Wirkung.",
    "passkey_origin": "Folgt aus passkey_rp_id: ohne Relying-Party-Id ohnehin ohne "
    "Wirkung.",
}


def translate(options: dict[str, Any]) -> dict[str, str]:
    """Maps add-on options to ``THERMOCTL_*`` values. Pure, no I/O.

    Only options with an actual value are returned -- an option left empty
    or absent must not overwrite the application's own default.
    """
    values: dict[str, Any] = {
        "THERMOCTL_DATABASE_URL": _database_url(options),
        "THERMOCTL_SECRET_KEY": _get(options, "secret_key"),
        "THERMOCTL_LOG_LEVEL": _get(options, "log_level"),
        "THERMOCTL_LOG_FORMAT": _get(options, "log_format"),
        "THERMOCTL_MQTT_ENABLED": _bool(_get(options, "mqtt", "enabled")),
        "THERMOCTL_MQTT_HOST": _get(options, "mqtt", "host"),
        "THERMOCTL_MQTT_PORT": _get(options, "mqtt", "port"),
        "THERMOCTL_MQTT_TLS": _bool(_get(options, "mqtt", "tls")),
        "THERMOCTL_MQTT_USERNAME": _get(options, "mqtt", "username"),
        "THERMOCTL_MQTT_PASSWORD": _get(options, "mqtt", "password"),
        "THERMOCTL_MQTT_CLIENT_ID": _get(options, "mqtt", "client_id"),
        "THERMOCTL_MQTT_BASE_TOPIC": _get(options, "mqtt", "base_topic"),
        "THERMOCTL_MQTT_PREFIX": _get(options, "mqtt", "prefix"),
        "THERMOCTL_MQTT_CA_CERT": _get(options, "mqtt", "ca_cert"),
        "THERMOCTL_MEROSS_EMAIL": _get(options, "meross", "email"),
        "THERMOCTL_MEROSS_PASSWORD": _get(options, "meross", "password"),
        "THERMOCTL_MEROSS_API_BASE": _get(options, "meross", "api_base"),
        "THERMOCTL_NOTIFY_WEBHOOK": _get(options, "notify", "webhook"),
        "THERMOCTL_NOTIFY_WEBHOOK_TOKEN": _get(options, "notify", "webhook_token"),
    }
    return {name: str(value) for name, value in values.items() if value not in (None, "")}


def exports_for(options: dict[str, Any], environ: dict[str, str]) -> list[str]:
    """The ``export`` lines to emit -- skips anything already in ``environ``.

    That is the precedence rule from the task: an operator-set environment
    variable always wins over the options file, whatever it says.
    """
    return [
        f"export {name}={shlex.quote(value)}"
        for name, value in sorted(translate(options).items())
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
