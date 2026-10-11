from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from thermoctl.db.base import Base, utcnow


class ChangeLog(Base):
    """Eine schreibende Änderung mit Vorher/Nachher -- die Grundlage fürs Zurückrollen.

    Neben dem `audit_event` (Freitext: wer, wann, was), nicht an seiner Stelle: ohne die
    strukturierten Werte ließe sich nichts zurücknehmen. Beide entstehen in derselben
    Transaktion wie die Änderung selbst.

    `zone_id` wird beim Löschen der Zone NULL, `zone_name` hält den Namen von damals --
    der Eintrag bleibt lesbar, eine Rücknahme ist dann nicht mehr möglich.
    """

    __tablename__ = "change_log"
    __table_args__ = (Index("ix_change_log_zone_id_occurred_at", "zone_id", "occurred_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False, index=True
    )
    source_id: Mapped[int] = mapped_column(ForeignKey("actor_source.id"), nullable=False)
    actor_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    actor_token_id: Mapped[int | None] = mapped_column(
        ForeignKey("api_token.id", ondelete="SET NULL"), nullable=True
    )
    # Zeichenkette statt ENUM (Grundsatz 3); die Werte stehen in `domain.change_log`.
    change_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    zone_id: Mapped[int | None] = mapped_column(
        ForeignKey("zone.id", ondelete="SET NULL"), nullable=True
    )
    zone_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Worauf sich die Änderung bezieht, wo eine Zone nicht genügt: Punkt-Id,
    # Übersteuerungs-Id, Urlaubs-Id, Profil-Id.
    object_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    revertible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    not_revertible_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reverted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reverted_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("change_log.id", ondelete="SET NULL"), nullable=True
    )
    # Gesetzt bei einem Rücknahme-Eintrag: welche Änderung er zurücknimmt.
    reverts_id: Mapped[int | None] = mapped_column(
        ForeignKey("change_log.id", ondelete="SET NULL"), nullable=True
    )
    forced: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class ChangeLogField(Base):
    """Ein geänderter Wert einer Änderung, als kanonische Zeichenkette.

    `None` heißt leer, geerbt oder nicht vorhanden -- bei einem Regelparameter also
    „erbt von der Anlage“.
    """

    __tablename__ = "change_log_field"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    change_id: Mapped[int] = mapped_column(
        ForeignKey("change_log.id", ondelete="CASCADE"), nullable=False, index=True
    )
    field: Mapped[str] = mapped_column(String(64), nullable=False)
    before_value: Mapped[str | None] = mapped_column(String(512), nullable=True)
    after_value: Mapped[str | None] = mapped_column(String(512), nullable=True)
