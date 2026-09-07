"""„Problem melden" -- der Weg von der Wohnung zum Betreiber.

Kein zweiter Zustellweg: die Meldung geht über dieselbe Meldekette wie jede
Störungsmeldung (`integrations/notification.py`, der vom Betreiber konfigurierte
Webhook) und steht danach im Audit.

Der Router trägt **keinen** Profil-Wächter: melden können soll auch, wer die
Anlagensicht benutzt. Abgesichert ist er über das eigene, zonenbezogene Recht
`report.create` -- ausdrücklich nicht über `zone.read`. Etwas nach außen auszulösen
darf nicht aus einem reinen Leserecht folgen; wer nur lesen darf, könnte sonst den
Webhook des Betreibers bedienen.
"""

from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from thermoctl import audit
from thermoctl.auth.dependencies import csrf_protection, current_principal, get_session
from thermoctl.config import get_settings
from thermoctl.db.base import utcnow
from thermoctl.db.models.identity import User
from thermoctl.db.models.zone import Zone
from thermoctl.domain.authz import visible_zones
from thermoctl.domain.control import settings as control_settings
from thermoctl.domain.fault_notice import notice_enabled
from thermoctl.domain.modes import DomainError
from thermoctl.domain.principal import Principal
from thermoctl.domain.problem_report import build_report
from thermoctl.integrations import notification
from thermoctl.web.urls import prefixed

# `include_in_schema=False`: wie jeder andere HTML-Router dieses Projekts.
router = APIRouter(dependencies=[Depends(csrf_protection)], include_in_schema=False)


def _zone_or_404(session: Session, principal: Principal, zone_id: int) -> Zone:
    """Dieselbe Auflösung wie überall: eine Zone, für die das Recht fehlt, gibt es
    hier schlicht nicht. 404 und nicht 403 -- der Unterschied zwischen „gibt es
    nicht" und „gehört jemand anderem" ist selbst eine Auskunft."""
    zone = next(
        (
            zone
            for zone in visible_zones(session, principal, "report.create")
            if zone.id == zone_id
        ),
        None,
    )
    if zone is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Zone nicht gefunden")
    return zone


@router.post("/zones/{zone_id}/report")
async def report_problem(
    zone_id: int,
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    """Nimmt die Meldung auf, schreibt sie ins Audit und schickt sie hinaus.

    **Die Reihenfolge ist Absicht.** Der Audit-Eintrag entsteht immer, der
    Zustellversuch kann scheitern. Eine Meldung, die im Webhook hängen bleibt und
    deshalb nirgends steht, wäre für den Mieter dasselbe wie eine, die er nie
    abgeschickt hat -- und für den Betreiber unauffindbar.

    Ist kein Webhook eingerichtet oder der Schalter aus, wird ehrlich genau das
    gesagt: aufgenommen, aber keine automatische Weiterleitung. Ausdrücklich kein
    stiller Erfolg -- eine Schaltfläche, die nichts tut und trotzdem „gesendet"
    meldet, ist die Attrappe, die diese Funktion nicht sein soll.
    """
    zone = _zone_or_404(session, principal, zone_id)
    form = await request.form()
    kind = str(form.get("kind", ""))
    note = str(form.get("note", ""))
    user_record = session.get(User, principal.user_id)

    try:
        notice = build_report(
            session, zone, kind, note, now=utcnow(), user=user_record
        )
    except DomainError as exc:
        return _back(request, {"report_errors": exc.notice, "zone_id": zone.id})

    audit.record(
        session,
        source="web",
        action="report.created",
        object_type="zone",
        object_id=str(zone.id),
        summary=notice.title,
        detail=notice.text,
        user_id=principal.user_id,
        token_id=principal.token_id,
    )

    env_settings = get_settings()
    row = control_settings(session)
    if env_settings.notify_webhook is None or not notice_enabled(notice.kind, row):
        return _back(
            request,
            {
                "report_notice": (
                    "Die Meldung wurde aufgenommen. Eine automatische Weiterleitung "
                    "ist für diese Anlage nicht eingerichtet."
                ),
                "zone_id": zone.id,
            },
        )
    await notification.deliver(
        request.app.state.session_factory, env_settings, notice
    )
    return _back(
        request, {"report_notice": "Die Meldung wurde weitergegeben.", "zone_id": zone.id}
    )


def _back(request: Request, parameter: dict[str, object]) -> Response:
    """Zurück auf die Startseite, mit der Rückmeldung im Abfrageparameter -- dasselbe
    Muster, das Übersteuerung und Abwesenheit schon benutzen, damit ein Neuladen die
    Meldung nicht ein zweites Mal auslöst."""
    return RedirectResponse(
        prefixed(request, "/?" + urlencode(parameter)), status.HTTP_303_SEE_OTHER
    )
