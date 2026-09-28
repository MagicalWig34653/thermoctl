"""Notbetriebskonfiguration; Transaktion und Commit gehören dem Aufrufer.

Alle Eingaben werden vor der ersten Änderung geprüft. Leere Kurven bedeuten
Festtakt. Herkunft gilt für das gesamte Profil einschließlich seiner Punkte;
Notsollwert und Aktivierung haben davon unabhängige Herkunft.
"""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Literal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from thermoctl.db.models.device import DeviceCapabilityLink, ZoneDevice
from thermoctl.db.models.lookup import DeviceCapability, DeviceRole
from thermoctl.db.models.operations import Setting
from thermoctl.db.models.sensor_failure import SensorFailureCurvePoint, SensorFailureProfile
from thermoctl.db.models.zone import Zone

Source = Literal["Zone", "Anlage", "Vorgabe"]


@dataclass
class PolicyError(ValueError):
    field: str
    notice: str

    def __str__(self) -> str:
        return self.notice


@dataclass(frozen=True)
class CurvePoint:
    outdoor_c: Decimal
    on_seconds: int
    off_seconds: int


@dataclass(frozen=True)
class ProfileValues:
    name: str
    fixed_on_seconds: int
    fixed_off_seconds: int
    recovery_seconds: int
    recovery_samples: int
    warm_restart_hysteresis_k: Decimal
    curve_points: tuple[CurvePoint, ...]


@dataclass(frozen=True)
class Profile:
    id: int
    version: int
    values: ProfileValues


@dataclass(frozen=True)
class Defaults:
    profile_id: int | None
    emergency_setpoint_c: Decimal


@dataclass(frozen=True)
class ZonePolicy:
    enabled: bool
    profile_id: int | None
    emergency_setpoint_c: Decimal | None


@dataclass(frozen=True)
class EffectivePolicy:
    profile: Profile
    profile_source: Source
    emergency_setpoint_c: Decimal
    setpoint_source: Source
    enabled: bool
    enabled_source: Source = "Zone"


def _settings(session: Session) -> Setting:
    row = session.get(Setting, 1)
    if row is None:
        raise PolicyError("setting", "Anlageneinstellungen fehlen: Einrichtung unvollständig.")
    return row


def _profile(session: Session, profile_id: int) -> SensorFailureProfile:
    row = session.get(SensorFailureProfile, profile_id)
    if row is None:
        raise PolicyError("profile_id", "Das Notbetriebsprofil wurde nicht gefunden.")
    return row


def migration_default_profile_id(session: Session) -> int:
    """Älteste ID mit Migrationsnamen: Migration legt das erste Profil an.

    Kein fest verdrahteter Primärschlüssel und kein Ersatzprofil zur Laufzeit.
    Namensduplikate verdrängen die ursprüngliche Vorgabe nicht. Umbenennung oder
    Löschung ohne Anlagenverweis wird als Konfigurationsfehler sichtbar.
    """
    profile_id = session.scalar(
        select(SensorFailureProfile.id)
        .where(SensorFailureProfile.name == "Notbetrieb Vorgabe")
        .order_by(SensorFailureProfile.id)
        .limit(1)
    )
    if profile_id is None:
        raise PolicyError(
            "sensor_failure_default_profile_id",
            "Das Vorgabeprofil „Notbetrieb Vorgabe“ fehlt; bitte ein Profil wählen.",
        )
    return profile_id


def read_profile(session: Session, profile_id: int) -> Profile:
    row = _profile(session, profile_id)
    points = session.scalars(
        select(SensorFailureCurvePoint)
        .where(SensorFailureCurvePoint.profile_id == profile_id)
        .order_by(SensorFailureCurvePoint.outdoor_c)
    )
    return Profile(
        row.id,
        row.version,
        ProfileValues(
            row.name,
            row.fixed_on_seconds,
            row.fixed_off_seconds,
            row.recovery_seconds,
            row.recovery_samples,
            row.warm_restart_hysteresis_k,
            tuple(CurvePoint(p.outdoor_c, p.on_seconds, p.off_seconds) for p in points),
        ),
    )


def read_defaults(session: Session) -> Defaults:
    row = _settings(session)
    return Defaults(
        row.sensor_failure_default_profile_id, row.sensor_failure_default_emergency_setpoint_c
    )


def read_zone_policy(session: Session, zone: Zone) -> ZonePolicy:
    """Unaufgelöste Überschreibungen für spätere Konfigurationsadapter."""
    return ZonePolicy(
        zone.sensor_failure_enabled,
        zone.sensor_failure_profile_id,
        zone.sensor_failure_emergency_setpoint_c,
    )


def effective_policy(session: Session, zone: Zone) -> EffectivePolicy:
    defaults = read_defaults(session)
    source: Source
    if zone.sensor_failure_profile_id is not None:
        profile_id, source = zone.sensor_failure_profile_id, "Zone"
    elif defaults.profile_id is not None:
        profile_id, source = defaults.profile_id, "Anlage"
    else:
        profile_id, source = migration_default_profile_id(session), "Vorgabe"
    setpoint = zone.sensor_failure_emergency_setpoint_c
    return EffectivePolicy(
        read_profile(session, profile_id),
        source,
        defaults.emergency_setpoint_c if setpoint is None else setpoint,
        "Anlage" if setpoint is None else "Zone",
        zone.sensor_failure_enabled,
    )


