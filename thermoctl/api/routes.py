from datetime import timedelta
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.api.schemas import (
    TokenAntwort,
    UebersteuerungAnlegen,
    UebersteuerungAntwort,
    ZoneAntwort,
)
from thermoctl.auth.dependencies import get_session
from thermoctl.auth.tokens import token_aufloesen
from thermoctl.db.base import utcnow
from thermoctl.db.models.credential import ApiToken
from thermoctl.db.models.operations import Setting
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.zone import Zone
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
