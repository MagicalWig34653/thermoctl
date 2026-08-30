import asyncio
import contextlib
import logging
import re
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from fastapi import FastAPI, Request, Response
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

import thermoctl
from thermoctl import audit
from thermoctl.api.routes import router as api_router
from thermoctl.config import Settings, get_settings
from thermoctl.db.base import utcnow
from thermoctl.db.engine import create_engine_from_settings, session_factory, session_scope
from thermoctl.db.models.credential import SetupToken
from thermoctl.db.models.lookup import SensorStatus
from thermoctl.db.models.operations import Setting
from thermoctl.db.models.state import ZoneState
from thermoctl.db.models.zone import Zone
from thermoctl.db.schema_state import SchemaPasstNicht, check_schema
from thermoctl.domain.authz import Forbidden
from thermoctl.domain.fault_notice import (
    FaultNotice,
    bridge_notice,
    sensornotice,
)
from thermoctl.domain.modes import DomainError, update_setpoints
from thermoctl.domain.remote_control import (
    RemoteControlError,
    boost,
    set_setpoint,
)
from thermoctl.domain.zone_settings import (
    ParameterOutOfRange,
    UnknownParameter,
    set_parameter,
)
from thermoctl.domain.zones import UnknownOperatingMode, set_operating_mode
from thermoctl.integrations.actuators import switching_allowed
from thermoctl.integrations.mqtt.client import MqttClient
from thermoctl.integrations.mqtt.commands import (
    Command,
    CommandError,
    commands_abonnements,
    ist_command,
    zerlegen,
)
from thermoctl.integrations.mqtt.zigbee2mqtt import (
    MessageKind,
    bridge_reachable,
    zuschneiden,
)
from thermoctl.integrations.notification import send
from thermoctl.logging import configure_logging, request_id_var
from thermoctl.services.ingest import advance_zone_state, process_message
from thermoctl.services.publishing import PublicationState, zone_state_senden
from thermoctl.services.publishing import cycle as publication_cycle
from thermoctl.services.retention import delete_old_measurements
from thermoctl.services.shadow_run import cycle
from thermoctl.setup import einrichtung_noetig, setup_token_erzeugen
from thermoctl.web import STATIC_DIR
from thermoctl.web.admin_views import router as admin_router
from thermoctl.web.audit_views import router as audit_router
from thermoctl.web.auth_views import router as auth_router
from thermoctl.web.control_views import router as control_router
from thermoctl.web.daily_views import router as alltag_router
from thermoctl.web.device_assignment_views import router as device_assignment_router
from thermoctl.web.device_views import router as devices_router
from thermoctl.web.mode_views import router as modes_router
from thermoctl.web.passkey_views import router as passkey_router
from thermoctl.web.schedule_views import router as schedule_router
from thermoctl.web.setup_views import router as setup_router
from thermoctl.web.start_views import router as start_router
from thermoctl.web.zone_views import router as zone_router

log = logging.getLogger(__name__)

# Vorgabewert, solange die Einrichtung noch nicht abgeschlossen ist und die
# `setting`-Zeile deshalb noch fehlt -- derselbe Wert wie die Spaltenvorgabe von
# `setting.shadow_interval_seconds`.
_SHADOW_INTERVAL_DEFAULT_S = 60


def _auditieren(session: Session, notice: FaultNotice) -> None:
    audit.record(
        session,
        source="system",
        action="notification.sent",
        object_type="fault",
        object_id=notice.schluessel,
        summary=notice.titel,
        detail=notice.text,
    )


# Starke Verweise auf die laufenden Versandaufgaben. Ohne sie kann der Aufraeumer eine
# Aufgabe einsammeln, bevor sie fertig ist — asyncio haelt selbst nur schwache Verweise.
_running_notices: set[asyncio.Task[None]] = set()


def _sensorzustaende(session: Session) -> dict[int, str]:
    return {
        zone_id: code
        for zone_id, code in session.execute(
            select(ZoneState.zone_id, SensorStatus.code).join(
                SensorStatus, SensorStatus.id == ZoneState.sensor_status_id
            )
        )
    }


