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
from thermoctl.db.schema_state import SchemaMismatch, check_schema
from thermoctl.domain.authz import Forbidden
from thermoctl.domain.fault_notice import (
    FaultNotice,
    bridge_notice,
    sensor_notice,
)
from thermoctl.domain.modes import DomainError, update_setpoints
from thermoctl.domain.remote_control import (
    RemoteControlError,
    boost,
    set_setpoint,
)
from thermoctl.domain.solar_setback import HourlyForecast
from thermoctl.domain.zone_settings import (
    ParameterOutOfRange,
    UnknownParameter,
    set_parameter,
)
from thermoctl.domain.zones import UnknownOperatingMode, set_operating_mode
from thermoctl.integrations.actuators import switching_allowed
from thermoctl.integrations.forecast import ForecastCache
from thermoctl.integrations.meross import UrllibJsonTransport, credentials_configured
from thermoctl.integrations.mqtt.client import MqttClient
from thermoctl.integrations.mqtt.commands import (
    Command,
    CommandError,
    command_subscriptions,
    ist_command,
    split_topic,
)
from thermoctl.integrations.mqtt.zigbee2mqtt import (
    MessageKind,
    bridge_reachable,
    trim,
)
from thermoctl.integrations.notification import send
from thermoctl.logging import configure_logging, request_id_var
from thermoctl.services.ingest import advance_zone_state, process_message
from thermoctl.services.meross_discovery import refresh as meross_refresh
from thermoctl.services.publishing import PublicationState, _send_zone_state
from thermoctl.services.publishing import cycle as publication_cycle
from thermoctl.services.retention import delete_old_measurements
from thermoctl.services.shadow_run import cycle
from thermoctl.setup import create_setup_token, setup_needed
from thermoctl.web import STATIC_DIR
from thermoctl.web.admin_views import router as admin_router
from thermoctl.web.audit_views import router as audit_router
from thermoctl.web.auth_views import router as auth_router
from thermoctl.web.control_views import router as control_router
from thermoctl.web.controller_views import router as controller_router
from thermoctl.web.daily_views import router as alltag_router
from thermoctl.web.device_assignment_views import router as device_assignment_router
from thermoctl.web.device_views import router as devices_router
from thermoctl.web.kiosk_admin_views import router as kiosk_admin_router
from thermoctl.web.kiosk_views import router as kiosk_router
from thermoctl.web.mode_views import router as modes_router
from thermoctl.web.passkey_views import router as passkey_router
from thermoctl.web.schedule_views import router as schedule_router
from thermoctl.web.setup_views import router as setup_router
from thermoctl.web.start_views import router as start_router
from thermoctl.web.zone_views import router as zone_router

log = logging.getLogger(__name__)

# Default value while setup is not yet complete and the `setting` row is
# therefore still missing -- the same value as the column default of
# `setting.shadow_interval_seconds`.
_SHADOW_INTERVAL_DEFAULT_S = 60


def _audit(session: Session, notice: FaultNotice) -> None:
    audit.record(
        session,
        source="system",
        action="notification.sent",
        object_type="fault",
        object_id=notice.key,
        summary=notice.title,
        detail=notice.text,
    )


# Strong references to the running dispatch tasks. Without them the garbage collector
# could collect a task before it finishes -- asyncio itself only holds weak references.
_running_notices: set[asyncio.Task[None]] = set()

# Same reason, for the detached Meross reconciliation started from the shadow loop
# below (`_start_meross_refresh`).
_running_meross_refreshes: set[asyncio.Task[None]] = set()


def _sensor_states(session: Session) -> dict[int, str]:
    return {
        zone_id: code
        for zone_id, code in session.execute(
            select(ZoneState.zone_id, SensorStatus.code).join(
                SensorStatus, SensorStatus.id == ZoneState.sensor_status_id
            )
        )
    }


def _sensor_notices(
    session: Session, before: dict[int, str]
) -> list[FaultNotice]:
    after = _sensor_states(session)
    notices: list[FaultNotice] = []
    for zone in session.scalars(select(Zone).order_by(Zone.id)):
        status = after.get(zone.id)
        if status is None:  # pragma: no cover
            # `advance_zone_state` creates a row for every existing zone. Only a row
            # deleted or corrupted concurrently could be missing here.
            continue
        notice = sensor_notice(f"sensor:{zone.id}", zone.name, before.get(zone.id), status)
        if notice is not None:
            _audit(session, notice)
            notices.append(notice)
    return notices


