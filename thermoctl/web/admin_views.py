from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.auth.dependencies import csrf_protection, current_principal, get_session
from thermoctl.auth.passwords import PasswordTooShort
from thermoctl.auth.tokens import issue_token
from thermoctl.db.base import utcnow
from thermoctl.db.models.credential import ApiToken
from thermoctl.db.models.identity import AccessGroup, GroupPermission, User
from thermoctl.db.models.lookup import Permission
from thermoctl.db.models.operations import Setting
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
from thermoctl.web import is_partial_swap
from thermoctl.web.forms import FormError, form_again, password_form_error

# `include_in_schema=False`: the OpenAPI description is the contract of the REST
# interface. These routes deliver HTML for humans, and in the interface under
# /docs there would otherwise be a form route next to every real endpoint whose
# 'Try it out' triggers a real change.
router = APIRouter(dependencies=[Depends(csrf_protection)], include_in_schema=False)

# `require()` raises `Forbidden` when a permission is missing -- the global handler
# in `thermoctl/app.py` translates that uniformly into 403. No route here catches
# that itself anymore: that used to be the case at this point before the final
# review and was deliberately removed, to avoid forgetting it again on every route.


def _user_list(
    request: Request, session: Session, own_id: int | None,
    errors: FormError | None = None,
    values: dict[str, object] | None = None, hint: str | None = None,
) -> Response:
    return form_again(
        request, "users.html", values or {}, errors,
        user=session.scalars(select(User).order_by(User.username)).all(),
        groups=session.scalars(select(AccessGroup).order_by(AccessGroup.name)).all(),
        own_id=own_id,
        hint=hint,
        is_htmx=is_partial_swap(request),
    )