def _sensor_notices(
    session: Session, vorher: dict[int, str]
) -> list[FaultNotice]:
    nachher = _sensorzustaende(session)
    notices: list[FaultNotice] = []
    for zone in session.scalars(select(Zone).order_by(Zone.id)):
        status = nachher.get(zone.id)
        if status is None:  # pragma: no cover
            # `zonenzustand_fortschreiben` legt fuer jede vorhandene Zone eine Zeile an.
            # Nur eine parallel geloeschte oder beschaedigte Zeile koennte hier fehlen.
            continue
        notice = sensornotice(f"sensor:{zone.id}", zone.name, vorher.get(zone.id), status)
        if notice is not None:
            _auditieren(session, notice)
            notices.append(notice)
    return notices


async def _shadow_intervall_s(session_factory: sessionmaker[Session]) -> int:
    with session_scope(session_factory) as session:
        settings = session.get(Setting, 1)
        return (
            settings.shadow_interval_seconds
            if settings is not None
            else _SHADOW_INTERVAL_DEFAULT_S
        )


async def _shadowschleife(app: FastAPI) -> None:
    """Wartet den konfigurierten Abstand ab, dann ein Zyklus -- endlos, bis abgebrochen.

    Eine Ausnahme beendet die Schleife nicht -- weder im Zyklus selbst noch beim Lesen
    des Abstands: protokollieren, weiter. Der naechste Versuch kommt nach dem naechsten
    Abstand; ein Dienst, der wegen einer einzelnen kaputten Zone oder einer noch nicht
    abgeschlossenen Einrichtung (die `setting`-Zeile fehlt dann noch) stehenbleibt, ist
    schlechter als einer, der es erneut versucht. Ein Abbruch (`asyncio.CancelledError`,
    beim Herunterfahren) ist davon ausdruecklich nicht betroffen: er erbt nicht von
    `Exception` und laeuft ungefangen durch, sonst liesse sich die Schleife nie beenden.
    """
    next_retention = utcnow() + timedelta(days=1)
    while True:
        try:
            interval = await _shadow_intervall_s(app.state.session_factory)
            await asyncio.sleep(interval)
            now = utcnow()
            notices: list[FaultNotice]
            with session_scope(app.state.session_factory) as session:
                vorher = _sensorzustaende(session)
                advance_zone_state(session, now)
                notices = _sensor_notices(session, vorher)
                cycle(session, now)
                # `getattr`: Die Schleife laeuft auch in Tests, die sich eine App
                # zusammenstecken, ohne den ganzen Lebenszyklus durchlaufen zu lassen.
                if getattr(app.state, "publisher", None) is not None:
                    await publication_cycle(
                        session,
                        app.state.publisher,
                        app.state.publication_state,
                        get_settings().mqtt_praefix,
                        now,
                    )
                if now >= next_retention:
                    delete_old_measurements(session, now)
                    next_retention = now + timedelta(days=1)
            # Der Versand laeuft nebenher, nicht im Takt. `senden` wartet bis zu zehn
            # Sekunden auf einen Webhook; bei mehreren gleichzeitig ausgefallenen Sensoren
            # summierte sich das und verschoebe den naechsten Regelzyklus. Sobald in
            # Teilprojekt 4 wirklich geschaltet wird, ist der Takt keine Nebensache mehr.
            # `senden` faengt seine Fehler selbst; die Aufgabe wird bewusst nicht
            # abgewartet, aber festgehalten, damit sie nicht vom Aufraeumer verschwindet.
            for notice in notices:
                aufgabe = asyncio.create_task(send(get_settings(), notice))
                _running_notices.add(aufgabe)
                aufgabe.add_done_callback(_running_notices.discard)
        except Exception:
            log.exception("Schattenzyklus fehlgeschlagen -- naechster Versuch folgt")