async def _solar_forecast(
    app: FastAPI, session: Session, now: datetime
) -> list[HourlyForecast] | None:
    """The hourly forecast for the configured location, or `None`.

    `None` covers three different reasons, and they are meant to collapse into the
    same outcome (CLAUDE.md: a failed source must not disturb control -- the safe
    direction is simply no setback): the feature is switched off, no location is
    configured (`solar_forecast_latitude`/`_longitude` unset -- there is no sensible
    default location, principle 1), or `ForecastCache.get()` could not reach the
    source this cycle.
    """
    settings_row = session.get(Setting, 1)
    if settings_row is None or not settings_row.solar_forecast_enabled:
        return None
    latitude = settings_row.solar_forecast_latitude
    longitude = settings_row.solar_forecast_longitude
    if latitude is None or longitude is None:
        return None
    cache: ForecastCache | None = getattr(app.state, "forecast_cache", None)
    if cache is None:
        # Only reachable when the lifespan never ran (the shadow loop invoked
        # directly against a bare `SimpleNamespace`, as some tests do) -- in normal
        # operation `_lifespan` always sets this up, whether or not MQTT is enabled.
        return None
    return await cache.get(latitude, longitude, now)


async def _shadow_interval_s(session_factory: sessionmaker[Session]) -> int:
    with session_scope(session_factory) as session:
        settings = session.get(Setting, 1)
        return (
            settings.shadow_interval_seconds
            if settings is not None
            else _SHADOW_INTERVAL_DEFAULT_S
        )


# How often the Meross device list is reconciled. A socket is rarely added, and every
# pass is a sign-in to somebody else's cloud -- hourly is enough, and it happens once at
# startup anyway.
MEROSS_REFRESH_S = 3600.0


async def _refresh_meross(app: FastAPI, session: Session, now: datetime) -> None:
    """Reconciles the Meross devices when credentials are stored.

    Errors stay here: the device list of somebody else's cloud must not halt the shadow
    cycle -- an installation that stops regulating because of a sign-in error would be
    worse than one that does not know a socket yet.
    """
    transport = getattr(app.state, "meross_transport", None)
    if transport is None:  # pragma: no cover - always set in the lifespan
        return
    await meross_refresh(session, get_settings(), transport, now)


def _start_meross_refresh(app: FastAPI, now: datetime) -> None:
    """Starts a Meross reconciliation detached from the shadow cycle.

    Sign-in and the device list are two HTTP calls to somebody else's cloud, each with
    a 20 second timeout (`integrations/meross.py`, `urlopen(..., timeout=20)`). Calling
    `_refresh_meross` from inside the cycle's own `session_scope` and awaiting it there
    -- as an earlier version did -- kept that transaction open for as long as the cloud
    took to answer or time out, and delayed publication, retention, the commit itself,
    and the next cycle behind it. The reconciliation needs none of that: it opens its
    own session, exactly like the notice dispatch below does, and the cycle does not
    wait for it.
    """
    task = asyncio.create_task(_run_detached_meross_refresh(app, now))
    _running_meross_refreshes.add(task)
    task.add_done_callback(_running_meross_refreshes.discard)


async def _run_detached_meross_refresh(app: FastAPI, now: datetime) -> None:
    with session_scope(app.state.session_factory) as session:
        await _refresh_meross(app, session, now)


def _shadow_loop_needed(settings: Settings) -> bool:
    """Whether the shadow loop has anything to do at all.

    Not simply `settings.mqtt_enabled`: the loop also carries the Meross
    reconciliation (finding 3 of the cross review), and the Meross cloud is not the
    local Zigbee2MQTT broker -- an installation with Meross credentials set and
    `THERMOCTL_MQTT_ENABLED=false` still needs its startup and hourly reconciliation.
    Everything else the loop does (sensor state, solar forecast, the dry-run decisions,
    retention) is already independent of MQTT; publication is the one part that needs
    a broker, and it already guards itself with `getattr(app.state, "publisher",
    None)` -- `publisher` stays `None` when `mqtt_enabled` is off, so that branch
    simply never runs.
    """
    return settings.mqtt_enabled or credentials_configured(settings)


