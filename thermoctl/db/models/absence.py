from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, Numeric
from sqlalchemy.orm import Mapped, mapped_column

from thermoctl.db.base import Base, utcnow


class Absence(Base):
    """Eine Abwesenheit eines Mieters für dessen eigene berechtigte Räume.

    Sie ist ausdrücklich nicht dasselbe wie Vacation: Ein Urlaub gilt anlagenweit,
    diese Abwesenheit klammert dagegen ausschließlich einzelne Zonenübersteuerungen
    für die eigenen Räume des Mieters. Die Zeilen werden nie gelöscht, sondern
    bleiben wie ZoneOverride und Vacation als Geschichte erhalten; ein vorzeitiges
    Ende wird nur durch cancelled_at festgehalten.
    """

    __tablename__ = "absence"
    __table_args__ = (
        CheckConstraint("ends_at > starts_at", name="abwesenheit_ende_nach_beginn"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    setback_temperature_c: Mapped[Decimal] = mapped_column(Numeric(4, 1), nullable=False)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    created_by_token_id: Mapped[int | None] = mapped_column(
        ForeignKey("api_token.id", ondelete="SET NULL"), nullable=True
    )
    source_id: Mapped[int] = mapped_column(ForeignKey("actor_source.id"), nullable=False)
