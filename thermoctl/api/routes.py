from datetime import timedelta
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.api.schemas import (
    BoostResponse,
    ControlParametersResponse,
    ControlResponse,
    CreateMode,
    CreateOverride,
    CreateSchedulePoint,
    DeviceResponse,
    ModeResponse,
    MoveSchedulePoint,
    OverrideResponse,
    SchedulePointResponse,
    SetArmed,
    SetpointResponse,
    TokenResponse,
    WriteControl,
    WriteControlParameters,
    WriteParameter,
    WriteSetpoints,
    WriteSolarLocation,
    WriteZone,
    ZoneResponse,
    ZoneStateResponse,
)
from thermoctl.auth.dependencies import get_session
from thermoctl.auth.tokens import resolve_token
from thermoctl.db.base import utcnow
from thermoctl.db.models.credential import ApiToken
from thermoctl.db.models.device import Device, DeviceCapabilityLink, ZoneDevice
from thermoctl.db.models.lookup import DeviceCapability, Integration, SensorStatus
from thermoctl.db.models.measurement import DeviceHealth
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.state import ZoneState
from thermoctl.db.models.zone import SetpointMode, Zone, ZoneSetpoint
from thermoctl.domain.authz import Forbidden, principal_for_token, require, visible_zones
from thermoctl.domain.control import (
    LIMITS,
    ControlError,
    arm,
    save_settings,
    save_solar_location,
    settings,
)
from thermoctl.domain.modes import DomainError, create_mode, update_setpoints
from thermoctl.domain.principal import Principal
from thermoctl.domain.remote_control import RemoteControlError
from thermoctl.domain.remote_control import boost as domain_boost
from thermoctl.domain.schedule import (
    ScheduleError,
    cancel_override,
    create_override,
    create_schedule_point,
    delete_schedule_point,
    end_of_next_switch,
    move_schedule_point,
)
from thermoctl.domain.zone_settings import (
    PARAMETERS,
    ParameterOutOfRange,
    UnknownParameter,
    control_parameters,
    save_control_parameters,
    set_parameter,
)
from thermoctl.domain.zones import ZoneNameTaken, create_zone, delete_zone, update_zone

router = APIRouter(prefix="/api/v1")


# `auto_error=False`: without this, FastAPI itself responds with 403 and an English
# message when the header is missing. We want 401 and the same response as for an
# invalid token — whether a header was missing or a token is invalid is none of the
# caller's business.
#
# The detour via `HTTPBearer` instead of an ordinary `Header()` parameter has a
# visible reason: only this way does the scheme show up as a `securityScheme` in the
# OpenAPI description. Before, `authorization` appeared on every route as an optional
# header parameter, and the interface under /docs had no login button — you would
# have had to type "Bearer <token>" by hand on every single call.
_bearer = HTTPBearer(auto_error=False, description="API-Token, ausgestellt unter /tokens")


def _token(
    session: Annotated[Session, Depends(get_session)],
    access: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)] = None,
) -> ApiToken:
    if access is None or access.scheme.lower() != "bearer":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Ungueltiges Token")
    token = resolve_token(session, access.credentials)
    if token is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Ungueltiges Token")
    return token


def _principal(
    session: Annotated[Session, Depends(get_session)],
    token: Annotated[ApiToken, Depends(_token)],
) -> Principal:
    return principal_for_token(session, token)


def _visible_zone(session: Session, principal: Principal, zone_id: int) -> Zone:
    zone = next(
        (z for z in visible_zones(session, principal, "zone.read") if z.id == zone_id),
        None,
    )
    if zone is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Zone nicht gefunden")
    return zone


def _domain_error(field: str, notice: str) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{field}: {notice}")


def _permission(principal: Principal, code: str, zone_id: int | None = None) -> None:
    try:
        require(principal, code, zone_id)
    except Forbidden as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc


def _mode_access(session: Session, principal: Principal) -> None:
    if not visible_zones(session, principal, "zone.read"):
        _permission(principal, "zone.read")


@router.get("/zones", response_model=list[ZoneResponse])
def zones(
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(_principal)],
) -> list[Zone]:
    return visible_zones(session, principal, "zone.read")


@router.get("/zones/{zone_id}", response_model=ZoneResponse)
def zone(
    zone_id: int,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(_principal)],
) -> Zone:
    return _visible_zone(session, principal, zone_id)


