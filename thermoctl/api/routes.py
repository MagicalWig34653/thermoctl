from datetime import timedelta
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.api.schemas import (
    GeraetAntwort,
    ModusAnlegen,
    ModusAntwort,
    RegelparameterAntwort,
    RegelparameterSchreiben,
    SollwertAntwort,
    SollwerteSchreiben,
    TokenAntwort,
    UebersteuerungAnlegen,
    UebersteuerungAntwort,
    ZeitplanpunktAnlegen,
    ZeitplanpunktAntwort,
    ZoneAntwort,
    ZonenzustandAntwort,
    ZoneSchreiben,
)
from thermoctl.auth.dependencies import get_session
from thermoctl.auth.tokens import token_aufloesen
from thermoctl.db.base import utcnow
from thermoctl.db.models.credential import ApiToken
from thermoctl.db.models.device import Device, DeviceCapabilityLink, ZoneDevice
from thermoctl.db.models.lookup import DeviceCapability, Integration, SensorStatus
from thermoctl.db.models.messwert import DeviceHealth
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.zone import SetpointMode, Zone, ZoneSetpoint
from thermoctl.db.models.zustand import ZoneState
from thermoctl.domain.authz import Forbidden, principal_fuer_token, require, visible_zones
from thermoctl.domain.modi import Domaenenfehler, modus_anlegen, sollwerte_aendern
from thermoctl.domain.principal import Principal
from thermoctl.domain.schedule import (
    Zeitplanfehler,
    ende_der_naechsten_schaltung,
    uebersteuerung_anlegen,
    uebersteuerung_aufheben,
    zeitplanpunkt_anlegen,
    zeitplanpunkt_loeschen,
)
from thermoctl.domain.zone_settings import regelparameter, regelparameter_speichern
from thermoctl.domain.zonen import ZonennameVergeben, zone_aendern, zone_anlegen, zone_loeschen

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


def _fachfehler(feld: str, meldung: str) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{feld}: {meldung}")


def _recht(principal: Principal, code: str, zone_id: int | None = None) -> None:
    try:
        require(principal, code, zone_id)
    except Forbidden as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc


def _moduszugriff(session: Session, principal: Principal) -> None:
    if not visible_zones(session, principal, "zone.read"):
        _recht(principal, "zone.read")


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


@router.post("/zones", response_model=ZoneAntwort, status_code=status.HTTP_201_CREATED)
def zone_erstellen(
    daten: ZoneSchreiben,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(_principal)],
) -> Zone:
    _recht(principal, "zone.manage")
    try:
        return zone_anlegen(session, principal, **daten.model_dump())
    except ZonennameVergeben as exc:
        raise _fachfehler("name", "Dieser Name ist bereits vergeben.") from exc


@router.put("/zones/{zone_id}", response_model=ZoneAntwort)
def zone_speichern(
    zone_id: int,
    daten: ZoneSchreiben,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(_principal)],
) -> Zone:
    zone_obj = _sichtbare_zone(session, principal, zone_id)
    _recht(principal, "zone.manage", zone_id)
    try:
        zone_aendern(session, zone_obj, principal, **daten.model_dump())
    except ZonennameVergeben as exc:
        raise _fachfehler("name", "Dieser Name ist bereits vergeben.") from exc
    return zone_obj


@router.delete("/zones/{zone_id}", status_code=status.HTTP_204_NO_CONTENT)
def zone_entfernen(
    zone_id: int,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(_principal)],
) -> Response:
    zone_obj = _sichtbare_zone(session, principal, zone_id)
    _recht(principal, "zone.manage", zone_id)
    zone_loeschen(session, zone_obj, principal)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/modes", response_model=list[ModusAntwort])
def modi(
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(_principal)],
) -> list[SetpointMode]:
    _moduszugriff(session, principal)
    return list(
        session.scalars(select(SetpointMode).order_by(SetpointMode.sort_order, SetpointMode.code))
    )


@router.post("/modes", response_model=ModusAntwort, status_code=status.HTTP_201_CREATED)
def modus_erstellen(
    daten: ModusAnlegen,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(_principal)],
) -> SetpointMode:
    _recht(principal, "mode.manage")
    try:
        return modus_anlegen(session, **daten.model_dump(), user_id=principal.user_id)
    except Domaenenfehler as exc:
        raise _fachfehler(exc.feld, exc.meldung) from exc


def _sollwertantworten(session: Session, zone_id: int) -> list[SollwertAntwort]:
    werte: dict[int, Decimal] = {
        modus_id: temperatur
        for modus_id, temperatur in session.execute(
            select(ZoneSetpoint.setpoint_mode_id, ZoneSetpoint.temperature_c).where(
                ZoneSetpoint.zone_id == zone_id
            )
        )
    }
    return [
        SollwertAntwort(
            mode_id=m.id, mode_code=m.code, mode_name=m.name, temperature_c=werte.get(m.id)
        )
        for m in session.scalars(
            select(SetpointMode).order_by(SetpointMode.sort_order, SetpointMode.code)
        )
    ]


@router.get("/zones/{zone_id}/setpoints", response_model=list[SollwertAntwort])
def sollwerte(
    zone_id: int,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(_principal)],
) -> list[SollwertAntwort]:
    _sichtbare_zone(session, principal, zone_id)
    return _sollwertantworten(session, zone_id)