async def _shadow_loop(app: FastAPI) -> None:
    """Waits out the configured interval, then one cycle -- forever, until cancelled.

    An exception does not end the loop -- neither in the cycle itself nor while reading
    the interval: log it, keep going. The next attempt follows the next interval; a
    service that stalls because of a single broken zone or a setup that is not yet
    complete (the `setting` row is then still missing) is worse than one that tries
    again. Cancellation (`asyncio.CancelledError`, on shutdown) is explicitly exempt
    from this: it does not inherit from `Exception` and passes through uncaught,
    otherwise the loop could never be stopped.
    """
    started = utcnow()
    next_retention = started + timedelta(days=1)
    # Right on the first pass: whoever has just entered credentials should see their
    # sockets without waiting an hour.
    next_meross = started
    while True:
        try:
            interval = await _shadow_interval_s(app.state.session_factory)
            await asyncio.sleep(interval)
            now = utcnow()
            notices: list[FaultNotice]
            with session_scope(app.state.session_factory) as session:
                before = _sensor_states(session)
                advance_zone_state(session, now)
                notices = _sensor_notices(session, before)
                forecast = await _solar_forecast(app, session, now)
                cycle(session, now, forecast)
                # `getattr`: the loop also runs in tests that assemble an app without
                # running through the full lifespan.
                if getattr(app.state, "publisher", None) is not None:
                    await publication_cycle(
                        session,
                        app.state.publisher,
                        app.state.publication_state,
                        get_settings().mqtt_prefix,
                        now,
                    )
                if now >= next_retention:
                    delete_old_measurements(session, now)
                    next_retention = now + timedelta(days=1)
            if now >= next_meross:
                next_meross = now + timedelta(seconds=MEROSS_REFRESH_S)
                _start_meross_refresh(app, now)
            # Dispatch runs alongside, not in step with the cycle. `send` waits up to
            # ten seconds for a webhook; with several sensors failing at once that would
            # add up and delay the next control cycle. Once subproject 4 actually starts
            # switching, the cycle timing stops being a side concern.
            # `send` catches its own errors; the task is deliberately not awaited, but
            # kept referenced so it does not disappear via garbage collection.
            for notice in notices:
                task = asyncio.create_task(send(get_settings(), notice))
                _running_notices.add(task)
                task.add_done_callback(_running_notices.discard)
        except Exception:
            log.exception("Schattenzyklus fehlgeschlagen -- naechster Versuch folgt")


def _execute_command(
    session: Session, topic: str, payload: bytes, settings: Settings
) -> Zone | None:
    """Executes a command sent from outside -- through the domain, like every adapter.

    Home Assistant gets a thermostat per zone, a boost button, and a dial per mode and
    control parameter; whoever turns one there ends up here. The limits and the audit
    entries come from the domain, so a command from outside is allowed exactly as much
    as a click in the interface -- and not a bit more.

    A command has no logged-in user. It runs under the source `system`, because nobody
    logged in for it, and that is meant to show up in the log that way too.

    Returns the zone whose state changed -- the caller reports it back immediately,
    instead of waiting for the next control cycle.
    """
    try:
        command = split_topic(topic, payload, settings.mqtt_prefix)
    except CommandError as exc:
        log.warning("Unbrauchbarer Befehl verworfen: %s", exc, extra={"topic": topic})
        return None

    zone = session.get(Zone, command.zone_id)
    if zone is None:
        log.warning("Befehl fuer unbekannte Zone verworfen", extra={"topic": topic})
        return None

    try:
        _apply(session, zone, command)
    except (
        DomainError,
        UnknownOperatingMode,
        RemoteControlError,
        UnknownParameter,
        ParameterOutOfRange,
    ) as exc:
        # A rejected input is not a fault of the service. But it must not disappear
        # silently either: whoever sets 99 degrees in Home Assistant should find the
        # reason in the log.
        log.warning("Befehl abgelehnt: %s", exc, extra={"topic": topic, "zone_id": zone.id})
        return None
    # Even a rejected command strictly needs a response -- but the correct response to
    # it is the unchanged state, and the caller sends that along right after anyway.
    return zone


