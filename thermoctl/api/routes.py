from datetime import timedelta
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.api.schemas import (
    GeraetAntwort,
    TokenAntwort,
    UebersteuerungAnlegen,
    UebersteuerungAntwort,
    ZoneAntwort,
    ZonenzustandAntwort,
)
from thermoctl.auth.dependencies import get_session
from thermoctl.auth.tokens import token_aufloesen
from thermoctl.db.base import utcnow
from thermoctl.db.models.credential import ApiToken
from thermoctl.db.models.device import Device, DeviceCapabilityLink, ZoneDevice
from thermoctl.db.models.lookup import DeviceCapability, Integration, SensorStatus
from thermoctl.db.models.messwert import DeviceHealth
from thermoctl.db.models.operations import Setting
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.zone import Zone
from thermoctl.db.models.zustand import ZoneState
from thermoctl.domain.authz import Forbidden, principal_fuer_token, require, visible_zones
from thermoctl.domain.principal import Principal
from thermoctl.domain.schedule import (
    naechster_punkt,
    uebersteuerung_anlegen,
    uebersteuerung_aufheben,
)

router = APIRouter(prefix="/api/v1")


def _token(
    session: Annotated[Session, Depends(get_session)],
    authorization: Annotated[str | None, Header()] = None,
) -> ApiToken:
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Ungueltiges Token")
    token = token_aufloesen(session, authorization.removeprefix("Bearer "))
    if token is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Ungueltiges Token")
    return token


def _principal(
    session: Annotated[Session, Depends(get_session)],
    token: Annotated[ApiToken, Depends(_token)],
) -> Principal:
    return principal_fuer_token(session, token)


def _sichtbare_zone(session: Session, principal: Principal, zone_id: int) -> Zone:
    zone = next(
        (z for z in visible_zones(session, principal, "zone.read") if z.id == zone_id),
        None,
    )
    if zone is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Zone nicht gefunden")
    return zone


@router.get("/zones", response_model=list[ZoneAntwort])
def zonen(
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(_principal)],
) -> list[Zone]:
    return visible_zones(session, principal, "zone.read")


@router.get("/zones/{zone_id}", response_model=ZoneAntwort)
def zone(
    zone_id: int,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(_principal)],
) -> Zone:
    return _sichtbare_zone(session, principal, zone_id)


@router.get("/devices", response_model=list[GeraetAntwort])
def geraete(
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(_principal)],
) -> list[GeraetAntwort]:
    require(principal, "device.read")
    faehigkeiten: dict[int, list[str]] = {}
    for geraet_id, code in session.execute(
        select(DeviceCapabilityLink.device_id, DeviceCapability.code)
        .join(DeviceCapability, DeviceCapability.id == DeviceCapabilityLink.capability_id)
        .order_by(DeviceCapability.code)
    ):
        faehigkeiten.setdefault(geraet_id, []).append(code)
    zonen: dict[int, set[str]] = {}
    for geraet_id, name in session.execute(
        select(ZoneDevice.device_id, Zone.name).join(Zone, Zone.id == ZoneDevice.zone_id)
    ):
        zonen.setdefault(geraet_id, set()).add(name)
    for geraet_id, name in session.execute(
        select(Zone.temperature_source_device_id, Zone.name).where(
            Zone.temperature_source_device_id.is_not(None)
        )
    ):
        if geraet_id is not None:
            zonen.setdefault(geraet_id, set()).add(name)

    zeilen = session.execute(
        select(Device, Integration, DeviceHealth)
        .join(Integration, Integration.id == Device.integration_id)
        .outerjoin(DeviceHealth, DeviceHealth.device_id == Device.id)
        .order_by(Device.display_name, Device.id)
    )
    return [
        GeraetAntwort(
            id=geraet.id,
            external_id=geraet.external_id,
            display_name=geraet.display_name,
            integration=anbindung.code,
            model=geraet.model,
            is_group=geraet.is_group,
            capabilities=faehigkeiten.get(geraet.id, []),
            last_payload_at=zustand.last_payload_at if zustand else None,
            battery_percent=zustand.battery_percent if zustand else None,
            link_quality=zustand.link_quality if zustand else None,
            availability=zustand.availability if zustand else None,
            zones=sorted(zonen.get(geraet.id, set())),
        )
        for geraet, anbindung, zustand in zeilen
    ]


@router.get("/zones/{zone_id}/state", response_model=ZonenzustandAntwort)
def zonenzustand(
    zone_id: int,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(_principal)],
) -> ZonenzustandAntwort:
    _sichtbare_zone(session, principal, zone_id)
    zeile = session.execute(
        select(ZoneState, SensorStatus)
        .join(SensorStatus, SensorStatus.id == ZoneState.sensor_status_id)
        .where(ZoneState.zone_id == zone_id)
    ).one_or_none()
    if zeile is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Zonenzustand nicht gefunden")
    zustand, sensorzustand = zeile
    return ZonenzustandAntwort(
        zone_id=zustand.zone_id,
        temperature_c=zustand.temperature_c,
        measured_at=zustand.measured_at,
        sensor_status=sensorzustand.code,
        window_open=zustand.window_open,
        updated_at=zustand.updated_at,
    )


@router.get("/me", response_model=TokenAntwort)
def ich(
    token: Annotated[ApiToken, Depends(_token)],
    principal: Annotated[Principal, Depends(_principal)],
) -> TokenAntwort:
    try:
        require(principal, "token.self")
    except Forbidden as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    return TokenAntwort(
        id=token.id,
        name=token.name,
        prefix=token.prefix,
        user_id=token.user_id,
        expires_at=token.expires_at,
    )


@router.post(
    "/zones/{zone_id}/override",
    response_model=UebersteuerungAntwort,
    status_code=status.HTTP_201_CREATED,
)
def uebersteuern(
    zone_id: int,
    daten: UebersteuerungAnlegen,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(_principal)],
) -> object:
    zone_obj = _sichtbare_zone(session, principal, zone_id)
    try:
        require(principal, "override.create", zone_id)
    except Forbidden as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc

    jetzt = utcnow()
    ende = jetzt + timedelta(minutes=daten.dauer_minuten) if daten.dauer_minuten else None
    if daten.bis_naechste_schaltung:
        einstellungen = session.get(Setting, 1)
        timezone = einstellungen.timezone if einstellungen is not None else "Europe/Berlin"
        lokal = jetzt.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo(timezone))
        punkte = list(
            session.scalars(select(SchedulePoint).where(SchedulePoint.zone_id == zone_id))
        )
        lokales_ende = naechster_punkt(punkte, lokal.replace(tzinfo=None))
        ende = None if lokales_ende is None else lokales_ende.replace(
            tzinfo=ZoneInfo(timezone)
        ).astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    return uebersteuerung_anlegen(
        session,
        zone_obj,
        daten.temperature_c,
        ende,
        user_id=principal.user_id,
        token_id=principal.token_id,
    )


@router.delete("/zones/{zone_id}/override", status_code=status.HTTP_204_NO_CONTENT)
def uebersteuerung_loeschen(
    zone_id: int,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(_principal)],
) -> Response:
    zone_obj = _sichtbare_zone(session, principal, zone_id)
    try:
        require(principal, "override.cancel", zone_id)
    except Forbidden as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    uebersteuerung_aufheben(session, zone_obj)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
