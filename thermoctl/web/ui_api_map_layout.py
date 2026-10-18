"""Anordnung der Gerätekarte für die Svelte-Oberfläche (`/ui-api/devices/map/layout`).

Dünner Adapter (Grundsatz 6): gespeichert wird nur die Darstellung -- Lage, eingeklappt,
Verschiebung und Zoom. Die Karte selbst (Knoten, Linien) kommt weiter aus `/devices/map`.

* `GET` liefert die persönliche Anordnung, sonst die gemeinsame Vorgabe, sonst nichts
  (`source`: `personal`, `default`, `none`); die Oberfläche ordnet bei `none` selbst an.
* `PUT` / `DELETE` ändern die persönliche Anordnung, `PUT` / `DELETE .../default` die
  gemeinsame Vorgabe -- Letzteres nur mit dem anlagenweiten Recht `device.manage`.

Sicherheitsmodell: Die gespeicherte Anordnung ist nie eine Berechtigung. Beim Lesen werden
Knoten, die der Principal nicht sehen darf (Geräte nur fremder Räume, Räume ohne
`device.read`), und verwaiste Knoten (Gerät oder Raum gelöscht) entfernt -- kein Name, kein
Platzhalter. Beim Speichern wird ein solcher Knoten wie ein unbekannter abgelehnt, mit
derselben Meldung. Ein Knoten verweist nur per Schlüssel auf Gerät oder Raum; ein gelöschtes
Gerät kann das Speichern oder Lesen nie scheitern lassen.

Version: `GET` liefert `version` (persönlich) und `default_version` (Vorgabe) samt
`has_default`; `PUT` muss die passende zurückgeben, sonst 409. Kein Audit-Eintrag: Eine
Darstellungseinstellung ohne Wirkung auf Anlage oder Regelung (wie das
Dashboard-Layout); die Vorgabe wirkt nur auf die Anordnung anderer Benutzer, nie auf Rechte
oder Daten.
"""

import re
from dataclasses import dataclass
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import CursorResult, delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from thermoctl.auth.dependencies import csrf_protection, current_principal, get_session
from thermoctl.db.base import utcnow
from thermoctl.db.models.device import Device
from thermoctl.db.models.map_layout import (
    COORD_LIMIT,
    MAX_NODE_KEY_LENGTH,
    MAX_NODES,
    ZOOM_MAX,
    ZOOM_MIN,
    MapLayout,
    MapLayoutDefault,
    MapLayoutDefaultNode,
    MapLayoutNode,
)
from thermoctl.domain.authz import has_permission, visible_zones
from thermoctl.domain.principal import Principal
from thermoctl.web.ui_api_devices import _hidden_device_ids
from thermoctl.web.ui_api_rooms import RoomApiError, _RoomsRoute

router = APIRouter(
    prefix="/ui-api",
    dependencies=[Depends(csrf_protection)],
    include_in_schema=False,
    route_class=_RoomsRoute,
)

#: Schlüssel, wie die Karte sie liefert (`mapping_map.py`): `dev-<id>` und `zone-<id>`.
NODE_KEY = re.compile(r"^(?:dev|zone)-[0-9]{1,9}$")
UNKNOWN_NODE = "Dieses Gerät oder dieser Raum ist nicht verfügbar."
CONFLICT = "Die Anordnung wurde inzwischen geändert. Bitte neu laden."

Coordinate = Annotated[float, Field(ge=-COORD_LIMIT, le=COORD_LIMIT, allow_inf_nan=False)]


# -- Ein-/Ausgabe -----------------------------------------------------------------------


class NodeModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: Annotated[str, Field(max_length=MAX_NODE_KEY_LENGTH, pattern=NODE_KEY.pattern)]
    x: Coordinate | None = None
    y: Coordinate | None = None
    collapsed: bool = False

    @model_validator(mode="after")
    def _both_or_neither(self) -> NodeModel:
        if (self.x is None) != (self.y is None):
            raise ValueError("x und y gehören zusammen")
        return self


class ViewModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: Coordinate
    y: Coordinate
    k: Annotated[float, Field(ge=ZOOM_MIN, le=ZOOM_MAX, allow_inf_nan=False)]


class LayoutIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=0)
    nodes: list[NodeModel] = Field(max_length=MAX_NODES)
    view: ViewModel | None = None


class LayoutOut(BaseModel):
    source: str  # "personal" | "default" | "none"
    version: int
    default_version: int
    has_default: bool
    may_set_default: bool
    nodes: list[NodeModel]
    view: ViewModel | None


# -- Zugriff ----------------------------------------------------------------------------


def _visible_keys(session: Session, principal: Principal) -> frozenset[str]:
    """Die Schlüssel der Knoten, die die Karte diesem Principal zeigt (wie `/devices/map`)."""
    zones = visible_zones(session, principal, "device.read")
    if not zones and not has_permission(principal, "device.read"):
        raise RoomApiError(403, "Dafür fehlt die Berechtigung.", "forbidden")
    hidden = _hidden_device_ids(session, principal)
    keys = {f"zone-{zone.id}" for zone in zones}
    keys.update(
        f"dev-{device_id}"
        for device_id in session.scalars(select(Device.id))
        if device_id not in hidden
    )
    return frozenset(keys)


def _may_set_default(principal: Principal) -> bool:
    return has_permission(principal, "device.manage")


# -- Zwei Speicher, eine Logik ----------------------------------------------------------


@dataclass(frozen=True)
class _Store:
    """Persönliche Anordnung (`user_id` gesetzt) oder gemeinsame Vorgabe (`None`)."""

    user_id: int | None

    def head(self, session: Session) -> MapLayout | MapLayoutDefault | None:
        if self.user_id is None:
            return session.get(MapLayoutDefault, 1)
        return session.get(MapLayout, self.user_id)

    def nodes(self, session: Session) -> list[NodeModel]:
        if self.user_id is None:
            rows: Any = session.scalars(
                select(MapLayoutDefaultNode).order_by(MapLayoutDefaultNode.id)
            )
        else:
            rows = session.scalars(
                select(MapLayoutNode)
                .where(MapLayoutNode.user_id == self.user_id)
                .order_by(MapLayoutNode.id)
            )
        return [NodeModel(key=r.node_key, x=r.x, y=r.y, collapsed=r.collapsed) for r in rows]

    def clear_nodes(self, session: Session) -> None:
        if self.user_id is None:
            session.execute(delete(MapLayoutDefaultNode))
        else:
            session.execute(delete(MapLayoutNode).where(MapLayoutNode.user_id == self.user_id))

    def add_nodes(self, session: Session, nodes: list[NodeModel]) -> None:
        if self.user_id is None:
            session.add_all(
                MapLayoutDefaultNode(node_key=n.key, x=n.x, y=n.y, collapsed=n.collapsed)
                for n in nodes
            )
        else:
            session.add_all(
                MapLayoutNode(
                    user_id=self.user_id, node_key=n.key, x=n.x, y=n.y, collapsed=n.collapsed
                )
                for n in nodes
            )

    def _new_head(self, view: ViewModel | None, customized: bool) -> MapLayout | MapLayoutDefault:
        values: dict[str, Any] = {
            "version": 1,
            "customized": customized,
            "view_x": view.x if view else None,
            "view_y": view.y if view else None,
            "view_k": view.k if view else None,
            "updated_at": utcnow(),
        }
        if self.user_id is None:
            return MapLayoutDefault(id=1, **values)
        return MapLayout(user_id=self.user_id, **values)

    def claim(self, session: Session, version: int, view: ViewModel | None) -> None:
        """Zählt die Version hoch -- atomar, nur wenn sie noch die gelesene ist.

        Mit Zeile: bedingtes UPDATE (bei zwei gleichzeitigen Speicherungen derselben Version
        trifft genau eine die Zeile). Ohne Zeile: INSERT, dessen Schlüsselkonflikt den
        Verlierer eines gleichzeitigen Erstspeicherns erkennt.
        """
        conflict = RoomApiError(409, CONFLICT, "conflict", "version")
        row = self.head(session)
        if row is None:
            if version != 0:
                raise conflict
            try:
                with session.begin_nested():
                    session.add(self._new_head(view, True))
            except IntegrityError as exc:
                raise conflict from exc
            return
        statement = (
            update(MapLayoutDefault).where(
                MapLayoutDefault.id == 1, MapLayoutDefault.version == version
            )
            if self.user_id is None
            else update(MapLayout).where(
                MapLayout.user_id == self.user_id, MapLayout.version == version
            )
        )
        result = session.execute(
            statement.values(
                version=version + 1,
                customized=True,
                view_x=view.x if view else None,
                view_y=view.y if view else None,
                view_k=view.k if view else None,
                updated_at=utcnow(),
            )
            .execution_options(synchronize_session=False)
        )
        if cast("CursorResult[Any]", result).rowcount != 1:
            raise conflict
        session.expire(row)

    def reset(self, session: Session) -> None:
        """Anordnung weg, Version zählt weiter (nie zurückgesetzt)."""
        self.clear_nodes(session)
        row = self.head(session)
        if row is None:
            try:
                with session.begin_nested():
                    session.add(self._new_head(None, False))
            except IntegrityError:
                # Ein gleichzeitiges Zurücksetzen hat die Zeile soeben angelegt: Ziel erreicht.
                pass
            return
        row.version += 1
        row.customized = False
        row.view_x = row.view_y = row.view_k = None
        row.updated_at = utcnow()

    def view(self, session: Session) -> ViewModel | None:
        row = self.head(session)
        if row is None or not row.customized or row.view_k is None:
            return None
        assert row.view_x is not None and row.view_y is not None
        return ViewModel(x=row.view_x, y=row.view_y, k=row.view_k)


