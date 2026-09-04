"""Fail-safe delivery of fault notices to the log and webhook."""

import asyncio
import json
import logging
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from sqlalchemy.orm import Session, sessionmaker

from thermoctl.config import Settings
from thermoctl.db.base import utcnow
from thermoctl.db.engine import session_scope
from thermoctl.db.models.operations import Setting
from thermoctl.domain.fault_notice import FaultNotice

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


def _send_webhook(settings: Settings, notice: FaultNotice) -> None:
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
    with _opener.open(request, timeout=10):  # noqa: S310 -- URL was deliberately configured
        pass


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
            "Stoerungsmeldung konnte nicht an den Webhook gesendet werden",
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
