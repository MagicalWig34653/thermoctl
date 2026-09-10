"""Der persönliche Bereich: Passwort, Passkeys, andere Sitzungen, Abmelden.

Bewusst getrennt von `/users`. Dort verwaltet jemand mit `user.manage` **fremde**
Konten; hier verwaltet jeder sein **eigenes**, und zwar ohne jedes Recht -- das eigene
Passwort zu ändern oder die eigenen anderen Sitzungen zu beenden ist kein
privilegierter Vorgang. Bis hierher lagen beide Dinge auf der Benutzerverwaltungsseite;
für ein Mieterprofil, das diese Seite gar nicht öffnen darf, wäre das eigene Passwort
damit unerreichbar gewesen.

Deshalb trägt dieser Router auch **keinen** Profil-Wächter: er gehört beiden
Oberflächen. In der Wohnungssicht ist er der Bereich „Mehr", in der Anlagensicht der
Eintrag „Konto und Sicherheit" im Kontomenü.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from thermoctl import audit
from thermoctl.auth.dependencies import csrf_protection, current_principal, get_session
from thermoctl.auth.passwords import PasswordTooShort
from thermoctl.auth.sessions import COOKIE_NAME, resolve_session, revoke_all_sessions
from thermoctl.db.models.identity import User
from thermoctl.domain.administration import set_password
from thermoctl.domain.principal import Principal
from thermoctl.domain.ui_profile import WebUiProfile
from thermoctl.web import templates
from thermoctl.web.forms import FormError, password_form_error

# `include_in_schema=False`: wie bei jedem anderen HTML-Router -- die
# OpenAPI-Beschreibung ist der Vertrag der REST-Schnittstelle, nicht der der
# Formularrouten.
router = APIRouter(dependencies=[Depends(csrf_protection)], include_in_schema=False)


def _account_page(
    request: Request,
    session: Session,
    principal: Principal,
    *,
    errors: FormError | None = None,
    hint: str | None = None,
) -> Response:
    user_record = session.get(User, principal.user_id)
    if user_record is None:  # pragma: no cover - `current_principal` hat ihn geladen
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Benutzer nicht gefunden")
    return templates.TemplateResponse(
        request,
        "account.html",
        {
            "account": user_record,
            "errors": {errors.field: errors.notice} if errors else {},
            "hint": hint,
            # Welche Hülle die Seite erbt. Aus dem Profil des angemeldeten
            # Principals, nicht aus einem Abfrageparameter -- das ist die einzige
            # Quelle, die ein Browser nicht setzen kann.
            "shell": (
                "base_tenant.html"
                if principal.ui_profile is WebUiProfile.TENANT
                else "base_admin.html"
            ),
        },
    )


@router.get("/account")
async def show_account(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    return _account_page(request, session, principal)


@router.post("/account/password")
async def change_own_password(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
    password: Annotated[str, Form()] = "",
) -> Response:
    """Ändert das eigene Passwort -- und nur das eigene.

    Es gibt hier ausdrücklich keine Benutzer-Id im Pfad oder im Formular. Ein fremdes
    Passwort setzt man über `/users/{id}/password` mit `user.manage`; hier gibt es
    keinen Weg, versehentlich oder absichtlich ein anderes Konto zu treffen.
    """
    user_record = session.get(User, principal.user_id)
    if user_record is None:  # pragma: no cover - `current_principal` hat ihn geladen
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Benutzer nicht gefunden")
    cookie = request.cookies.get(COOKIE_NAME)
    running = resolve_session(session, cookie) if cookie else None
    try:
        set_password(
            session, user_record, password,
            actor_id=principal.user_id,
            # Die Sitzung vor dem Nutzer bleibt bestehen, jede andere endet.
            keep_session_id=running.id if running is not None else None,
        )
    except PasswordTooShort as exc:
        return _account_page(
            request, session, principal, errors=password_form_error(exc)
        )
    return _account_page(
        request, session, principal,
        hint="Passwort geändert. Andere Sitzungen wurden beendet.",
    )


@router.post("/account/sessions/revoke-others")
async def revoke_other_sessions(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    """Beendet jede Sitzung dieses Kontos außer der gerade benutzten."""
    cookie = request.cookies.get(COOKIE_NAME)
    running = resolve_session(session, cookie) if cookie else None
    ended = revoke_all_sessions(
        session, principal.user_id, keep_id=running.id if running is not None else None
    )
    audit.record(
        session, source="web", action="session.revoked_others", object_type="user",
        object_id=str(principal.user_id),
        summary=f"{ended} weitere Sitzung(en) beendet", user_id=principal.user_id,
    )
    return _account_page(
        request, session, principal, hint=f"{ended} weitere Sitzung(en) beendet."
    )


@router.get("/account/help")
async def show_help(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    """Was die Anzeigen bedeuten -- in Alltagssprache.

    Keine Anlagendaten, keine Zonennamen, keine Zustände: reiner Erklärtext. Deshalb
    braucht die Seite auch kein Recht und liest nichts aus der Datenbank.
    """
    return templates.TemplateResponse(
        request,
        "help.html",
        {
            "shell": (
                "base_tenant.html"
                if principal.ui_profile is WebUiProfile.TENANT
                else "base_admin.html"
            ),
        },
    )
