from datetime import timedelta
from decimal import Decimal, InvalidOperation
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from thermoctl.auth.dependencies import aktueller_principal, csrf_schutz, get_session
from thermoctl.db.base import utcnow
from thermoctl.db.models.zone import Zone
from thermoctl.domain.authz import visible_zones
from thermoctl.domain.modes import DomainError, update_setpoints
from thermoctl.domain.principal import Principal
from thermoctl.domain.schedule import (
    cancel_override,
    create_override,
    end_of_next_switch,
    resolved_setpoint,
    temperature_for_mode,
)
from thermoctl.domain.zone_settings import (
    ControlParameters,
    control_parameters,
    save_control_parameters,
)
from thermoctl.web.forms import FormError, form_again

# `include_in_schema=False`: Die OpenAPI-Beschreibung ist der Vertrag der
# REST-Schnittstelle. Diese Wege liefern HTML fuer Menschen, und in der Oberflaeche
# unter /docs stuende sonst neben jedem echten Endpunkt ein Formularweg, dessen
# 'Try it out' eine echte Aenderung ausloest.
router = APIRouter(dependencies=[Depends(csrf_schutz)], include_in_schema=False)

FELDER = (
    "hysteresis_k",
    "min_on_seconds",
    "min_off_seconds",
    "sensor_timeout_seconds",
    "temperature_offset_k",
    "window_resume_delay_seconds",
)
GANZZAHLEN = frozenset(FELDER) - {"hysteresis_k", "temperature_offset_k"}


def _zone_or_404(session: Session, principal: Principal, zone_id: int, permission: str) -> Zone:
    zone = next((z for z in visible_zones(session, principal, permission) if z.id == zone_id), None)
    if zone is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Zone nicht gefunden")
    return zone


def _parameterpage(
    request: Request,
    zone: Zone,
    wirksam: ControlParameters,
    values: dict[str, str],
    errors: FormError | None = None,
) -> Response:
    return form_again(
        request,
        "parameter.html",
        values,
        errors,
        zone=zone,
        wirksam=wirksam,
    )


