from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, Numeric
from sqlalchemy.orm import Mapped, mapped_column

from thermoctl.db.base import Base, utcnow


class ZoneOverride(Base):
    """An override of the schedule.

    Three endings: until the next schedule point, for a duration, or permanently. In
    the first two cases, `ends_at` is computed concretely when the row is created,
    rather than stored as a rule — this way the database always states when it ends,
    and a later schedule change does not retroactively shift a running override.

    Rows are never deleted; they are the history.
    """

    __tablename__ = "zone_override"
    __table_args__ = (
        CheckConstraint(
            "(setpoint_mode_id IS NULL) <> (temperature_c IS NULL)",
            name="entweder_modus_oder_temperatur",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    zone_id: Mapped[int] = mapped_column(
        ForeignKey("zone.id", ondelete="CASCADE"), nullable=False, index=True
    )
    setpoint_mode_id: Mapped[int | None] = mapped_column(
        ForeignKey("setpoint_mode.id"), nullable=True
    )
    temperature_c: Mapped[Decimal | None] = mapped_column(Numeric(4, 1), nullable=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    created_by_token_id: Mapped[int | None] = mapped_column(
        ForeignKey("api_token.id", ondelete="SET NULL"), nullable=True
    )
    source_id: Mapped[int] = mapped_column(ForeignKey("actor_source.id"), nullable=False)
