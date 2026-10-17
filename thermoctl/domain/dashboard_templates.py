"""Vorlagen für die Übersicht: lesen, ersetzen, in ein Benutzerlayout kopieren.

Eine Vorlage ist eine Momentaufnahme: Beim Anlegen eines Benutzers wird ihr Inhalt in
dessen Layout kopiert, danach besteht keine Verbindung mehr -- eine spätere Änderung der
Vorlage berührt bestehende Benutzer nicht. Die Vorlage vergibt keine Rechte: Was der
Benutzer nicht sehen darf, filtert das Lesen seines Layouts (`ui_api_dashboard`) weg.

Kacheln werden hier als einfache Zuordnungen mit den Feldern von `TILE_FIELDS` geführt;
die Prüfung gegen Rechte und Schranken liegt vor dem Aufruf (Adapter).
"""

from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from thermoctl.db.base import utcnow
from thermoctl.db.models.dashboard import (
    DashboardLayout,
    DashboardSection,
    DashboardTemplate,
    DashboardTemplateSection,
    DashboardTemplateTile,
    DashboardTile,
)

TILE_FIELDS = (
    "kind",
    "zone_id",
    "title",
    "period_days",
    "width",
    "metric",
    "destination",
    "note",
)

Tile = dict[str, Any]
Section = tuple[str, list[Tile]]


class TemplateError(Exception):
    """Fachlicher Fehler (Name vergeben, Vorlage unbekannt)."""


def _tile_of(row: DashboardTile | DashboardTemplateTile) -> Tile:
    return {field: getattr(row, field) for field in TILE_FIELDS}


def template_sections(session: Session, template_id: int) -> list[Section]:
    sections = session.scalars(
        select(DashboardTemplateSection)
        .where(DashboardTemplateSection.template_id == template_id)
        .order_by(DashboardTemplateSection.position)
    ).all()
    result: list[Section] = []
    for section in sections:
        tiles = session.scalars(
            select(DashboardTemplateTile)
            .where(DashboardTemplateTile.section_id == section.id)
            .order_by(DashboardTemplateTile.position)
        ).all()
        result.append((section.title, [_tile_of(tile) for tile in tiles]))
    return result


def list_templates(session: Session) -> list[DashboardTemplate]:
    return list(session.scalars(select(DashboardTemplate).order_by(DashboardTemplate.name)))


def _name_free(session: Session, name: str, *, except_id: int | None = None) -> None:
    other = session.scalar(select(DashboardTemplate).where(DashboardTemplate.name == name))
    if other is not None and other.id != except_id:
        raise TemplateError(f"Eine Vorlage „{name}“ gibt es bereits.")


def _write_sections(session: Session, template_id: int, sections: list[Section]) -> None:
    section_ids = select(DashboardTemplateSection.id).where(
        DashboardTemplateSection.template_id == template_id
    )
    # Ausdrücklich in dieser Reihenfolge (SQLite erzwingt Fremdschlüssel nur auf Anforderung).
    session.execute(
        delete(DashboardTemplateTile).where(DashboardTemplateTile.section_id.in_(section_ids))
    )
    session.execute(
        delete(DashboardTemplateSection).where(DashboardTemplateSection.template_id == template_id)
    )
    for position, (title, tiles) in enumerate(sections):
        row = DashboardTemplateSection(template_id=template_id, position=position, title=title)
        session.add(row)
        session.flush()
        session.add_all(
            DashboardTemplateTile(section_id=row.id, position=index, **tile)
            for index, tile in enumerate(tiles)
        )
    session.flush()


def create_template(session: Session, name: str, sections: list[Section]) -> DashboardTemplate:
    _name_free(session, name)
    template = DashboardTemplate(name=name, updated_at=utcnow())
    session.add(template)
    session.flush()
    _write_sections(session, template.id, sections)
    return template


def replace_template(
    session: Session, template: DashboardTemplate, name: str, sections: list[Section]
) -> None:
    _name_free(session, name, except_id=template.id)
    template.name = name
    template.updated_at = utcnow()
    _write_sections(session, template.id, sections)


def delete_template(session: Session, template: DashboardTemplate) -> None:
    _write_sections(session, template.id, [])
    session.delete(template)
    session.flush()


def apply_template(session: Session, user_id: int, template_id: int) -> None:
    """Kopiert die Vorlage als gespeichertes Layout in den Benutzer (ersetzt ein vorhandenes)."""
    if session.get(DashboardTemplate, template_id) is None:
        raise TemplateError("Diese Vorlage gibt es nicht.")
    sections = template_sections(session, template_id)
    section_ids = select(DashboardSection.id).where(DashboardSection.user_id == user_id)
    session.execute(delete(DashboardTile).where(DashboardTile.section_id.in_(section_ids)))
    session.execute(delete(DashboardSection).where(DashboardSection.user_id == user_id))
    for position, (title, tiles) in enumerate(sections):
        row = DashboardSection(user_id=user_id, position=position, title=title)
        session.add(row)
        session.flush()
        session.add_all(
            DashboardTile(section_id=row.id, position=index, **tile)
            for index, tile in enumerate(tiles)
        )
    layout = session.get(DashboardLayout, user_id)
    if layout is None:
        session.add(
            DashboardLayout(user_id=user_id, version=1, customized=True, updated_at=utcnow())
        )
    else:
        layout.version += 1
        layout.customized = True
        layout.updated_at = utcnow()
    session.flush()