@router.post("/zones", response_model=ZoneResponse, status_code=status.HTTP_201_CREATED)
def create_zone_view(
    data: WriteZone,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(_principal)],
) -> Zone:
    _permission(principal, "zone.manage")
    try:
        return create_zone(session, principal, **data.model_dump())
    except ZoneNameTaken as exc:
        raise _domain_error("name", "Dieser Name ist bereits vergeben.") from exc


@router.put("/zones/{zone_id}", response_model=ZoneResponse)
def save_zone(
    zone_id: int,
    data: WriteZone,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(_principal)],
) -> Zone:
    zone_obj = _visible_zone(session, principal, zone_id)
    _permission(principal, "zone.manage", zone_id)
    try:
        update_zone(session, zone_obj, principal, **data.model_dump())
    except ZoneNameTaken as exc:
        raise _domain_error("name", "Dieser Name ist bereits vergeben.") from exc
    return zone_obj


@router.delete("/zones/{zone_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_zone(
    zone_id: int,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(_principal)],
) -> Response:
    zone_obj = _visible_zone(session, principal, zone_id)
    _permission(principal, "zone.manage", zone_id)
    delete_zone(session, zone_obj, principal)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/modes", response_model=list[ModeResponse])
def modes(
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(_principal)],
) -> list[SetpointMode]:
    _mode_access(session, principal)
    return list(
        session.scalars(select(SetpointMode).order_by(SetpointMode.sort_order, SetpointMode.code))
    )


@router.post("/modes", response_model=ModeResponse, status_code=status.HTTP_201_CREATED)
def create_mode_view(
    data: CreateMode,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(_principal)],
) -> SetpointMode:
    _permission(principal, "mode.manage")
    try:
        return create_mode(session, **data.model_dump(), user_id=principal.user_id)
    except DomainError as exc:
        raise _domain_error(exc.field, exc.notice) from exc


def _setpoint_responses(session: Session, zone_id: int) -> list[SetpointResponse]:
    values: dict[int, Decimal] = {
        mode_id: temperature
        for mode_id, temperature in session.execute(
            select(ZoneSetpoint.setpoint_mode_id, ZoneSetpoint.temperature_c).where(
                ZoneSetpoint.zone_id == zone_id
            )
        )
    }
    return [
        SetpointResponse(
            mode_id=m.id, mode_code=m.code, mode_name=m.name, temperature_c=values.get(m.id)
        )
        for m in session.scalars(
            select(SetpointMode).order_by(SetpointMode.sort_order, SetpointMode.code)
        )
    ]


@router.get("/zones/{zone_id}/setpoints", response_model=list[SetpointResponse])
def setpoints(
    zone_id: int,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(_principal)],
) -> list[SetpointResponse]:
    _visible_zone(session, principal, zone_id)
    return _setpoint_responses(session, zone_id)


@router.put("/zones/{zone_id}/setpoints", response_model=list[SetpointResponse])
def save_setpoints(
    zone_id: int,
    data: WriteSetpoints,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(_principal)],
) -> list[SetpointResponse]:
    zone_obj = _visible_zone(session, principal, zone_id)
    _permission(principal, "setpoint.write", zone_id)
    values = {entry.mode_id: entry.temperature_c for entry in data.setpoints}
    try:
        update_setpoints(session, zone_obj, values, user_id=principal.user_id)
    except DomainError as exc:
        raise _domain_error("temperature_c", exc.notice) from exc
    return _setpoint_responses(session, zone_id)


def _schedule_responses(session: Session, zone_id: int) -> list[SchedulePointResponse]:
    rows = session.execute(
        select(SchedulePoint, SetpointMode)
        .join(SetpointMode, SetpointMode.id == SchedulePoint.setpoint_mode_id)
        .where(SchedulePoint.zone_id == zone_id)
        .order_by(SchedulePoint.weekday, SchedulePoint.minute_of_day)
    )
    return [
        SchedulePointResponse(
            id=p.id,
            weekday=p.weekday,
            minute_of_day=p.minute_of_day,
            mode_id=m.id,
            mode_name=m.name,
        )
        for p, m in rows
    ]


@router.get("/zones/{zone_id}/schedule", response_model=list[SchedulePointResponse])
def schedule(
    zone_id: int,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(_principal)],
) -> list[SchedulePointResponse]:
    _visible_zone(session, principal, zone_id)
    return _schedule_responses(session, zone_id)