def _execute_command(
    session: Session, topic: str, payload: bytes, settings: Settings
) -> Zone | None:
    """Fuehrt einen von aussen gesendeten Befehl aus -- ueber die Domaene, wie jeder Adapter.

    Home Assistant bekommt je Zone einen Thermostat, einen Boost-Knopf und je Modus und
    Regelparameter einen Drehregler; wer dort dreht, landet hier. Die Grenzen und die
    Audit-Eintraege kommen aus der Domaene, damit ein Befehl von aussen genau so viel darf
    wie ein Klick in der Oberflaeche -- und keinen Deut mehr.

    Ein Befehl hat keinen angemeldeten Benutzer. Er laeuft unter der Quelle `system`,
    denn niemand hat sich dafuer angemeldet, und das soll im Protokoll auch so dastehen.

    Gibt die Zone zurueck, deren Zustand sich geaendert hat -- der Aufrufer meldet ihn
    sofort zurueck, statt bis zum naechsten Regelzyklus zu warten.
    """
    try:
        command = zerlegen(topic, payload, settings.mqtt_praefix)
    except CommandError as exc:
        log.warning("Unbrauchbarer Befehl verworfen: %s", exc, extra={"topic": topic})
        return None

    zone = session.get(Zone, command.zone_id)
    if zone is None:
        log.warning("Befehl fuer unbekannte Zone verworfen", extra={"topic": topic})
        return None

    try:
        _anwenden(session, zone, command)
    except (
        DomainError,
        UnknownOperatingMode,
        RemoteControlError,
        UnknownParameter,
        ParameterOutOfRange,
    ) as exc:
        # Eine abgewiesene Eingabe ist keine Stoerung des Dienstes. Sie darf aber auch
        # nicht still verschwinden: Wer in Home Assistant 99 Grad einstellt, soll den
        # Grund im Protokoll finden.
        log.warning("Befehl abgelehnt: %s", exc, extra={"topic": topic, "zone_id": zone.id})
        return None
    # Auch ein abgelehnter Befehl braucht streng genommen eine Antwort -- aber die
    # richtige Antwort darauf ist der unveraenderte Zustand, und den schickt der
    # Aufrufer ohnehin gleich mit.
    return zone


def _anwenden(session: Session, zone: Zone, command: Command) -> None:
    """Was ein zerlegter Befehl bewirkt. Getrennt, damit die Fehlerbehandlung eine bleibt."""
    now = utcnow()
    if command.kind == "operating_mode" and command.operating_mode is not None:
        set_operating_mode(session, zone, command.operating_mode, akteur_id=None, source="system")
    elif command.kind == "setpoint" and command.temperature is not None:
        set_setpoint(session, zone, command.temperature, now, source="system")
    elif command.kind == "boost":
        boost(session, zone, now, source="system")
    elif command.kind == "mode" and command.mode_id and command.temperature is not None:
        update_setpoints(
            session, zone, {command.mode_id: command.temperature},
            user_id=None, source="system",
        )
    elif command.kind == "parameter" and command.parameter and command.zahl is not None:
        set_parameter(
            session, zone, command.parameter, command.zahl, user_id=None, source="system"
        )


