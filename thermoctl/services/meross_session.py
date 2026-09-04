"""Keeps a signed-in Meross session alive across control cycles.

Signing in and building the MQTT connection details (`integrations/meross.py::sign_in`,
`integrations/meross_mqtt.py::MerossConnection.build`) are HTTP calls to the
manufacturer's cloud. Doing that on every switching decision -- inside the same
transaction that also holds the shadow cycle's database session -- is exactly the fault
that once locked the whole SQLite file for up to 40 seconds while unrelated requests
answered with 500 and 401 (`app.py`'s `_run_detached_meross_refresh` carries the same
lesson for the device-list reconciliation). This module gives the shadow loop a place
to sign in *once*, well before any
transaction opens, and reuse the result until it is due for a refresh or a command
reports it invalid.

**Lifetime, decided here:** `SESSION_TTL` below. Meross documents no token lifetime
anywhere public, so a conservative period is chosen rather than a guessed exact expiry
-- long enough that a healthy account signs in only a few times a day (each sign-in is
itself a cloud round trip that can fail or rate-limit), short enough that a rotated
password or a revoked session is picked up the same day rather than staying stuck on a
connection nothing will ever accept again.

**On rejection:** `ensure_transport()` returns `None`, nothing raises, and nothing is
cached. The caller (`services/publishing.py`) treats a `None` transport exactly like a
failed send -- it records the attempt as `failed` in the command log, for every device
that would have gone through this session, and moves on. A rejected sign-in must not
stop the rest of the cycle: Zigbee2MQTT actuators never touch this module at all, and
other zones' Meross actuators get their own `failed` entry rather than being silently
skipped or, worse, raising out of the cycle.

**On a failed command:** `invalidate()` marks the cached connection bad so the *next*
cycle signs in again instead of waiting out the rest of `SESSION_TTL` against a
connection already known not to work. It does not retry within the same cycle -- a
retry loop against somebody else's cloud, from inside a control cycle, is its own can
of worms and not one this change opens. The known trade-off: a persistent, non-auth
failure (the broker itself unreachable, say) also triggers a fresh sign-in every cycle
instead of backing off, because a `MerossError` from a failed send does not distinguish
"the account needs a new session" from "the network is down right now". Accepted for
this round because the operator sees every attempt in the command log regardless of the
reason -- but noted here rather than left silently in the code.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from thermoctl.config import Settings
from thermoctl.integrations.meross import JsonTransport, MerossError, sign_in
from thermoctl.integrations.meross_mqtt import (
    AiomqttCommandTransport,
    MerossCommandTransport,
    MerossConnection,
)

log = logging.getLogger(__name__)

# See the module docstring for why this is a conservative guess rather than a measured
# expiry.
SESSION_TTL = timedelta(hours=6)


@dataclass
class MerossSessionCache:
    """Process-local cached sign-in state. One instance lives on `app.state`."""

    connection: MerossConnection | None = None
    expires_at: datetime | None = None
    # Set by the caller when a command sent through `connection` failed, so the next
    # call to `ensure_transport()` signs in again rather than trusting a connection
    # already known to be bad for the rest of `SESSION_TTL`.
    invalid: bool = False


def invalidate(cache: MerossSessionCache) -> None:
    """Marks the cached connection bad. The next `ensure_transport()` signs in again."""
    cache.invalid = True


async def ensure_transport(
    settings: Settings,
    http: JsonTransport,
    cache: MerossSessionCache,
    now: datetime,
) -> MerossCommandTransport | None:
    """Returns a transport to switch Meross devices through, signing in first if needed.

    **Must be called before any database transaction opens** -- `sign_in()` is an HTTP
    call to the Meross cloud with its own timeout (`integrations/meross.py`,
    `UrllibJsonTransport`, 20 seconds). `None` means no attempt should be made this
    cycle: no account is configured, or the cloud refused the sign-in.
    """
    # Written as an explicit narrowing check, not `credentials_configured(settings)`:
    # mypy cannot follow a boolean helper's implication that both fields below are
    # set, and `sign_in` needs both narrowed to `str`, not `str | None` (the same
    # reasoning as `services/meross_discovery.py::fetch_devices`).
    if settings.meross_email is None or settings.meross_password is None:
        cache.connection = None
        cache.expires_at = None
        cache.invalid = False
        return None

    if (
        cache.connection is not None
        and not cache.invalid
        and cache.expires_at is not None
        and now < cache.expires_at
    ):
        return AiomqttCommandTransport(cache.connection)

    try:
        account = await sign_in(
            http,
            settings.meross_api_base,
            settings.meross_email,
            settings.meross_password.get_secret_value(),
        )
        connection = MerossConnection.build(account)
    except MerossError as exc:
        log.error(
            "Meross-Anmeldung abgelehnt -- Aktoren bleiben diesen Zyklus unerreichbar",
            extra={"grund": str(exc)},
        )
        cache.connection = None
        cache.expires_at = None
        cache.invalid = False
        return None

    cache.connection = connection
    cache.expires_at = now + SESSION_TTL
    cache.invalid = False
    return AiomqttCommandTransport(connection)
