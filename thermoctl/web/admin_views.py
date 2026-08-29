from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.auth.dependencies import aktueller_principal, csrf_schutz, get_session
from thermoctl.auth.passwords import PasswordTooShort
from thermoctl.auth.tokens import token_ausstellen
from thermoctl.db.base import utcnow
from thermoctl.db.models.credential import ApiToken
from thermoctl.db.models.identity import AccessGroup, GroupPermission, User
from thermoctl.db.models.lookup import Permission
from thermoctl.db.models.zone import Zone
from thermoctl.domain.authz import Forbidden, require
from thermoctl.domain.principal import Principal
from thermoctl.domain.verwaltung import (
    Verwaltungsfehler,
    benutzer_aktiv_setzen,
    benutzer_anlegen,
    gruppe_anlegen,
    gruppe_loeschen,
    passwort_setzen,
    recht_entziehen,
    recht_vergeben,
    token_widerrufen,
)
from thermoctl.web.formulare import Formularfehler, formular_erneut, passwort_formularfehler

# `include_in_schema=False`: Die OpenAPI-Beschreibung ist der Vertrag der
# REST-Schnittstelle. Diese Wege liefern HTML fuer Menschen, und in der Oberflaeche
# unter /docs stuende sonst neben jedem echten Endpunkt ein Formularweg, dessen
# 'Try it out' eine echte Aenderung ausloest.
router = APIRouter(dependencies=[Depends(csrf_schutz)], include_in_schema=False)

# `require()` wirft bei fehlendem Recht `Forbidden` -- der globale Handler in
# `thermoctl/app.py` uebersetzt das einheitlich in 403. Keine Route hier faengt
# das mehr selbst ab: das war vor dem Abschlussreview an dieser Stelle noch der
# Fall und wurde bewusst entfernt, um es nicht an jeder Route erneut zu vergessen.


def _benutzerliste(
    request: Request, session: Session, eigene_id: int | None,
    fehler: Formularfehler | None = None,
    werte: dict[str, object] | None = None, hinweis: str | None = None,
) -> Response:
    return formular_erneut(
        request, "benutzer.html", werte or {}, fehler,
        benutzer=session.scalars(select(User).order_by(User.username)).all(),
        gruppen=session.scalars(select(AccessGroup).order_by(AccessGroup.name)).all(),
        eigene_id=eigene_id,
        hinweis=hinweis,
        ist_htmx="HX-Request" in request.headers,
    )


