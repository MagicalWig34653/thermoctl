"""Die Startseite: eine Statustafel, keine Kennzahlensammlung.

Sie beantwortet genau eine Frage -- *tut das Haus gerade das, was ich ihm gesagt habe?*
-- und zwar fuer alle Zonen auf einmal. Alles, was diese Frage nicht beantwortet, gehoert
woandershin.

Frueher standen hier zwei Zaehlkacheln: die Zahl der Zonen und die Zahl der **Benutzer**.
Wie viele Konten es gibt, sagt ueber eine Heizung nichts; die Zahl stand da, weil sie
leicht zu ermitteln war. Beide sind weg.

Anders als die geschuetzten Verwaltungsseiten antwortet die Seite einem nicht angemeldeten
Besucher nicht mit 401, sondern leitet auf die Anmeldung weiter: Wer die Adresse des
Dienstes im Browser eingibt, soll ein Anmeldeformular sehen und keine Fehlermeldung.
"""

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session
from starlette.responses import Response

from thermoctl.auth.dependencies import csrf_schutz, get_session
from thermoctl.auth.sessions import COOKIE_NAME, sitzung_aufloesen
from thermoctl.db.base import utcnow
from thermoctl.db.models.identity import User
from thermoctl.db.models.lookup import SensorStatus
from thermoctl.db.models.operations import Setting
from thermoctl.db.models.override import ZoneOverride
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.zone import SetpointMode, ZoneSetpoint
from thermoctl.db.models.zustand import ShadowDecision, ZoneState
from thermoctl.domain.authz import hat_recht, principal_fuer_benutzer, visible_zones
from thermoctl.domain.schedule import aufgeloester_sollwert, wochenabschnitte
from thermoctl.setup import einrichtung_noetig
from thermoctl.web import templates, waermeanteil

# `include_in_schema=False`: Die OpenAPI-Beschreibung ist der Vertrag der
# REST-Schnittstelle. Diese Wege liefern HTML fuer Menschen, und in der Oberflaeche
# unter /docs stuende sonst neben jedem echten Endpunkt ein Formularweg, dessen
# 'Try it out' eine echte Aenderung ausloest.
router = APIRouter(dependencies=[Depends(csrf_schutz)], include_in_schema=False)


MINUTEN_PRO_TAG = 1440


def _tagesspur(
    session: Session, zone_ids: list[int], wochentag: int
) -> dict[int, list[dict[str, object]]]:
    """Der heutige Zeitplan je Zone als Abschnitte mit Anteil, Zeit und Solltemperatur.

    Dieselbe Zerlegung wie die Wochenansicht (`wochenabschnitte`), nur auf einen Tag
    beschraenkt -- eine zweite Fassung derselben Logik im Browser waere genau das, was
    Grundsatz 6 verbietet.
    """
    if not zone_ids:
        return {}
    modi = {m.id: m for m in session.scalars(select(SetpointMode))}
    namen = {kennung: modus.name for kennung, modus in modi.items()}
    temperaturen: dict[tuple[int, int], Decimal] = {
        (zone_id, modus_id): temperatur
        for zone_id, modus_id, temperatur in session.execute(
            select(
                ZoneSetpoint.zone_id,
                ZoneSetpoint.setpoint_mode_id,
                ZoneSetpoint.temperature_c,
            ).where(ZoneSetpoint.zone_id.in_(zone_ids))
        )
    }
    punkte_je_zone: dict[int, list[SchedulePoint]] = {zone_id: [] for zone_id in zone_ids}
    for punkt in session.scalars(
        select(SchedulePoint).where(SchedulePoint.zone_id.in_(zone_ids))
    ):
        punkte_je_zone[punkt.zone_id].append(punkt)

    spuren: dict[int, list[dict[str, object]]] = {}
    for zone_id, punkte in punkte_je_zone.items():
        abschnitte = [
            a for a in wochenabschnitte(punkte, namen) if a.wochentag == wochentag
        ]
        spuren[zone_id] = [
            {
                "start": abschnitt.startminute,
                "breite": (abschnitt.endminute - abschnitt.startminute)
                * 100
                / MINUTEN_PRO_TAG,
                "links": abschnitt.startminute * 100 / MINUTEN_PRO_TAG,
                "modusname": abschnitt.modusname,
                "temperatur": temperaturen.get((zone_id, abschnitt.modus_id)),
                "waerme": waermeanteil(temperaturen.get((zone_id, abschnitt.modus_id))),
            }
            for abschnitt in abschnitte
        ]
    return spuren