@router.post(
    "/zones/{zone_id}/schedule",
    response_model=SchedulePointResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_schedule_point_view(
    zone_id: int,
    data: CreateSchedulePoint,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(_principal)],
) -> SchedulePointResponse:
    zone_obj = _visible_zone(session, principal, zone_id)
    _permission(principal, "schedule.manage", zone_id)
    try:
        point = create_schedule_point(
            session,
            zone_obj,
            weekday=data.weekday,
            minute=data.minute_of_day,
            mode_id=data.mode_id,
            user_id=principal.user_id,
            token_id=principal.token_id,
            source="api",
        )
    except ScheduleError as exc:
        raise _domain_error(exc.field, exc.notice) from exc
    mode = session.get(SetpointMode, point.setpoint_mode_id)
    assert mode is not None
    return SchedulePointResponse(
        id=point.id,
        weekday=point.weekday,
        minute_of_day=point.minute_of_day,
        mode_id=mode.id,
        mode_name=mode.name,
    )


@router.delete("/zones/{zone_id}/schedule/{point_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_schedule_point(
    zone_id: int,
    point_id: int,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(_principal)],
) -> Response:
    zone_obj = _visible_zone(session, principal, zone_id)
    _permission(principal, "schedule.manage", zone_id)
    point = session.get(SchedulePoint, point_id)
    if point is None or point.zone_id != zone_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Zeitplanpunkt nicht gefunden")
    delete_schedule_point(
        session, zone_obj, point, user_id=principal.user_id, token_id=principal.token_id
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/zones/{zone_id}/parameters", response_model=ControlParametersResponse)
def parameter(
    zone_id: int,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(_principal)],
) -> ControlParametersResponse:
    zone_obj = _visible_zone(session, principal, zone_id)
    return ControlParametersResponse(**control_parameters(session, zone_obj).__dict__)


@router.put("/zones/{zone_id}/parameters", response_model=ControlParametersResponse)
def save_parameter(
    zone_id: int,
    data: WriteControlParameters,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(_principal)],
) -> ControlParametersResponse:
    zone_obj = _visible_zone(session, principal, zone_id)
    _permission(principal, "zone.manage", zone_id)
    save_control_parameters(
        session,
        zone_obj,
        data.model_dump(),
        user_id=principal.user_id,
        token_id=principal.token_id,
        source="api",
    )
    return ControlParametersResponse(**control_parameters(session, zone_obj).__dict__)


@router.put("/zones/{zone_id}/parameters/{name}", response_model=ControlParametersResponse)
def save_single_parameter(
    zone_id: int,
    name: str,
    data: WriteParameter,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(_principal)],
) -> ControlParametersResponse:
    """Sets **one** control parameter and leaves the rest as they are.

    Alongside the PUT for all parameters, not instead of it: whoever just wants to
    change the hysteresis would otherwise have to read all six first and send them
    back -- fixing every inherited value as a zone override in the process.
    """
    zone_obj = _visible_zone(session, principal, zone_id)
    _permission(principal, "zone.manage", zone_id)
    try:
        set_parameter(
            session,
            zone_obj,
            name,
            data.value,
            user_id=principal.user_id,
            token_id=principal.token_id,
            source="api",
        )
    except UnknownParameter as exc:
        # A name that doesn't exist is not invalid input but a route that doesn't
        # exist -- and the list of valid ones belongs in the response.
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"{exc} Möglich sind: {', '.join(p.name for p in PARAMETERS)}.",
        ) from exc
    except ParameterOutOfRange as exc:
        raise _domain_error(name, str(exc)) from exc
    return ControlParametersResponse(**control_parameters(session, zone_obj).__dict__)


@router.post(
    "/zones/{zone_id}/boost",
    response_model=BoostResponse,
    status_code=status.HTTP_201_CREATED,
)
def boost(
    zone_id: int,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(_principal)],
) -> BoostResponse:
    """Pulls the next switch forward.

    The same permission as an override, because it is one -- just one whose value and
    end are determined by the schedule instead of the caller.
    """
    zone_obj = _visible_zone(session, principal, zone_id)
    _permission(principal, "override.create", zone_id)
    try:
        result = domain_boost(
            session,
            zone_obj,
            utcnow(),
            user_id=principal.user_id,
            token_id=principal.token_id,
            source="api",
        )
    except RemoteControlError as exc:
        # No schedule, no stored setpoint: the request is understood but cannot be
        # carried out in this state.
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return BoostResponse(
        zone_id=zone_obj.id,
        mode_code=result.mode_code,
        temperature_c=result.temperature,
        gilt_bis=result.bis,
    )


