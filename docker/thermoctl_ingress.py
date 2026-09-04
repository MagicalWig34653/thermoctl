#!/usr/bin/env python3
"""Determines ``THERMOCTL_ROOT_PATH`` under Home Assistant Ingress at start-up.

Standalone, standard library only -- same reasoning as ``thermoctl_optionen.py``: this
runs before the application is installed as an importable package.

Under Ingress, the Supervisor assigns the path prefix itself, in the form
``/api/hassio_ingress/<random token>/``. An operator cannot know it in advance and so
cannot enter it into the add-on configuration. The Supervisor's own API tells us: a GET
to ``http://supervisor/addons/self/info``, authenticated with the token from the
``SUPERVISOR_TOKEN`` environment variable, returns (among other things) the add-on's
``ingress_entry`` -- exactly the path it is reachable under.

Both the URL's host and the token exist only inside an add-on container. Their absence
(``SUPERVISOR_TOKEN`` unset) is exactly how this script recognises ordinary
``docker compose`` operation and does nothing at all -- no network call, no output.

The Supervisor's answer is not trusted blindly: it is validated the same way any
attacker-influenced input into a value that ends up in every generated link and
``Location`` header would be, before ``exports_for`` ever considers exporting it. And it
never wins over a ``THERMOCTL_ROOT_PATH`` an operator already set explicitly -- same
precedence rule as every add-on option in ``thermoctl_optionen.py``.

Called from ``docker/entrypoint.sh``, which evaluates this script's stdout as shell. The
output is therefore exactly one ``export THERMOCTL_ROOT_PATH=...`` line, or nothing --
never a log line, never the token. A failed Supervisor query (no network, timeout,
error status, broken JSON, a rejected value) is not fatal: it is logged to stderr in a
form that cannot contain the token, and the start continues without a prefix.
"""

from __future__ import annotations

import json
import os
import shlex
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

#: Overridable so tests do not need a real Supervisor. In the container this is
#: always the Supervisor's own internal API.
SUPERVISOR_URL = os.environ.get(
    "THERMOCTL_INGRESS_SUPERVISOR_URL", "http://supervisor/addons/self/info"
)

#: Kept short: this runs on every container start and must never make the start hang.
#: A real Supervisor answers on its internal network essentially instantly.
SUPERVISOR_TIMEOUT = float(os.environ.get("THERMOCTL_INGRESS_TIMEOUT", "5"))

#: The type `urllib.request.urlopen` matches closely enough for the one call site here.
Opener = Callable[..., Any]


def _gueltiger_pfad(wert: Any) -> bool:
    """Strict allow-list for a value that becomes ``THERMOCTL_ROOT_PATH``.

    This value is not merely stored -- it is used to build every link, redirect and
    cookie path the interface emits (see `thermoctl.config.Settings.root_path`). A
    Supervisor response is external input, so it is checked here as strictly as any
    other untrusted string headed for that role, independent of and in addition to
    the normalisation `Settings` itself does:

    - must be a non-empty string starting with exactly one ``/`` (a bare path, not
      an absolute URL reachable via some other host -- a second leading ``/`` makes
      it a protocol-relative URL a browser would resolve against a different host)
    - no ``..`` (no path traversal out of the prefix)
    - no line break (this can end up in log lines and HTTP headers)
    - no quote character (this is later shell-quoted and also has no business in a
      URL path)
    - no ``://`` (rules out a scheme sneaked in anywhere in the value, not just at
      the front)
    """
    if not isinstance(wert, str) or not wert:
        return False
    if not wert.startswith("/") or wert.startswith("//"):
        return False
    if ".." in wert:
        return False
    if "\n" in wert or "\r" in wert:
        return False
    if '"' in wert or "'" in wert:
        return False
    if "://" in wert:
        return False
    return True


def _supervisor_abfragen(url: str, token: str, timeout: float, opener: Opener) -> Any:
    """Makes the one Supervisor API call and returns the parsed JSON body.

    Raises on any failure (network, timeout, HTTP error status, malformed JSON) --
    the caller decides what "failure" means for the start-up, this function only
    talks to the Supervisor. The token is used exclusively as the Authorization
    header value here and is never put into any exception message this function
    itself constructs.
    """
    # The URL is either the fixed Supervisor address or an explicit test override,
    # never attacker-controlled -- same shape as the http:// call in the Dockerfile's
    # own HEALTHCHECK.
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})  # noqa: S310
    with opener(request, timeout=timeout) as response:
        body = response.read()
    return json.loads(body)


def ermittle_root_path(
    token: str | None,
    *,
    url: str = SUPERVISOR_URL,
    timeout: float = SUPERVISOR_TIMEOUT,
    opener: Opener = urllib.request.urlopen,
) -> str | None:
    """Returns a validated ingress path, or ``None`` if none applies.

    ``None`` covers every "do nothing" case alike: no token (not running as an
    add-on), a failed request, an unexpected response shape, or a value that fails
    `_gueltiger_pfad`. The caller does not need to distinguish them -- all of them
    mean "start without a prefix" -- but each is logged to stderr with enough detail
    to diagnose without ever including the token or the raw untrusted value's full
    content beyond what was already validated as safe to print.
    """
    if not token:
        return None
    try:
        antwort = _supervisor_abfragen(url, token, timeout, opener)
    except urllib.error.HTTPError as error:
        print(
            f"# thermoctl-ingress: Supervisor antwortete mit Status {error.code}, "
            "starte ohne Ingress-Pfad",
            file=sys.stderr,
        )
        return None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
        print(
            f"# thermoctl-ingress: Supervisor-Abfrage fehlgeschlagen ({type(error).__name__}), "
            "starte ohne Ingress-Pfad",
            file=sys.stderr,
        )
        return None

    if not isinstance(antwort, dict) or antwort.get("result") != "ok":
        print(
            "# thermoctl-ingress: unerwartete Supervisor-Antwort, starte ohne Ingress-Pfad",
            file=sys.stderr,
        )
        return None

    daten = antwort.get("data")
    pfad = daten.get("ingress_entry") if isinstance(daten, dict) else None

    if not _gueltiger_pfad(pfad):
        print(
            "# thermoctl-ingress: Supervisor lieferte einen ungueltigen ingress_entry-Wert, "
            "starte ohne Ingress-Pfad",
            file=sys.stderr,
        )
        return None

    return pfad


def main() -> int:
    if "THERMOCTL_ROOT_PATH" in os.environ:
        # Precedence rule from thermoctl_optionen.py: an operator-set value always
        # wins, whatever the Supervisor says.
        return 0

    pfad = ermittle_root_path(os.environ.get("SUPERVISOR_TOKEN"))
    if pfad is not None:
        print(f"export THERMOCTL_ROOT_PATH={shlex.quote(pfad)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
