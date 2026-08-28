from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.auth.dependencies import aktueller_principal, get_session
from thermoctl.db.models.credential import ApiToken
from thermoctl.db.models.identity import AccessGroup, User
from thermoctl.domain.authz import Forbidden, require
from thermoctl.domain.principal import Principal
from thermoctl.web import templates

router = APIRouter()


def _verboten(fehler: Forbidden) -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(fehler))


@router.get("/benutzer")
async def benutzerliste(
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    try:
        require(principal, "user.manage")
    except Forbidden as fehler:
        raise _verboten(fehler) from fehler
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
    try:
        require(principal, "group.manage")
    except Forbidden as fehler:
        raise _verboten(fehler) from fehler
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
    try:
        require(principal, "token.self")
    except Forbidden as fehler:
        raise _verboten(fehler) from fehler
    token = session.scalars(
        select(ApiToken).where(ApiToken.user_id == principal.user_id).order_by(ApiToken.name)
    ).all()
    return templates.TemplateResponse(
        request, "tokens.html", {"token": token, "ist_htmx": "HX-Request" in request.headers}
    )