@router.get("/")
def start(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    # Vor der Anmeldung: Solange es keinen einzigen Benutzer gibt, kann sich niemand
    # anmelden. Ein Anmeldeformular waere dann eine Sackgasse -- wer die Adresse des
    # Dienstes eingibt, gehoert zur Einrichtung. Dass die Weiterleitung den leeren
    # Zustand verraet, ist kein Zugewinn fuer einen Angreifer: /setup antwortet ohnehin
    # sichtbar anders als nach abgeschlossener Einrichtung, und die Einrichtung selbst
    # haengt am Einmal-Token aus dem Log, nicht an der Erreichbarkeit der Seite.
    if einrichtung_noetig(session):
        return RedirectResponse("/setup", status_code=303)

    cookie_wert = request.cookies.get(COOKIE_NAME)
    sitzung = sitzung_aufloesen(session, cookie_wert) if cookie_wert else None
    benutzer = session.get(User, sitzung.user_id) if sitzung else None
    if benutzer is None or not benutzer.is_active:
        return RedirectResponse("/login", status_code=303)

    request.state.benutzer = benutzer
    principal = principal_fuer_benutzer(session, benutzer)
    sichtbare_zonen = visible_zones(session, principal, "zone.read")
    jetzt = utcnow()
    einstellungen = session.get(Setting, 1)
    zustaende = {
        zone_id: (zustand, sensorstatus)
        for zone_id, zustand, sensorstatus in session.execute(
            select(ZoneState.zone_id, ZoneState, SensorStatus)
            .join(SensorStatus, SensorStatus.id == ZoneState.sensor_status_id)
            .where(ZoneState.zone_id.in_([zone.id for zone in sichtbare_zonen]))
        )
    }
    zone_ids = [zone.id for zone in sichtbare_zonen]
    uebersteuerungen: dict[int, ZoneOverride] = {}
    for eintrag in session.scalars(
        select(ZoneOverride)
        .where(
            ZoneOverride.zone_id.in_(zone_ids),
            ZoneOverride.cancelled_at.is_(None),
            ZoneOverride.starts_at <= jetzt,
            or_(ZoneOverride.ends_at.is_(None), ZoneOverride.ends_at > jetzt),
        )
        .order_by(ZoneOverride.created_at.desc())
    ):
        uebersteuerungen.setdefault(eintrag.zone_id, eintrag)
    entscheidungen: dict[int, ShadowDecision] = {}
    for entscheidung in session.scalars(
        select(ShadowDecision)
        .where(ShadowDecision.zone_id.in_(zone_ids))
        .order_by(ShadowDecision.decided_at.desc(), ShadowDecision.id.desc())
    ):
        entscheidungen.setdefault(entscheidung.zone_id, entscheidung)

    return templates.TemplateResponse(
        request,
        "start.html",
        {
            "benutzer": benutzer,
            "zonen": sichtbare_zonen,
            "zustaende": zustaende,
            "sollwerte": {
                zone.id: aufgeloester_sollwert(session, zone, jetzt) for zone in sichtbare_zonen
            },
            "uebersteuerungen": uebersteuerungen,
            "entscheidungen": entscheidungen,
            "darf_uebersteuern": {
                zone.id
                for zone in sichtbare_zonen
                if hat_recht(principal, "override.create", zone.id)
            },
            "darf_aufheben": {
                zone.id
                for zone in sichtbare_zonen
                if hat_recht(principal, "override.cancel", zone.id)
            },
            "darf_parameter": {
                zone.id for zone in sichtbare_zonen if hat_recht(principal, "zone.manage", zone.id)
            },
            "uebersteuerungsfehler": request.query_params.get("uebersteuerungsfehler"),
            "fehler_zone_id": request.query_params.get("zone_id"),
            "uebersteuerungswerte": request.query_params,
            # Die Anlage in einem Satz: Schaltet sie wirklich, laeuft die Bruecke, und
            # gibt es Sensoren, die schweigen? Genau die drei Dinge, die eine Anzeige
            # unglaubwuerdig machen, wenn man sie nicht kennt.
            "scharf": bool(einstellungen and einstellungen.control_armed),
            "bruecke": getattr(request.app.state, "bruecke_erreichbar", None),
            "stumme_sensoren": [
                zone.display_name
                for zone in sichtbare_zonen
                if zone.id in zustaende and zustaende[zone.id][1].code != "ok"
            ],
            "tagesspuren": _tagesspur(
                session, zone_ids, jetzt.isoweekday()
            ),
            "jetzt_anteil": (jetzt.hour * 60 + jetzt.minute) * 100 / MINUTEN_PRO_TAG,
        },
    )