# -- Lesen ------------------------------------------------------------------------------


def _read(session: Session, principal: Principal) -> LayoutOut:
    visible = _visible_keys(session, principal)
    personal = _Store(principal.user_id)
    shared = _Store(None)
    own = personal.head(session)
    default = shared.head(session)
    if own is not None and own.customized:
        source, store = "personal", personal
    elif default is not None and default.customized:
        source, store = "default", shared
    else:
        source, store = "none", None
    return LayoutOut(
        source=source,
        version=own.version if own is not None else 0,
        default_version=default.version if default is not None else 0,
        has_default=default is not None and default.customized,
        may_set_default=_may_set_default(principal),
        nodes=[n for n in store.nodes(session) if n.key in visible] if store else [],
        view=store.view(session) if store else None,
    )


@router.get("/devices/map/layout")
def read_layout(
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> LayoutOut:
    return _read(session, principal)


# -- Schreiben --------------------------------------------------------------------------


def _save(session: Session, principal: Principal, store: _Store, body: LayoutIn) -> LayoutOut:
    visible = _visible_keys(session, principal)
    keys = [n.key for n in body.nodes]
    if len(set(keys)) != len(keys):
        raise RoomApiError(422, "Jeder Knoten darf nur einmal vorkommen.", "validation", "nodes")
    if any(key not in visible for key in keys):
        # Unbekannt, gelöscht und nicht sichtbar sind ununterscheidbar.
        raise RoomApiError(422, UNKNOWN_NODE, "validation", "nodes")
    store.claim(session, body.version, body.view)
    store.clear_nodes(session)
    store.add_nodes(session, body.nodes)
    session.flush()
    return _read(session, principal)


@router.put("/devices/map/layout")
def save_layout(
    body: LayoutIn,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> LayoutOut:
    return _save(session, principal, _Store(principal.user_id), body)


@router.delete("/devices/map/layout")
def reset_layout(
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> LayoutOut:
    """Zurück zur Vorgabe (oder zur automatischen Anordnung)."""
    _visible_keys(session, principal)  # 403 ohne Recht
    _Store(principal.user_id).reset(session)
    session.flush()
    return _read(session, principal)


def _need_default_right(principal: Principal) -> None:
    if not _may_set_default(principal):
        raise RoomApiError(403, "Dafür fehlt die Berechtigung.", "forbidden")


@router.put("/devices/map/layout/default")
def save_default(
    body: LayoutIn,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> LayoutOut:
    _need_default_right(principal)
    return _save(session, principal, _Store(None), body)


@router.delete("/devices/map/layout/default")
def remove_default(
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> LayoutOut:
    _need_default_right(principal)
    _Store(None).reset(session)
    session.flush()
    return _read(session, principal)