@router.get("/devices", response_model=list[DeviceResponse])
def devices(
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(_principal)],
) -> list[DeviceResponse]:
    require(principal, "device.read")
    capabilities: dict[int, list[str]] = {}
    for device_id, code in session.execute(
        select(DeviceCapabilityLink.device_id, DeviceCapability.code)
        .join(DeviceCapability, DeviceCapability.id == DeviceCapabilityLink.capability_id)
        .order_by(DeviceCapability.code)
    ):
        capabilities.setdefault(device_id, []).append(code)
    zones: dict[int, set[str]] = {}
    for device_id, name in session.execute(
        select(ZoneDevice.device_id, Zone.name).join(Zone, Zone.id == ZoneDevice.zone_id)
    ):
        zones.setdefault(device_id, set()).add(name)
    for device_id, name in session.execute(
        select(Zone.temperature_source_device_id, Zone.name).where(
            Zone.temperature_source_device_id.is_not(None)
        )
    ):
        if device_id is not None:
            zones.setdefault(device_id, set()).add(name)

    rows = session.execute(
        select(Device, Integration, DeviceHealth)
        .join(Integration, Integration.id == Device.integration_id)
        .outerjoin(DeviceHealth, DeviceHealth.device_id == Device.id)
        .order_by(Device.display_name, Device.id)
    )
    return [
        DeviceResponse(
            id=device.id,
            external_id=device.external_id,
            display_name=device.display_name,
            integration=integration.code,
            model=device.model,
            is_group=device.is_group,
            capabilities=capabilities.get(device.id, []),
            last_payload_at=state.last_payload_at if state else None,
            battery_percent=state.battery_percent if state else None,
            link_quality=state.link_quality if state else None,
            availability=state.availability if state else None,
            zones=sorted(zones.get(device.id, set())),
        )
        for device, integration, state in rows
    ]


@router.get("/zones/{zone_id}/state", response_model=ZoneStateResponse)
def zone_state(
    zone_id: int,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(_principal)],
) -> ZoneStateResponse:
    _visible_zone(session, principal, zone_id)
    row = session.execute(
        select(ZoneState, SensorStatus)
        .join(SensorStatus, SensorStatus.id == ZoneState.sensor_status_id)
        .where(ZoneState.zone_id == zone_id)
    ).one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Zonenzustand nicht gefunden")
    state, sensor_state = row
    return ZoneStateResponse(
        zone_id=state.zone_id,
        temperature_c=state.temperature_c,
        measured_at=state.measured_at,
        sensor_status=sensor_state.code,
        window_open=state.window_open,
        updated_at=state.updated_at,
    )


@router.get("/me", response_model=TokenResponse)
def ich(
    token: Annotated[ApiToken, Depends(_token)],
    principal: Annotated[Principal, Depends(_principal)],
) -> TokenResponse:
    try:
        require(principal, "token.self")
    except Forbidden as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    return TokenResponse(
        id=token.id,
        name=token.name,
        prefix=token.prefix,
        user_id=token.user_id,
        expires_at=token.expires_at,
    )


@router.post(
    "/zones/{zone_id}/override",
    response_model=OverrideResponse,
    status_code=status.HTTP_201_CREATED,
)
def override_zone(
    zone_id: int,
    data: CreateOverride,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(_principal)],
) -> object:
    zone_obj = _visible_zone(session, principal, zone_id)
    try:
        require(principal, "override.create", zone_id)
    except Forbidden as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc

    now = utcnow()
    end_at = now + timedelta(minutes=data.duration_minutes) if data.duration_minutes else None
    if data.until_next_switch:
        # The same function as in the interface. Until the final review of subproject
        # 3, the calculation was here a second time — both adapters could have drifted
        # apart after a fix to the timezone handling.
        end_at = end_of_next_switch(session, zone_obj)
    try:
        return create_override(
            session,
            zone_obj,
            data.temperature_c,
            end_at,
            user_id=principal.user_id,
            token_id=principal.token_id,
            source="api",
        )
    except DomainError as exc:  # pragma: no cover
        # Not reachable through this route: `CreateOverride` already enforces the
        # bounds and the single decimal place, so nothing the domain would object to
        # gets this far. It stays because the domain is the place that decides -- if
        # the schema and the domain ever disagree, this turns the disagreement into a
        # 422 instead of a 500.
        raise _domain_error(exc.field, exc.notice) from exc


