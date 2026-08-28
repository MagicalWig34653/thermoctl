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
    """Heisst nicht `group` — das ist in SQLite wie MariaDB ein reserviertes Wort."""

    __tablename__ = "access_group"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class UserAccessGroup(Base):
    __tablename__ = "user_access_group"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), primary_key=True
    )
    access_group_id: Mapped[int] = mapped_column(
        ForeignKey("access_group.id", ondelete="CASCADE"), primary_key=True
    )


class GroupPermission(Base):
    """`zone_id = NULL` heisst anlagenweit.

    Fuer nicht zonenbezogene Berechtigungen muss `zone_id` leer sein; das prueft die
    Domaenenlogik anhand von `Permission.is_zone_scoped`, weil eine Datenbankbedingung
    ueber zwei Tabellen hinweg nicht portabel formulierbar ist.
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
