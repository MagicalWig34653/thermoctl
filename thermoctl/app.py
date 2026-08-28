import logging
import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response

import thermoctl
from thermoctl.config import get_settings
from thermoctl.logging import configure_logging, request_id_var

log = logging.getLogger(__name__)


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings)
    log.info(
        "thermoctl startet",
        extra={
            "database": settings.sanitized_database_url(),
            "bind": f"{settings.bind_host}:{settings.bind_port}",
            "secure_cookies": settings.secure_cookies,
        },
    )
    app = FastAPI(title="thermoctl", version=thermoctl.__version__)

    @app.middleware("http")
    async def anfrage_id(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        kennung = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        marke = request_id_var.set(kennung)
        try:
            antwort = await call_next(request)
        finally:
            request_id_var.reset(marke)
        antwort.headers["X-Request-ID"] = kennung
        return antwort

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "version": thermoctl.__version__}

    return app
