"""Die Kioskansicht der Svelte-Oberfläche (`/kiosk-api`).

**Eigener Principal, eigene Schnittstelle.** Dieser Router gehört weder zu `/ui-api` noch
zu `/api/v1`: Er kennt genau einen Zugang, das Kiosk-Cookie (`thermoctl.auth.kiosk`).
Sitzungs-Cookie, Bearer-Token und OAuth-Token öffnen hier nichts -- sie werden nicht einmal
gelesen -- und umgekehrt trägt ein Kiosk-Cookie weder `/ui-api` noch `/api/v1`, weil diese
beiden es nie auswerten. Auflösung des Cookies und Rechte kommen aus denselben Funktionen
wie in `kiosk_views` (`kiosk_token_from_cookie`, `principal_for_token`, `visible_zones`,
`has_permission`); hier steht keine Fachregel (Grundsatz 6).

**Eigene CSRF-Semantik.** Schreibende Aufrufe verlangen den HMAC des Kiosk-Cookies
(`kiosk_csrf_protection`) im Header `X-CSRF-Token`; mit der Sitzung hat das nichts zu tun.
Die Schnittstelle antwortet immer mit `{detail, code}` und `Cache-Control: no-store`.

**Nichts außerhalb des Token-Umfangs.** Räume, die der Token nicht lesen darf, sind nicht
auffindbar: derselbe 404 wie für eine unbekannte Nummer, im Wortlaut ohne Namen. Die
Antworten enthalten ausschließlich Räume und Aktionen, die der Token erlaubt.

Ein abgelaufener oder widerrufener Token ist ein 401 (`kiosk_token_invalid`); die Ansicht
zeigt dann einen Hinweis und keine Anmeldeseite, denn es gibt keine Anmeldung, zu der sie
führen könnte.
"""

from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl import audit
from thermoctl.auth.dependencies import get_session
from thermoctl.auth.kiosk import kiosk_csrf_protection, kiosk_token_from_cookie
from thermoctl.db.base import utcnow
from thermoctl.db.models.lookup import SensorStatus
from thermoctl.db.models.operations import Setting
from thermoctl.db.models.state import ZoneState
from thermoctl.db.models.zone import Zone
from thermoctl.domain.authz import has_permission, principal_for_token, visible_zones
from thermoctl.domain.modes import DomainError
from thermoctl.domain.principal import Principal
from thermoctl.domain.remote_control import RemoteControlError, set_setpoint
from thermoctl.domain.remote_control import boost as domain_boost
from thermoctl.domain.schedule import cancel_override, resolved_setpoint, running_override
from thermoctl.domain.zones import latest_decisions_by_zone
from thermoctl.services.emergency_state import zone_emergency_view
from thermoctl.web.shared.kiosk import THERMOSTAT_STEP


