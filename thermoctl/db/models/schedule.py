from sqlalchemy import CheckConstraint, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from thermoctl.db.base import Base


class SchedulePoint(Base):
    """A schedule point holds until the next one — like classic heating controllers.

    It follows that there can be neither gaps nor overlaps. `minute_of_day` is an
    integer and not a TIME, because integer compares and sorts identically across
    SQLite and MariaDB. The time is local time (`setting.timezone`), so that the
    night setback does not shift with daylight saving changes.
    """

    __tablename__ = "schedule_point"
    __table_args__ = (
        UniqueConstraint("zone_id", "weekday", "minute_of_day", name="zeitpunkt_je_zone"),
        CheckConstraint("weekday BETWEEN 1 AND 7", name="wochentag_1_bis_7"),
        CheckConstraint("minute_of_day BETWEEN 0 AND 1439", name="minute_im_tag"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    zone_id: Mapped[int] = mapped_column(
        ForeignKey("zone.id", ondelete="CASCADE"), nullable=False
    )
    weekday: Mapped[int] = mapped_column(Integer, nullable=False)  # 1 = Monday … 7 = Sunday
    minute_of_day: Mapped[int] = mapped_column(Integer, nullable=False)
    setpoint_mode_id: Mapped[int] = mapped_column(
        ForeignKey("setpoint_mode.id"), nullable=False
    )
