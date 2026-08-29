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
from thermoctl.domain.modi import Domaenenfehler
from thermoctl.domain.principal import Principal
from thermoctl.domain.schedule import (
    ende_der_naechsten_schaltung,
    uebersteuerung_anlegen,
    uebersteuerung_aufheben,
)
from thermoctl.domain.zone_settings import Regelparameter, regelparameter, regelparameter_speichern
from thermoctl.web.formulare import Formularfehler, formular_erneut

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


def _zone_oder_404(session: Session, principal: Principal, zone_id: int, recht: str) -> Zone:
    zone = next((z for z in visible_zones(session, principal, recht) if z.id == zone_id), None)
    if zone is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Zone nicht gefunden")
    return zone


def _parameterseite(
    request: Request,
    zone: Zone,
    wirksam: Regelparameter,
    werte: dict[str, str],
    fehler: Formularfehler | None = None,
) -> Response:
    return formular_erneut(
        request,
        "parameter.html",
        werte,
        fehler,
        zone=zone,
        wirksam=wirksam,
    )


@router.get("/zonen/{zone_id}/parameter")
async def parameter_anzeigen(
    zone_id: int,
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    zone = _zone_oder_404(session, principal, zone_id, "zone.manage")
    werte = {
        name: str(getattr(zone, name)) if getattr(zone, name) is not None else "" for name in FELDER
    }
    return _parameterseite(request, zone, regelparameter(session, zone), werte)


def _parameter_pruefen(werte: dict[str, str]) -> dict[str, Decimal | int | None]:
    ergebnis: dict[str, Decimal | int | None] = {}
    for name, text in werte.items():
        if not text:
            ergebnis[name] = None
            continue
        try:
            wert: Decimal | int = (
                int(text) if name in GANZZAHLEN else Decimal(text.replace(",", "."))
            )
        except (ValueError, InvalidOperation) as exc:
            raise Formularfehler(name, "Bitte eine gültige Zahl eingeben.") from exc
        if name != "temperature_offset_k" and wert < 0:
            raise Formularfehler(name, "Dieser Wert darf nicht negativ sein.")
        ergebnis[name] = wert
    return ergebnis


@router.post("/zonen/{zone_id}/parameter")
async def parameter_speichern(
    zone_id: int,
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    zone = _zone_oder_404(session, principal, zone_id, "zone.manage")
    formular = await request.form()
    werte = {name: str(formular.get(name, "")).strip() for name in FELDER}
    try:
        geprueft = _parameter_pruefen(werte)
    except Formularfehler as exc:
        return _parameterseite(request, zone, regelparameter(session, zone), werte, exc)
    regelparameter_speichern(
        session, zone, geprueft, user_id=principal.user_id, token_id=principal.token_id
    )
    return RedirectResponse(f"/zonen/{zone.id}/parameter", status.HTTP_303_SEE_OTHER)


@router.post("/zonen/{zone_id}/uebersteuerung")
async def uebersteuerung_erstellen(
    zone_id: int,
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    zone = _zone_oder_404(session, principal, zone_id, "override.create")
    formular = await request.form()
    temperatur_text = str(formular.get("temperature_c", "")).strip()
    art = str(formular.get("ende", "dauerhaft"))
    try:
        temperatur = Decimal(temperatur_text.replace(",", "."))
        if temperatur < Decimal("5") or temperatur > Decimal("35"):
            raise InvalidOperation
        if art == "naechste_schaltung":
            ende = ende_der_naechsten_schaltung(session, zone)
        elif art == "dauer":
            dauer = int(str(formular.get("dauer_minuten", "")))
            if dauer <= 0:
                raise ValueError
            ende = utcnow() + timedelta(minutes=dauer)
        elif art == "dauerhaft":
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
                "temperature_c": temperatur_text,
                "ende": art,
                "dauer_minuten": str(formular.get("dauer_minuten", "")),
            }
        )
        return RedirectResponse(f"/?{parameter}", status.HTTP_303_SEE_OTHER)
    try:
        uebersteuerung_anlegen(
            session, zone, temperatur, ende,
            user_id=principal.user_id, token_id=principal.token_id,
        )
    except Domaenenfehler as exc:
        # Die Grenze liegt seit dem Abschlussreview in der Domaene, damit sie fuer alle
        # drei Adapter gilt. Hier wird sie nur noch angezeigt.
        parameter = urlencode(
            {
                "uebersteuerungsfehler": exc.meldung,
                "zone_id": zone.id,
                "temperature_c": temperatur_text,
                "ende": art,
                "dauer_minuten": str(formular.get("dauer_minuten", "")),
            }
        )
        return RedirectResponse(f"/?{parameter}", status.HTTP_303_SEE_OTHER)
    return RedirectResponse("/", status.HTTP_303_SEE_OTHER)


@router.post("/zonen/{zone_id}/uebersteuerung/aufheben")
async def uebersteuerung_beenden(
    zone_id: int,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    zone = _zone_oder_404(session, principal, zone_id, "override.cancel")
    uebersteuerung_aufheben(session, zone)
    return RedirectResponse("/", status.HTTP_303_SEE_OTHER)
