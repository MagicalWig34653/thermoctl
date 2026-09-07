from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from thermoctl.db.base import Base, utcnow


class User(Base):
    __tablename__ = "user"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AccessGroup(Base):
    """Not called `group` — that is a reserved word in both SQLite and MariaDB."""

    __tablename__ = "access_group"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Welche Oberfläche die Mitglieder dieser Gruppe bekommen -- "admin" oder
    # "tenant", siehe `domain.ui_profile`. Ausdrücklich keine Berechtigung: was ein
    # Mitglied darf, steht weiterhin allein in `group_permission`. Als Zeichenkette
    # und nicht als ENUM, weil das Projekt datenbankagnostisch bleibt (Grundsatz 3);
    # gelesen wird sie über `parse_profile`, das einen unbekannten Wert auf den
    # Vorgabewert zurückfallen lässt.
    ui_profile: Mapped[str] = mapped_column(
        String(16), default="admin", server_default="admin", nullable=False
    )


class UserAccessGroup(Base):
    __tablename__ = "user_access_group"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), primary_key=True
    )
    access_group_id: Mapped[int] = mapped_column(
        ForeignKey("access_group.id", ondelete="CASCADE"), primary_key=True
    )


class GroupPermission(Base):
    """`zone_id = NULL` means plant-wide.

    For permissions that are not zone-scoped, `zone_id` must be empty; the domain logic
    checks this via `Permission.is_zone_scoped`, because a database constraint spanning
    two tables cannot be formulated in a portable way.
    """

    __tablename__ = "group_permission"
    __table_args__ = (
        UniqueConstraint(
            "access_group_id", "permission_id", "zone_id", name="recht_je_gruppe_und_zone"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    access_group_id: Mapped[int] = mapped_column(
        ForeignKey("access_group.id", ondelete="CASCADE"), nullable=False
    )
    permission_id: Mapped[int] = mapped_column(ForeignKey("permission.id"), nullable=False)
    zone_id: Mapped[int | None] = mapped_column(
        ForeignKey("zone.id", ondelete="CASCADE"), nullable=True
    )