def _integer(field: str, value: int, minimum: int, maximum: int = 2_147_483_647) -> None:
    if type(value) is not int or not minimum <= value <= maximum:
        raise PolicyError(
            field, f"{field}: ganze Zahl zwischen {minimum} und {maximum} erforderlich."
        )


def _decimal(
    field: str, value: Decimal, minimum: Decimal, maximum: Decimal, step: Decimal = Decimal("0.01")
) -> None:
    # Speicherpräzision ausdrücklich prüfen: kein stilles Runden durch MariaDB/SQLite.
    if not value.is_finite() or not minimum <= value <= maximum:
        raise PolicyError(field, f"{field}: Wert zwischen {minimum} und {maximum} erforderlich.")
    if value % step != 0:
        raise PolicyError(field, f"{field}: Schritte von {step} erforderlich.")


def _setpoint(value: Decimal) -> None:
    _decimal("emergency_setpoint_c", value, Decimal("5"), Decimal("30"), Decimal("0.5"))


def validate_profile(values: ProfileValues) -> None:
    if not values.name.strip() or len(values.name) > 128:
        raise PolicyError("name", "Profilname: 1 bis 128 Zeichen erforderlich.")
    _integer("fixed_on_seconds", values.fixed_on_seconds, 60, 7200)
    _integer("fixed_off_seconds", values.fixed_off_seconds, 60, 7200)
    _integer("recovery_seconds", values.recovery_seconds, 0)
    _integer("recovery_samples", values.recovery_samples, 1)
    _decimal(
        "warm_restart_hysteresis_k",
        values.warm_restart_hysteresis_k,
        Decimal("0"),
        Decimal("99.99"),
    )
    for index, point in enumerate(values.curve_points):
        prefix = f"curve_points[{index}]"
        _decimal(f"{prefix}.outdoor_c", point.outdoor_c, Decimal("-999.99"), Decimal("999.99"))
        _integer(f"{prefix}.on_seconds", point.on_seconds, 0 if point.on_seconds == 0 else 60, 7200)
        _integer(f"{prefix}.off_seconds", point.off_seconds, 60, 7200)
    if not values.curve_points:
        return
    if len(values.curve_points) < 2:
        raise PolicyError("curve_points", "Kennlinie: mindestens zwei Kurvenpunkte erforderlich.")
    points = sorted(values.curve_points, key=lambda p: p.outdoor_c)
    if len({p.outdoor_c for p in points}) != len(points):
        raise PolicyError("curve_points", "Kennlinie: doppelte Außentemperaturen sind unzulässig.")
    if points[-1].on_seconds != 0 or sum(p.on_seconds == 0 for p in points) != 1:
        raise PolicyError("curve_points", "Kennlinie: genau ein oberer Aus-Punkt erforderlich.")
    for cold, warm in zip(points, points[1:], strict=False):
        # Kreuzmultiplikation vermeidet Rundung des Tastgrads.
        if warm.on_seconds * cold.off_seconds > cold.on_seconds * warm.off_seconds:
            raise PolicyError(
                "curve_points",
                "Kennlinie: Tastgrad darf mit steigender Außentemperatur nicht steigen.",
            )


def save_profile(
    session: Session, values: ProfileValues, *, profile_id: int | None = None
) -> Profile:
    validate_profile(values)
    row = (
        _profile(session, profile_id) if profile_id is not None else SensorFailureProfile(version=0)
    )
    if profile_id is not None:
        validate_active_profiles(session, changed_profile=Profile(profile_id, row.version, values))
    for name in (
        "name",
        "fixed_on_seconds",
        "fixed_off_seconds",
        "recovery_seconds",
        "recovery_samples",
        "warm_restart_hysteresis_k",
    ):
        setattr(row, name, getattr(values, name))
    row.version += 1
    session.add(row)
    session.flush()
    session.execute(
        delete(SensorFailureCurvePoint).where(SensorFailureCurvePoint.profile_id == row.id)
    )
    session.add_all(
        SensorFailureCurvePoint(
            profile_id=row.id,
            outdoor_c=p.outdoor_c,
            on_seconds=p.on_seconds,
            off_seconds=p.off_seconds,
        )
        for p in values.curve_points
    )
    session.flush()
    return Profile(
        row.id,
        row.version,
        replace(values, curve_points=tuple(sorted(values.curve_points, key=lambda p: p.outdoor_c))),
    )


