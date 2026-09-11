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

**On rejection:** `ensure_transport()` returns `None` and nothing raises; the connection
itself is not cached, but the rejection *is*, as backoff state (see below) -- otherwise
nothing would ever slow the next attempt down. The caller (`services/publishing.py`)
treats a `None` transport exactly like a failed send -- it records the attempt as
`failed` in the command log, for every device that would have gone through this
session, and moves on. A rejected sign-in must not stop the rest of the cycle:
Zigbee2MQTT actuators never touch this module at all, and other zones' Meross actuators
get their own `failed` entry rather than being silently skipped or, worse, raising out
of the cycle.

**On a failed command:** `invalidate()` marks the cached connection bad so the *next*
cycle signs in again instead of waiting out the rest of `SESSION_TTL` against a
connection already known not to work. It does not retry within the same cycle -- a
retry loop against somebody else's cloud, from inside a control cycle, is its own can
of worms and not one this change opens. `services/publishing.py` calls it only for a
failure `integrations/actuators.py::MerossSwitch` itself identifies as a broker-level
rejection (`SwitchResult.session_fault`) -- a device that merely did not answer no
longer invalidates a session that was never the problem; see that module's docstring
for why the two are distinguishable at all.

**On a rejected sign-in: backoff, not another attempt next cycle.** The fault this
exists to fix: a rejected sign-in used to leave the cache empty, and an empty cache has
no `SESSION_TTL` to wait out -- so the *next* cycle, 32 seconds later by default, tried
again, failed the same way, and `invalidate()` (before this round, called on every
failed command) made sure of it by then discarding a connection that had never even
been re-established. Sixteen attempts in eight minutes against a real account is what
put it into `apiStatus=1301, Beyond Login Limit` in the first place, and every one of
the sixteen renewed the same lockout it was reacting to -- a self-sustaining loop
nothing outside the process could break.

`retry_after`/`next_backoff` below hold the state: doubling from `BACKOFF_INITIAL` up to
`BACKOFF_MAX` on each further rejection, reset by the next success. Exponential and
capped, not fixed or unbounded, because the two things known about the failure pull in
opposite directions -- `SESSION_TTL` is six hours, so a healthy account signs in only a
few times a day and a short fixed delay would still hammer a genuine lockout many times
before it lifts; but nothing here knows how long Meross's own limit actually lasts (it
publishes no number, the same reason `SESSION_TTL` itself is a guess), so committing to
one long fixed wait could leave a plant needlessly unable to heat for the length of that
guess even after the cloud already recovered. A short first step costs little when the
rejection was a one-off blip; each doubling costs geometrically less in extra wait than
it buys in fewer wasted attempts against a limit that is, empirically, measured in
minutes to hours, not seconds.

A rejection whose `apiStatus` marks it as permanent (wrong credentials --
`is_permanent_login_failure()` in `integrations/meross.py`) skips the climb and jumps
straight to `BACKOFF_MAX`: waiting is exactly as pointless after the first attempt as
the tenth, since nothing about a wrong password gets less wrong with the clock. It is
retried this slowly rather than not at all so an operator who fixes the account does
not also have to restart the process.

**Two callers share this backoff, and the HTTP session behind it -- not just this
module's own `ensure_transport()`.** `services/meross_discovery.py::fetch_devices`
used to sign in completely independently for the device-list reconciliation, and the
real lockout was hit with both signing in roughly two seconds apart on every shadow
cycle -- twice the login rate against the same limit for no reason: both calls
authenticate the same account. `valid_http_session()` below lets `fetch_devices` reuse
this module's still-fresh sign-in instead of getting its own -- with the shadow cycle's
own default interval, that session is all but always still valid whenever the hourly
reconciliation (`MEROSS_RECONCILE_INTERVAL_SECONDS`) runs, so in the common case (both
switching and discovery enabled) discovery's own sign-in count drops from roughly one
every hour to close to zero, on top of sharing the backoff for whenever it still needs
one. This does not need a fourth caller class or a store of its own: the same
`MerossSessionCache` that already carries the MQTT connection now carries the HTTP
session it was built from too, and both consult and update it -- so a rejection *or* a
successful sign-in from either side is visible to the other.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from thermoctl.config import Settings
from thermoctl.integrations.meross import (
    JsonTransport,
    MerossError,
    MerossSession,
    is_permanent_login_failure,
    sign_in,
)
from thermoctl.integrations.meross_mqtt import (
    AiomqttCommandTransport,
    MerossCommandTransport,
    MerossConnection,
)

log = logging.getLogger(__name__)

# See the module docstring for why this is a conservative guess rather than a measured
# expiry.
SESSION_TTL = timedelta(hours=6)

# See the module docstring's "On a rejected sign-in" section for the reasoning behind
# these three numbers.
BACKOFF_INITIAL = timedelta(minutes=1)
BACKOFF_MULTIPLIER = 2
BACKOFF_MAX = timedelta(minutes=30)


