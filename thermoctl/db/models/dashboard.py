"""Persönliches Dashboard-Layout je Benutzer (Abschnitte, Kacheln, Version).

Datenbankagnostisch (Grundsatz 3): Kachelart, Kennzahl, Ziel, Breite und Zeitraum sind
kurze Strings beziehungsweise Zahlen mit `CheckConstraint`, kein ENUM und kein JSON.
Die Schranken stehen als Konstanten hier, damit Modell, Migration-Prüfungen und
Schnittstelle dieselben Zahlen benutzen.

Eine Kachel verweist nur auf eine Zone, sie besitzt sie nicht: Das Entfernen einer Kachel
löscht nie eine Zone, das Löschen einer Zone aber ihre Kacheln (`ON DELETE CASCADE`).
Der Fremdschlüssel ist die einzige Verbindung -- ob ein Benutzer die Zone sehen darf,
entscheidet bei jedem Lesen die Rechteprüfung, nicht das gespeicherte Layout.
"""

from datetime import datetime
from typing import Literal, get_args

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from thermoctl.db.base import Base, utcnow

MAX_SECTIONS = 12
MAX_TILES_PER_SECTION = 30
MAX_TITLE_LENGTH = 80
MAX_NOTE_LENGTH = 500

TileKind = Literal[
    "room",
    "chart",
    "metric",
    "agenda",
    "schedule",
    "heating",
    "devices",
    "system",
    "note",
    "shortcut",
    "control",
]
Metric = Literal["rooms", "heating", "issues", "outdoor"]
Destination = Literal[
    "schedule", "absence", "history", "devices", "mapping", "solar", "operation", "account"
]
Width = Literal[3, 4, 6, 8, 12]
PeriodDays = Literal[1, 7, 30, 90]

TILE_KINDS: tuple[str, ...] = get_args(TileKind)
METRICS: tuple[str, ...] = get_args(Metric)
DESTINATIONS: tuple[str, ...] = get_args(Destination)
WIDTHS: tuple[int, ...] = get_args(Width)
PERIODS_DAYS: tuple[int, ...] = get_args(PeriodDays)


def _in(column: str, values: tuple[str, ...] | tuple[int, ...]) -> str:
    quoted = ", ".join(f"'{v}'" if isinstance(v, str) else str(v) for v in values)
    return f"{column} IN ({quoted})"


class DashboardLayout(Base):
    """Eine Zeile je Benutzer: Version für die Konflikterkennung beim Speichern.

    `version` zählt jede Änderung (Speichern, Standard wiederherstellen) hoch und wird
    nie zurückgesetzt, auch nicht beim Wiederherstellen -- sonst könnte ein veralteter
    Stand nach Löschen und Neuanlegen wieder zur aktuellen Version passen. Ohne Zeile
    gilt Version 0 und das Standardlayout.
    """

    __tablename__ = "dashboard_layout"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), primary_key=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    customized: Mapped[bool] = mapped_column(Boolean, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class DashboardSection(Base):
    __tablename__ = "dashboard_section"
    __table_args__ = (
        UniqueConstraint("user_id", "position", name="uq_dashboard_section_user_position"),
        CheckConstraint("position >= 0", name="position_nichtnegativ"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(MAX_TITLE_LENGTH), nullable=False)


class DashboardTile(Base):
    __tablename__ = "dashboard_tile"
    __table_args__ = (
        UniqueConstraint("section_id", "position", name="uq_dashboard_tile_section_position"),
        CheckConstraint("position >= 0", name="position_nichtnegativ"),
        CheckConstraint(_in("kind", TILE_KINDS), name="kind_bekannt"),
        CheckConstraint(_in("width", WIDTHS), name="width_bekannt"),
        CheckConstraint(_in("period_days", PERIODS_DAYS), name="period_bekannt"),
        CheckConstraint(
            "metric IS NULL OR " + _in("metric", METRICS), name="metric_bekannt"
        ),
        CheckConstraint(
            "destination IS NULL OR " + _in("destination", DESTINATIONS),
            name="destination_bekannt",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    section_id: Mapped[int] = mapped_column(
        ForeignKey("dashboard_section.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    zone_id: Mapped[int | None] = mapped_column(
        ForeignKey("zone.id", ondelete="CASCADE"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(MAX_TITLE_LENGTH), nullable=False)
    period_days: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    metric: Mapped[str | None] = mapped_column(String(16), nullable=True)
    destination: Mapped[str | None] = mapped_column(String(16), nullable=True)
    note: Mapped[str] = mapped_column(Text, nullable=False)


class DashboardTemplate(Base):
    """Eine benannte Vorlage für die Übersicht neuer Benutzer.

    Spiegelt Abschnitte und Kacheln eines persönlichen Layouts (eigene Tabellen statt
    JSON, Grundsatz 3). Eine Vorlage kopiert beim Anlegen eines Benutzers ihren Inhalt in
    dessen Layout; danach besteht keine Verbindung mehr. Rechte prüft weiterhin das Lesen
    des Layouts, nicht die Vorlage.
    """

    __tablename__ = "dashboard_template"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(MAX_TITLE_LENGTH), nullable=False, unique=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class DashboardTemplateSection(Base):
    __tablename__ = "dashboard_template_section"
    __table_args__ = (
        UniqueConstraint("template_id", "position", name="uq_dashboard_template_section_pos"),
        CheckConstraint("position >= 0", name="position_nichtnegativ"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    template_id: Mapped[int] = mapped_column(
        ForeignKey("dashboard_template.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(MAX_TITLE_LENGTH), nullable=False)


class DashboardTemplateTile(Base):
    __tablename__ = "dashboard_template_tile"
    __table_args__ = (
        UniqueConstraint("section_id", "position", name="uq_dashboard_template_tile_pos"),
        CheckConstraint("position >= 0", name="position_nichtnegativ"),
        CheckConstraint(_in("kind", TILE_KINDS), name="kind_bekannt"),
        CheckConstraint(_in("width", WIDTHS), name="width_bekannt"),
        CheckConstraint(_in("period_days", PERIODS_DAYS), name="period_bekannt"),
        CheckConstraint(
            "metric IS NULL OR " + _in("metric", METRICS), name="metric_bekannt"
        ),
        CheckConstraint(
            "destination IS NULL OR " + _in("destination", DESTINATIONS),
            name="destination_bekannt",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    section_id: Mapped[int] = mapped_column(
        ForeignKey("dashboard_template_section.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    zone_id: Mapped[int | None] = mapped_column(
        ForeignKey("zone.id", ondelete="CASCADE"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(MAX_TITLE_LENGTH), nullable=False)
    period_days: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    metric: Mapped[str | None] = mapped_column(String(16), nullable=True)
    destination: Mapped[str | None] = mapped_column(String(16), nullable=True)
    note: Mapped[str] = mapped_column(Text, nullable=False)