def save_defaults(
    session: Session, *, profile_id: int | None, emergency_setpoint_c: Decimal
) -> None:
    _setpoint(emergency_setpoint_c)
    row = _settings(session)
    resolved = migration_default_profile_id(session) if profile_id is None else profile_id
    profile = read_profile(session, resolved)
    validate_profile(profile.values)
    validate_active_profiles(session, default_profile=profile)
    row.sensor_failure_default_profile_id = profile_id
    row.sensor_failure_default_emergency_setpoint_c = emergency_setpoint_c


def save_zone_policy(
    session: Session,
    zone: Zone,
    *,
    enabled: bool,
    profile_id: int | None,
    emergency_setpoint_c: Decimal | None,
) -> None:
    if emergency_setpoint_c is not None:
        _setpoint(emergency_setpoint_c)
    if profile_id is not None:
        profile = read_profile(session, profile_id)
    else:
        defaults = read_defaults(session)
        resolved = (
            migration_default_profile_id(session)
            if defaults.profile_id is None
            else defaults.profile_id
        )
        profile = read_profile(session, resolved)
    validate_profile(profile.values)
    if enabled:
        validate_zone_timing(session, zone, profile_values=profile.values)
    zone.sensor_failure_enabled = enabled
    zone.sensor_failure_profile_id = profile_id
    zone.sensor_failure_emergency_setpoint_c = emergency_setpoint_c


def _assignment(session: Session, assignment_id: int) -> ZoneDevice:
    row = session.get(ZoneDevice, assignment_id)
    if row is None:
        raise PolicyError("zone_device_id", "Die Geräte-Zuordnung wurde nicht gefunden.")
    role = session.get(DeviceRole, row.device_role_id)
    capabilities = set(
        session.scalars(
            select(DeviceCapability.code)
            .join(DeviceCapabilityLink, DeviceCapabilityLink.capability_id == DeviceCapability.id)
            .where(DeviceCapabilityLink.device_id == row.device_id)
        )
    )
    if (
        role is None
        or role.code != "actuator"
        or "thermostat" not in capabilities
        or ("switch" in capabilities)
    ):
        raise PolicyError(
            "temperature_backup_offset_k",
            "Temperaturausgleich ist nur für Thermostat-Aktor-Zuordnungen zulässig.",
        )
    return row


def read_backup_offset(session: Session, assignment_id: int) -> Decimal:
    value = _assignment(session, assignment_id).temperature_backup_offset_k
    return Decimal("0") if value is None else value


def save_backup_offset(session: Session, assignment_id: int, value: Decimal | None) -> None:
    row = _assignment(session, assignment_id)
    if value is not None:
        _decimal("temperature_backup_offset_k", value, Decimal("-99.99"), Decimal("99.99"))
    row.temperature_backup_offset_k = value


ParameterValues = Mapping[str, Decimal | int | bool | None]


def validate_zone_timing(
    session: Session,
    zone: Zone,
    *,
    profile_values: ProfileValues | None = None,
    zone_values: ParameterValues | None = None,
    setting_values: ParameterValues | None = None,
) -> None:
    """Festphasen müssen Mindestzeiten und mindestens ein Regelintervall erfüllen.

    Kurvenzwischenwerte werden später vom Taktgeber begrenzt; hier werden sie
    nicht auf Mindestzeiten aufgerundet oder als Festphasen missverstanden.
    """
    settings = _settings(session)
    profile = (
        profile_values
        if profile_values is not None
        else effective_policy(session, zone).profile.values
    )
    zv = zone_values or {}
    sv = setting_values or {}
    interval = sv.get("shadow_interval_seconds", settings.shadow_interval_seconds)
    assert interval is not None
    for phase in ("on", "off"):
        field = f"min_{phase}_seconds"
        minimum = zv.get(field, getattr(zone, field))
        if minimum is None:
            minimum = sv.get(f"default_{field}", getattr(settings, f"default_{field}"))
        assert minimum is not None
        required = max(int(minimum), int(interval))
        if zv.get("pi_enabled", zone.pi_enabled):
            pi_minimum = zv.get(f"pi_{field}", getattr(zone, f"pi_{field}"))
            assert pi_minimum is not None
            required = max(required, int(pi_minimum))
        if getattr(profile, f"fixed_{phase}_seconds") < required:
            raise PolicyError(
                f"fixed_{phase}_seconds",
                f"Zone '{zone.display_name}': Festtakt ({phase}) muss mindestens "
                f"{required} Sekunden gemäß Mindestzeiten/Regelintervall dauern.",
            )


def validate_active_profiles(
    session: Session,
    *,
    setting_values: ParameterValues | None = None,
    changed_profile: Profile | None = None,
    default_profile: Profile | None = None,
) -> None:
    """Prüft geplante Änderungen ohne ORM-Werte vorübergehend zu überschreiben."""
    for zone in session.scalars(select(Zone).where(Zone.sensor_failure_enabled.is_(True))):
        profile = (
            default_profile
            if default_profile is not None and zone.sensor_failure_profile_id is None
            else effective_policy(session, zone).profile
        )
        if changed_profile is not None and profile.id == changed_profile.id:
            profile = changed_profile
        validate_zone_timing(
            session, zone, profile_values=profile.values, setting_values=setting_values
        )