@dataclass
class MerossSessionCache:
    """Process-local cached sign-in state. One instance lives on `app.state`."""

    connection: MerossConnection | None = None
    # The HTTP sign-in `connection` was built from -- kept alongside it so
    # `services/meross_discovery.py::fetch_devices` can reuse the token for its own,
    # HTTP-only call (`device_list()`) instead of signing in a second time for the
    # same account. Shares `expires_at` and `invalid` below with `connection`: the
    # two come from the same sign-in and go stale together.
    http_session: MerossSession | None = None
    expires_at: datetime | None = None
    # Set by the caller when a command sent through `connection` failed, so the next
    # call to `ensure_transport()` signs in again rather than trusting a connection
    # already known to be bad for the rest of `SESSION_TTL`.
    invalid: bool = False
    # No sign-in attempt (from either caller -- see the module docstring) is made
    # before this time once a rejection has set it. `None` while no rejection is
    # outstanding.
    retry_after: datetime | None = None
    # The delay `record_rejection()` applies on the *next* rejection -- doubled each
    # time up to `BACKOFF_MAX`, reset to `BACKOFF_INITIAL` by `record_success()`.
    next_backoff: timedelta = BACKOFF_INITIAL
    # The cloud's own reason for the last rejected sign-in, so
    # `integrations/actuators.py` can report more than "no valid session" to the
    # operator (principle 5) without either caller reaching into container logs.
    # Carries only what `MerossError` already carries -- the cloud's `apiStatus` and
    # its own `info` text, never the account's email or password (principle 2).
    # Cleared by `record_success()`.
    last_rejection: str | None = None


def invalidate(cache: MerossSessionCache) -> None:
    """Marks the cached connection bad. The next `ensure_transport()` signs in again."""
    cache.invalid = True


def backoff_active(cache: MerossSessionCache, now: datetime) -> bool:
    """Whether a rejection's backoff is still running -- see the module docstring.

    Shared by `ensure_transport()` below and `services/meross_discovery.py`: both sign
    in against the same account and the same cloud-side limit, so a rejection from
    either side must hold both off, not just the one that hit it.
    """
    return cache.retry_after is not None and now < cache.retry_after


def record_rejection(cache: MerossSessionCache, now: datetime, error: MerossError) -> None:
    """Applies backoff after a rejected sign-in and remembers why (principle 5)."""
    cache.last_rejection = str(error)
    if is_permanent_login_failure(error):
        cache.next_backoff = BACKOFF_MAX
    cache.retry_after = now + cache.next_backoff
    cache.next_backoff = min(cache.next_backoff * BACKOFF_MULTIPLIER, BACKOFF_MAX)


def record_success(cache: MerossSessionCache) -> None:
    """A working sign-in proves the account and the cloud-side limit are fine again.

    Resets the backoff so one past rejection does not keep holding both callers off for
    the rest of `BACKOFF_MAX` once the cloud has already lifted whatever caused it.
    """
    cache.retry_after = None
    cache.next_backoff = BACKOFF_INITIAL
    cache.last_rejection = None


def _session_still_fresh(cache: MerossSessionCache, now: datetime) -> bool:
    """Whether `cache.expires_at` still names a time in the future and nothing has
    since marked the sign-in it came from bad (`invalidate()`)."""
    return not cache.invalid and cache.expires_at is not None and now < cache.expires_at


def record_login(cache: MerossSessionCache, now: datetime, account: MerossSession) -> None:
    """Remembers a successful sign-in's HTTP session and its expiry, and resets the
    backoff (`record_success()`) -- the state `ensure_transport()` and
    `services/meross_discovery.py::fetch_devices` both write after signing in
    themselves, so a fresh sign-in from either side benefits the other too.
    """
    cache.http_session = account
    cache.expires_at = now + SESSION_TTL
    record_success(cache)


def valid_http_session(cache: MerossSessionCache, now: datetime) -> MerossSession | None:
    """The cached HTTP sign-in, if it is still fresh enough to reuse -- `None` if it
    has expired, was invalidated, or nothing has signed in yet.

    For `services/meross_discovery.py::fetch_devices`: at the shadow cycle's default
    interval, `ensure_transport()` below has almost always already signed in more
    recently than the hourly device-list reconciliation runs, so this lets that
    reconciliation reuse the token instead of paying for a sign-in of its own -- see
    the module docstring for why that, not a longer `SESSION_TTL`, is where the real
    saving is.
    """
    if cache.http_session is not None and _session_still_fresh(cache, now):
        return cache.http_session
    return None


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
        cache.http_session = None
        cache.expires_at = None
        cache.invalid = False
        return None

    if cache.connection is not None and _session_still_fresh(cache, now):
        return AiomqttCommandTransport(cache.connection)

    if backoff_active(cache, now):
        # A rejection is still in its backoff window (module docstring, "On a
        # rejected sign-in") -- not logged again here, the rejection that started it
        # already was, and logging every skipped cycle would be the same spam under
        # a different name.
        log.debug(
            "Meross-Anmeldung übersprungen -- noch in der Wartezeit nach Ablehnung",
            extra={"naechster_versuch": cache.retry_after.isoformat()}
            if cache.retry_after is not None
            else {},
        )
        return None

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
        cache.http_session = None
        cache.expires_at = None
        cache.invalid = False
        record_rejection(cache, now, exc)
        return None

    cache.connection = connection
    cache.invalid = False
    record_login(cache, now, account)
    return AiomqttCommandTransport(connection)
