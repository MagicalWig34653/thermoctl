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

from thermoctl.db.base import Base


class Device(Base):
    """Ein Geraet, wie es ueber seine Anbindung erreichbar ist.

    Getrennt von der Rolle, die es in einer Zone spielt: derselbe Schaltaktor kann ueber
    Zigbee2MQTT oder Meross haengen, ohne dass die Zone davon etwas merkt.
    """

    __tablename__ = "device"
    __table_args__ = (
        UniqueConstraint("integration_id", "external_id", name="adresse_je_anbindung"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    integration_id: Mapped[int] = mapped_column(ForeignKey("integration.id"), nullable=False)
    # 191 Zeichen: unter utf8mb4 die Grenze indizierbarer Schluessellaenge in MariaDB
    external_id: Mapped[str] = mapped_column(String(191), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class DeviceCapabilityLink(Base):
    __tablename__ = "device_capability_link"

    device_id: Mapped[int] = mapped_column(
        ForeignKey("device.id", ondelete="CASCADE"), primary_key=True
    )
    capability_id: Mapped[int] = mapped_column(ForeignKey("device_capability.id"), primary_key=True)


class ZoneDevice(Base):
    __tablename__ = "zone_device"
    __table_args__ = (
        UniqueConstraint("zone_id", "device_id", "device_role_id", name="rolle_je_zuordnung"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    zone_id: Mapped[int] = mapped_column(ForeignKey("zone.id", ondelete="CASCADE"), nullable=False)
    device_id: Mapped[int] = mapped_column(ForeignKey("device.id"), nullable=False)
    device_role_id: Mapped[int] = mapped_column(ForeignKey("device_role.id"), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
