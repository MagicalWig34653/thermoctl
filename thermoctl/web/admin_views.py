from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.auth.dependencies import aktueller_principal, get_session
from thermoctl.db.models.credential import ApiToken
from thermoctl.db.models.identity import AccessGroup, User
from thermoctl.domain.authz import require
from thermoctl.domain.principal import Principal
from thermoctl.web import templates

router = APIRouter()

# `require()` wirft bei fehlendem Recht `Forbidden` -- der globale Handler in
# `thermoctl/app.py` uebersetzt das einheitlich in 403. Keine Route hier faengt
# das mehr selbst ab: das war vor dem Abschlussreview an dieser Stelle noch der
# Fall und wurde bewusst entfernt, um es nicht an jeder Route erneut zu vergessen.


@router.get("/benutzer")
async def benutzerliste(
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    require(principal, "user.manage")
    benutzer = session.scalars(select(User).order_by(User.username)).all()
    return templates.TemplateResponse(
        request,
        "benutzer.html",
        {"benutzer": benutzer, "ist_htmx": "HX-Request" in request.headers},
    )


@router.get("/gruppen")
async def gruppenliste(
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    require(principal, "group.manage")
    gruppen = session.scalars(select(AccessGroup).order_by(AccessGroup.name)).all()
    return templates.TemplateResponse(
        request, "gruppen.html", {"gruppen": gruppen, "ist_htmx": "HX-Request" in request.headers}
    )


@router.get("/tokens")
async def tokenliste(
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    require(principal, "token.self")
    token = session.scalars(
        select(ApiToken).where(ApiToken.user_id == principal.user_id).order_by(ApiToken.name)
    ).all()
    return templates.TemplateResponse(
        request, "tokens.html", {"token": token, "ist_htmx": "HX-Request" in request.headers}
    )
