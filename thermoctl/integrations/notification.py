"""Ausfallsichere Ausgabe von Stoerungsmeldungen in Log und Webhook."""

import asyncio
import json
import logging
from urllib.request import Request, urlopen

from thermoctl.config import Settings
from thermoctl.domain.fault_notice import FaultNotice

log = logging.getLogger(__name__)


def _send_webhook(settings: Settings, notice: FaultNotice) -> None:
    daten = json.dumps(
        {
            "schluessel": notice.schluessel,
            "schwere": notice.schwere,
            "titel": notice.titel,
            "text": notice.text,
        }
    ).encode("utf-8")
    kopfzeilen = {"Content-Type": "application/json"}
    if settings.notify_webhook_token is not None:
        token = settings.notify_webhook_token.get_secret_value()
        kopfzeilen["Authorization"] = f"Bearer {token}"
    anfrage = Request(  # noqa: S310 -- Adresse ist Betreiberkonfiguration
        settings.notify_webhook or "", data=daten, headers=kopfzeilen, method="POST"
    )
    with urlopen(anfrage, timeout=10):  # noqa: S310 -- URL wurde bewusst konfiguriert
        pass


async def send(settings: Settings, notice: FaultNotice) -> None:
    """Loggt immer und versucht den optionalen Webhook, ohne Fehler weiterzugeben."""
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