def _apply(session: Session, zone: Zone, command: Command) -> None:
    """What a parsed command does. Kept separate so error handling stays in one place."""
    now = utcnow()
    if command.kind == "operating_mode" and command.operating_mode is not None:
        set_operating_mode(session, zone, command.operating_mode, actor_id=None, source="system")
    elif command.kind == "setpoint" and command.temperature is not None:
        set_setpoint(session, zone, command.temperature, now, source="system")
    elif command.kind == "boost":
        boost(session, zone, now, source="system")
    elif command.kind == "mode" and command.mode_id and command.temperature is not None:
        update_setpoints(
            session, zone, {command.mode_id: command.temperature},
            user_id=None, source="system",
        )
    elif command.kind == "parameter" and command.parameter and command.number is not None:
        set_parameter(
            session, zone, command.parameter, command.number, user_id=None, source="system"
        )


async def _process_mqtt_message(
    app: FastAPI, settings: Settings, topic: str, payload: bytes
) -> None:
    """Handler for the MQTT client: each message in its own session.

    A dedicated session per message instead of a shared one -- a broken payload (the
    MQTT client already catches exceptions) must not leave a half-finished transaction
    for the next message.
    """
    received_at: datetime = utcnow()

    # Our own command topics first: they live under our own prefix and have nothing to
    # do with the Zigbee2MQTT parsing.
    if ist_command(topic, settings.mqtt_prefix):
        with session_scope(app.state.session_factory) as session:
            zone = _execute_command(session, topic, payload, settings)
            # Respond immediately, still in the same session. The climate card in Home
            # Assistant is not optimistic: it waits for the state and shows the old one
            # until then. If it only arrived on the next control cycle, the mode just
            # chosen would jump back for a minute -- to the user it looked as if the
            # operating mode could not be changed.
            publisher = getattr(app.state, "publisher", None)
            if zone is not None and publisher is not None:
                await _send_zone_state(
                    session, publisher, zone, settings.mqtt_prefix, received_at
                )
        return

    trimmed = trim(topic, settings.mqtt_base_topic)
    notice: FaultNotice | None = None
    if trimmed.kind == MessageKind.BRIDGE_STATE:
        reachable = bridge_reachable(payload)
        if reachable is not None:
            notice = bridge_notice(app.state.bridge_reachable, reachable)
            app.state.bridge_reachable = reachable
    with session_scope(app.state.session_factory) as session:
        process_message(
            session,
            topic,
            payload,
            base=settings.mqtt_base_topic,
            received_at=received_at,
        )
        if notice is not None:
            _audit(session, notice)
    if notice is not None:
        await send(settings, notice)


def _unused_setup_token_exists(session: Session) -> bool:
    """Prevents every restart from generating and logging another, unused token while
    one is already pending."""
    return (
        session.scalar(select(SetupToken.id).where(SetupToken.consumed_at.is_(None)).limit(1))
        is not None
    )


