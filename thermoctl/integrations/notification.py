"""Fail-safe delivery of fault notices to the log and webhook."""

import asyncio
import json
import logging
from urllib.error import HTTPError
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
