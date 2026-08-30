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
from thermoctl.web import ist_teilaustausch, templates

# `include_in_schema=False`: Die OpenAPI-Beschreibung ist der Vertrag der
# REST-Schnittstelle. Diese Wege liefern HTML fuer Menschen, und in der Oberflaeche
# unter /docs stuende sonst neben jedem echten Endpunkt ein Formularweg, dessen
# 'Try it out' eine echte Aenderung ausloest.
router = APIRouter(dependencies=[Depends(csrf_schutz)], include_in_schema=False)

ENTRIES_PER_PAGE = 50


def _datum(value: str, feld: str, errors: dict[str, str]) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        errors[feld] = "Bitte ein gültiges Datum eingeben."
        return None


@router.get("/audit")
async def audit_list(
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
    from_date: str = "",
    to_date: str = "",
    user: str = "",
    action_code: str = "",
    object: str = "",  # noqa: A002 - Abfrageparameter, nicht die eingebaute Funktion
    source: str = "",
    page: str = "1",
) -> Response:
    require(principal, "audit.read")

    errors: dict[str, str] = {}
    from_day = _datum(from_date, "from_date", errors)
    to_day = _datum(to_date, "to_date", errors)
    if from_day is not None and to_day is not None and to_day < from_day:
        errors["to_date"] = "Das Bis-Datum darf nicht vor dem Von-Datum liegen."
    try:
        pagennummer = max(1, int(page))
    except ValueError:
        pagennummer = 1
        errors["page"] = "Die Seitennummer muss eine ganze Zahl sein."

    abfrage = (
        select(AuditEvent, ActorSource, User, ApiToken)
        .join(ActorSource, ActorSource.id == AuditEvent.source_id)
        .outerjoin(User, User.id == AuditEvent.actor_user_id)
        .outerjoin(ApiToken, ApiToken.id == AuditEvent.actor_token_id)
    )
    if not errors:
        if from_day is not None:
            abfrage = abfrage.where(
                AuditEvent.occurred_at >= datetime.combine(from_day, time.min)
            )
        if to_day is not None:
            # Exklusive Grenze am Folgetag schliesst den gesamten Bis-Tag ein und
            # vermeidet datenbankspezifische Datumsfunktionen.
            next_day = datetime.combine(to_day, time.min) + timedelta(days=1)
            abfrage = abfrage.where(AuditEvent.occurred_at < next_day)
        if user:
            abfrage = abfrage.where(
                or_(User.username == user, ApiToken.name == user)
            )
        if action_code:
            abfrage = abfrage.where(AuditEvent.action == action_code)
        if source:
            abfrage = abfrage.where(ActorSource.code == source)
        if object:
            objekttyp, trennzeichen, objekt_id = object.partition(":")
            abfrage = abfrage.where(AuditEvent.object_type == objekttyp)
            if trennzeichen:
                abfrage = abfrage.where(AuditEvent.object_id == objekt_id)

    entries: Sequence[Row[tuple[AuditEvent, ActorSource, User, ApiToken]]] = ()
    hat_weitere = False
    if not errors:
        zeilen = session.execute(
            abfrage.order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc())
            .offset((pagennummer - 1) * ENTRIES_PER_PAGE)
            .limit(ENTRIES_PER_PAGE + 1)
        ).all()
        hat_weitere = len(zeilen) > ENTRIES_PER_PAGE
        entries = zeilen[:ENTRIES_PER_PAGE]

    sources = session.execute(
        select(ActorSource.code, ActorSource.label).order_by(ActorSource.label)
    ).all()
    aktionen = session.scalars(
        select(AuditEvent.action).distinct().order_by(AuditEvent.action)
    ).all()
    filter_values = {
        "from_date": from_date,
        "to_date": to_date,
        "user": user,
        "action_code": action_code,
        "object": object,
        "source": source,
    }
    return templates.TemplateResponse(
        request,
        "audit.html",
        {
            "entries": entries,
            "sources": sources,
            "aktionen": aktionen,
            "filter": filter_values,
            "errors": errors,
            "page": pagennummer,
            "hat_weitere": hat_weitere,
            "basis_parameter": urlencode(filter_values),
            "ist_htmx": ist_teilaustausch(request),
        },
    )