@router.delete("/zones/{zone_id}/override", status_code=status.HTTP_204_NO_CONTENT)
def delete_override(
    zone_id: int,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(_principal)],
) -> Response:
    zone_obj = _visible_zone(session, principal, zone_id)
    try:
        require(principal, "override.cancel", zone_id)
    except Forbidden as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    cancel_override(session, zone_obj)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Control ------------------------------------------------------------------
#
# The same domain functions as the interface. The adapter only translates formats
# and errors; every limit and every permission check lives exactly once, in the domain.


def _control_response(session: Session) -> ControlResponse:
    row = settings(session)
    return ControlResponse(
        control_armed=row.control_armed,
        timezone=row.timezone,
        solar_forecast_enabled=row.solar_forecast_enabled,
        solar_forecast_latitude=row.solar_forecast_latitude,
        solar_forecast_longitude=row.solar_forecast_longitude,
        **{field: getattr(row, field) for field in LIMITS},
    )


@router.get("/control", response_model=ControlResponse)
def control(
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(_principal)],
) -> ControlResponse:
    # Whoever may see the plant may read this: nobody should have to guess "is this
    # thing actually switching right now?".
    _permission(principal, "zone.read")
    return _control_response(session)


@router.put("/control/armed", response_model=ControlResponse)
def control_set_armed(
    data: SetArmed,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(_principal)],
) -> ControlResponse:
    """Flips the bolt that the database holds.

    Its own permission `control.arm`, not `setting.manage`: this here moves a valve.
    The second bolt -- `MqttClient(switching_allowed=...)` -- stays untouched.
    """
    _permission(principal, "control.arm")
    try:
        arm(
            session,
            data.armed,
            reason=data.reason,
            user_id=principal.user_id,
            token_id=principal.token_id,
            source="api",
        )
    except ControlError as exc:
        raise _domain_error(exc.field, exc.notice) from exc
    return _control_response(session)


@router.put("/control/defaults", response_model=ControlResponse)
def control_defaults(
    data: WriteControl,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(_principal)],
) -> ControlResponse:
    _permission(principal, "setting.manage")
    values = {field: str(getattr(data, field)) for field in LIMITS}
    try:
        save_settings(
            session,
            values,
            data.timezone,
            user_id=principal.user_id,
            token_id=principal.token_id,
            source="api",
        )
    except ControlError as exc:
        raise _domain_error(exc.field, exc.notice) from exc
    return _control_response(session)


@router.put("/control/solar-location", response_model=ControlResponse)
def control_solar_location(
    data: WriteSolarLocation,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(_principal)],
) -> ControlResponse:
    """Switch and location for the solar forecast.

    An empty coordinate is a valid answer and means "off" -- there is no sensible
    default location (principle 1), so a caller that has none says so by leaving the
    fields empty rather than by sending someone else's.
    """
    _permission(principal, "setting.manage")
    try:
        save_solar_location(
            session,
            enabled=data.enabled,
            latitude_text=data.latitude,
            longitude_text=data.longitude,
            user_id=principal.user_id,
            token_id=principal.token_id,
            source="api",
        )
    except ControlError as exc:
        raise _domain_error(exc.field, exc.notice) from exc
    return _control_response(session)


@router.put("/zones/{zone_id}/schedule/{point_id}", response_model=SchedulePointResponse)
def reposition_schedule_point(
    zone_id: int,
    point_id: int,
    data: MoveSchedulePoint,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(_principal)],
) -> SchedulePointResponse:
    """Moves a point to a different time -- the counterpart to dragging it in the week
    view. The point keeps its id so a caller can keep tracking it."""
    zone_obj = _visible_zone(session, principal, zone_id)
    _permission(principal, "schedule.manage", zone_id)
    point = session.get(SchedulePoint, point_id)
    if point is None or point.zone_id != zone_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Zeitplanpunkt nicht gefunden")
    try:
        move_schedule_point(
            session,
            zone_obj,
            point,
            weekday=data.weekday,
            minute=data.minute_of_day,
            user_id=principal.user_id,
            token_id=principal.token_id,
            source="api",
        )
    except ScheduleError as exc:
        raise _domain_error(exc.field, exc.notice) from exc
    mode = session.get(SetpointMode, point.setpoint_mode_id)
    assert mode is not None
    return SchedulePointResponse(
        id=point.id,
        weekday=point.weekday,
        minute_of_day=point.minute_of_day,
        mode_id=mode.id,
        mode_name=mode.name,
    )
