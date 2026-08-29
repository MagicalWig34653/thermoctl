import logging
import re
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.orm import Session

import thermoctl
from thermoctl.api.routes import router as api_router
from thermoctl.config import get_settings
from thermoctl.db.engine import create_engine_from_settings, session_factory, session_scope
from thermoctl.db.models.credential import SetupToken
from thermoctl.domain.authz import Forbidden
from thermoctl.logging import configure_logging, request_id_var
from thermoctl.setup import einrichtung_noetig, setup_token_erzeugen
from thermoctl.web import STATIC_DIR
from thermoctl.web.admin_views import router as admin_router
from thermoctl.web.auth_views import router as auth_router
from thermoctl.web.geraete_views import router as geraete_router
from thermoctl.web.setup_views import router as setup_router
from thermoctl.web.start_views import router as start_router

log = logging.getLogger(__name__)


def _unverbrauchtes_setup_token_vorhanden(session: Session) -> bool:
    """Verhindert, dass jeder Neustart ein weiteres, ungenutztes Token erzeugt und
    loggt, solange bereits eines aussteht."""
    return (
        session.scalar(select(SetupToken.id).where(SetupToken.consumed_at.is_(None)).limit(1))
        is not None
    )


# Hoechstens 64 Zeichen, ausschliesslich Buchstaben, Ziffern, Bindestrich und
# Unterstrich. Alles andere -- insbesondere Zeilenumbrueche, die im Textformat
# des Logs zusaetzliche Zeilen vortaeuschen koennten, oder beliebig lange Werte,
# die jede Logzeile aufblaehen -- gilt als unplausibel.
_ANFRAGE_ID_MUSTER = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Laeuft beim tatsaechlichen Start des Dienstes (nicht beim blossen Bau der
    FastAPI-Instanz durch `create_app()`) -- insbesondere nicht bei einem
    `TestClient`, der ohne `with`-Block genutzt wird, wie es die Testsuite tut.
    """
    with session_scope(app.state.session_factory) as session:
        if einrichtung_noetig(session) and not _unverbrauchtes_setup_token_vorhanden(session):
            klartext = setup_token_erzeugen(session)
            # Einzige Stelle im ganzen Projekt, an der ein Geheimnis absichtlich im
            # Log erscheint (Ausnahme vermerkt in thermoctl/logging.py). Das Log ist
            # der einzige Kanal, ueber den der Betreiber an dieses Einmal-Token
            # kommt -- ohne es gewinnt im unguenstigen Fall der Erste im Netz, der
            # die Einrichtungsseite findet. Absichtlich in den Meldungstext
            # interpoliert statt als `extra=`-Feld: Nur der Meldungstext entgeht der
            # Maskierung in logging.py, ein Zusatzfeld mit "token" im Namen wuerde
            # dort geschwaerzt.
            log.info("Einrichtung erforderlich. Einmal-Token: %s", klartext)
    yield


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
    app = FastAPI(title="thermoctl", version=thermoctl.__version__, lifespan=_lifespan)
    # Die Engine wird mit abgelegt, damit Aufrufer sie schliessen koennen. Ohne das
    # bleibt bei jeder erzeugten Anwendung eine offene Datenbankverbindung zurueck --
    # im Betrieb bis zum Prozessende, in Tests bei jedem Aufbau erneut.
    app.state.engine = create_engine_from_settings(settings)
    app.state.session_factory = session_factory(app.state.engine)
    app.include_router(start_router)
    app.include_router(auth_router)
    app.include_router(setup_router)
    app.include_router(admin_router)
    app.include_router(geraete_router)
    app.include_router(api_router)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.exception_handler(Forbidden)
    async def forbidden_handler(request: Request, exc: Forbidden) -> Response:
        # Einheitliche Uebersetzung einer Rechtsverweigerung in 403 -- eine Route,
        # die das nicht selbst tut (und das kuenftig vergisst), soll trotzdem nicht
        # mit 500 antworten. Auflage aus dem Abschlussreview von Teilprojekt 1.
        return JSONResponse(status_code=403, content={"detail": str(exc)})

    @app.middleware("http")
    async def anfrage_id(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        mitgegeben = request.headers.get("X-Request-ID")
        # Eine unplausible mitgegebene Kennung ist kein Grund, eine sonst
        # gueltige Anfrage abzuweisen -- sie wird schlicht durch eine frisch
        # erzeugte ersetzt, als waere keine mitgeliefert worden.
        kennung = (
            mitgegeben
            if mitgegeben is not None and _ANFRAGE_ID_MUSTER.fullmatch(mitgegeben)
            else uuid.uuid4().hex
        )
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
