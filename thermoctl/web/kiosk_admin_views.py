"""Administration of kiosk tokens: the credential a wall tablet bookmarks.

Separate from `/tokens` (a user's own developer tokens, one plant-wide permission
code) on purpose: a kiosk token names a set of zones and a view-only/control switch,
not a permission code, and it is meant to be handed to a device rather than kept by
its issuer. Gated behind `token.manage` ("Fremde Tokens verwalten") rather than
`token.self`: a tablet's credential is nobody's personal token.
"""

from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.auth.dependencies import aktueller_principal, csrf_schutz, get_session
from thermoctl.db.base import utcnow
from thermoctl.db.models.credential import ApiToken
from thermoctl.db.models.identity import User
from thermoctl.db.models.zone import Zone
from thermoctl.domain.administration import revoke_token
from thermoctl.domain.authz import require
from thermoctl.domain.kiosk import KioskError, issue_kiosk_token, kiosk_scope
from thermoctl.domain.principal import Principal
from thermoctl.web import ist_teilaustausch
from thermoctl.web.forms import FormError, form_again

# `include_in_schema=False`: see the same note in every other HTML-only router --
# these are pages for humans, not the REST contract described under /docs.
router = APIRouter(dependencies=[Depends(csrf_schutz)], include_in_schema=False)


def _kiosk_token_list(
    request: Request, session: Session,
    errors: FormError | None = None, values: dict[str, object] | None = None,
    plaintext: str | None = None, hint: str | None = None,
) -> Response:
    token_rows = session.scalars(
        select(ApiToken).where(ApiToken.is_kiosk.is_(True)).order_by(ApiToken.name)
    ).all()
    zone_names = {
        zone_id: name for zone_id, name in session.execute(select(Zone.id, Zone.display_name))
    }
    # Zone names joined into one string here, not with a `|map(zone_names.get)` in
    # the template: Jinja's `map` filter takes the *name* of a filter to apply, not
    # an arbitrary callable -- passing `zone_names.get` looked like it should work
    # and instead failed at render time with "No filter named <built-in method
    # get ...>".
    scopes: dict[int, tuple[str, bool]] = {}
    for token in token_rows:
        zone_ids, control_allowed = kiosk_scope(session, token)
        zone_label = ", ".join(zone_names[z] for z in zone_ids if z in zone_names) or "keine"
        scopes[token.id] = (zone_label, control_allowed)
    return form_again(
        request, "kiosk_tokens.html", values or {}, errors,
        token=token_rows,
        scopes=scopes,
        zones=session.scalars(select(Zone).order_by(Zone.sort_order, Zone.name)).all(),
        plaintext=plaintext,
        hint=hint,
        ist_htmx=ist_teilaustausch(request),
    )


@router.get("/kiosk-tokens")
async def kiosk_token_list(
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    require(principal, "token.manage")
    return _kiosk_token_list(request, session)


@router.post("/kiosk-tokens")
async def kiosk_token_issue_view(
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
    name: Annotated[str, Form()] = "",
    control_allowed: Annotated[str, Form()] = "",
    valid_days: Annotated[str, Form()] = "",
) -> Response:
    require(principal, "token.manage")
    form = await request.form()
    zone_ids: list[int] = []
    for value in form.getlist("zone_id"):
        try:
            zone_ids.append(int(str(value)))
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unbrauchbare Zone") from None
    values: dict[str, object] = {"name": name, "zone_id": [str(z) for z in zone_ids]}

    if not name.strip():
        return _kiosk_token_list(
            request, session,
            FormError("name", "Das Token braucht einen Namen, sonst ist es an einem "
                                   "Tablet später nicht wiederzuerkennen."),
            values,
        )

    # `aktueller_principal` always resolves to an existing, active user -- see the
    # identical comment on `/tokens` in admin_views.py.
    besitzer = session.get(User, principal.user_id)
    if besitzer is None:  # pragma: no cover
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Nur fuer angemeldete Benutzer")

    expiry = utcnow() + timedelta(days=int(valid_days)) if valid_days else None
    try:
        _token, plaintext = issue_kiosk_token(
            session, besitzer, name, zone_ids,
            # A checkbox that is left unchecked sends no field at all -- `bool("")`
            # is therefore exactly the right test, not a comparison against a
            # specific value the switch never actually sends.
            control_allowed=bool(control_allowed), expires_at=expiry,
        )
    except KioskError as exc:
        return _kiosk_token_list(request, session, FormError("zone_id", str(exc)), values)
    return _kiosk_token_list(request, session, plaintext=plaintext)


@router.post("/kiosk-tokens/{token_id}/revoke")
async def kiosk_token_revoke_view(
    request: Request,
    token_id: int,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    require(principal, "token.manage")
    token = session.get(ApiToken, token_id)
    if token is None or not token.is_kiosk:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Kiosk-Token nicht gefunden")
    revoke_token(session, token, akteur_id=principal.user_id)
    return RedirectResponse("/kiosk-tokens", status_code=status.HTTP_303_SEE_OTHER)
