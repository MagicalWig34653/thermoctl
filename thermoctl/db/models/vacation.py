from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, Numeric
from sqlalchemy.orm import Mapped, mapped_column

from thermoctl.db.base import Base, utcnow


class Vacation(Base):
    """An anlagenweiter Urlaubsbetrieb: one setback temperature for every zone, over
    a fixed, explicitly chosen window.

    Deliberately its own table and not a row of `zone_override` per zone (the
    alternative considered in the design): a per-zone override would either miss a
    zone added after the vacation started, or need to be retrofitted onto it with no
    natural trigger to do so, and cancelling one of the per-zone rows by hand would
    silently exempt exactly that zone for the rest of the vacation with nothing
    recording that it happened. A single row that `resolved_setpoint()` consults
    directly covers every zone uniformly, present and future, and there is exactly
    one place to end it early.

    Unlike `ZoneOverride`, `ends_at` is `NOT NULL` -- the project owner's explicit
    requirement is a fixed begin *and* a fixed end, decided at input time, not an
    open-ended state someone has to remember to close.

    Rows are never deleted, the same reasoning as `ZoneOverride`: they are the
    history of when the plant was told to run down for an absence.
    """

    __tablename__ = "vacation"
    __table_args__ = (
        CheckConstraint("ends_at > starts_at", name="urlaub_ende_nach_beginn"),
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
