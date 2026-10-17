"""Persönliches Dashboard-Layout für die Svelte-Oberfläche (`/ui-api/dashboard`).

Dünner Adapter (Grundsatz 6): gespeichert wird nur die Anordnung -- Abschnitte, Kacheln,
Kachelart, Breite, Zeitraum, Titel, Notiz und ein Verweis auf eine Zone. Welche Daten eine
Kachel zeigt, holt die Oberfläche aus den Lese-Endpunkten, die ihre Rechte selbst prüfen.

Sicherheitsmodell: Das gespeicherte Layout ist nie eine Berechtigung.

* **Lesen** filtert bei jedem Abruf gegen die aktuellen Rechte. Eine Kachel mit einer Zone,
  die der Principal nicht mit `zone.read` sehen darf, und eine Kachel, deren Funktion ihm
  nicht (mehr) zusteht, fehlt in der Antwort vollständig -- kein Name, kein Platzhalter.
* **Schreiben** lehnt eine Zone, die es nicht gibt, und eine, die der Principal nicht sehen
  darf, mit derselben Meldung ab (kein Hinweis auf Existenz).
* Die Fremdschlüssel-Kaskade löscht mit einer Zone ihre Kacheln; eine Kachel zu entfernen
  löscht nie eine Zone.

Version: `GET` liefert `version`, `PUT` muss sie zurückgeben. Eine Änderung zwischen Lesen
und Speichern (anderer Tab, anderes Gerät) endet in 409, nicht im stillen Überschreiben.

Kein Audit-Eintrag: Das Layout ist eine persönliche Darstellungseinstellung ohne Wirkung auf
Anlage, Regelung oder fremde Benutzer. Das Repo protokolliert (`audit`, `change_log`) nur
Sicherheits- und Anlagenänderungen; ähnliche Darstellungseinstellungen werden serverseitig
nirgends erfasst.

Fehler sind `{"detail": ..., "code": ...}` (optional `"field"`), wie bei den Räumen.
Notizen sind reiner Text: sie werden als JSON-String gespeichert und ausgeliefert, nie als
HTML gedeutet; die Oberfläche setzt sie als Text ein.
"""

from dataclasses import dataclass
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import CursorResult, delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from thermoctl.auth.dependencies import csrf_protection, current_principal, get_session
from thermoctl.db.base import utcnow
from thermoctl.db.models.dashboard import (
    MAX_NOTE_LENGTH,
    MAX_SECTIONS,
    MAX_TILES_PER_SECTION,
    MAX_TITLE_LENGTH,
    DashboardLayout,
    DashboardSection,
    DashboardTile,
    Destination,
    Metric,
    PeriodDays,
    TileKind,
    Width,
)
from thermoctl.domain.authz import has_permission, visible_zones
from thermoctl.domain.principal import Principal
from thermoctl.domain.ui_profile import WebUiProfile
from thermoctl.web.ui_api_rooms import RoomApiError, _RoomsRoute

router = APIRouter(
    prefix="/ui-api",
    dependencies=[Depends(csrf_protection)],
    include_in_schema=False,
    route_class=_RoomsRoute,
)

#: Kachelarten, die eine Zone zeigen und ohne sie sinnlos sind.
ZONE_KINDS = frozenset({"room", "chart", "schedule", "heating", "control"})
#: Kachelarten und Ziele, die nur zur Anlagensicht gehören (wie die Seiten, auf die sie
#: zeigen): ein Mieterprofil bekommt sie auch bei vorhandenem Recht nicht.
ADMIN_KINDS = frozenset({"devices", "system"})
ADMIN_DESTINATIONS = frozenset({"devices", "mapping", "solar", "operation"})

#: Welches Recht ein Kachelziel braucht (`None`: keines, `"zone.read"`: irgendeine Zone).
DESTINATION_PERMISSION: dict[str, str | None] = {
    "schedule": "zone.read",
    "absence": "zone.read",
    "history": "zone.read",
    "devices": "device.read",
    "mapping": "device.read",
    "solar": "setting.manage",
    "operation": "control.arm",
    "account": None,
}

UNKNOWN_ZONE = "Dieser Raum ist nicht verfügbar."
UNAVAILABLE_TILE = "Diese Kachel ist nicht verfügbar."

Title = Annotated[str, StringConstraints(strip_whitespace=True, max_length=MAX_TITLE_LENGTH)]


# -- Ein-/Ausgabe -----------------------------------------------------------------------


class TileModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: TileKind
    zone_id: int | None = None
    title: Title = ""
    period_days: PeriodDays = 1
    width: Width = 4
    metric: Metric | None = None
    destination: Destination | None = None
    note: str = Field("", max_length=MAX_NOTE_LENGTH)


class SectionModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Annotated[Title, Field(min_length=1)]
    tiles: list[TileModel] = Field(max_length=MAX_TILES_PER_SECTION)


class LayoutIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=0)
    sections: list[SectionModel] = Field(max_length=MAX_SECTIONS)


class LayoutOut(BaseModel):
    version: int
    customized: bool
    sections: list[SectionModel]


