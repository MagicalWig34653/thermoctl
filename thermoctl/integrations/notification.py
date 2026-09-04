"""Fail-safe delivery of fault notices to the log and webhook."""

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from thermoctl.config import Settings
from thermoctl.domain.fault_notice import FaultNotice

log = logging.getLogger(__name__)


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


async def send(settings: Settings, notice: FaultNotice) -> None:
    """Always logs and attempts the optional webhook, without propagating errors."""
    log.warning(
        "%s: %s",
        notice.title,
        notice.text,
        extra={"schluessel": notice.key, "schwere": notice.severity},
    )
    if settings.notify_webhook is None:
        return
    try:
        await asyncio.to_thread(_send_webhook, settings, notice)
    except Exception:
        log.exception(
            "Stoerungsmeldung konnte nicht an den Webhook gesendet werden",
            extra={"schluessel": notice.key},
        )


# The notice a human explicitly asked for, not one control derived from a state
# change. `severity="test"` keeps it out of `FaultNotice`'s two real categories
# ("stoerung", "entwarnung") -- a receiving system that branches on `schwere`
# should not mistake this for either.
_TEST_NOTICE = FaultNotice(
    key="test",
    severity="test",
    title="Testmeldung von thermoctl",
    text="Dies ist eine Testmeldung. Keine Stoerung liegt vor.",
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
