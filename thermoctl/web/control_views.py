"""Zwei Seiten, die dieselbe Einstellungszeile bedienen -- und trotzdem getrennt gehoeren.

`/steuerung` ist **Betrieb**: Schaltet die Anlage gerade wirklich, was entscheidet sie
gerade, und der Knopf, der beides umlegt. Das sieht man sich an, wenn etwas nicht stimmt.

`/einstellungen` sind die **Regelvorgaben**: Hysterese, Mindestschaltdauern, Zykluszeit,
Aufbewahrung, Zeitzone. Die stellt man einmal ein und dann jahrelang nicht mehr.

Zuerst standen beide auf einer Seite. Das war bequem zu bauen und falsch zu benutzen: Wer
nachsehen wollte, ob die Anlage scharf ist, scrollte an neun Zahlenfeldern vorbei, die ihn
in dem Moment nicht interessierten -- und wer eine Vorgabe aendern wollte, landete zuerst
beim Scharfschalt-Knopf.
"""

from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.auth.dependencies import aktueller_principal, csrf_schutz, get_session
from thermoctl.config import get_settings
from thermoctl.db.base import utcnow
from thermoctl.db.models.lookup import SensorStatus
from thermoctl.db.models.state import ShadowDecision, ZoneState
from thermoctl.domain.authz import has_permission, require, visible_zones
from thermoctl.domain.control import (
    GANZZAHLIG,
    LABELS,
    LIMITS,
    ControlError,
    arm,
    save_settings,
    settings,
)
from thermoctl.domain.interfaces import uebersicht
from thermoctl.domain.principal import Principal
from thermoctl.domain.schedule import resolved_setpoint
from thermoctl.domain.statistics import as_duration, heizzeiten
from thermoctl.web import templates

# `include_in_schema=False`: Die OpenAPI-Beschreibung ist der Vertrag der
# REST-Schnittstelle. Diese Wege liefern HTML fuer Menschen, und in der Oberflaeche
# unter /docs stuende sonst neben jedem echten Endpunkt ein Formularweg, dessen
# 'Try it out' eine echte Aenderung ausloest.
router = APIRouter(dependencies=[Depends(csrf_schutz)], include_in_schema=False)


def _page(
    request: Request,
    session: Session,
    principal: Principal,
    *,
    errors: ControlError | None = None,
) -> Response:
    zeile = settings(session)
    zones = visible_zones(session, principal, "zone.read")
    now = utcnow()

    zustaende = {
        zone_id: (state, status)
        for zone_id, state, status in session.execute(
            select(ZoneState.zone_id, ZoneState, SensorStatus)
            .join(SensorStatus, SensorStatus.id == ZoneState.sensor_status_id)
            .where(ZoneState.zone_id.in_([zone.id for zone in zones]))
        )
    }
    entscheidungen: dict[int, ShadowDecision] = {}
    for entscheidung in session.scalars(
        select(ShadowDecision)
        .where(ShadowDecision.zone_id.in_([zone.id for zone in zones]))
        .order_by(ShadowDecision.decided_at.desc(), ShadowDecision.id.desc())
    ):
        entscheidungen.setdefault(entscheidung.zone_id, entscheidung)

    return templates.TemplateResponse(
        request,
        "steuerung.html",
        {
            "settings": zeile,
            "zones": zones,
            "zustaende": zustaende,
            "entscheidungen": entscheidungen,
            "setpoints": {
                zone.id: resolved_setpoint(session, zone, now) for zone in zones
            },
            "errors": {errors.feld: errors.notice} if errors else {},
            "darf_scharf": has_permission(principal, "control.arm"),
            # Der erste Riegel sitzt im Konstruktor des MQTT-Clients und wird beim Start
            # aus der Datenbank gelesen. Wer im laufenden Betrieb scharf schaltet, hat
            # damit einen Zustand, in dem die Anlage scharf entscheidet und trotzdem
            # nichts sendet. Das ist beabsichtigt -- aber es muss dastehen, sonst sucht
            # jemand stundenlang den Fehler.
            "sending_allowed": getattr(request.app.state, "sending_allowed", False),
        },
    )


def _defaults_page(
    request: Request,
    session: Session,
    principal: Principal,
    *,
    values: dict[str, str] | None = None,
    errors: ControlError | None = None,
) -> Response:
    zeile = settings(session)
    if values is None:
        values = {feld: str(getattr(zeile, feld)) for feld in LIMITS}
        values["timezone"] = zeile.timezone
    return templates.TemplateResponse(
        request,
        "einstellungen.html",
        {
            "felder": [(feld, LABELS[feld], feld in GANZZAHLIG) for feld in LIMITS],
            "values": values,
            "errors": {errors.feld: errors.notice} if errors else {},
            "darf_aendern": has_permission(principal, "setting.manage"),
        },
    )