# At most 64 characters, letters, digits, hyphen, and underscore only. Anything
# else -- in particular line breaks, which could fake additional lines in the
# text format of the log, or arbitrarily long values that bloat every log line
# -- counts as implausible.
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Runs on actual startup of the service (not merely on building the FastAPI
    instance via `create_app()`) -- in particular not for a `TestClient` used
    without a `with` block, as the test suite does.
    """
    # Before any query: a missing or outdated schema should come out as one
    # sentence naming the next command, not as a traceback from deep inside
    # SQLAlchemy.
    try:
        check_schema(app.state.engine)
    except SchemaMismatch as errors:
        log.error("%s", errors)
        raise

    with session_scope(app.state.session_factory) as session:
        if setup_needed(session) and not _unused_setup_token_exists(session):
            plaintext = create_setup_token(session)
            # The only place in the whole project where a secret intentionally
            # appears in the log (exception noted in thermoctl/logging.py). The log
            # is the only channel through which the operator gets this one-time
            # token -- without it, in the unfavorable case, whoever finds the setup
            # page first on the network wins. Deliberately interpolated into the
            # message text instead of an `extra=` field: only the message text
            # escapes the redaction in logging.py, an extra field with "token" in
            # its name would be redacted there.
            log.info("Einrichtung erforderlich. Einmal-Token: %s", plaintext)

    # The MQTT client runs only with `mqtt_enabled` -- the test suite builds the
    # application constantly (every `TestClient`), and doing so must not trigger a
    # network connection. The shadow loop is broader than MQTT, though (see below).
    settings = get_settings()
    app.state.bridge_reachable = None
    app.state.publisher = None
    app.state.publication_state = PublicationState()
    # No network call happens here -- `ForecastCache` fetches lazily, on the first
    # cycle that finds the feature configured. Set up unconditionally (unlike the
    # MQTT client below) so a later `settings.solar_forecast_enabled` flip takes
    # effect on the very next shadow cycle, without a restart.
    app.state.forecast_cache = ForecastCache()
    app.state.meross_transport = UrllibJsonTransport()
    # The **first** bolt, set when the client is built. It comes from the database, as
    # the comment in `MqttClient` has specified since subproject 2 -- and it is read
    # once here, not on every send. Whoever arms the plant while it is running
    # therefore has to restart the service once before anything is actually sent; the
    # operations page says so too. The second bolt (`setting.control_armed`, checked on
    # every send) takes effect immediately, on the other hand -- in the safe direction.
    with session_scope(app.state.session_factory) as session:
        app.state.sending_allowed = switching_allowed(session)
    background_tasks: list[asyncio.Task[None]] = []
    if settings.mqtt_enabled:
        client = MqttClient(
            settings,
            lambda topic, payload: _process_mqtt_message(app, settings, topic, payload),
            switching_allowed=app.state.sending_allowed,
            extra_subscriptions=command_subscriptions(settings.mqtt_prefix),
        )
        app.state.publisher = client
        background_tasks.append(asyncio.create_task(client.run()))
    if _shadow_loop_needed(settings):
        background_tasks.append(asyncio.create_task(_shadow_loop(app)))

    try:
        yield
    finally:
        # Cancel and wait, not just cancel: otherwise a task that is currently stuck
        # in an ongoing database operation could prevent the process from exiting --
        # every restart of the container would become a test of patience.
        for task in background_tasks:
            task.cancel()
        for task in background_tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task


# Addresses under which the service is only reachable from the same machine. Anything
# else means: someone on the network can reach it.
_NUR_OERTLICH = frozenset({"127.0.0.1", "::1", "localhost"})


def _warn_if_reachable_unprotected(settings: Settings) -> None:
    """Warns when the service hangs on the network and session cookies go unencrypted.

    `secure_cookies` defaults to false because otherwise initial setup over `http://`
    would fail -- and whoever then does not switch it sends their login in plaintext
    over Wi-Fi. The service cannot enforce this (behind a reverse proxy it only sees
    HTTP), but it can say so. A warning that appears once in the log at startup is the
    only place this gets noticed before something happens.
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
    # `docs_url=None` disables the bundled interface; we serve it ourselves below,
    # because FastAPI's version pulls its files from a CDN. That would violate what
    # holds for the other third-party libraries (see static/HERKUNFT.md) twice over: on
    # a home network without internet access the page would stay blank, and every call
    # would tell a third party when someone opens the heating control.
    #
    # `redoc_url=None` with no replacement: ReDoc also pulls from a CDN, and a second
    # read view of the same description is not worth the extra bundled weight. `/docs`
    # covers both.
    app = FastAPI(
        title="thermoctl",
        version=thermoctl.__version__,
        lifespan=_lifespan,
        docs_url=None,
        redoc_url=None,
    )
    # The engine is stored alongside so callers can close it. Without this, every
    # created application leaves behind an open database connection -- in production
    # until the process ends, in tests again on every setup.
    app.state.engine = create_engine_from_settings(settings)
    app.state.session_factory = session_factory(app.state.engine)
    app.include_router(start_router)
    app.include_router(control_router)
    app.include_router(controller_router)
    app.include_router(auth_router)
    app.include_router(setup_router)
    app.include_router(admin_router)
    app.include_router(kiosk_admin_router)
    app.include_router(kiosk_router)
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
        # Uniform translation of a denied permission into 403 -- a route that does
        # not do this itself (and forgets to in the future) should still not respond
        # with 500. Requirement from the final review of subproject 1.
        return JSONResponse(status_code=403, content={"detail": str(exc)})

    @app.middleware("http")
    async def request_id(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        supplied = request.headers.get("X-Request-ID")
        # An implausible supplied identifier is no reason to reject an otherwise
        # valid request -- it is simply replaced by a freshly generated one, as if
        # none had been supplied.
        identifier = (
            supplied
            if supplied is not None and _REQUEST_ID_PATTERN.fullmatch(supplied)
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
        """The OpenAPI interface, served entirely from our own directory.

        Deliberately without a login: the description reveals which routes exist, but
        not a single value and no secret -- and the same thing is in `docs/api.md` in
        the public repository anyway once published. Nothing here can be tried out
        without a token; every call goes through the same check as anywhere else.
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
