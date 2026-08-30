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
from thermoctl.domain.administration import (
    AdministrationError,
    create_group,
    create_user,
    delete_group,
    revoke_token,
    set_group_permissions,
    set_password,
    set_user_active,
)
from thermoctl.domain.authz import PERMISSION_AREAS, Forbidden, require
from thermoctl.domain.principal import Principal
from thermoctl.web import ist_teilaustausch
from thermoctl.web.forms import FormError, form_again, password_form_error

# `include_in_schema=False`: Die OpenAPI-Beschreibung ist der Vertrag der
# REST-Schnittstelle. Diese Wege liefern HTML fuer Menschen, und in der Oberflaeche
# unter /docs stuende sonst neben jedem echten Endpunkt ein Formularweg, dessen
# 'Try it out' eine echte Aenderung ausloest.
router = APIRouter(dependencies=[Depends(csrf_schutz)], include_in_schema=False)

# `require()` wirft bei fehlendem Recht `Forbidden` -- der globale Handler in
# `thermoctl/app.py` uebersetzt das einheitlich in 403. Keine Route hier faengt
# das mehr selbst ab: das war vor dem Abschlussreview an dieser Stelle noch der
# Fall und wurde bewusst entfernt, um es nicht an jeder Route erneut zu vergessen.


def _user_list(
    request: Request, session: Session, own_id: int | None,
    errors: FormError | None = None,
    values: dict[str, object] | None = None, hint: str | None = None,
) -> Response:
    return form_again(
        request, "benutzer.html", values or {}, errors,
        user=session.scalars(select(User).order_by(User.username)).all(),
        groups=session.scalars(select(AccessGroup).order_by(AccessGroup.name)).all(),
        own_id=own_id,
        hint=hint,
        ist_htmx=ist_teilaustausch(request),
    )