# -- Rechte -----------------------------------------------------------------------------


@dataclass(frozen=True)
class _Access:
    """Was dieser Principal jetzt sehen darf -- einmal je Anfrage bestimmt."""

    principal: Principal
    zone_order: tuple[int, ...]  # Reihenfolge der Anlage (`visible_zones`)
    zone_ids: frozenset[int]

    @property
    def admin_ui(self) -> bool:
        return self.principal.ui_profile is WebUiProfile.ADMIN

    def permitted(self, code: str | None) -> bool:
        if code is None:
            return True
        if code == "zone.read":
            return bool(self.zone_ids)
        return has_permission(self.principal, code)

    def tile_allowed(self, tile: TileModel) -> bool:
        """Darf diese Kachel angezeigt werden? Die einzige Stelle für diese Frage."""
        kind = tile.kind
        if kind in ADMIN_KINDS and not self.admin_ui:
            return False
        if kind in ZONE_KINDS:
            return tile.zone_id in self.zone_ids
        if kind in {"metric", "agenda"}:
            return self.permitted("zone.read")
        if kind == "devices":
            return self.permitted("device.read")
        if kind == "system":
            return self.permitted("control.arm")
        if kind == "shortcut":
            destination = tile.destination
            if destination is None:
                return False
            if destination in ADMIN_DESTINATIONS and not self.admin_ui:
                return False
            return self.permitted(DESTINATION_PERMISSION[destination])
        return True  # "note": gehört dem Benutzer, braucht kein Recht


def _access(session: Session, principal: Principal) -> _Access:
    zones = visible_zones(session, principal, "zone.read")
    order = tuple(zone.id for zone in zones)
    return _Access(principal, order, frozenset(order))


# -- Standardlayout ---------------------------------------------------------------------


def _default_layout(access: _Access) -> list[SectionModel]:
    zone_ids = access.zone_order
    """Das Layout ohne gespeicherte Anpassung; entsteht bei jedem Abruf aus den aktuell
    sichtbaren Räumen (neue Räume erscheinen von selbst, bis jemand speichert)."""
    summary = [
        TileModel(kind="metric", metric=metric, width=3)
        for metric in ("rooms", "heating", "issues", "outdoor")
    ]
    # Die Kachel „Anlagenstatus“ (Freigabe, Versand, Primärrolle) gehört zum Standard: wer sie
    # nicht darf, bekommt sie durch `_filtered` nicht; die festen Anlagenhinweise oben in der
    # Übersicht hängen nicht von ihr ab.
    summary.append(TileModel(kind="system", width=6))
    today: list[TileModel] = []
    if zone_ids:
        today.append(TileModel(kind="chart", zone_id=zone_ids[0], width=8))
    today.append(TileModel(kind="agenda"))
    rooms = [
        TileModel(kind="room", zone_id=zone_id) for zone_id in zone_ids[:MAX_TILES_PER_SECTION]
    ]
    return [
        SectionModel(title="Auf einen Blick", tiles=summary),
        SectionModel(title="Dein Tag", tiles=today),
        SectionModel(title="Deine Räume", tiles=rooms),
    ]


# -- Lesen ------------------------------------------------------------------------------


def _filtered(access: _Access, sections: list[SectionModel]) -> list[SectionModel]:
    return [
        SectionModel(
            title=section.title,
            tiles=[tile for tile in section.tiles if access.tile_allowed(tile)],
        )
        for section in sections
    ]


def _stored_sections(session: Session, user_id: int) -> list[SectionModel]:
    sections = list(
        session.scalars(
            select(DashboardSection)
            .where(DashboardSection.user_id == user_id)
            .order_by(DashboardSection.position)
        )
    )
    tiles_by_section: dict[int, list[TileModel]] = {section.id: [] for section in sections}
    rows = session.scalars(
        select(DashboardTile)
        .where(DashboardTile.section_id.in_(list(tiles_by_section)))
        .order_by(DashboardTile.section_id, DashboardTile.position)
    )
    for row in rows:
        tiles_by_section[row.section_id].append(
            TileModel(
                kind=row.kind,
                zone_id=row.zone_id,
                title=row.title,
                period_days=row.period_days,
                width=row.width,
                metric=row.metric,
                destination=row.destination,
                note=row.note,
            )
        )
    return [
        SectionModel(title=section.title, tiles=tiles_by_section[section.id])
        for section in sections
    ]


def _read(session: Session, principal: Principal) -> LayoutOut:
    access = _access(session, principal)
    row = session.get(DashboardLayout, principal.user_id)
    if row is None or not row.customized:
        return LayoutOut(
            version=row.version if row is not None else 0,
            customized=False,
            sections=_filtered(access, _default_layout(access)),
        )
    return LayoutOut(
        version=row.version,
        customized=True,
        sections=_filtered(access, _stored_sections(session, principal.user_id)),
    )


