"""Anordnung der Gerätekarte: persönlich je Benutzer und als gemeinsame Vorgabe.

Datenbankagnostisch (Grundsatz 3): je Knoten eine Zeile (Knotenschlüssel, Lage,
eingeklappt), die Ansicht (Verschiebung, Zoom) als drei Zahlenspalten, keine JSON-Spalte.
Der Knotenschlüssel ist die Kennung, die die Karte liefert (`dev-<id>` für ein Gerät,
`zone-<id>` für einen Raum). Er ist bewusst **kein** Fremdschlüssel: Ein gelöschtes Gerät
oder ein gelöschter Raum lässt höchstens eine verwaiste Zeile zurück, die beim Lesen
herausgefiltert wird und beim nächsten Speichern wegfällt -- die Anordnung darf nie am
Löschen eines Geräts scheitern.

`version` zählt jede Änderung hoch (Speichern, Zurücksetzen) und wird nie zurückgesetzt;
so kann ein veralteter Stand nach Löschen und Neuanlegen nicht wieder passen. Ohne Zeile
gilt Version 0. Die Schranken stehen hier, damit Modell, Migration und Schnittstelle
dieselben Zahlen benutzen.
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from thermoctl.db.base import Base, utcnow

MAX_NODES = 500
MAX_NODE_KEY_LENGTH = 32
#: Betragsgrenze für Lage und Verschiebung (Bildpunkte der Karte).
COORD_LIMIT = 100_000.0
#: Zoomgrenzen; dieselben Zahlen wie `MIN_ZOOM`/`MAX_ZOOM` in
#: `frontend/src/lib/devices/geometry.ts`.
ZOOM_MIN = 0.2
ZOOM_MAX = 2.0

_COORD = f"BETWEEN -{COORD_LIMIT:.0f} AND {COORD_LIMIT:.0f}"
_VIEW_OK = (
    "(view_x IS NULL AND view_y IS NULL AND view_k IS NULL) OR "
    "(view_x IS NOT NULL AND view_y IS NOT NULL AND view_k IS NOT NULL AND "
    f"view_x {_COORD} AND view_y {_COORD} AND view_k BETWEEN {ZOOM_MIN} AND {ZOOM_MAX})"
)
_POS_OK = (
    "(x IS NULL AND y IS NULL) OR "
    f"(x IS NOT NULL AND y IS NOT NULL AND x {_COORD} AND y {_COORD})"
)


class MapLayout(Base):
    """Kopf der persönlichen Anordnung: Version und Ansicht."""

    __tablename__ = "map_layout"
    __table_args__ = (CheckConstraint(_VIEW_OK, name="ansicht_gueltig"),)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), primary_key=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    customized: Mapped[bool] = mapped_column(Boolean, nullable=False)
    view_x: Mapped[float | None] = mapped_column(Float, nullable=True)
    view_y: Mapped[float | None] = mapped_column(Float, nullable=True)
    view_k: Mapped[float | None] = mapped_column(Float, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class MapLayoutNode(Base):
    __tablename__ = "map_layout_node"
    __table_args__ = (
        UniqueConstraint("user_id", "node_key", name="uq_map_layout_node_user_key"),
        CheckConstraint(_POS_OK, name="lage_gueltig"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    node_key: Mapped[str] = mapped_column(String(MAX_NODE_KEY_LENGTH), nullable=False)
    x: Mapped[float | None] = mapped_column(Float, nullable=True)
    y: Mapped[float | None] = mapped_column(Float, nullable=True)
    collapsed: Mapped[bool] = mapped_column(Boolean, nullable=False)


class MapLayoutDefault(Base):
    """Kopf der gemeinsamen Vorgabe: genau eine Zeile (`id = 1`), ohne Benutzer."""

    __tablename__ = "map_layout_default"
    __table_args__ = (
        CheckConstraint("id = 1", name="einzige_zeile"),
        CheckConstraint(_VIEW_OK, name="ansicht_gueltig"),
    )

    # autoincrement=False is mandatory: MariaDB refuses a CHECK on an AUTO_INCREMENT column
    # (error 1901) and the single row is always id 1 anyway (same as `setting`, `cluster_claim`).
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False, default=1)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    customized: Mapped[bool] = mapped_column(Boolean, nullable=False)
    view_x: Mapped[float | None] = mapped_column(Float, nullable=True)
    view_y: Mapped[float | None] = mapped_column(Float, nullable=True)
    view_k: Mapped[float | None] = mapped_column(Float, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class MapLayoutDefaultNode(Base):
    __tablename__ = "map_layout_default_node"
    __table_args__ = (
        UniqueConstraint("node_key", name="uq_map_layout_default_node_key"),
        CheckConstraint(_POS_OK, name="lage_gueltig"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    node_key: Mapped[str] = mapped_column(String(MAX_NODE_KEY_LENGTH), nullable=False)
    x: Mapped[float | None] = mapped_column(Float, nullable=True)
    y: Mapped[float | None] = mapped_column(Float, nullable=True)
    collapsed: Mapped[bool] = mapped_column(Boolean, nullable=False)
