"""Persistenz für Notbetrieb; Zeitpunkte sind naive UTC wie im übrigen Schema.

Positive Aus-Dauern werden durch portable CheckConstraints geschützt: Null oder
negative Pausen sind auch bei direkten DB-Schreibzugriffen ungültig. Ein-Dauer null
ist dagegen der Warm-Aus-Punkt. Kennlinienübergreifende Validierung gehört in
Auftrag 4. Kennlinienvorgaben liegen ausschließlich in der Data-Migration.
Historie überlebt gelöschte Anlagenobjekte durch SET NULL und skalare Snapshots;
Laufzustände werden mit ihrem Besitzer gelöscht. Simulation und Versand besitzen
getrennte Phasen, Fristen und Erfolgszeiten, damit Trockenläufe nichts bestätigen.
"""

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
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from thermoctl.db.base import Base


class SensorFailureProfile(Base):
    __tablename__ = "sensor_failure_profile"
    __table_args__ = (
        CheckConstraint("fixed_off_seconds > 0", name="off_positive"),
        CheckConstraint("fixed_on_seconds >= 0", name="on_nonnegative"),
        CheckConstraint("recovery_seconds >= 0", name="recovery_nonnegative"),
        CheckConstraint("recovery_samples > 0", name="samples_positive"),
        CheckConstraint("warm_restart_hysteresis_k >= 0", name="hysteresis_nonnegative"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    version: Mapped[int] = mapped_column(Integer)
    fixed_on_seconds: Mapped[int] = mapped_column(Integer)
    fixed_off_seconds: Mapped[int] = mapped_column(Integer)
    recovery_seconds: Mapped[int] = mapped_column(Integer)
    recovery_samples: Mapped[int] = mapped_column(Integer)
    warm_restart_hysteresis_k: Mapped[Decimal] = mapped_column(Numeric(4, 2))


class SensorFailureCurvePoint(Base):
    __tablename__ = "sensor_failure_curve_point"
    __table_args__ = (
        UniqueConstraint("profile_id", "outdoor_c", name="curve_temperature_per_profile"),
        CheckConstraint("off_seconds > 0", name="off_positive"),
        CheckConstraint("on_seconds >= 0", name="on_nonnegative"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("sensor_failure_profile.id", ondelete="CASCADE")
    )
    outdoor_c: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    on_seconds: Mapped[int] = mapped_column(Integer)
    off_seconds: Mapped[int] = mapped_column(Integer)


class SensorFailureEpisode(Base):
    __tablename__ = "sensor_failure_episode"
    __table_args__ = (
        CheckConstraint("trigger_kind IN ('wandfuehler', 'alle_quellen')", name="trigger_kind"),
        CheckConstraint(
            "notification_state IN ('offen', 'gemeldet', 'entwarnung_gesendet')",
            name="notification_state",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    zone_id: Mapped[int | None] = mapped_column(ForeignKey("zone.id", ondelete="SET NULL"))
    zone_name: Mapped[str] = mapped_column(String(128))
    started_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime)
    trigger_kind: Mapped[str] = mapped_column(String(32))
    profile_version: Mapped[int] = mapped_column(Integer)
    notification_state: Mapped[str] = mapped_column(String(32), default="offen")
    # Effective values, independent of later profile edits/deletion.
    fixed_on_seconds: Mapped[int] = mapped_column(Integer)
    fixed_off_seconds: Mapped[int] = mapped_column(Integer)
    recovery_seconds: Mapped[int] = mapped_column(Integer)
    recovery_samples: Mapped[int] = mapped_column(Integer)
    warm_restart_hysteresis_k: Mapped[Decimal] = mapped_column(Numeric(4, 2))
    emergency_setpoint_c: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    sensor_timeout_seconds: Mapped[int] = mapped_column(Integer)
    source_device_id: Mapped[int | None] = mapped_column(
        ForeignKey("device.id", ondelete="SET NULL")
    )
    source_device_name: Mapped[str | None] = mapped_column(String(128))


class ZoneSensorFailureState(Base):
    __tablename__ = "zone_sensor_failure_state"
    __table_args__ = (
        CheckConstraint(
            "stage IN ('normal', 'ersatzquelle', 'notbetrieb', 'rueckkehrpruefung')",
            name="stage",
        ),
        CheckConstraint("recovery_sample_count >= 0", name="samples_nonnegative"),
    )

    zone_id: Mapped[int] = mapped_column(
        ForeignKey("zone.id", ondelete="CASCADE"), primary_key=True
    )
    episode_id: Mapped[int | None] = mapped_column(
        ForeignKey("sensor_failure_episode.id", ondelete="SET NULL")
    )
    stage: Mapped[str] = mapped_column(String(32), default="normal")
    failure_started_at: Mapped[datetime | None] = mapped_column(DateTime)
    active_source_device_id: Mapped[int | None] = mapped_column(
        ForeignKey("device.id", ondelete="SET NULL")
    )
    source_measured_at: Mapped[datetime | None] = mapped_column(DateTime)
    recovery_started_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_counted_measurement_at: Mapped[datetime | None] = mapped_column(DateTime)
    recovery_sample_count: Mapped[int] = mapped_column(Integer, default=0)
    # Mirrors `domain.emergency_operation.ZoneEmergencyState.handover_due_signalled`:
    # without this column the once-per-episode handover latch would live only in
    # memory and either re-fire or vanish across a process restart mid-episode.
    handover_due_signalled: Mapped[bool] = mapped_column(Boolean, default=False)


class ActuatorEmergencyState(Base):
    __tablename__ = "actuator_emergency_state"
    __table_args__ = (
        CheckConstraint("phase IN ('ein', 'aus')", name="phase"),
        CheckConstraint("simulated_phase IN ('ein', 'aus')", name="simulated_phase"),
        CheckConstraint("off_seconds > 0", name="off_positive"),
        CheckConstraint("on_seconds >= 0", name="on_nonnegative"),
        CheckConstraint("simulated_off_seconds > 0", name="simulated_off_positive"),
        CheckConstraint("simulated_on_seconds >= 0", name="simulated_on_nonnegative"),
        CheckConstraint("cycle_source IN ('kennlinie', 'festtakt')", name="cycle_source"),
        CheckConstraint(
            "simulated_cycle_source IN ('kennlinie', 'festtakt')", name="simulated_cycle_source"
        ),
    )

    zone_device_id: Mapped[int] = mapped_column(
        ForeignKey("zone_device.id", ondelete="CASCADE"), primary_key=True
    )
    episode_id: Mapped[int] = mapped_column(
        ForeignKey("sensor_failure_episode.id", ondelete="CASCADE")
    )
    phase: Mapped[str | None] = mapped_column(String(8))
    phase_deadline_at: Mapped[datetime | None] = mapped_column(DateTime)
    on_seconds: Mapped[int | None] = mapped_column(Integer)
    off_seconds: Mapped[int | None] = mapped_column(Integer)
    # Taktquelle (`festtakt`/`kennlinie`) und Wiederanlaufsperre je Zuordnung --
    # `domain.emergency_cycle.CycleState.source`/`.warm_locked` haben ohne diese
    # beiden Spalten keinen Weg zurück in die Persistenz: vor ihrer Einführung
    # (Kreuzreview von 88bc87a) wurden beide bei jedem Zyklus live aus dem
    # aktuellen Profil/der aktuellen Außentemperatur neu geschätzt statt aus der
    # Historie übernommen -- die Wiederanlaufsperre konnte dadurch nie über
    # einen Zyklus hinweg wirken.
    cycle_source: Mapped[str | None] = mapped_column(String(16))
    warm_locked: Mapped[bool | None] = mapped_column(Boolean)
    simulated_phase: Mapped[str | None] = mapped_column(String(8))
    simulated_phase_deadline_at: Mapped[datetime | None] = mapped_column(DateTime)
    simulated_on_seconds: Mapped[int | None] = mapped_column(Integer)
    simulated_off_seconds: Mapped[int | None] = mapped_column(Integer)
    simulated_cycle_source: Mapped[str | None] = mapped_column(String(16))
    simulated_warm_locked: Mapped[bool | None] = mapped_column(Boolean)
    handover_attempted_at: Mapped[datetime | None] = mapped_column(DateTime)
    handover_result: Mapped[str | None] = mapped_column(String(64))
    simulated_handover_attempted_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_successful_command_state: Mapped[bool | None] = mapped_column(Boolean)
    last_successful_command_at: Mapped[datetime | None] = mapped_column(DateTime)
    simulated_last_command_state: Mapped[bool | None] = mapped_column(Boolean)
    simulated_last_command_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_evaluated_at: Mapped[datetime | None] = mapped_column(DateTime)
    profile_version: Mapped[int] = mapped_column(Integer)


class ActuatorDecision(Base):
    __tablename__ = "actuator_decision"
    __table_args__ = (
        CheckConstraint(
            "action IN ('normal', 'no_write', 'switch_on', 'switch_off', 'handover')",
            name="action",
        ),
        CheckConstraint("phase IN ('ein', 'aus')", name="phase"),
        CheckConstraint("cycle_source IN ('kennlinie', 'festtakt')", name="cycle_source"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    episode_id: Mapped[int | None] = mapped_column(
        ForeignKey("sensor_failure_episode.id", ondelete="SET NULL"), index=True
    )
    zone_device_id: Mapped[int | None] = mapped_column(
        ForeignKey("zone_device.id", ondelete="SET NULL"), index=True
    )
    zone_name: Mapped[str] = mapped_column(String(128))
    device_name: Mapped[str] = mapped_column(String(128))
    decided_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    action: Mapped[str] = mapped_column(String(32))
    reason_code: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(Text)
    phase: Mapped[str | None] = mapped_column(String(8))
    phase_deadline_at: Mapped[datetime | None] = mapped_column(DateTime)
    simulated: Mapped[bool] = mapped_column(Boolean)
    cycle_source: Mapped[str | None] = mapped_column(String(16))
    on_seconds: Mapped[int | None] = mapped_column(Integer)
    off_seconds: Mapped[int | None] = mapped_column(Integer)
    outdoor_c: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    profile_version: Mapped[int] = mapped_column(Integer)


class SensorFailureSourceComparison(Base):
    """Ersatzquelle-gegen-Wandfühler-Vergleichsprotokoll (Plan Abschnitt 6, R2).

    Eine Zeile je neuem Kandidaten-Messzeitpunkt, nicht je Regelzyklus --
    `services/shadow_run.py` schreibt nur, wenn Wandfühler und Kandidat beide
    einen Wert haben oder die Zone gerade in Stufe `ersatzquelle` steht, und
    überspringt einen bereits protokollierten Messzeitpunkt desselben Geräts.
    Grundlage für eine spätere Kalibrierhilfe (Offset-Vorschlag), nicht selbst
    Teil der Regelkette. Zone/Gerät überleben ihre Löschung als Namenssnapshot
    (SET NULL), wie jede andere Historie in diesem Modul.
    """

    __tablename__ = "sensor_failure_source_comparison"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    zone_id: Mapped[int | None] = mapped_column(
        ForeignKey("zone.id", ondelete="SET NULL"), index=True
    )
    zone_name: Mapped[str] = mapped_column(String(128))
    device_id: Mapped[int | None] = mapped_column(
        ForeignKey("device.id", ondelete="SET NULL"), index=True
    )
    device_name: Mapped[str] = mapped_column(String(128))
    measured_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    wall_probe_c: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    raw_c: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    corrected_c: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    echo: Mapped[bool] = mapped_column(Boolean)
    usable: Mapped[bool] = mapped_column(Boolean)
