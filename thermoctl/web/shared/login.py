"""Anmeldung: Drosselung, Pruefung, Session-Cookies -- geteilt von HTML-Login und /ui-api."""
import asyncio
import secrets
import unicodedata
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from anyio import to_thread
from fastapi import Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl import audit
from thermoctl.auth.csrf import CSRF_COOKIE_NAME, csrf_token
from thermoctl.auth.passwords import hash_password, verify_password
from thermoctl.auth.sessions import COOKIE_NAME, create_session, session_lifetime_s
from thermoctl.config import get_settings
from thermoctl.db.base import utcnow
from thermoctl.db.models.identity import User
from thermoctl.web.urls import cookie_path

# Failed attempts counted per username. Lives in process memory, not in the
# database: it's meant to slow down guessing, not persist. There is deliberately no
# account lockout — in a single-household system it would mostly be a convenient way
# to lock yourself out.
FEHLVERSUCHE: dict[str, int] = {}


# The counter is keyed by whatever username was typed, so an unauthenticated
# caller chooses the keys. Without a bound, a stream of invented names grows this
# dict for as long as the process runs. The cap is generous next to the number of
# accounts a household has, and evicting the oldest entry is the right trade: the
# counter exists to slow down guessing at one name, not to remember every name
# ever tried.
MAX_FEHLVERSUCHE_EINTRAEGE = 1024


def merke_fehlversuch(username: str, versuche: int) -> None:
    FEHLVERSUCHE[username] = versuche
    while len(FEHLVERSUCHE) > MAX_FEHLVERSUCHE_EINTRAEGE:
        FEHLVERSUCHE.pop(next(iter(FEHLVERSUCHE)))


ERROR_MESSAGE = "Benutzername oder Passwort falsch."


# Generated once at load time, from randomness: only the computation time of
# `verify_password` is needed, never the corresponding password. Deliberately not a
# hardcoded value -- there's no hash in the repo, not even a meaningless one.
VERGLEICHS_HASH = hash_password(secrets.token_urlsafe(32))


async def sleep(seconds: float) -> None:
    """A dedicated function instead of a direct `time.sleep()` call, so tests can
    replace the delay via `monkeypatch.setattr` without actually waiting."""
    await asyncio.sleep(seconds)  # pragma: no cover - every test replaces this line


# Column width of `user.username` / `audit_log.object_id`, and a ceiling for the password
# so an unauthenticated caller cannot make Argon2id hash megabytes. Over the limit the
# answer is the same neutral failure as for wrong data.
MAX_USERNAME_LENGTH = 64


MAX_PASSWORD_LENGTH = 1024


# How many checks for one key may be waiting or running at the same time. Without a bound
# the delay is no obstacle at all: N parallel requests all wait the same second and then
# all get their guess. With it, one key manages at most `MAX_PARALLEL_ATTEMPTS` guesses per
# delay window (5 s at most), however many requests are sent at once. Over the bound the
# caller is refused at once, without a password check -- cheap for us, no guess for them.
MAX_PARALLEL_ATTEMPTS = 5

_IN_FLIGHT: dict[str, int] = {}


class TooManyAttempts(Exception):
    """More checks for one key at once than `MAX_PARALLEL_ATTEMPTS`; none was carried out."""


@asynccontextmanager
async def throttled_attempt(key: str) -> AsyncIterator[None]:
    """One password check for `key`: counted first, then delayed, then (in the body) made.

    Shared by the login and the password change, so there is one throttle, not two.

    The attempt is **counted before the wait**, not after the check: if the counter were read,
    waited on and written back afterwards, every parallel request would see the same count,
    wait the same time and then check -- the delay would slow down one guess at a time and not
    at all a flood of them. Between reading and reserving there is no `await`, so on the
    event loop the two steps cannot be interleaved. The body reports
    a success with `reset_failures`; a failure needs nothing, it is counted already.

    Raises `TooManyAttempts` -- before anything was counted or waited -- if
    `MAX_PARALLEL_ATTEMPTS` checks for this key are already under way.
    """
    in_flight = _IN_FLIGHT.get(key, 0)
    if in_flight >= MAX_PARALLEL_ATTEMPTS:
        raise TooManyAttempts(key)
    previous_failures = FEHLVERSUCHE.get(key, 0)
    _IN_FLIGHT[key] = in_flight + 1
    merke_fehlversuch(key, previous_failures + 1)
    try:
        # `await`, not `time.sleep`: this handler runs on the same event loop as the
        # control cycle. A blocking sleep here stops the loop for everyone, so an
        # unauthenticated caller could delay heating decisions by hammering /login --
        # the actuators would keep their last state while the controller is stalled.
        await sleep(min(2**previous_failures, 5))
        yield
    finally:
        if _IN_FLIGHT[key] <= 1:
            del _IN_FLIGHT[key]
        else:
            _IN_FLIGHT[key] -= 1


def reset_failures(key: str) -> None:
    FEHLVERSUCHE[key] = 0