@router.get("/zones/{zone_id}/parameters")
async def show_parameter(
    zone_id: int,
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    zone = _zone_or_404(session, principal, zone_id, "zone.manage")
    values = {
        name: str(getattr(zone, name)) if getattr(zone, name) is not None else "" for name in FELDER
    }
    return _parameterpage(request, zone, control_parameters(session, zone), values)


def _check_parameters(values: dict[str, str]) -> dict[str, Decimal | int | None]:
    result: dict[str, Decimal | int | None] = {}
    for name, text in values.items():
        if not text:
            result[name] = None
            continue
        try:
            value: Decimal | int = (
                int(text) if name in GANZZAHLEN else Decimal(text.replace(",", "."))
            )
        except (ValueError, InvalidOperation) as exc:
            raise FormError(name, "Bitte eine gültige Zahl eingeben.") from exc
        if name != "temperature_offset_k" and value < 0:
            raise FormError(name, "Dieser Wert darf nicht negativ sein.")
        result[name] = value
    return result


@router.post("/zones/{zone_id}/parameters")
async def save_parameter(
    zone_id: int,
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    zone = _zone_or_404(session, principal, zone_id, "zone.manage")
    form = await request.form()
    values = {name: str(form.get(name, "")).strip() for name in FELDER}
    try:
        checked = _check_parameters(values)
    except FormError as exc:
        return _parameterpage(request, zone, control_parameters(session, zone), values, exc)
    save_control_parameters(
        session, zone, checked, user_id=principal.user_id, token_id=principal.token_id
    )
    return RedirectResponse(f"/zones/{zone.id}/parameters", status.HTTP_303_SEE_OTHER)


@router.post("/zones/{zone_id}/override")
async def create_override_view(
    zone_id: int,
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    zone = _zone_or_404(session, principal, zone_id, "override.create")
    form = await request.form()
    temperature_text = str(form.get("temperature_c", "")).strip()
    kind = str(form.get("end", "dauerhaft"))
    try:
        # Nur noch die Zahl selbst. Die Grenze prueft `uebersteuerung_anlegen` weiter
        # unten -- sie stand hier ein zweites Mal, mit eigenen Zahlen, und haette beim
        # naechsten Verschieben abweichen muessen. Genau dieser Fehler ist dem Projekt
        # schon einmal passiert; die Meldung kommt aus der Domaene.
        temperature = Decimal(temperature_text.replace(",", "."))
        if kind == "naechste_schaltung":
            ende = end_of_next_switch(session, zone)
        elif kind == "dauer":
            duration = int(str(form.get("duration_minutes", "")))
            if duration <= 0:
                raise ValueError
            ende = utcnow() + timedelta(minutes=duration)
        elif kind == "dauerhaft":
            ende = None
        else:
            raise ValueError
    # Klammern, obwohl Python 3.14 sie nicht mehr verlangt (PEP 758): Ohne sie sieht die
    # Zeile aus wie die Python-2-Form, die etwas anderes bedeutete.
    except (InvalidOperation, ValueError):
        parameter = urlencode(
            {
                "uebersteuerungsfehler": "Bitte Temperatur, Art und Dauer prüfen.",
                "zone_id": zone.id,
                "temperature_c": temperature_text,
                "end": kind,
                "duration_minutes": str(form.get("duration_minutes", "")),
            }
        )
        return RedirectResponse(f"/?{parameter}", status.HTTP_303_SEE_OTHER)
    try:
        create_override(
            session, zone, temperature, ende,
            user_id=principal.user_id, token_id=principal.token_id,
        )
    except DomainError as exc:
        # Die Grenze liegt seit dem Abschlussreview in der Domaene, damit sie fuer alle
        # drei Adapter gilt. Hier wird sie nur noch angezeigt.
        parameter = urlencode(
            {
                "uebersteuerungsfehler": exc.notice,
                "zone_id": zone.id,
                "temperature_c": temperature_text,
                "end": kind,
                "duration_minutes": str(form.get("duration_minutes", "")),
            }
        )
        return RedirectResponse(f"/?{parameter}", status.HTTP_303_SEE_OTHER)
    return RedirectResponse("/", status.HTTP_303_SEE_OTHER)


@router.post("/zones/{zone_id}/override/cancel")
async def end_override(
    zone_id: int,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    zone = _zone_or_404(session, principal, zone_id, "override.cancel")
    cancel_override(session, zone)
    return RedirectResponse("/", status.HTTP_303_SEE_OTHER)


# Ein Klick auf dem Thermostat der Startseite. Eine halbe Stufe, weil ein Raum darunter
# nicht spuerbar anders wird und man sonst zu oft klickt.
THERMOSTAT_STEP = Decimal("0.5")


@router.post("/zones/{zone_id}/thermostat")
async def adjust_thermostat(
    zone_id: int,
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    """Verstellt die hinterlegte Temperatur des Modus, der gerade gilt.

    Das ist ausdruecklich **keine** Uebersteuerung: Wer hier drueckt, aendert den
    Sollwert des laufenden Modus dauerhaft -- "Tag soll ein halbes Grad waermer sein",
    nicht "jetzt einmal waermer". Beides ist ein alltaeglicher Wunsch, und beides mit
    demselben Bedienelement zu machen waere die sicherste Art, das Falsche zu treffen.
    Deshalb steht daneben, welcher Modus verstellt wird.

    Die Schrittweite wird hier auf den *aktuellen* Wert gerechnet und nicht im Browser:
    Zwei Klicks sind dann zwei Stufen, auch wenn die Seite dazwischen nicht neu geladen
    hat.
    """
    zone = _zone_or_404(session, principal, zone_id, "setpoint.write")
    form = await request.form()
    try:
        mode_id = int(str(form.get("mode_id", "")))
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Kein Modus angegeben") from exc

    richtung = str(form.get("direction", ""))
    if richtung not in ("hoch", "runter"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unbekannte Richtung")

    # Der Wert, den die Seite zeigt -- nicht die hinterlegte Zeile. Die beiden sind
    # nicht dasselbe: Hat eine Zone fuer den Frostschutz keinen eigenen Sollwert, zeigt
    # `aufgeloester_sollwert` den Notnagel von 16 Grad an. Das Thermostat suchte
    # bisher die Zeile, fand keine und antwortete mit 404 -- auf der Seite sah es aus,
    # als passiere beim Druecken nichts. Genau das ist der Zustand einer frisch
    # eingerichteten Anlage, in der noch niemand Sollwerte gepflegt hat.
    jetziger = temperature_for_mode(session, zone, mode_id)
    if jetziger is None:
        angezeigt = resolved_setpoint(session, zone, utcnow())
        if angezeigt.mode_id == mode_id:
            jetziger = angezeigt.temperature_c
    if jetziger is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Für diesen Modus gibt es keinen Sollwert")

    neu = jetziger + (THERMOSTAT_STEP if richtung == "hoch" else -THERMOSTAT_STEP)
    try:
        update_setpoints(
            session, zone, {mode_id: neu}, user_id=principal.user_id
        )
    except DomainError as exc:
        # An der Grenze angekommen. Kein Fehlerzustand, sondern das
        # Ende des Weges -- die Seite zeigt danach schlicht den unveraenderten Wert.
        parameter = urlencode({"thermostatfehler": exc.notice, "zone_id": zone.id})
        return RedirectResponse(f"/?{parameter}", status.HTTP_303_SEE_OTHER)
    return RedirectResponse("/", status.HTTP_303_SEE_OTHER)