@router.put("/zones/{zone_id}/setpoints", response_model=list[SollwertAntwort])
def sollwerte_speichern(
    zone_id: int,
    daten: SollwerteSchreiben,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(_principal)],
) -> list[SollwertAntwort]:
    zone_obj = _sichtbare_zone(session, principal, zone_id)
    _recht(principal, "setpoint.write", zone_id)
    werte = {eintrag.mode_id: eintrag.temperature_c for eintrag in daten.setpoints}
    try:
        sollwerte_aendern(session, zone_obj, werte, user_id=principal.user_id)
    except Domaenenfehler as exc:
        raise _fachfehler("temperature_c", exc.meldung) from exc
    return _sollwertantworten(session, zone_id)


def _zeitplanantworten(session: Session, zone_id: int) -> list[ZeitplanpunktAntwort]:
    zeilen = session.execute(
        select(SchedulePoint, SetpointMode)
        .join(SetpointMode, SetpointMode.id == SchedulePoint.setpoint_mode_id)
        .where(SchedulePoint.zone_id == zone_id)
        .order_by(SchedulePoint.weekday, SchedulePoint.minute_of_day)
    )
    return [
        ZeitplanpunktAntwort(
            id=p.id,
            weekday=p.weekday,
            minute_of_day=p.minute_of_day,
            mode_id=m.id,
            mode_name=m.name,
        )
        for p, m in zeilen
    ]


@router.get("/zones/{zone_id}/schedule", response_model=list[ZeitplanpunktAntwort])
def zeitplan(
    zone_id: int,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(_principal)],
) -> list[ZeitplanpunktAntwort]:
    _sichtbare_zone(session, principal, zone_id)
    return _zeitplanantworten(session, zone_id)


@router.post(
    "/zones/{zone_id}/schedule",
    response_model=ZeitplanpunktAntwort,
    status_code=status.HTTP_201_CREATED,
)
def zeitplanpunkt_erstellen(
    zone_id: int,
    daten: ZeitplanpunktAnlegen,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(_principal)],
) -> ZeitplanpunktAntwort:
    zone_obj = _sichtbare_zone(session, principal, zone_id)
    _recht(principal, "schedule.manage", zone_id)
    try:
        punkt = zeitplanpunkt_anlegen(
            session,
            zone_obj,
            wochentag=daten.weekday,
            minute=daten.minute_of_day,
            modus_id=daten.mode_id,
            user_id=principal.user_id,
            token_id=principal.token_id,
        )
    except Zeitplanfehler as exc:
        raise _fachfehler(exc.feld, exc.meldung) from exc
    modus = session.get(SetpointMode, punkt.setpoint_mode_id)
    assert modus is not None
    return ZeitplanpunktAntwort(
        id=punkt.id,
        weekday=punkt.weekday,
        minute_of_day=punkt.minute_of_day,
        mode_id=modus.id,
        mode_name=modus.name,
    )


@router.delete("/zones/{zone_id}/schedule/{punkt_id}", status_code=status.HTTP_204_NO_CONTENT)
def zeitplanpunkt_entfernen(
    zone_id: int,
    punkt_id: int,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(_principal)],
) -> Response:
    zone_obj = _sichtbare_zone(session, principal, zone_id)
    _recht(principal, "schedule.manage", zone_id)
    punkt = session.get(SchedulePoint, punkt_id)
    if punkt is None or punkt.zone_id != zone_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Zeitplanpunkt nicht gefunden")
    zeitplanpunkt_loeschen(
        session, zone_obj, punkt, user_id=principal.user_id, token_id=principal.token_id
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/zones/{zone_id}/parameters", response_model=RegelparameterAntwort)
def parameter(
    zone_id: int,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(_principal)],
) -> RegelparameterAntwort:
    zone_obj = _sichtbare_zone(session, principal, zone_id)
    return RegelparameterAntwort(**regelparameter(session, zone_obj).__dict__)


@router.put("/zones/{zone_id}/parameters", response_model=RegelparameterAntwort)
def parameter_speichern(
    zone_id: int,
    daten: RegelparameterSchreiben,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(_principal)],
) -> RegelparameterAntwort:
    zone_obj = _sichtbare_zone(session, principal, zone_id)
    _recht(principal, "zone.manage", zone_id)
    regelparameter_speichern(
        session,
        zone_obj,
        daten.model_dump(),
        user_id=principal.user_id,
        token_id=principal.token_id,
    )
    return RegelparameterAntwort(**regelparameter(session, zone_obj).__dict__)


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
        # Dieselbe Funktion wie in der Oberflaeche. Bis zum Abschlussreview von
        # Teilprojekt 3 stand die Rechnung hier ein zweites Mal — beide Adapter haetten
        # nach einer Korrektur an der Zeitzonenbehandlung auseinanderlaufen koennen.
        ende = ende_der_naechsten_schaltung(session, zone_obj)
    try:
        return uebersteuerung_anlegen(
            session,
            zone_obj,
            daten.temperature_c,
            ende,
            user_id=principal.user_id,
            token_id=principal.token_id,
        )
    except Domaenenfehler as exc:
        # Das Schema faengt den Wertebereich bereits ab; die Domaene prueft ihn seit dem
        # Abschlussreview zusaetzlich selbst. Bleibt trotzdem etwas uebrig, ist es ein
        # Eingabefehler und keine Stoerung des Dienstes.
        raise _fachfehler(exc.feld, exc.meldung) from exc


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
