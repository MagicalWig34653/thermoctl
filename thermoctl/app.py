import asyncio
import contextlib
import logging
import re
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

import thermoctl
from thermoctl.api.routes import router as api_router
from thermoctl.config import Settings, get_settings
from thermoctl.db.base import utcnow
from thermoctl.db.engine import create_engine_from_settings, session_factory, session_scope
from thermoctl.db.models.credential import SetupToken
from thermoctl.db.models.operations import Setting
from thermoctl.domain.authz import Forbidden
from thermoctl.integrations.mqtt.client import MqttClient
from thermoctl.logging import configure_logging, request_id_var
from thermoctl.services.aufbewahrung import alte_messwerte_loeschen
from thermoctl.services.ingest import nachricht_verarbeiten, zonenzustand_fortschreiben
from thermoctl.services.schattenlauf import zyklus
from thermoctl.setup import einrichtung_noetig, setup_token_erzeugen
from thermoctl.web import STATIC_DIR
from thermoctl.web.admin_views import router as admin_router
from thermoctl.web.alltag_views import router as alltag_router
from thermoctl.web.audit_views import router as audit_router
from thermoctl.web.auth_views import router as auth_router
from thermoctl.web.geraete_views import router as geraete_router
from thermoctl.web.geraetezuordnung_views import router as geraetezuordnung_router
from thermoctl.web.modi_views import router as modi_router
from thermoctl.web.setup_views import router as setup_router
from thermoctl.web.start_views import router as start_router
from thermoctl.web.zeitplan_views import router as zeitplan_router
from thermoctl.web.zonen_views import router as zonen_router

log = logging.getLogger(__name__)

# Vorgabewert, solange die Einrichtung noch nicht abgeschlossen ist und die
# `setting`-Zeile deshalb noch fehlt -- derselbe Wert wie die Spaltenvorgabe von
# `setting.shadow_interval_seconds`.
_SCHATTENINTERVALL_STANDARD_S = 60


async def _shadow_intervall_s(session_factory: sessionmaker[Session]) -> int:
    with session_scope(session_factory) as session:
        einstellungen = session.get(Setting, 1)
        return (
            einstellungen.shadow_interval_seconds
            if einstellungen is not None
            else _SCHATTENINTERVALL_STANDARD_S
        )


async def _schattenschleife(app: FastAPI) -> None:
    """Wartet den konfigurierten Abstand ab, dann ein Zyklus -- endlos, bis abgebrochen.

    Eine Ausnahme beendet die Schleife nicht -- weder im Zyklus selbst noch beim Lesen
    des Abstands: protokollieren, weiter. Der naechste Versuch kommt nach dem naechsten
    Abstand; ein Dienst, der wegen einer einzelnen kaputten Zone oder einer noch nicht
    abgeschlossenen Einrichtung (die `setting`-Zeile fehlt dann noch) stehenbleibt, ist
    schlechter als einer, der es erneut versucht. Ein Abbruch (`asyncio.CancelledError`,
    beim Herunterfahren) ist davon ausdruecklich nicht betroffen: er erbt nicht von
    `Exception` und laeuft ungefangen durch, sonst liesse sich die Schleife nie beenden.
    """
    naechste_aufbewahrung = utcnow() + timedelta(days=1)
    while True:
        try:
            abstand = await _shadow_intervall_s(app.state.session_factory)
            await asyncio.sleep(abstand)
            jetzt = utcnow()
            with session_scope(app.state.session_factory) as session:
                zonenzustand_fortschreiben(session, jetzt)
                zyklus(session, jetzt)
                if jetzt >= naechste_aufbewahrung:
                    alte_messwerte_loeschen(session, jetzt)
                    naechste_aufbewahrung = jetzt + timedelta(days=1)
        except Exception:
            log.exception("Schattenzyklus fehlgeschlagen -- naechster Versuch folgt")