def _digit(char: str) -> str:
    return str(unicodedata.decimal(char)) if unicodedata.category(char) == "Nd" else char


def login_key(username: str) -> str:
    """The throttle key of a login: the typed name, folded -- **never** whether it exists.

    A key that depends on the account tells apart, by timing alone, a name that exists from
    one that does not: on MariaDB `lino`, `LINO` and `Lino ` are one account and so one
    counter, while the same spellings of an invented name would each have a fresh one. Keyed
    by the typed name only, existing and invented names behave the same for every spelling.

    The name is folded the way the database compares it, so the spellings one account answers
    to share a counter: compatibility forms and ligatures (NFKD), accents dropped, case folded
    (`ß` = `ss`), digits of other scripts as ASCII digits, invisible format and control
    characters (zero-width space, soft hyphen, byte order mark, NUL ...) removed, blanks at
    the edges removed. What a collation still treats as equal beyond that gets a counter of
    its own -- a throttle weakened a little for such names, no oracle.
    """
    folded = []
    for char in unicodedata.normalize("NFKD", username):
        if unicodedata.combining(char) or unicodedata.category(char) in ("Cc", "Cf"):
            continue
        folded.append(_digit(char))
    return f"name:{''.join(folded).strip().casefold()}"


async def attempt_login(
    request: Request, session: Session, username: str, password: str
) -> tuple[User, str, int] | None:
    """Checks credentials, throttles, audits and opens the session.

    Shared by the HTML form and the JSON login of the Svelte app, so neither has its
    own copy of the throttle, the constant-time comparison or the audit entries.
    Returns the user, the session secret and its lifetime, or `None` on failure.
    """
    if len(username) > MAX_USERNAME_LENGTH or len(password) > MAX_PASSWORD_LENGTH:
        return None
    try:
        # A lone surrogate (`"\ud800"` is valid JSON) cannot be sent to the database: the lookup
        # would raise instead of answering. It is no name anyone has -- the neutral failure.
        username.encode("utf-8")
        password.encode("utf-8")
    except UnicodeEncodeError:
        return None
    user = session.scalar(select(User).where(User.username == username))
    key = login_key(username)
    # The wait below must not hold a database connection: the lookup opened a transaction,
    # and a stream of invented names would otherwise park one pooled connection per
    # request for the length of the delay -- the pool gone, the whole service with it.
    session.commit()
    try:
        async with throttled_attempt(key):
            # The password check ALWAYS runs, even for an unknown username -- then against
            # a throwaway hash. Otherwise Python's short-circuit evaluation would skip
            # `verify_password` for an unknown name, and the request would be measurably
            # faster than for an existing name: Argon2id is deliberately slow, and exactly
            # this computation time would reveal which accounts exist. The same message
            # and the same wait time alone are not enough for that.
            if user is None:
                # Same reason as the sleep above: Argon2id is deliberately expensive, and
                # that cost must not be paid on the event loop.
                await to_thread.run_sync(verify_password, password, VERGLEICHS_HASH)
                successful = False
            else:
                # Checked for an inactive account too, and only then combined: a short-circuit
                # `is_active and verify` answers a deactivated account in a fraction of the time.
                password_ok = await to_thread.run_sync(
                    verify_password, password, user.password_hash
                )
                successful = password_ok and user.is_active
            if successful:
                reset_failures(key)
    except TooManyAttempts:
        return None

    if not successful:
        # The attempt is counted already (`throttled_attempt`). The same summary for
        # existing and non-existing usernames alike — the audit entry is allowed to
        # reveal what happened, but the HTTP response to the caller must not reveal
        # whether the username exists.
        audit.record(
            session, source="web", action="login_failed", object_type="user",
            object_id=username, summary=f"Anmeldung als '{username}' fehlgeschlagen",
        )
        return None

    assert user is not None
    user.last_login_at = utcnow()

    lifetime_s = session_lifetime_s(session)
    _entry, secret = create_session(
        session, user, lifetime_s,
        user_agent=request.headers.get("user-agent"),
        ip=request.client.host if request.client is not None else None,
    )
    audit.record(
        session, source="web", action="login", object_type="user",
        object_id=str(user.id), summary=f"Anmeldung als '{user.username}'",
        user_id=user.id,
    )
    return user, secret, lifetime_s


def set_session_cookies(
    response: Response, request: Request, secret: str, lifetime_s: int
) -> None:
    """The session cookie and its CSRF companion, shared with the JSON login."""
    settings = get_settings()
    response.set_cookie(
        COOKIE_NAME, secret, max_age=lifetime_s,
        httponly=True, samesite="lax", secure=settings.secure_cookies,
        path=cookie_path(request),
    )
    # Not httpOnly: the interface (Svelte app) reads the value and sends it along as an
    # `X-CSRF-Token` header.
    response.set_cookie(
        CSRF_COOKIE_NAME, csrf_token(secret, settings.secret_key.get_secret_value()),
        max_age=lifetime_s, httponly=False, samesite="lax", secure=settings.secure_cookies,
        path=cookie_path(request),
    )
