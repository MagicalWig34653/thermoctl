"""Übersicht pflegen: Vorlagen und die Übersicht beliebiger Benutzer (`/ui-api/dashboard/...`).

Nur mit dem Recht `user.manage` (und Anlagensicht, wie die übrige Benutzerverwaltung).
Dünner Adapter: Speichern, Zurücksetzen und die Rechtefilterung sind dieselben Funktionen
wie beim eigenen Layout (`ui_api_dashboard`); hier wird nur anstelle des Administrators der
bearbeitete Benutzer als Principal eingesetzt. Damit begrenzen *seine* Rechte, welche Räume
und Funktionen in seine Übersicht dürfen -- ein Administrator kann ihm nichts geben, was er
nicht sehen darf, und sieht im Editor genau das, was der Benutzer sieht.

Vorlagen werden mit den Rechten des Administrators geprüft und gefiltert (ein Raum, den er
selbst nicht sehen darf, kann nicht in eine Vorlage). Jede Änderung an einer fremden
Übersicht oder an einer Vorlage steht im Änderungsprotokoll.
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy.orm import Session

from thermoctl import audit
from thermoctl.auth.dependencies import csrf_protection, current_principal, get_session
from thermoctl.db.models.dashboard import MAX_SECTIONS, MAX_TITLE_LENGTH, DashboardTemplate
from thermoctl.db.models.identity import User
from thermoctl.domain import dashboard_templates as templates
from thermoctl.domain.authz import principal_for_user
from thermoctl.domain.dashboard_templates import TILE_FIELDS, TemplateError
from thermoctl.domain.principal import Principal
from thermoctl.web.ui_api_access import _admin
from thermoctl.web.ui_api_dashboard import (
    LayoutIn,
    LayoutOut,
    SectionModel,
    TileModel,
    _access,
    _filtered,
    _normalised,
    _read,
    reset_layout,
    save_layout,
)
from thermoctl.web.ui_api_rooms import RoomApiError, _RoomsRoute

router = APIRouter(
    prefix="/ui-api",
    dependencies=[Depends(csrf_protection)],
    include_in_schema=False,
    route_class=_RoomsRoute,
)

Name = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_TITLE_LENGTH)
]


class TemplateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name
    sections: list[SectionModel] = Field(max_length=MAX_SECTIONS)


class FromUserIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name


class TemplateOut(BaseModel):
    id: int
    name: str
    sections: list[SectionModel]


class TemplateItem(BaseModel):
    id: int
    name: str
    tile_count: int


class TemplateListOut(BaseModel):
    templates: list[TemplateItem]


# -- Hilfen -----------------------------------------------------------------------------


def _target(session: Session, user_id: int) -> Principal:
    user = session.get(User, user_id)
    if user is None:
        raise RoomApiError(404, "Benutzer nicht gefunden.", "not_found")
    return principal_for_user(session, user)


def _template_or_404(session: Session, template_id: int) -> DashboardTemplate:
    template = session.get(DashboardTemplate, template_id)
    if template is None:
        raise RoomApiError(404, "Vorlage nicht gefunden.", "not_found")
    return template


def _as_sections(sections: list[SectionModel]) -> list[templates.Section]:
    return [
        (
            section.title,
            [{field: getattr(tile, field) for field in TILE_FIELDS} for tile in section.tiles],
        )
        for section in sections
    ]


def _template_out(
    session: Session, principal: Principal, template: DashboardTemplate
) -> TemplateOut:
    access = _access(session, principal)
    stored = [
        SectionModel(title=title, tiles=[TileModel(**tile) for tile in tiles])
        for title, tiles in templates.template_sections(session, template.id)
    ]
    return TemplateOut(id=template.id, name=template.name, sections=_filtered(access, stored))


def _conflict(exc: TemplateError) -> RoomApiError:
    return RoomApiError(409, str(exc), "name_taken", field="name")


# -- Übersicht eines Benutzers ----------------------------------------------------------


@router.get("/dashboard/users/{user_id}")
def read_user_dashboard(
    user_id: int,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> LayoutOut:
    _admin(principal, "user.manage")
    return _read(session, _target(session, user_id))


@router.put("/dashboard/users/{user_id}")
def save_user_dashboard(
    user_id: int,
    body: LayoutIn,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> LayoutOut:
    _admin(principal, "user.manage")
    target = _target(session, user_id)
    out = save_layout(session, target, body)
    audit.record(
        session, source="web", action="dashboard.user_saved", object_type="user",
        object_id=str(user_id), summary="Übersicht eines Benutzers geändert",
        user_id=principal.user_id,
    )
    return out


@router.delete("/dashboard/users/{user_id}")
def reset_user_dashboard(
    user_id: int,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> LayoutOut:
    _admin(principal, "user.manage")
    target = _target(session, user_id)
    out = reset_layout(session, target)
    audit.record(
        session, source="web", action="dashboard.user_reset", object_type="user",
        object_id=str(user_id), summary="Übersicht eines Benutzers auf Standard gesetzt",
        user_id=principal.user_id,
    )
    return out


# -- Vorlagen ---------------------------------------------------------------------------


@router.get("/dashboard/templates")
def list_templates(
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> TemplateListOut:
    _admin(principal, "user.manage")
    items = [
        TemplateItem(
            id=template.id,
            name=template.name,
            tile_count=sum(
                len(tiles) for _, tiles in templates.template_sections(session, template.id)
            ),
        )
        for template in templates.list_templates(session)
    ]
    return TemplateListOut(templates=items)


@router.get("/dashboard/templates/{template_id}")
def read_template(
    template_id: int,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> TemplateOut:
    _admin(principal, "user.manage")
    return _template_out(session, principal, _template_or_404(session, template_id))


def _checked(
    session: Session, principal: Principal, sections: list[SectionModel]
) -> list[SectionModel]:
    access = _access(session, principal)
    return [
        SectionModel(
            title=section.title, tiles=[_normalised(access, tile) for tile in section.tiles]
        )
        for section in sections
    ]


@router.post("/dashboard/templates", status_code=201)
def create_template(
    body: TemplateIn,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> TemplateOut:
    _admin(principal, "user.manage")
    checked = _checked(session, principal, body.sections)
    try:
        template = templates.create_template(session, body.name, _as_sections(checked))
    except TemplateError as exc:
        raise _conflict(exc) from None
    audit.record(
        session, source="web", action="dashboard.template_created",
        object_type="dashboard_template",
        object_id=str(template.id), summary=f"Übersichtsvorlage „{template.name}“ angelegt",
        user_id=principal.user_id,
    )
    return _template_out(session, principal, template)


@router.post("/dashboard/templates/from-user/{user_id}", status_code=201)
def create_template_from_user(
    user_id: int,
    body: FromUserIn,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> TemplateOut:
    """Die Übersicht eines Benutzers (so, wie er sie sieht) als neue Vorlage übernehmen."""
    _admin(principal, "user.manage")
    layout = _read(session, _target(session, user_id))
    access = _access(session, principal)
    sections = _filtered(access, layout.sections)
    try:
        template = templates.create_template(session, body.name, _as_sections(sections))
    except TemplateError as exc:
        raise _conflict(exc) from None
    audit.record(
        session, source="web", action="dashboard.template_created",
        object_type="dashboard_template",
        object_id=str(template.id),
        summary=f"Übersichtsvorlage „{template.name}“ aus der Übersicht eines Benutzers angelegt",
        user_id=principal.user_id,
    )
    return _template_out(session, principal, template)


@router.put("/dashboard/templates/{template_id}")
def save_template(
    template_id: int,
    body: TemplateIn,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> TemplateOut:
    _admin(principal, "user.manage")
    template = _template_or_404(session, template_id)
    checked = _checked(session, principal, body.sections)
    try:
        templates.replace_template(session, template, body.name, _as_sections(checked))
    except TemplateError as exc:
        raise _conflict(exc) from None
    audit.record(
        session, source="web", action="dashboard.template_saved",
        object_type="dashboard_template",
        object_id=str(template.id), summary=f"Übersichtsvorlage „{template.name}“ geändert",
        user_id=principal.user_id,
    )
    return _template_out(session, principal, template)


@router.delete("/dashboard/templates/{template_id}", status_code=204)
def remove_template(
    template_id: int,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> None:
    _admin(principal, "user.manage")
    template = _template_or_404(session, template_id)
    name = template.name
    templates.delete_template(session, template)
    audit.record(
        session, source="web", action="dashboard.template_deleted",
        object_type="dashboard_template",
        object_id=str(template_id), summary=f"Übersichtsvorlage „{name}“ gelöscht",
        user_id=principal.user_id,
    )