async def _mqtt_nachricht_verarbeiten(
    app: FastAPI, settings: Settings, topic: str, nutzlast: bytes
) -> None:
    """Handler fuer den MQTT-Client: jede Nachricht in einer eigenen Sitzung.

    Eine eigene Sitzung je Nachricht statt einer geteilten -- eine kaputte Nutzlast (der
    MQTT-Client faengt Ausnahmen bereits ab) darf keine halbfertige Transaktion fuer die
    naechste Nachricht hinterlassen.
    """
    empfangen_am: datetime = utcnow()
    with session_scope(app.state.session_factory) as session:
        nachricht_verarbeiten(
            session,
            topic,
            nutzlast,
            basis=settings.mqtt_base_topic,
            empfangen_am=empfangen_am,
        )


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

    # Beide Hintergrundaufgaben laufen ausschliesslich mit `mqtt_enabled` -- die
    # Testsuite baut die Anwendung staendig (jeder `TestClient`), und sie darf dabei
    # weder eine Endlosschleife noch eine Netzverbindung anstossen.
    settings = get_settings()
    hintergrundaufgaben: list[asyncio.Task[None]] = []
    if settings.mqtt_enabled:
        client = MqttClient(
            settings,
            lambda topic, nutzlast: _mqtt_nachricht_verarbeiten(app, settings, topic, nutzlast),
        )
        hintergrundaufgaben.append(asyncio.create_task(client.laufen()))
        hintergrundaufgaben.append(asyncio.create_task(_schattenschleife(app)))

    try:
        yield
    finally:
        # Abbrechen und abwarten, nicht nur abbrechen: Sonst kann eine Aufgabe, die
        # gerade in einer laufenden Datenbankoperation steckt, den Prozess am Beenden
        # hindern -- jeder Neustart des Containers waere eine Geduldsprobe.
        for aufgabe in hintergrundaufgaben:
            aufgabe.cancel()
        for aufgabe in hintergrundaufgaben:
            with contextlib.suppress(asyncio.CancelledError):
                await aufgabe


# Adressen, unter denen der Dienst nur vom selben Rechner erreichbar ist. Alles andere
# bedeutet: irgendjemand im Netz kann ihn aufrufen.
_NUR_OERTLICH = frozenset({"127.0.0.1", "::1", "localhost"})


def _warnen_wenn_ungeschuetzt_erreichbar(settings: Settings) -> None:
    """Warnt, wenn der Dienst im Netz haengt und Sitzungscookies unverschluesselt gehen.

    `secure_cookies` steht standardmaessig auf false, weil die Erstinbetriebnahme sonst
    ueber `http://` scheitert — und wer sie dann nicht umstellt, schickt seine Anmeldung
    im Klartext durchs WLAN. Der Dienst kann das nicht erzwingen (hinter einem
    Reverse-Proxy sieht er nur HTTP), aber er kann es sagen. Eine Warnung, die einmal beim
    Start im Log steht, ist der einzige Ort, an dem es auffaellt, bevor etwas passiert.
    """
    if settings.secure_cookies or settings.bind_host in _NUR_OERTLICH:
        return
    log.warning(
        "Der Dienst ist im Netz erreichbar, aber THERMOCTL_SECURE_COOKIES ist aus. "
        "Sitzungscookies gehen dann auch unverschluesselt hinaus. Hinter TLS gehoert "
        "THERMOCTL_SECURE_COOKIES=true; ohne TLS gehoert die Bindung auf 127.0.0.1.",
        extra={"bind": f"{settings.bind_host}:{settings.bind_port}"},
    )


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
    _warnen_wenn_ungeschuetzt_erreichbar(settings)
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
    app.include_router(audit_router)
    app.include_router(geraete_router)
    app.include_router(geraetezuordnung_router)
    app.include_router(zonen_router)
    app.include_router(modi_router)
    app.include_router(zeitplan_router)
    app.include_router(alltag_router)
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