@router.get("/users")
async def user_list(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    require(principal, "user.manage")
    return _user_list(request, session, principal.user_id)


@router.post("/users")
async def user_create_view(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
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
            actor_id=principal.user_id,
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
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
    active: Annotated[str, Form()] = "",
) -> Response:
    require(principal, "user.manage")
    user_record = session.get(User, user_id)
    if user_record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Benutzer nicht gefunden")
    try:
        set_user_active(
            session, user_record, active == "yes", actor_id=principal.user_id
        )
    except AdministrationError as exc:
        # The lockout guard is not a form error on a field but a statement about the
        # state of the plant -- it belongs as a hint above the list.
        return _user_list(request, session, principal.user_id, hint=str(exc))
    return RedirectResponse("/users", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/users/{user_id}/password")
async def user_password_view(
    request: Request,
    user_id: int,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
    password: Annotated[str, Form()] = "",
) -> Response:
    # Everyone may change their own password; someone else's only with `user.manage`.
    # Otherwise nobody could change their own password without being an administrator.
    if user_id != principal.user_id:
        require(principal, "user.manage")
    user_record = session.get(User, user_id)
    if user_record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Benutzer nicht gefunden")
    try:
        set_password(session, user_record, password, actor_id=principal.user_id)
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
    # Per group, the set of granted (code, zone) pairs -- the template can then check
    # each checkbox directly, instead of searching through a list.
    taken: dict[int, set[tuple[str, int | None]]] = {g.id: set() for g in groups}
    for group_id, code, zone_id in session.execute(
        select(GroupPermission.access_group_id, Permission.code, GroupPermission.zone_id)
        .join(Permission, Permission.id == GroupPermission.permission_id)
    ):
        if group_id in taken:
            taken[group_id].add((code, zone_id))

    permissions = {r.code: r for r in session.scalars(select(Permission))}
    zone_names = {
        zone_id: name
        for zone_id, name in session.execute(select(Zone.id, Zone.display_name))
    }
    # What a group is allowed to do, in one sentence. Without it you'd have to read 16
    # checkboxes to know what a group is for -- and that's the first question you have.
    summary: dict[int, list[str]] = {}
    for group_id, entries in taken.items():
        sentence = []
        for code, zone_id in sorted(entries, key=lambda p: (p[0], p[1] or 0)):
            permission = permissions.get(code)
            if permission is None:  # pragma: no cover
                # Unreachable: `taken` above was filled by joining `group_permission`
                # against `permission`, and `permissions` is that same table read in
                # full -- every code here has a row. The foreign key makes sure of it
                # from the other side, too: a permission still granted somewhere
                # cannot be deleted. Kept so the lookup does not silently rely on
                # both of those staying true.
                continue
            wo = f" ({zone_names.get(zone_id, 'unbekannte Zone')})" if zone_id else ""
            sentence.append(permission.description + wo)
        summary[group_id] = sentence
    scopes = [
        (
            name,
            hint_text,
            [permissions[code] for code in codes if code in permissions],
        )
        for name, hint_text, codes in PERMISSION_AREAS
    ]
    return form_again(
        request, "groups.html", values or {}, errors,
        groups=groups,
        taken=taken,
        summary=summary,
        scopes=scopes,
        all_zones=session.scalars(select(Zone).order_by(Zone.name)).all(),
        hint=hint,
        is_htmx=is_partial_swap(request),
    )


@router.get("/groups")
async def group_list(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    require(principal, "group.manage")
    return _group_list(request, session)


@router.post("/groups")
async def group_create_view(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
    name: Annotated[str, Form()] = "",
    description: Annotated[str, Form()] = "",
) -> Response:
    require(principal, "group.manage")
    try:
        create_group(
            session, name=name, description=description or None,
            actor_id=principal.user_id,
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
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    require(principal, "group.manage")
    group = session.get(AccessGroup, group_id)
    if group is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Gruppe nicht gefunden")
    try:
        delete_group(session, group, actor_id=principal.user_id)
    except AdministrationError as exc:
        return _group_list(request, session, hint=str(exc))
    return RedirectResponse("/groups", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/groups/{group_id}/permissions")
async def permissions_set_view(
    request: Request,
    group_id: int,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    """Accepts the **entire** desired permission state of a group.

    There used to be two endpoints: one for granting a single permission and one for
    revoking a single entry. Whoever set up a group clicked through a flat list of
    sixteen codes, one at a time, and never saw what the group is allowed to do overall.
    Now a form sends all checkmarks at once, and the domain computes the diff.

    The fields are named `recht` and carry either `code` (whole plant) or
    `code:zone_id`.
    """
    require(principal, "group.manage")
    group = session.get(AccessGroup, group_id)
    if group is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Gruppe nicht gefunden")

    form = await request.form()
    wanted: set[tuple[str, int | None]] = set()
    for entry in form.getlist("permission"):
        code, _, zone = str(entry).partition(":")
        if not code:
            continue
        try:
            wanted.add((code, int(zone) if zone else None))
        except ValueError:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "Unbrauchbare Zonenangabe"
            ) from None

    try:
        set_group_permissions(
            session, group, wanted, actor_id=principal.user_id
        )
    except AdministrationError as exc:
        return _group_list(request, session, hint=str(exc))
    return RedirectResponse("/groups", status_code=status.HTTP_303_SEE_OTHER)


def _token_list(
    request: Request, session: Session, principal: Principal,
    errors: FormError | None = None, values: dict[str, object] | None = None,
    plaintext: str | None = None, hint: str | None = None,
) -> Response:
    settings = session.get(Setting, 1)
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
        timezone=settings.timezone if settings is not None else None,
        is_htmx=is_partial_swap(request),
    )


@router.get("/tokens")
async def token_list(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    require(principal, "token.self")
    return _token_list(request, session, principal)


@router.post("/tokens")
async def token_issue_view(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
    name: Annotated[str, Form()] = "",
    code: Annotated[str, Form()] = "",
    valid_days: Annotated[str, Form()] = "",
) -> Response:
    require(principal, "token.self")
    # Neither case is reachable via this route: `aktueller_principal` resolves a
    # session cookie and therefore always yields an existing user. The checks are
    # here anyway because `token_ausstellen` needs an owner, and a `None` there would
    # be a silent failure instead of a clear response.
    if principal.user_id is None:  # pragma: no cover
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Nur fuer angemeldete Benutzer")
    owner = session.get(User, principal.user_id)
    if owner is None:  # pragma: no cover
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
        _token, plaintext = issue_token(
            session, owner, name, [(code, None)] if code else [], expiry
        )
    except Forbidden as exc:
        return _token_list(request, session, principal, hint=str(exc))
    # The plaintext appears exactly once: only its hash is stored, and a token you
    # could look up later would not be one.
    return _token_list(request, session, principal, plaintext=plaintext)


@router.post("/tokens/{token_id}/revoke")
async def token_revoke_view(
    request: Request,
    token_id: int,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    require(principal, "token.self")
    token = session.get(ApiToken, token_id)
    if token is None or token.user_id != principal.user_id:
        # Someone else's tokens are unfindable, not forbidden -- otherwise the
        # response would reveal which ids exist.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Token nicht gefunden")
    revoke_token(session, token, actor_id=principal.user_id)
    return RedirectResponse("/tokens", status_code=status.HTTP_303_SEE_OTHER)
