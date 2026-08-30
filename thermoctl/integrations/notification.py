"""Fail-safe delivery of fault notices to the log and webhook."""

import asyncio
import json
import logging
from urllib.request import Request, urlopen

from thermoctl.config import Settings
from thermoctl.domain.fault_notice import FaultNotice

log = logging.getLogger(__name__)


def _send_webhook(settings: Settings, notice: FaultNotice) -> None:
    data = json.dumps(
        {
            "schluessel": notice.schluessel,
            "schwere": notice.schwere,
            "titel": notice.titel,
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
    with urlopen(request, timeout=10):  # noqa: S310 -- URL was deliberately configured
        pass


async def send(settings: Settings, notice: FaultNotice) -> None:
    """Always logs and attempts the optional webhook, without propagating errors."""
    log.warning(
        "%s: %s",
        notice.titel,
        notice.text,
        extra={"schluessel": notice.schluessel, "schwere": notice.schwere},
    )
    if settings.notify_webhook is None:
        return
    try:
        await asyncio.to_thread(_send_webhook, settings, notice)
    except Exception:
        log.exception(
            "Stoerungsmeldung konnte nicht an den Webhook gesendet werden",
            extra={"schluessel": notice.schluessel},
        )