@router.get("/dashboard")
def read_dashboard(
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> LayoutOut:
    return _read(session, principal)


# -- Schreiben --------------------------------------------------------------------------


def _normalised(access: _Access, tile: TileModel) -> TileModel:
    """Prüft eine Kachel und streicht, was zu ihrer Art nicht gehört.

    Die Oberfläche trägt an jeder Kachel alle Felder; was die Art nicht braucht, wird
    ohne Beanstandung verworfen, was sie braucht und fehlt, ist ein 422.
    """
    kind = tile.kind
    if kind in ZONE_KINDS:
        if tile.zone_id is None or tile.zone_id not in access.zone_ids:
            # Unbekannt und nicht sichtbar sind ununterscheidbar.
            raise RoomApiError(422, UNKNOWN_ZONE, "validation", "zone_id")
    if kind == "metric" and tile.metric is None:
        raise RoomApiError(422, "Bitte eine Kennzahl wählen.", "validation", "metric")
    if kind == "shortcut" and tile.destination is None:
        raise RoomApiError(422, "Bitte ein Ziel wählen.", "validation", "destination")
    if not access.tile_allowed(tile):
        raise RoomApiError(422, UNAVAILABLE_TILE, "validation", "kind")
    return TileModel(
        kind=kind,
        zone_id=tile.zone_id if kind in ZONE_KINDS else None,
        title=tile.title,
        period_days=tile.period_days if kind in {"chart", "heating"} else 1,
        width=tile.width,
        metric=tile.metric if kind == "metric" else None,
        destination=tile.destination if kind == "shortcut" else None,
        note=tile.note if kind == "note" else "",
    )


def _claim_version(session: Session, user_id: int, version: int) -> None:
    """Zählt die Version hoch -- atomar, nur wenn sie noch die gelesene ist.

    Mit Zeile: bedingtes UPDATE (zwei gleichzeitige Speicherungen derselben Version:
    genau eine trifft die Zeile). Ohne Zeile: INSERT, dessen Schlüsselkonflikt den
    Verlierer eines gleichzeitigen Erstspeicherns erkennt.
    """
    conflict = RoomApiError(
        409,
        "Das Layout wurde inzwischen geändert. Bitte neu laden.",
        "conflict",
        "version",
    )
    now = utcnow()
    row = session.get(DashboardLayout, user_id)
    if row is None:
        if version != 0:
            raise conflict
        try:
            with session.begin_nested():
                session.add(
                    DashboardLayout(user_id=user_id, version=1, customized=True, updated_at=now)
                )
        except IntegrityError as exc:
            raise conflict from exc
        return
    result = session.execute(
        update(DashboardLayout)
        .where(DashboardLayout.user_id == user_id, DashboardLayout.version == version)
        .values(version=version + 1, customized=True, updated_at=now)
        .execution_options(synchronize_session=False)
    )
    if cast("CursorResult[Any]", result).rowcount != 1:
        raise conflict
    session.expire(row)


def _clear_sections(session: Session, user_id: int) -> None:
    """Ausdrücklich in dieser Reihenfolge und nicht auf die Kaskade verlassen (SQLite
    erzwingt Fremdschlüssel nur auf Anforderung)."""
    section_ids = select(DashboardSection.id).where(DashboardSection.user_id == user_id)
    session.execute(delete(DashboardTile).where(DashboardTile.section_id.in_(section_ids)))
    session.execute(delete(DashboardSection).where(DashboardSection.user_id == user_id))


@router.put("/dashboard")
def save_dashboard(
    body: LayoutIn,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> LayoutOut:
    access = _access(session, principal)
    sections = [
        SectionModel(
            title=section.title,
            tiles=[_normalised(access, tile) for tile in section.tiles],
        )
        for section in body.sections
    ]
    _claim_version(session, principal.user_id, body.version)
    _clear_sections(session, principal.user_id)
    for position, section in enumerate(sections):
        row = DashboardSection(user_id=principal.user_id, position=position, title=section.title)
        session.add(row)
        session.flush()
        session.add_all(
            DashboardTile(
                section_id=row.id,
                position=index,
                kind=tile.kind,
                zone_id=tile.zone_id,
                title=tile.title,
                period_days=tile.period_days,
                width=tile.width,
                metric=tile.metric,
                destination=tile.destination,
                note=tile.note,
            )
            for index, tile in enumerate(section.tiles)
        )
    session.flush()
    return _read(session, principal)


@router.delete("/dashboard")
def reset_dashboard(
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> LayoutOut:
    """Standard wiederherstellen: gespeicherte Anordnung weg, Version zählt weiter."""
    _clear_sections(session, principal.user_id)
    row = session.get(DashboardLayout, principal.user_id)
    if row is None:
        try:
            with session.begin_nested():
                session.add(
                    DashboardLayout(
                        user_id=principal.user_id, version=1, customized=False, updated_at=utcnow()
                    )
                )
        except IntegrityError:
            # Ein gleichzeitiges Zurücksetzen (oder Erstspeichern) hat die Zeile soeben
            # angelegt: Das Ziel -- Standard, Zeile vorhanden -- ist erreicht, also kein Fehler.
            pass
    else:
        row.version += 1
        row.customized = False
        row.updated_at = utcnow()
    session.flush()
    return _read(session, principal)
