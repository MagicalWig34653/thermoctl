# ruff: noqa: E501
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    false,
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
    is_group: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False
    )
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


class ControllerBinding(Base):
    """Was ein Tastendruck an einem Bediengeraet ausloest.

    Warum das in der Datenbank steht und nicht im Quelltext: Wie ein Geraet seine Tasten
    benennt, entscheidet Zigbee2MQTT je Modell -- der eine schickt `single_plus`, der
    naechste `button_1_single`, der uebernaechste `up_open`. Eine Tabelle im Code waere
    genau die Sorte harte Verdrahtung, gegen die dieses Projekt gebaut ist (Grundsatz 1),
    und sie waere fuer jedes Geraet falsch, das noch nicht darin steht.

    Stattdessen merkt sich der Dienst, welche Aktionen ein Geraet **tatsaechlich**
    geschickt hat, und die Oberflaeche laesst sie zuordnen. Damit funktioniert jedes
    Geraet, das ueberhaupt ein `action`-Feld sendet, ohne dass jemand sein Datenblatt
    liest.
    """

    __tablename__ = "controller_binding"
    __table_args__ = (
        UniqueConstraint("device_id", "action_code", name="aktion_je_geraet"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_id: Mapped[int] = mapped_column(
        ForeignKey("device.id", ondelete="CASCADE"), nullable=False
    )
    # Der Wert, den Zigbee2MQTT im Feld `action` schickt.
    action_code: Mapped[str] = mapped_column(String(64), nullable=False)
    command_id: Mapped[int] = mapped_column(
        ForeignKey("controller_command.id"), nullable=False
    )
    # Nur fuer Befehle, die eine Groesse brauchen -- die Schrittweite beim Verstellen.
    # Sonst None: Ein Boost hat keine Schrittweite, und eine 0 daneben waere eine
    # Behauptung ueber etwas, das es nicht gibt.
    #
    # Eine Nachkommastelle, nicht zwei: Ein Sollwert traegt nach `temperatur_pruefen`
    # ohnehin nur eine, und ein Viertelgrad Schrittweite waere eine Zusage, die beim
    # ersten Druck gebrochen wuerde.
    step_k: Mapped[Decimal | None] = mapped_column(Numeric(3, 1), nullable=True)


class DeviceProperty(Base):
    __tablename__ = "device_property"
    __table_args__ = (UniqueConstraint("device_id", "name", name="merkmal_je_geraet"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("device.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(191), nullable=False)
    value_type: Mapped[str] = mapped_column(String(16), nullable=False)
    unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    min_value: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    max_value: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    is_readable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    is_writable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    last_value_text: Mapped[str | None] = mapped_column(String(191), nullable=True)
    last_value_number: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    last_value_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class DevicePropertyValue(Base):
    __tablename__ = "device_property_value"
    __table_args__ = (UniqueConstraint("property_id", "value", name="wert_je_merkmal"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    property_id: Mapped[int] = mapped_column(ForeignKey("device_property.id", ondelete="CASCADE"), nullable=False)
    value: Mapped[str] = mapped_column(String(191), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class ControllerChannel(Base):
    __tablename__ = "controller_channel"
    __table_args__ = (UniqueConstraint("device_id", "property_name", name="kanal_je_merkmal"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("device.id", ondelete="CASCADE"), nullable=False)
    property_name: Mapped[str] = mapped_column(String(191), nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    kind_id: Mapped[int] = mapped_column(ForeignKey("channel_kind.id"), nullable=False)
    zone_id: Mapped[int | None] = mapped_column(ForeignKey("zone.id", ondelete="CASCADE"), nullable=True)
    source_device_id: Mapped[int | None] = mapped_column(ForeignKey("device.id", ondelete="SET NULL"), nullable=True)
    fixed_text: Mapped[str | None] = mapped_column(String(191), nullable=True)
    fixed_number: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