class KioskApiError(Exception):
    """Ein erwarteter Fehlschlag, der als `{detail, code}` beim Aufrufer ankommt."""

    def __init__(self, status_code: int, detail: str, code: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.code = code


class _KioskRoute(APIRoute):
    """Übersetzt Fehler in den einheitlichen Körper und verbietet jedes Zwischenspeichern.

    Auch die HTTP-Ausnahme der CSRF-Prüfung läuft hier durch: Sie kommt aus
    `kiosk_csrf_protection` als 403 mit Klartext und wird zu `{detail, code: "csrf"}`.
    """

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            try:
                response = await original(request)
            except KioskApiError as exc:
                response = JSONResponse(
                    {"detail": exc.detail, "code": exc.code}, status_code=exc.status_code
                )
            except HTTPException as exc:
                # Nur die CSRF-Prüfung wirft hier; ein anderer Grund wäre ein Programmfehler.
                response = JSONResponse(
                    {"detail": str(exc.detail), "code": "csrf"}, status_code=exc.status_code
                )
            except RequestValidationError:
                response = JSONResponse(
                    {"detail": "Die Eingabe ist ungültig.", "code": "validation"},
                    status_code=422,
                )
            response.headers["Cache-Control"] = "no-store"
            return response

        return handler


router = APIRouter(
    prefix="/kiosk-api",
    dependencies=[Depends(kiosk_csrf_protection)],
    include_in_schema=False,
    route_class=_KioskRoute,
)

Sess = Annotated[Session, Depends(get_session)]


def kiosk_principal(request: Request, session: Sess) -> Principal:
    """Der Principal des Kiosk-Cookies -- die einzige Art, hier angemeldet zu sein."""
    token = kiosk_token_from_cookie(request, session)
    if token is None:
        raise KioskApiError(
            401, "Dieser Kiosk-Zugang ist nicht mehr gültig.", "kiosk_token_invalid"
        )
    if session.get(Setting, 1) is None:
        # Ohne Einrichtung gibt es nichts zu regeln -- und `resolved_setpoint` bräche ab.
        raise KioskApiError(503, "Die Einrichtung ist unvollständig.", "setup_incomplete")
    return principal_for_token(session, token)


Prin = Annotated[Principal, Depends(kiosk_principal)]


# -- Ausgabe ----------------------------------------------------------------------------


class SensorOut(BaseModel):
    code: str
    label: str


class SetpointOut(BaseModel):
    temperature: str
    reason: str


class OverrideOut(BaseModel):
    ends_at: str | None
    temperature: str | None


class EmergencyOut(BaseModel):
    headline: str
    detail: str


class ActionsOut(BaseModel):
    setpoint: bool
    boost: bool
    cancel_override: bool


class RoomOut(BaseModel):
    id: int
    name: str
    mode_label: str
    temperature: str | None
    measured_at: str | None
    sensor: SensorOut | None
    sensor_stuck: bool
    setpoint: SetpointOut
    would_heat: bool | None
    override: OverrideOut | None
    emergency: EmergencyOut | None
    actions: ActionsOut


class StateOut(BaseModel):
    now: str
    timezone: str
    rooms: list[RoomOut]


def _iso(moment: datetime | None) -> str | None:
    """Naive UTC der Datenbank als ISO-8601 mit Zeitzone."""
    return moment.replace(tzinfo=UTC).isoformat() if moment is not None else None


def _dec(value: Decimal | None) -> str | None:
    return format(value, "f") if value is not None else None


def _rooms(
    session: Session, principal: Principal, zones: list[Zone], now: datetime
) -> list[RoomOut]:
    """Die Räume wie `kiosk_views._dashboard` sie zeigt, nur als Daten."""
    zone_ids = [zone.id for zone in zones]
    settings = session.get(Setting, 1)
    assert settings is not None  # `kiosk_principal` hat das schon geprüft
    states = {
        zone_id: (state, sensor_status)
        for zone_id, state, sensor_status in session.execute(
            select(ZoneState.zone_id, ZoneState, SensorStatus)
            .join(SensorStatus, SensorStatus.id == ZoneState.sensor_status_id)
            .where(ZoneState.zone_id.in_(zone_ids))
        )
    }
    decisions = latest_decisions_by_zone(session, zone_ids)

    rooms: list[RoomOut] = []
    for zone in zones:
        entry = states.get(zone.id)
        state = entry[0] if entry is not None else None
        sensor_status = entry[1] if entry is not None else None
        setpoint = resolved_setpoint(session, zone, now)
        running = running_override(session, zone, now)
        banner = zone_emergency_view(session, zone, now, settings).banner
        decision = decisions.get(zone.id)
        rooms.append(
            RoomOut(
                id=zone.id,
                name=zone.display_name,
                mode_label=zone.operating_mode.label,
                temperature=_dec(state.temperature_c) if state is not None else None,
                measured_at=_iso(state.measured_at) if state is not None else None,
                # Nur der Zustand, der nicht "ok" ist, wie in der HTML-Ansicht.
                sensor=(
                    SensorOut(code=sensor_status.code, label=sensor_status.label)
                    if sensor_status is not None and sensor_status.code != "ok"
                    else None
                ),
                sensor_stuck=bool(state.sensor_stuck) if state is not None else False,
                setpoint=SetpointOut(
                    temperature=format(setpoint.temperature_c, "f"), reason=setpoint.reason
                ),
                would_heat=decision.would_heat if decision is not None else None,
                override=(
                    OverrideOut(
                        ends_at=_iso(running.ends_at), temperature=_dec(running.temperature_c)
                    )
                    if running is not None
                    else None
                ),
                emergency=(
                    EmergencyOut(headline=banner.headline, detail=banner.detail)
                    if banner is not None
                    else None
                ),
                actions=ActionsOut(
                    setpoint=has_permission(principal, "setpoint.write", zone.id),
                    boost=has_permission(principal, "override.create", zone.id),
                    # `override.cancel` wie in der HTML-Ansicht: Aufheben kann eine
                    # fremde Übersteuerung zurücknehmen und hat dafür ein eigenes Recht.
                    cancel_override=running is not None
                    and has_permission(principal, "override.cancel", zone.id),
                ),
            )
        )
    return rooms


def _zone(session: Session, principal: Principal, zone_id: int) -> Zone:
    """Unbekannt und nicht erlaubt sind ununterscheidbar -- auch im Wortlaut."""
    zone = next(
        (z for z in visible_zones(session, principal, "zone.read") if z.id == zone_id), None
    )
    if zone is None:
        raise KioskApiError(404, "Raum nicht gefunden.", "not_found")
    return zone


def _need(principal: Principal, permission: str, zone: Zone) -> None:
    if not has_permission(principal, permission, zone.id):
        raise KioskApiError(403, "Dafür fehlt die Berechtigung.", "forbidden")


def _room(session: Session, principal: Principal, zone: Zone, now: datetime) -> RoomOut:
    return _rooms(session, principal, [zone], now)[0]


# -- Endpunkte --------------------------------------------------------------------------


@router.get("/state")
async def kiosk_state(session: Sess, principal: Prin) -> StateOut:
    now = utcnow()
    settings = session.get(Setting, 1)
    assert settings is not None  # `kiosk_principal` hat das schon geprüft
    return StateOut(
        now=_iso(now) or "",
        timezone=settings.timezone,
        rooms=_rooms(session, principal, visible_zones(session, principal, "zone.read"), now),
    )


class StepIn(BaseModel):
    direction: Literal["up", "down"]


@router.post("/rooms/{zone_id}/setpoint-step")
async def kiosk_setpoint_step(
    zone_id: int, body: StepIn, session: Sess, principal: Prin
) -> RoomOut:
    zone = _zone(session, principal, zone_id)
    _need(principal, "setpoint.write", zone)
    now = utcnow()
    current = resolved_setpoint(session, zone, now).temperature_c
    updated = current + (THERMOSTAT_STEP if body.direction == "up" else -THERMOSTAT_STEP)
    try:
        set_setpoint(session, zone, updated, now, token_id=principal.token_id, source="kiosk")
    except DomainError as exc:
        raise KioskApiError(422, exc.notice, "rejected") from exc
    return _room(session, principal, zone, now)


@router.post("/rooms/{zone_id}/boost")
async def kiosk_boost(zone_id: int, session: Sess, principal: Prin) -> RoomOut:
    zone = _zone(session, principal, zone_id)
    _need(principal, "override.create", zone)
    now = utcnow()
    try:
        result = domain_boost(session, zone, now, token_id=principal.token_id, source="kiosk")
    except RemoteControlError as exc:
        raise KioskApiError(422, str(exc), "rejected") from exc
    audit.record(
        session, source="kiosk", action="override.boost", object_type="zone",
        object_id=str(zone.id),
        summary=f"Nächste Schaltung in '{zone.display_name}' vorgezogen",
        token_id=principal.token_id,
        detail=f"Modus {result.mode_code}, {result.temperature} °C bis {result.bis.isoformat()}",
    )
    return _room(session, principal, zone, now)


@router.post("/rooms/{zone_id}/override/cancel")
async def kiosk_cancel_override(zone_id: int, session: Sess, principal: Prin) -> RoomOut:
    zone = _zone(session, principal, zone_id)
    _need(principal, "override.cancel", zone)
    cancel_override(session, zone)
    return _room(session, principal, zone, utcnow())
