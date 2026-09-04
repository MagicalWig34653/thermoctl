"""Fail-safe delivery of fault notices to the log and webhook."""

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from sqlalchemy.orm import Session, sessionmaker

from thermoctl.config import Settings
from thermoctl.db.base import utcnow
from thermoctl.db.engine import session_scope
from thermoctl.db.models.operations import Setting
from thermoctl.domain.fault_notice import NOTICE_KIND_TEST, FaultNotice

log = logging.getLogger(__name__)

# `notify_last_error` is shown back in the interface (part two of this feature) --
# a webhook's own error text is not a trustworthy source of truth and must not
# grow unbounded there. Hard cutoff, not a smart summary: simple, and it can never
# itself become the source of an oversized value.
_MAX_ERROR_LENGTH = 200


class _NoRedirectHandler(HTTPRedirectHandler):
    """Refuses every redirect a webhook answers with.

    The stdlib's default `HTTPRedirectHandler` follows a redirect and carries every
    header but `Content-*` along with it -- `Authorization` included, across hosts.
    A webhook that answers with a redirect is not a case worth supporting: whoever
    controls it (or takes it over) could otherwise point thermoctl, bearer token and
    all, at any address of their choosing. See
    docs/sicherheitsdurchsicht-2026-09-02.md. Deliberately does not require
    `https` -- a webhook to an address on the operator's own network over plain
    `http` is a legitimate case (the finding names it explicitly), and the
    redirect, not the scheme, is the actual hole.
    """

    def redirect_request(
        self,
        req: Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> Request | None:
        raise HTTPError(req.full_url, code, "Webhook-Weiterleitung abgelehnt", None, None)  # type: ignore[arg-type]


# Built once: this opener, not `urlopen`'s default one, is what makes
# `_send_webhook` refuse redirects. Tests replace this attribute to observe what
# is sent without going over the network.
_opener = build_opener(_NoRedirectHandler())


def _send_webhook(settings: Settings, notice: FaultNotice) -> int:
    """Posts the notice and returns the webhook's HTTP status code.

    Raises on any failure (a non-2xx status, a refused redirect, a timeout, an
    unreachable host) -- callers decide for themselves whether that failure is
    something to swallow (`send`, a real fault notice must never crash the caller)
    or something to report back (`send_test`, that report is the whole point).
    """
    data = json.dumps(
        {
            "schluessel": notice.key,
            "schwere": notice.severity,
            "titel": notice.title,
            "text": notice.text,
        }
    ).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if settings.notify_webhook_token is not None:
        token = settings.notify_webhook_token.get_secret_value()
        headers["Authorization"] = f"Bearer {token}"
    request = Request(  # noqa: S310 -- address is operator configuration
        settings.notify_webhook or "", data=data, headers=headers, method="POST"
    )
    with _opener.open(request, timeout=10) as response:  # noqa: S310
        return int(response.status)


def _short_reason(exc: BaseException) -> str:
    """A short, operator-facing reason for `notify_last_error` -- never the
    webhook's token (it never appears in an exception's text, it lives only in the
    request header) and hard-truncated regardless, since this text ends up
    unfiltered in the interface."""
    text = str(exc).strip() or exc.__class__.__name__
    if len(text) > _MAX_ERROR_LENGTH:
        text = text[: _MAX_ERROR_LENGTH - 1].rstrip() + "…"
    return text


async def _attempt_delivery(settings: Settings, notice: FaultNotice) -> tuple[bool, str | None]:
    """Logs unconditionally, then attempts the optional webhook once.

    The shared primitive behind both `send()` and `deliver()` below -- `send()` is
    the original fire-and-forget behaviour every existing caller still gets;
    `deliver()` additionally needs to know whether the attempt succeeded, to write
    that down. Returns `(True, None)` when nothing was attempted (no webhook
    configured) -- there was nothing to fail.
    """
    log.warning(
        "%s: %s",
        notice.title,
        notice.text,
        extra={"schluessel": notice.key, "schwere": notice.severity},
    )
    if settings.notify_webhook is None:
        return True, None
    try:
        await asyncio.to_thread(_send_webhook, settings, notice)
    except Exception as exc:
        log.exception(
            "Störungsmeldung konnte nicht an den Webhook gesendet werden",
            extra={"schluessel": notice.key},
        )
        return False, _short_reason(exc)
    return True, None


async def send(settings: Settings, notice: FaultNotice) -> None:
    """Always logs and attempts the optional webhook, without propagating errors."""
    await _attempt_delivery(settings, notice)


async def deliver(
    session_factory: sessionmaker[Session], settings: Settings, notice: FaultNotice
) -> None:
    """Sends a notice like `send()` above, and durably records the webhook's
    delivery outcome in the `setting` row -- what the interface's delivery display
    (part two of this feature) reads.

    Meant for every notice actually leaving the service: production notices whose
    kind passed `domain.fault_notice.notice_enabled`, and the interface's own test
    notice -- deliberately callable on its own for that, since a test button has no
    shadow cycle around it to piggyback on.

    **Ordering matters and is deliberate.** The network attempt
    (`_attempt_delivery`, via `asyncio.to_thread`) runs first and to completion,
    entirely outside any transaction. Only once it is fully done does a *new*,
    short-lived `session_scope` open, purely to write down when it was tried and
    what happened -- never while the webhook call is in flight. This project has
    already paid once for the opposite order: a network call awaited from inside an
    open transaction locked the SQLite file for as long as the call took. Nothing
    here repeats that; see `tests/test_notification.py` for the test that checks
    this property directly rather than the call order in the source.

    Writes nothing when no webhook is configured at all -- there was no delivery
    attempt to report on, only the same unconditional log line `send()` also
    writes.
    """
    ok, error = await _attempt_delivery(settings, notice)
    if settings.notify_webhook is None:
        return
    with session_scope(session_factory) as session:
        setting = session.get(Setting, 1)
        if setting is not None:
            setting.notify_last_attempt_at = utcnow()
            setting.notify_last_ok = ok
            setting.notify_last_error = error


# The notice a human explicitly asked for, not one control derived from a state
# change. `severity="test"` keeps it out of `FaultNotice`'s two real categories
# ("stoerung", "entwarnung") -- a receiving system that branches on `schwere`
# should not mistake this for either.
_TEST_NOTICE = FaultNotice(
    kind=NOTICE_KIND_TEST,
    key="test",
    severity="test",
    title="Testmeldung von thermoctl",
    text="Dies ist eine Testmeldung. Keine Störung liegt vor.",
)

# How much of a failed webhook's own text ends up in the answer shown to the
# operator. The far end is not a trusted source -- capped, so a webhook that
# answers with megabytes of HTML cannot turn a settings page into a problem of
# its own, and short enough that nothing worth hiding fits in it either.
_ERROR_TEXT_LIMIT = 200


@dataclass(frozen=True)
class WebhookTestResult:
    """What came back from a real send attempt against the configured webhook.

    Meant for display, not for logs: `error` is already short and, where a
    webhook token is configured, already has it removed -- see `_scrub`.
    """

    ok: bool
    status_code: int | None
    duration_seconds: float
    error: str | None


def _scrub(text: str, settings: Settings) -> str:
    """Removes the webhook token from a piece of text, then shortens it.

    The token only ever leaves as an `Authorization` header, never as part of a URL
    or a message thermoctl itself builds -- this exists as a second line of defence
    in case a future `urllib` exception ever echoes the request back verbatim.
    """
    if settings.notify_webhook_token is not None:
        token = settings.notify_webhook_token.get_secret_value()
        if token:
            text = text.replace(token, "***")
    return text[:_ERROR_TEXT_LIMIT]


async def send_test(settings: Settings) -> WebhookTestResult:
    """Sends a marked test notice over the exact path a real fault notice takes.

    Not a second implementation of the webhook call: same request construction
    (`_send_webhook`), same redirect refusal, same timeout. Only the notice content
    differs, and what happens with the outcome -- `send` logs and swallows it,
    this reports it back so a typo in the address shows up immediately instead of
    only the next time a sensor actually fails.

    Callers are expected to have checked `settings.notify_webhook is not None`
    already (the interface hides or explains the button otherwise); called
    without one configured, this reports that plainly instead of raising.
    """
    if settings.notify_webhook is None:
        return WebhookTestResult(
            ok=False,
            status_code=None,
            duration_seconds=0.0,
            error="Kein Webhook hinterlegt.",
        )
    start = time.monotonic()
    try:
        status_code = await asyncio.to_thread(_send_webhook, settings, _TEST_NOTICE)
    except HTTPError as exc:
        return WebhookTestResult(
            ok=False,
            status_code=exc.code,
            duration_seconds=time.monotonic() - start,
            error=f"Die Gegenstelle antwortete mit Status {exc.code}.",
        )
    except URLError as exc:
        return WebhookTestResult(
            ok=False,
            status_code=None,
            duration_seconds=time.monotonic() - start,
            error=_scrub(str(exc.reason), settings),
        )
    except Exception as exc:  # noqa: BLE001 -- an unfamiliar failure still needs an answer
        return WebhookTestResult(
            ok=False,
            status_code=None,
            duration_seconds=time.monotonic() - start,
            error=_scrub(str(exc), settings),
        )
    return WebhookTestResult(
        ok=True,
        status_code=status_code,
        duration_seconds=time.monotonic() - start,
        error=None,
    )