@router.get("/benutzer")
async def benutzerliste(
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    require(principal, "user.manage")
    return _benutzerliste(request, session, principal.user_id)


@router.post("/benutzer")
async def benutzer_anlegen_ansicht(
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
    username: Annotated[str, Form()] = "",
    display_name: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    gruppe_id: Annotated[str, Form()] = "",
) -> Response:
    require(principal, "user.manage")
    werte: dict[str, object] = {
        "username": username, "display_name": display_name, "gruppe_id": gruppe_id,
    }
    try:
        benutzer_anlegen(
            session, username=username, display_name=display_name, passwort=password,
            gruppen_ids=[int(gruppe_id)] if gruppe_id else [],
            akteur_id=principal.user_id,
        )
    except PasswordTooShort as exc:
        return _benutzerliste(
            request, session, principal.user_id, passwort_formularfehler(exc), werte
        )
    except Verwaltungsfehler as exc:
        return _benutzerliste(
            request, session, principal.user_id, Formularfehler("username", str(exc)), werte
        )
    return RedirectResponse("/benutzer", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/benutzer/{benutzer_id}/aktiv")
async def benutzer_aktiv_ansicht(
    request: Request,
    benutzer_id: int,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
    aktiv: Annotated[str, Form()] = "",
) -> Response:
    require(principal, "user.manage")
    nutzer = session.get(User, benutzer_id)
    if nutzer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Benutzer nicht gefunden")
    try:
        benutzer_aktiv_setzen(
            session, nutzer, aktiv == "ja", akteur_id=principal.user_id
        )
    except Verwaltungsfehler as exc:
        # Die Aussperrsperre ist kein Formfehler an einem Feld, sondern eine Aussage
        # ueber den Zustand der Anlage -- sie gehoert als Hinweis ueber die Liste.
        return _benutzerliste(request, session, principal.user_id, hinweis=str(exc))
    return RedirectResponse("/benutzer", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/benutzer/{benutzer_id}/passwort")
async def benutzer_passwort_ansicht(
    request: Request,
    benutzer_id: int,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
    password: Annotated[str, Form()] = "",
) -> Response:
    # Das eigene Passwort darf jeder aendern; fremde nur mit `user.manage`. Sonst
    # koennte niemand sein eigenes Passwort wechseln, ohne Verwalter zu sein.
    if benutzer_id != principal.user_id:
        require(principal, "user.manage")
    nutzer = session.get(User, benutzer_id)
    if nutzer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Benutzer nicht gefunden")
    try:
        passwort_setzen(session, nutzer, password, akteur_id=principal.user_id)
    except PasswordTooShort as exc:
        return _benutzerliste(
            request, session, principal.user_id, passwort_formularfehler(exc), {}
        )
    return RedirectResponse("/benutzer", status_code=status.HTTP_303_SEE_OTHER)


def _gruppenliste(
    request: Request, session: Session, fehler: Formularfehler | None = None,
    werte: dict[str, object] | None = None, hinweis: str | None = None,
) -> Response:
    gruppen = list(session.scalars(select(AccessGroup).order_by(AccessGroup.name)))
    rechte_je_gruppe: dict[int, list[tuple[GroupPermission, str, str | None]]] = {}
    for gruppe in gruppen:
        zeilen = session.execute(
            select(GroupPermission, Permission.code, Zone.name)
            .join(Permission, Permission.id == GroupPermission.permission_id)
            .outerjoin(Zone, Zone.id == GroupPermission.zone_id)
            .where(GroupPermission.access_group_id == gruppe.id)
            .order_by(Permission.code)
        ).all()
        rechte_je_gruppe[gruppe.id] = [(g, c, z) for g, c, z in zeilen]
    return formular_erneut(
        request, "gruppen.html", werte or {}, fehler,
        gruppen=gruppen,
        rechte_je_gruppe=rechte_je_gruppe,
        alle_rechte=session.scalars(select(Permission).order_by(Permission.code)).all(),
        alle_zonen=session.scalars(select(Zone).order_by(Zone.name)).all(),
        hinweis=hinweis,
        ist_htmx="HX-Request" in request.headers,
    )


@router.get("/gruppen")
async def gruppenliste(
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    require(principal, "group.manage")
    return _gruppenliste(request, session)


@router.post("/gruppen")
async def gruppe_anlegen_ansicht(
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
    name: Annotated[str, Form()] = "",
    description: Annotated[str, Form()] = "",
) -> Response:
    require(principal, "group.manage")
    try:
        gruppe_anlegen(
            session, name=name, beschreibung=description or None,
            akteur_id=principal.user_id,
        )
    except Verwaltungsfehler as exc:
        return _gruppenliste(
            request, session, Formularfehler("name", str(exc)),
            {"name": name, "description": description},
        )
    return RedirectResponse("/gruppen", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/gruppen/{gruppen_id}/loeschen")
async def gruppe_loeschen_ansicht(
    request: Request,
    gruppen_id: int,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    require(principal, "group.manage")
    gruppe = session.get(AccessGroup, gruppen_id)
    if gruppe is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Gruppe nicht gefunden")
    try:
        gruppe_loeschen(session, gruppe, akteur_id=principal.user_id)
    except Verwaltungsfehler as exc:
        return _gruppenliste(request, session, hinweis=str(exc))
    return RedirectResponse("/gruppen", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/gruppen/{gruppen_id}/rechte")
async def recht_vergeben_ansicht(
    request: Request,
    gruppen_id: int,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
    code: Annotated[str, Form()] = "",
    zone_id: Annotated[str, Form()] = "",
) -> Response:
    require(principal, "group.manage")
    gruppe = session.get(AccessGroup, gruppen_id)
    if gruppe is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Gruppe nicht gefunden")
    try:
        recht_vergeben(
            session, gruppe, code, int(zone_id) if zone_id else None,
            akteur_id=principal.user_id,
        )
    except Verwaltungsfehler as exc:
        return _gruppenliste(request, session, hinweis=str(exc))
    return RedirectResponse("/gruppen", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/gruppen/{gruppen_id}/rechte/{eintrag_id}/loeschen")
async def recht_entziehen_ansicht(
    request: Request,
    gruppen_id: int,
    eintrag_id: int,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    require(principal, "group.manage")
    eintrag = session.get(GroupPermission, eintrag_id)
    if eintrag is None or eintrag.access_group_id != gruppen_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Rechteintrag nicht gefunden")
    try:
        recht_entziehen(session, eintrag, akteur_id=principal.user_id)
    except Verwaltungsfehler as exc:
        return _gruppenliste(request, session, hinweis=str(exc))
    return RedirectResponse("/gruppen", status_code=status.HTTP_303_SEE_OTHER)


def _tokenliste(
    request: Request, session: Session, principal: Principal,
    fehler: Formularfehler | None = None, werte: dict[str, object] | None = None,
    klartext: str | None = None, hinweis: str | None = None,
) -> Response:
    return formular_erneut(
        request, "tokens.html", werte or {}, fehler,
        token=session.scalars(
            select(ApiToken)
            .where(ApiToken.user_id == principal.user_id)
            .order_by(ApiToken.name)
        ).all(),
        alle_rechte=session.scalars(select(Permission).order_by(Permission.code)).all(),
        klartext=klartext,
        hinweis=hinweis,
        ist_htmx="HX-Request" in request.headers,
    )


@router.get("/tokens")
async def tokenliste(
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    require(principal, "token.self")
    return _tokenliste(request, session, principal)


@router.post("/tokens")
async def token_ausstellen_ansicht(
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
    name: Annotated[str, Form()] = "",
    code: Annotated[str, Form()] = "",
    gueltig_tage: Annotated[str, Form()] = "",
) -> Response:
    require(principal, "token.self")
    # Beide Faelle sind ueber diesen Weg nicht erreichbar: `aktueller_principal` loest ein
    # Sitzungscookie auf und liefert deshalb immer einen vorhandenen Benutzer. Die
    # Pruefungen stehen trotzdem hier, weil `token_ausstellen` einen Besitzer braucht und
    # ein `None` dort ein stiller Fehler waere statt einer klaren Antwort.
    if principal.user_id is None:  # pragma: no cover
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Nur fuer angemeldete Benutzer")
    besitzer = session.get(User, principal.user_id)
    if besitzer is None:  # pragma: no cover
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Nur fuer angemeldete Benutzer")
    if not name.strip():
        return _tokenliste(
            request, session, principal,
            Formularfehler("name", "Das Token braucht einen Namen, sonst ist es spaeter "
                                   "nicht auseinanderzuhalten."),
            {"name": name, "code": code},
        )
    ablauf: datetime | None = None
    if gueltig_tage:
        ablauf = utcnow() + timedelta(days=int(gueltig_tage))
    try:
        _token, klartext = token_ausstellen(
            session, besitzer, name, [(code, None)] if code else [], ablauf
        )
    except Forbidden as exc:
        return _tokenliste(request, session, principal, hinweis=str(exc))
    # Der Klartext erscheint genau einmal: Gespeichert wird nur sein Hash, und ein Token,
    # das man spaeter nachschlagen kann, ist keines.
    return _tokenliste(request, session, principal, klartext=klartext)


@router.post("/tokens/{token_id}/widerrufen")
async def token_widerrufen_ansicht(
    request: Request,
    token_id: int,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    require(principal, "token.self")
    token = session.get(ApiToken, token_id)
    if token is None or token.user_id != principal.user_id:
        # Fremde Tokens sind nicht auffindbar, nicht verboten -- sonst verriete die
        # Antwort, welche Kennungen es gibt.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Token nicht gefunden")
    token_widerrufen(session, token, akteur_id=principal.user_id)
    return RedirectResponse("/tokens", status_code=status.HTTP_303_SEE_OTHER)
