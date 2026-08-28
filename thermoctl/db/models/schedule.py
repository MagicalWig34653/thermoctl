from sqlalchemy import CheckConstraint, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from thermoctl.db.base import Base


class SchedulePoint(Base):
    """Ein Schaltpunkt gilt bis zum naechsten — wie bei klassischen Heizungsreglern.

    Daraus folgt, dass es weder Luecken noch Ueberlappungen geben kann. `minute_of_day`
    ist ein Integer und kein TIME, weil Integer ueber SQLite und MariaDB identisch
    vergleicht und sortiert. Die Zeit ist lokale Zeit (`setting.timezone`), damit sich
    die Nachtabsenkung bei der Zeitumstellung nicht verschiebt.
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
    weekday: Mapped[int] = mapped_column(Integer, nullable=False)  # 1 = Montag … 7 = Sonntag
    minute_of_day: Mapped[int] = mapped_column(Integer, nullable=False)
    setpoint_mode_id: Mapped[int] = mapped_column(
        ForeignKey("setpoint_mode.id"), nullable=False
    )
