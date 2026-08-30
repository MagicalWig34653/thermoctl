from decimal import Decimal, InvalidOperation
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.auth.dependencies import aktueller_principal, csrf_schutz, get_session
from thermoctl.db.models.zone import SetpointMode, Zone, ZoneSetpoint
from thermoctl.domain.authz import require, visible_zones
from thermoctl.domain.modes import (
    MAXIMUM_TEMPERATURE_C,
    MINIMUM_TEMPERATURE_C,
    DomainError,
    check_temperature,
    create_mode,
    delete_guard,
    delete_mode,
    update_mode,
    update_setpoints,
)
from thermoctl.domain.principal import Principal
from thermoctl.web import templates

# `include_in_schema=False`: Die OpenAPI-Beschreibung ist der Vertrag der
# REST-Schnittstelle. Diese Wege liefern HTML fuer Menschen, und in der Oberflaeche
# unter /docs stuende sonst neben jedem echten Endpunkt ein Formularweg, dessen
# 'Try it out' eine echte Aenderung ausloest.
router = APIRouter(dependencies=[Depends(csrf_schutz)], include_in_schema=False)


def _modes(session: Session) -> list[SetpointMode]:
    return list(
        session.scalars(
            select(SetpointMode).order_by(SetpointMode.sort_order, SetpointMode.name)
        )
    )


def _mode_or_404(session: Session, mode_id: int) -> SetpointMode:
    mode = session.get(SetpointMode, mode_id)
    if mode is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return mode


def _sortierung(value: str) -> int:
    try:
        return int(value)
    except ValueError as exc:
        raise DomainError("sort_order", "Die Sortierung muss eine ganze Zahl sein.") from exc


def _mode_form(
    request: Request,
    *,
    values: dict[str, str],
    errors: DomainError | None = None,
    mode: SetpointMode | None = None,
) -> Response:
    return templates.TemplateResponse(
        request,
        "modus_formular.html",
        {
            "values": values,
            "errors": {errors.feld: errors.notice} if errors is not None else {},
            "mode": mode,
        },
    )


