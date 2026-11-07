"""Messpunkte für zähe Antwortzeiten: Anfragedauer und Verzögerung der Ereignisschleife.

Reine Beobachtung -- nichts hier greift in die Regelung ein. Ins Log gehen nur Zahlen
und der Routenpfad (Vorlage wie ``/kiosk/{plaintext}``, nie der konkrete Pfad mit Token,
nie Query oder Header; Grundsatz 2).
"""

import asyncio
import logging
import time
from typing import Any

from sqlalchemy import Engine
from starlette.requests import Request

log = logging.getLogger(__name__)

#: Ab dieser Dauer wird eine Anfrage als WARNING gemeldet.
SLOW_REQUEST_MS = 1000
#: Wie oft der Messer schläft, und ab welcher Überschreitung er meldet.
LOOP_INTERVAL_S = 0.25
LOOP_LAG_WARN_S = 0.5


def loggable_path(request: Request) -> str:
    """Pfad ohne Query und ohne Geheimnisse: die Routenvorlage, sonst nur das erste Segment."""
    route = request.scope.get("route")
    template = getattr(route, "path", None)
    if isinstance(template, str):
        return template
    first = request.scope.get("path", "").lstrip("/").split("/", 1)[0]
    return f"/{first}"


def pool_checked_out(engine: Engine | None) -> int | None:
    """Anzahl der gerade ausgeliehenen Verbindungen, falls der Pool sie zählt."""
    pool: Any = getattr(engine, "pool", None)
    counter = getattr(pool, "checkedout", None)
    if counter is None:
        return None
    try:
        return int(counter())
    except Exception:  # pragma: no cover - Messung darf nie stören
        return None


def log_request(
    *, method: str, path: str, status: int, started: float, engine: Engine | None
) -> None:
    duration_ms = round((time.perf_counter() - started) * 1000)
    extra = {
        "method": method,
        "path": path,
        "status": status,
        "dauer_ms": duration_ms,
        "pool_checked_out": pool_checked_out(engine),
    }
    if duration_ms > SLOW_REQUEST_MS:
        log.warning("Langsame Anfrage", extra=extra)
    else:
        log.info("Anfrage", extra=extra)


async def monitor_loop_lag(
    engine: Engine | None,
    *,
    interval: float = LOOP_INTERVAL_S,
    threshold: float = LOOP_LAG_WARN_S,
) -> None:
    """Schläft ``interval`` Sekunden und meldet, wenn es deutlich länger gedauert hat.

    Die Überschreitung ist die Zeit, in der die Schleife nichts anderes ausführen
    konnte -- also synchrone Arbeit in einer Coroutine. Endet nur durch Abbruch.
    """
    loop = asyncio.get_running_loop()
    while True:
        before = loop.time()
        await asyncio.sleep(interval)
        lag = loop.time() - before - interval
        if lag > threshold:
            log.warning(
                "Ereignisschleife blockiert",
                extra={
                    "verzoegerung_ms": round(lag * 1000),
                    "pool_checked_out": pool_checked_out(engine),
                },
            )
