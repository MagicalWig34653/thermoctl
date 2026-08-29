from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    false,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from thermoctl.db.base import Base, utcnow


class Setting(Base):
    """Genau eine Zeile mit typisierten Spalten — ersetzt die EAV-Tabelle `heizung_conf`.

    Eine neue Einstellung ist eine Alembic-Migration statt eines Strings, der erst zur
    Laufzeit als Fehler auffaellt.
    """

    __tablename__ = "setting"
    __table_args__ = (CheckConstraint("id = 1", name="genau_eine_zeile"),)

    # autoincrement=False ist hier Pflicht, nicht Kosmetik: MariaDB vergibt sonst
    # AUTO_INCREMENT und verbietet dann jede CHECK-Bedingung auf derselben Spalte
    # (Fehler 1901). Fachlich ist es ohnehin richtig — eine Tabelle mit genau einer
    # Zeile braucht keinen automatisch vergebenen Schluessel.
    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=False, default=1
    )
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Berlin", nullable=False)
    polling_interval_seconds: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    default_hysteresis_k: Mapped[Decimal] = mapped_column(
        Numeric(4, 2), default=Decimal("0.30"), nullable=False
    )
    default_min_on_seconds: Mapped[int] = mapped_column(Integer, default=300, nullable=False)
    default_min_off_seconds: Mapped[int] = mapped_column(Integer, default=300, nullable=False)
    default_sensor_timeout_seconds: Mapped[int] = mapped_column(
        Integer, default=1800, nullable=False
    )
    default_window_resume_delay_seconds: Mapped[int] = mapped_column(
        Integer, default=120, nullable=False
    )
    frost_protection_mode_id: Mapped[int] = mapped_column(
        ForeignKey("setpoint_mode.id"), nullable=False
    )
    session_lifetime_seconds: Mapped[int] = mapped_column(
        Integer,
        default=1209600,
        nullable=False,  # 14 Tage
    )
    control_armed: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False
    )
    measurement_retention_days: Mapped[int] = mapped_column(
        Integer, default=30, server_default=text("30"), nullable=False
    )
    shadow_interval_seconds: Mapped[int] = mapped_column(
        Integer, default=60, server_default=text("60"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class AuditEvent(Base):
    """Was Wochen spaeter noch beantwortbar sein soll.

    Wird in derselben Transaktion geschrieben wie die Aenderung, damit kein Eintrag zu
    einer Aenderung existiert, die nicht stattfand.
    """

    __tablename__ = "audit_event"

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
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    object_type: Mapped[str] = mapped_column(String(64), nullable=False)
    object_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    summary: Mapped[str] = mapped_column(String(255), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
