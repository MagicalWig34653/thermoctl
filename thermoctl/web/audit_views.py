from collections.abc import Sequence
from datetime import date, datetime, time, timedelta
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import or_, select
from sqlalchemy.engine import Row
from sqlalchemy.orm import Session

from thermoctl.auth.dependencies import aktueller_principal, csrf_schutz, get_session
from thermoctl.db.models.credential import ApiToken
from thermoctl.db.models.identity import User
from thermoctl.db.models.lookup import ActorSource
from thermoctl.db.models.operations import AuditEvent
from thermoctl.domain.authz import require
from thermoctl.domain.principal import Principal
from thermoctl.web import templates

router = APIRouter(dependencies=[Depends(csrf_schutz)])

EINTRAEGE_JE_SEITE = 50


def _datum(wert: str, feld: str, fehler: dict[str, str]) -> date | None:
    if not wert:
        return None
    try:
        return date.fromisoformat(wert)
    except ValueError:
        fehler[feld] = "Bitte ein gültiges Datum eingeben."
        return None


@router.get("/audit")
async def auditliste(
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
    von: str = "",
    bis: str = "",
    benutzer: str = "",
    aktion: str = "",
    objekt: str = "",
    quelle: str = "",
    seite: str = "1",
) -> Response:
    require(principal, "audit.read")

    fehler: dict[str, str] = {}
    von_datum = _datum(von, "von", fehler)
    bis_datum = _datum(bis, "bis", fehler)
    if von_datum is not None and bis_datum is not None and bis_datum < von_datum:
        fehler["bis"] = "Das Bis-Datum darf nicht vor dem Von-Datum liegen."
    try:
        seitennummer = max(1, int(seite))
    except ValueError:
        seitennummer = 1
        fehler["seite"] = "Die Seitennummer muss eine ganze Zahl sein."

    abfrage = (
        select(AuditEvent, ActorSource, User, ApiToken)
        .join(ActorSource, ActorSource.id == AuditEvent.source_id)
        .outerjoin(User, User.id == AuditEvent.actor_user_id)
        .outerjoin(ApiToken, ApiToken.id == AuditEvent.actor_token_id)
    )
    if not fehler:
        if von_datum is not None:
            abfrage = abfrage.where(
                AuditEvent.occurred_at >= datetime.combine(von_datum, time.min)
            )
        if bis_datum is not None:
            # Exklusive Grenze am Folgetag schliesst den gesamten Bis-Tag ein und
            # vermeidet datenbankspezifische Datumsfunktionen.
            folgetag = datetime.combine(bis_datum, time.min) + timedelta(days=1)
            abfrage = abfrage.where(AuditEvent.occurred_at < folgetag)
        if benutzer:
            abfrage = abfrage.where(
                or_(User.username == benutzer, ApiToken.name == benutzer)
            )
        if aktion:
            abfrage = abfrage.where(AuditEvent.action == aktion)
        if quelle:
            abfrage = abfrage.where(ActorSource.code == quelle)
        if objekt:
            objekttyp, trennzeichen, objekt_id = objekt.partition(":")
            abfrage = abfrage.where(AuditEvent.object_type == objekttyp)
            if trennzeichen:
                abfrage = abfrage.where(AuditEvent.object_id == objekt_id)

    eintraege: Sequence[Row[tuple[AuditEvent, ActorSource, User, ApiToken]]] = ()
    hat_weitere = False
    if not fehler:
        zeilen = session.execute(
            abfrage.order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc())
            .offset((seitennummer - 1) * EINTRAEGE_JE_SEITE)
            .limit(EINTRAEGE_JE_SEITE + 1)
        ).all()
        hat_weitere = len(zeilen) > EINTRAEGE_JE_SEITE
        eintraege = zeilen[:EINTRAEGE_JE_SEITE]

    quellen = session.execute(
        select(ActorSource.code, ActorSource.label).order_by(ActorSource.label)
    ).all()
    aktionen = session.scalars(
        select(AuditEvent.action).distinct().order_by(AuditEvent.action)
    ).all()
    filterwerte = {
        "von": von,
        "bis": bis,
        "benutzer": benutzer,
        "aktion": aktion,
        "objekt": objekt,
        "quelle": quelle,
    }
    return templates.TemplateResponse(
        request,
        "audit.html",
        {
            "eintraege": eintraege,
            "quellen": quellen,
            "aktionen": aktionen,
            "filter": filterwerte,
            "fehler": fehler,
            "seite": seitennummer,
            "hat_weitere": hat_weitere,
            "basis_parameter": urlencode(filterwerte),
            "ist_htmx": "HX-Request" in request.headers,
        },
    )