@router.get("/users")
async def user_list(
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    require(principal, "user.manage")
    return _user_list(request, session, principal.user_id)


@router.post("/users")
async def user_create_view(
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
    username: Annotated[str, Form()] = "",
    display_name: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    group_id: Annotated[str, Form()] = "",
) -> Response:
    require(principal, "user.manage")
    values: dict[str, object] = {
        "username": username, "display_name": display_name, "group_id": group_id,
    }
    try:
        create_user(
            session, username=username, display_name=display_name, password=password,
            group_ids=[int(group_id)] if group_id else [],
            akteur_id=principal.user_id,
        )
    except PasswordTooShort as exc:
        return _user_list(
            request, session, principal.user_id, password_form_error(exc), values
        )
    except AdministrationError as exc:
        return _user_list(
            request, session, principal.user_id, FormError("username", str(exc)), values
        )
    return RedirectResponse("/users", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/users/{user_id}/active")
async def user_active_view(
    request: Request,
    user_id: int,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
    active: Annotated[str, Form()] = "",
) -> Response:
    require(principal, "user.manage")
    nutzer = session.get(User, user_id)
    if nutzer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Benutzer nicht gefunden")
    try:
        set_user_active(
            session, nutzer, active == "ja", akteur_id=principal.user_id
        )
    except AdministrationError as exc:
        # Die Aussperrsperre ist kein Formfehler an einem Feld, sondern eine Aussage
        # ueber den Zustand der Anlage -- sie gehoert als Hinweis ueber die Liste.
        return _user_list(request, session, principal.user_id, hint=str(exc))
    return RedirectResponse("/users", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/users/{user_id}/password")
async def user_password_view(
    request: Request,
    user_id: int,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
    password: Annotated[str, Form()] = "",
) -> Response:
    # Das eigene Passwort darf jeder aendern; fremde nur mit `user.manage`. Sonst
    # koennte niemand sein eigenes Passwort wechseln, ohne Verwalter zu sein.
    if user_id != principal.user_id:
        require(principal, "user.manage")
    nutzer = session.get(User, user_id)
    if nutzer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Benutzer nicht gefunden")
    try:
        set_password(session, nutzer, password, akteur_id=principal.user_id)
    except PasswordTooShort as exc:
        return _user_list(
            request, session, principal.user_id, password_form_error(exc), {}
        )
    return RedirectResponse("/users", status_code=status.HTTP_303_SEE_OTHER)


def _group_list(
    request: Request, session: Session, errors: FormError | None = None,
    values: dict[str, object] | None = None, hint: str | None = None,
) -> Response:
    groups = list(session.scalars(select(AccessGroup).order_by(AccessGroup.name)))
    # Je Gruppe die Menge der vergebenen (Code, Zone) -- die Vorlage fragt damit jedes
    # Kaestchen direkt ab, statt eine Liste zu durchsuchen.
    vergeben: dict[int, set[tuple[str, int | None]]] = {g.id: set() for g in groups}
    for group_id, code, zone_id in session.execute(
        select(GroupPermission.access_group_id, Permission.code, GroupPermission.zone_id)
        .join(Permission, Permission.id == GroupPermission.permission_id)
    ):
        if group_id in vergeben:
            vergeben[group_id].add((code, zone_id))

    permissions = {r.code: r for r in session.scalars(select(Permission))}
    zone_names = {
        zone_id: name
        for zone_id, name in session.execute(select(Zone.id, Zone.display_name))
    }
    # Was eine Gruppe darf, in einem Satz. Ohne ihn muss man 16 Kaestchen lesen, um zu
    # wissen, wofuer eine Gruppe da ist -- und das ist die erste Frage, die man hat.
    zusammenfassung: dict[int, list[str]] = {}
    for group_id, entries in vergeben.items():
        satz = []
        for code, zone_id in sorted(entries, key=lambda p: (p[0], p[1] or 0)):
            permission = permissions.get(code)
            if permission is None:
                continue
            wo = f" ({zone_names.get(zone_id, 'unbekannte Zone')})" if zone_id else ""
            satz.append(permission.description + wo)
        zusammenfassung[group_id] = satz
    bereiche = [
        (
            name,
            hint_text,
            [permissions[code] for code in codes if code in permissions],
        )
        for name, hint_text, codes in PERMISSION_AREAS
    ]
    return form_again(
        request, "gruppen.html", values or {}, errors,
        groups=groups,
        vergeben=vergeben,
        zusammenfassung=zusammenfassung,
        bereiche=bereiche,
        alle_zonen=session.scalars(select(Zone).order_by(Zone.name)).all(),
        hint=hint,
        ist_htmx=ist_teilaustausch(request),
    )


@router.get("/groups")
async def group_list(
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    require(principal, "group.manage")
    return _group_list(request, session)


@router.post("/groups")
async def group_create_view(
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
    name: Annotated[str, Form()] = "",
    description: Annotated[str, Form()] = "",
) -> Response:
    require(principal, "group.manage")
    try:
        create_group(
            session, name=name, beschreibung=description or None,
            akteur_id=principal.user_id,
        )
    except AdministrationError as exc:
        return _group_list(
            request, session, FormError("name", str(exc)),
            {"name": name, "description": description},
        )
    return RedirectResponse("/groups", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/groups/{group_id}/delete")
async def group_delete_view(
    request: Request,
    group_id: int,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    require(principal, "group.manage")
    group = session.get(AccessGroup, group_id)
    if group is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Gruppe nicht gefunden")
    try:
        delete_group(session, group, akteur_id=principal.user_id)
    except AdministrationError as exc:
        return _group_list(request, session, hint=str(exc))
    return RedirectResponse("/groups", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/groups/{group_id}/permissions")
async def permissions_set_view(
    request: Request,
    group_id: int,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    """Nimmt den **ganzen** gewuenschten Rechtestand einer Gruppe entgegen.

    Vorher gab es zwei Endpunkte: einen zum Vergeben eines einzelnen Rechts und einen
    zum Entziehen eines einzelnen Eintrags. Wer eine Gruppe einrichtete, klickte sich
    durch eine flache Liste von sechzehn Codes, einen nach dem anderen, und sah dabei
    nie, was die Gruppe insgesamt darf. Jetzt schickt ein Formular alle Haken auf einmal,
    und die Domaene bildet die Differenz.

    Die Felder heissen `recht` und tragen entweder `code` (ganze Anlage) oder
    `code:zone_id`.
    """
    require(principal, "group.manage")
    group = session.get(AccessGroup, group_id)
    if group is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Gruppe nicht gefunden")

    form = await request.form()
    gewuenscht: set[tuple[str, int | None]] = set()
    for entry in form.getlist("permission"):
        code, _, zone = str(entry).partition(":")
        if not code:
            continue
        try:
            gewuenscht.add((code, int(zone) if zone else None))
        except ValueError:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "Unbrauchbare Zonenangabe"
            ) from None

    try:
        set_group_permissions(
            session, group, gewuenscht, akteur_id=principal.user_id
        )
    except AdministrationError as exc:
        return _group_list(request, session, hint=str(exc))
    return RedirectResponse("/groups", status_code=status.HTTP_303_SEE_OTHER)


def _token_list(
    request: Request, session: Session, principal: Principal,
    errors: FormError | None = None, values: dict[str, object] | None = None,
    plaintext: str | None = None, hint: str | None = None,
) -> Response:
    return form_again(
        request, "tokens.html", values or {}, errors,
        token=session.scalars(
            select(ApiToken)
            .where(ApiToken.user_id == principal.user_id)
            .order_by(ApiToken.name)
        ).all(),
        alle_rechte=session.scalars(select(Permission).order_by(Permission.code)).all(),
        plaintext=plaintext,
        hint=hint,
        ist_htmx=ist_teilaustausch(request),
    )


@router.get("/tokens")
async def token_list(
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    require(principal, "token.self")
    return _token_list(request, session, principal)


@router.post("/tokens")
async def token_issue_view(
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
    name: Annotated[str, Form()] = "",
    code: Annotated[str, Form()] = "",
    valid_days: Annotated[str, Form()] = "",
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
        return _token_list(
            request, session, principal,
            FormError("name", "Das Token braucht einen Namen, sonst ist es spaeter "
                                   "nicht auseinanderzuhalten."),
            {"name": name, "code": code},
        )
    expiry: datetime | None = None
    if valid_days:
        expiry = utcnow() + timedelta(days=int(valid_days))
    try:
        _token, plaintext = token_ausstellen(
            session, besitzer, name, [(code, None)] if code else [], expiry
        )
    except Forbidden as exc:
        return _token_list(request, session, principal, hint=str(exc))
    # Der Klartext erscheint genau einmal: Gespeichert wird nur sein Hash, und ein Token,
    # das man spaeter nachschlagen kann, ist keines.
    return _token_list(request, session, principal, plaintext=plaintext)


@router.post("/tokens/{token_id}/revoke")
async def token_revoke_view(
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
    revoke_token(session, token, akteur_id=principal.user_id)
    return RedirectResponse("/tokens", status_code=status.HTTP_303_SEE_OTHER)