async def _process_mqtt_message(
    app: FastAPI, settings: Settings, topic: str, payload: bytes
) -> None:
    """Handler fuer den MQTT-Client: jede Nachricht in einer eigenen Sitzung.

    Eine eigene Sitzung je Nachricht statt einer geteilten -- eine kaputte Nutzlast (der
    MQTT-Client faengt Ausnahmen bereits ab) darf keine halbfertige Transaktion fuer die
    naechste Nachricht hinterlassen.
    """
    empfangen_am: datetime = utcnow()

    # Eigene Befehls-Topics zuerst: Sie liegen unter unserem eigenen Praefix und haben
    # mit dem Zigbee2MQTT-Zuschnitt nichts zu tun.
    if ist_command(topic, settings.mqtt_praefix):
        with session_scope(app.state.session_factory) as session:
            zone = _execute_command(session, topic, payload, settings)
            # Sofort antworten, noch in derselben Sitzung. Die Climate-Karte in Home
            # Assistant ist nicht optimistisch: Sie wartet auf den Zustand und zeigt bis
            # dahin den alten. Kam der erst im naechsten Regelzyklus, sprang der eben
            # gewaehlte Modus fuer eine Minute zurueck -- fuer den Benutzer sah es aus,
            # als lasse sich die Betriebsart nicht umstellen.
            publisher = getattr(app.state, "publisher", None)
            if zone is not None and publisher is not None:
                await zone_state_senden(
                    session, publisher, zone, settings.mqtt_praefix, empfangen_am
                )
        return

    zuschnitt = zuschneiden(topic, settings.mqtt_base_topic)
    notice: FaultNotice | None = None
    if zuschnitt.kind == MessageKind.BRIDGE_STATE:
        reachable = bridge_reachable(payload)
        if reachable is not None:
            notice = bridge_notice(app.state.bridge_reachable, reachable)
            app.state.bridge_reachable = reachable
    with session_scope(app.state.session_factory) as session:
        process_message(
            session,
            topic,
            payload,
            basis=settings.mqtt_base_topic,
            empfangen_am=empfangen_am,
        )
        if notice is not None:
            _auditieren(session, notice)
    if notice is not None:
        await send(settings, notice)


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
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Laeuft beim tatsaechlichen Start des Dienstes (nicht beim blossen Bau der
    FastAPI-Instanz durch `create_app()`) -- insbesondere nicht bei einem
    `TestClient`, der ohne `with`-Block genutzt wird, wie es die Testsuite tut.
    """
    # Vor jeder Abfrage: Ein fehlendes oder veraltetes Schema soll als ein Satz
    # herauskommen, der den naechsten Befehl nennt, nicht als Traceback aus der
    # Tiefe von SQLAlchemy.
    try:
        check_schema(app.state.engine)
    except SchemaPasstNicht as errors:
        log.error("%s", errors)
        raise

    with session_scope(app.state.session_factory) as session:
        if einrichtung_noetig(session) and not _unverbrauchtes_setup_token_vorhanden(session):
            plaintext = setup_token_erzeugen(session)
            # Einzige Stelle im ganzen Projekt, an der ein Geheimnis absichtlich im
            # Log erscheint (Ausnahme vermerkt in thermoctl/logging.py). Das Log ist
            # der einzige Kanal, ueber den der Betreiber an dieses Einmal-Token
            # kommt -- ohne es gewinnt im unguenstigen Fall der Erste im Netz, der
            # die Einrichtungsseite findet. Absichtlich in den Meldungstext
            # interpoliert statt als `extra=`-Feld: Nur der Meldungstext entgeht der
            # Maskierung in logging.py, ein Zusatzfeld mit "token" im Namen wuerde
            # dort geschwaerzt.
            log.info("Einrichtung erforderlich. Einmal-Token: %s", plaintext)

    # Beide Hintergrundaufgaben laufen ausschliesslich mit `mqtt_enabled` -- die
    # Testsuite baut die Anwendung staendig (jeder `TestClient`), und sie darf dabei
    # weder eine Endlosschleife noch eine Netzverbindung anstossen.
    settings = get_settings()
    app.state.bridge_reachable = None
    app.state.publisher = None
    app.state.publication_state = PublicationState()
    # Der **erste** Riegel, beim Bau des Clients gesetzt. Er kommt aus der Datenbank, wie
    # es der Kommentar in `MqttClient` seit Teilprojekt 2 vorsieht -- und er wird hier
    # einmal gelesen, nicht bei jedem Senden. Wer die Anlage im laufenden Betrieb scharf
    # schaltet, muss den Dienst deshalb einmal neu starten, bevor wirklich gesendet wird;
    # die Betriebsseite sagt das auch. Der zweite Riegel (`setting.control_armed`, bei
    # jedem Senden geprueft) wirkt dagegen sofort -- in die sichere Richtung.
    with session_scope(app.state.session_factory) as session:
        app.state.sending_allowed = switching_allowed(session)
    hintergrundaufgaben: list[asyncio.Task[None]] = []
    if settings.mqtt_enabled:
        client = MqttClient(
            settings,
            lambda topic, payload: _process_mqtt_message(app, settings, topic, payload),
            switching_allowed=app.state.sending_allowed,
            zusatz_abonnements=commands_abonnements(settings.mqtt_praefix),
        )
        app.state.publisher = client
        hintergrundaufgaben.append(asyncio.create_task(client.run()))
        hintergrundaufgaben.append(asyncio.create_task(_shadowschleife(app)))

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


def _warn_if_reachable_unprotected(settings: Settings) -> None:
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
    _warn_if_reachable_unprotected(settings)
    # `docs_url=None` schaltet die mitgelieferte Oberflaeche ab; wir liefern sie unten
    # selbst aus, weil FastAPIs Fassung ihre Dateien aus einem CDN zieht. Das widerspraeche
    # gleich zweimal dem, was fuer die uebrigen Fremdbibliotheken gilt (siehe
    # static/HERKUNFT.md): Im Heimnetz ohne Internetzugang bliebe die Seite leer, und jeder
    # Aufruf verriete einem Dritten, wann jemand die Heizungssteuerung oeffnet.
    #
    # `redoc_url=None` ohne Ersatz: ReDoc zieht ebenfalls aus einem CDN, und eine zweite
    # Lesefassung derselben Beschreibung ist den zusaetzlichen Mitgeliefert-Ballast nicht
    # wert. `/docs` deckt beides ab.
    app = FastAPI(
        title="thermoctl",
        version=thermoctl.__version__,
        lifespan=_lifespan,
        docs_url=None,
        redoc_url=None,
    )
    # Die Engine wird mit abgelegt, damit Aufrufer sie schliessen koennen. Ohne das
    # bleibt bei jeder erzeugten Anwendung eine offene Datenbankverbindung zurueck --
    # im Betrieb bis zum Prozessende, in Tests bei jedem Aufbau erneut.
    app.state.engine = create_engine_from_settings(settings)
    app.state.session_factory = session_factory(app.state.engine)
    app.include_router(start_router)
    app.include_router(control_router)
    app.include_router(auth_router)
    app.include_router(setup_router)
    app.include_router(admin_router)
    app.include_router(audit_router)
    app.include_router(devices_router)
    app.include_router(device_assignment_router)
    app.include_router(zone_router)
    app.include_router(modes_router)
    app.include_router(passkey_router)
    app.include_router(schedule_router)
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
        identifier = (
            mitgegeben
            if mitgegeben is not None and _REQUEST_ID_PATTERN.fullmatch(mitgegeben)
            else uuid.uuid4().hex
        )
        marker = request_id_var.set(identifier)
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(marker)
        response.headers["X-Request-ID"] = identifier
        return response

    @app.get("/docs", include_in_schema=False)
    async def swagger_ui() -> HTMLResponse:
        """Die OpenAPI-Oberflaeche, vollstaendig aus dem eigenen Verzeichnis.

        Bewusst ohne Anmeldung: Die Beschreibung verraet, welche Wege es gibt, aber keinen
        einzigen Wert und kein Geheimnis — und dasselbe steht ab der Veroeffentlichung
        ohnehin in `docs/api.md` im oeffentlichen Repository. Ausprobieren laesst sich von
        hier aus nichts ohne Token; jeder Aufruf durchlaeuft dieselbe Pruefung wie sonst.
        """
        return get_swagger_ui_html(
            openapi_url="/openapi.json",
            title="thermoctl — REST-Schnittstelle",
            swagger_js_url="/static/vendor/swagger-ui/swagger-ui-bundle.js",
            swagger_css_url="/static/vendor/swagger-ui/swagger-ui.css",
            swagger_favicon_url="/static/favicon.svg",
        )

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "version": thermoctl.__version__}

    return app