@router.get("/modes")
async def mode_list(
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    require(principal, "mode.manage")
    modes = _modes(session)
    return templates.TemplateResponse(request, "modi.html", {"modes": modes})


@router.get("/modes/new")
async def mode_new_form(
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
) -> Response:
    require(principal, "mode.manage")
    return _mode_form(
        request, values={"code": "", "name": "", "sort_order": "0"}
    )


@router.post("/modes")
async def create_mode_view(
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    require(principal, "mode.manage")
    form = await request.form()
    values = {name: str(form.get(name, "")) for name in ("code", "name", "sort_order")}
    try:
        create_mode(
            session,
            code=values["code"],
            name=values["name"],
            sort_order=_sortierung(values["sort_order"]),
            user_id=principal.user_id,
        )
    except DomainError as exc:
        return _mode_form(request, values=values, errors=exc)
    return RedirectResponse("/modes", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/modes/{mode_id}")
async def mode_edit_form(
    request: Request,
    mode_id: int,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    require(principal, "mode.manage")
    mode = _mode_or_404(session, mode_id)
    return _mode_form(
        request,
        mode=mode,
        values={"code": mode.code, "name": mode.name, "sort_order": str(mode.sort_order)},
    )


@router.post("/modes/{mode_id}")
async def save_mode(
    request: Request,
    mode_id: int,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    require(principal, "mode.manage")
    mode = _mode_or_404(session, mode_id)
    form = await request.form()
    values = {name: str(form.get(name, "")) for name in ("code", "name", "sort_order")}
    try:
        update_mode(
            session,
            mode,
            code=values["code"],
            name=values["name"],
            sort_order=_sortierung(values["sort_order"]),
            user_id=principal.user_id,
        )
    except DomainError as exc:
        return _mode_form(request, mode=mode, values=values, errors=exc)
    return RedirectResponse("/modes", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/modes/{mode_id}/delete")
async def mode_delete_form(
    request: Request,
    mode_id: int,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    require(principal, "mode.manage")
    mode = _mode_or_404(session, mode_id)
    return templates.TemplateResponse(
        request,
        "modus_loeschen.html",
        {"mode": mode, "sperre": delete_guard(session, mode)},
    )


@router.post("/modes/{mode_id}/delete")
async def remove_mode(
    request: Request,
    mode_id: int,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    require(principal, "mode.manage")
    mode = _mode_or_404(session, mode_id)
    try:
        delete_mode(session, mode, user_id=principal.user_id)
    except DomainError as exc:
        return templates.TemplateResponse(
            request, "modus_loeschen.html", {"mode": mode, "sperre": exc.notice}
        )
    return RedirectResponse("/modes", status_code=status.HTTP_303_SEE_OTHER)


def _zone_or_404(session: Session, principal: Principal, zone_id: int) -> Zone:
    zones = visible_zones(session, principal, "setpoint.write")
    zone = next((entry for entry in zones if entry.id == zone_id), None)
    if zone is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return zone


def _setpointpage(
    request: Request,
    session: Session,
    zone: Zone,
    *,
    values: dict[str, str] | None = None,
    errors: dict[str, str] | None = None,
) -> Response:
    modes = _modes(session)
    if values is None:
        stored = {
            zeile.setpoint_mode_id: zeile.temperature_c
            for zeile in session.scalars(
                select(ZoneSetpoint).where(ZoneSetpoint.zone_id == zone.id)
            )
        }
        values = {
            f"sollwert_{mode.id}": str(stored.get(mode.id, "")) for mode in modes
        }
    return templates.TemplateResponse(
        request,
        "sollwerte.html",
        {
            # Aus der Domaene: Zahlen im Markup waeren eine zweite Fassung
            # der Grenze und blieben beim naechsten Verschieben zurueck.
            "mindesttemperatur": MINIMUM_TEMPERATURE_C,
            "hoechsttemperatur": MAXIMUM_TEMPERATURE_C,
            "zone": zone,
            "modes": modes,
            "values": values,
            "errors": errors or {},
        },
    )


@router.get("/zones/{zone_id}/setpoints")
async def setpoints_form(
    request: Request,
    zone_id: int,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    zone = _zone_or_404(session, principal, zone_id)
    return _setpointpage(request, session, zone)


@router.post("/zones/{zone_id}/setpoints")
async def save_setpoints(
    request: Request,
    zone_id: int,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    zone = _zone_or_404(session, principal, zone_id)
    modes = _modes(session)
    form = await request.form()
    raw_values = {
        f"sollwert_{mode.id}": str(form.get(f"sollwert_{mode.id}", "")).strip()
        for mode in modes
    }
    values: dict[int, Decimal | None] = {}
    for mode in modes:
        feld = f"sollwert_{mode.id}"
        if not raw_values[feld]:
            values[mode.id] = None
            continue
        try:
            values[mode.id] = Decimal(raw_values[feld])
        except InvalidOperation:
            return _setpointpage(
                request,
                session,
                zone,
                values=raw_values,
                errors={feld: "Der Sollwert muss eine Zahl sein."},
            )
    try:
        update_setpoints(session, zone, values, user_id=principal.user_id)
    except DomainError as exc:
        # Die Domänenregel kennt bewusst keine HTML-Feldnamen. Der erste Wert, der
        # ihre Temperaturregel verletzt, wird am zugehörigen Modusfeld angezeigt.
        for mode in modes:
            temperature = values[mode.id]
            if temperature is not None:
                try:
                    check_temperature(temperature)
                except DomainError:
                    return _setpointpage(
                        request,
                        session,
                        zone,
                        values=raw_values,
                        errors={f"sollwert_{mode.id}": exc.notice},
                    )
        # Unerreichbar, solange jeder `Domaenenfehler` aus `sollwerte_aendern` aus
        # `temperatur_pruefen` stammt -- die Schleife darueber ruft dieselbe Pruefung
        # erneut auf und findet den Wert, der ihn ausgeloest hat. Die Zeile bleibt als
        # Notausgang: Kommt in der Domaene spaeter eine Regel dazu, die sich nicht einem
        # einzelnen Feld zuordnen laesst, faellt sie hier auf, statt still eine falsche
        # Feldmeldung zu erzeugen.
        raise  # pragma: no cover
    return RedirectResponse(
        f"/zones/{zone.id}/setpoints", status_code=status.HTTP_303_SEE_OTHER
    )