@router.get("/control")
async def show_control(
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    # Lesen darf, wer die Anlage sehen darf. Der Betriebszustand ist die Antwort auf
    # "schaltet das Ding gerade wirklich?" -- diese Frage soll niemand raten muessen.
    require(principal, "zone.read")
    return _page(request, session, principal)


@router.get("/settings")
async def show_settings(
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    require(principal, "zone.read")
    return _defaults_page(request, session, principal)


@router.post("/settings")
async def save_defaults(
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    require(principal, "setting.manage")
    form = await request.form()
    values = {
        name: str(form.get(name, "")).strip()
        for name in (*LIMITS, "timezone")
    }
    try:
        save_settings(
            session,
            values,
            values["timezone"],
            user_id=principal.user_id,
            token_id=principal.token_id,
        )
    except ControlError as exc:
        return _defaults_page(request, session, principal, values=values, errors=exc)
    return RedirectResponse("/settings", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/control/arm")
async def arm_view(
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    # Eigenes Recht, nicht `setting.manage`: Das hier bewegt ein Ventil.
    require(principal, "control.arm")
    form = await request.form()
    armed = str(form.get("armed", "")) == "ja"
    try:
        arm(
            session,
            armed,
            reason=str(form.get("begruendung", "")),
            user_id=principal.user_id,
            token_id=principal.token_id,
        )
    except ControlError as exc:
        return _page(request, session, principal, errors=exc)
    return RedirectResponse("/control", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/interfaces")
async def show_interfaces(
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    """Was von aussen angebunden ist -- und ob es wirklich laeuft.

    `setting.manage`, nicht `zone.read`: Die Seite nennt Broker-Adressen, Webhook-Ziele
    und Kontonamen. Nichts davon ist ein Geheimnis im engeren Sinn, aber es ist auch
    nichts, was jeder Bediener der Heizung sehen muss.
    """
    require(principal, "setting.manage")
    return templates.TemplateResponse(
        request,
        "schnittstellen.html",
        {
            "interfaces": uebersicht(
                session,
                get_settings(),
                getattr(request.app.state, "bridge_reachable", None),
            ),
        },
    )


# Zeitraeume, die man wirklich wissen will. Kein freies Datumsfeld: Die Frage lautet
# "diese Woche" oder "diesen Monat", nicht "vom 14. bis zum 23.".
ZEITRAEUME: dict[str, tuple[str, int]] = {
    "7": ("7 Tage", 7),
    "30": ("30 Tage", 30),
    "90": ("90 Tage", 90),
}


@router.get("/statistics")
async def show_statistics(
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    """Wann und wie lange geheizt wurde, je Zone und Tag.

    Im Trockenlauf ist das eine Aussage darueber, was thermoctl geheizt *haette* -- die
    Seite sagt das auch, statt eine Zahl hinzustellen, die man fuer die Vergangenheit der
    Anlage haelt.
    """
    require(principal, "zone.read")
    zones = visible_zones(session, principal, "zone.read")
    zeile = settings(session)

    schluessel = request.query_params.get("zeitraum", "7")
    if schluessel not in ZEITRAEUME:
        schluessel = "7"
    _label, days = ZEITRAEUME[schluessel]

    bis = utcnow()
    von = (bis - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    values = heizzeiten(
        session,
        [zone.id for zone in zones],
        von,
        bis,
        cycle_seconds=zeile.shadow_interval_seconds,
    )
    # Der laengste Tageswert ueberhaupt bestimmt die Hoehe der Balken. Je Zone zu
    # skalieren waere bequemer zu lesen und faelscht den Vergleich zwischen Zonen --
    # und genau der ist der Grund, warum die Zonen untereinander stehen.
    maximum = max(
        (t.seconds for stat in values.values() for t in stat.days), default=0
    )
    return templates.TemplateResponse(
        request,
        "statistik.html",
        {
            "zones": zones,
            "values": values,
            "maximum": maximum,
            "zeitraeume": [(s, b) for s, (b, _t) in ZEITRAEUME.items()],
            "zeitraum": schluessel,
            "armed": zeile.control_armed,
            "as_duration": as_duration,
        },
    )
