from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.orm import Session

from thermoctl import audit
from thermoctl.db.models.operations import Setting
from thermoctl.db.models.zone import Zone

MAXIMUM_VALVE_PROTECTION_INTERVAL_DAYS = 3650
MAXIMUM_VALVE_PROTECTION_DURATION_MINUTES = 5_256_000


@dataclass(frozen=True)
class ControlParameters:
    hysteresis_k: Decimal
    min_on_seconds: int
    min_off_seconds: int
    sensor_timeout_seconds: int
    temperature_offset_k: Decimal
    window_resume_delay_seconds: int
    # The zone's cap on the solar setback in Kelvin -- inherited from
    # `setting.default_solar_setback_max_k` exactly like the six fields above.
    solar_setback_max_k: Decimal
    valve_protection_enabled: bool = False
    valve_protection_interval_days: int = 30
    valve_protection_duration_minutes: int = 10
    pi_enabled: bool = False
    pi_gain_per_k: Decimal = Decimal("0.25")
    pi_integral_time_minutes: int = 180
    pi_min_on_seconds: int = 60
    pi_min_off_seconds: int = 60


def _or_standard[T](zone_value: T | None, default: T) -> T:
    """Only None counts as 'not set' — 0 and 0.0 are valid zone values."""
    return default if zone_value is None else zone_value


def control_parameters(session: Session, zone: Zone) -> ControlParameters:
    """The effective control parameters of a zone.

    Empty zone fields mean 'global default'. That way every value lives in exactly one
    place, and a change to the default affects every zone that has not explicitly
    overridden it.
    """
    e = session.get(Setting, 1)
    assert e is not None, "setting-Zeile fehlt — Einrichtung unvollstaendig"
    return ControlParameters(
        hysteresis_k=_or_standard(zone.hysteresis_k, e.default_hysteresis_k),
        min_on_seconds=_or_standard(zone.min_on_seconds, e.default_min_on_seconds),
        min_off_seconds=_or_standard(zone.min_off_seconds, e.default_min_off_seconds),
        sensor_timeout_seconds=_or_standard(
            zone.sensor_timeout_seconds, e.default_sensor_timeout_seconds
        ),
        temperature_offset_k=_or_standard(zone.temperature_offset_k, Decimal("0.00")),
        window_resume_delay_seconds=_or_standard(
            zone.window_resume_delay_seconds, e.default_window_resume_delay_seconds
        ),
        solar_setback_max_k=_or_standard(
            zone.solar_setback_max_k, e.default_solar_setback_max_k
        ),
        valve_protection_enabled=zone.valve_protection_enabled,
        valve_protection_interval_days=zone.valve_protection_interval_days,
        valve_protection_duration_minutes=zone.valve_protection_duration_minutes,
        pi_enabled=zone.pi_enabled,
        pi_gain_per_k=zone.pi_gain_per_k,
        pi_integral_time_minutes=zone.pi_integral_time_minutes,
        pi_min_on_seconds=zone.pi_min_on_seconds,
        pi_min_off_seconds=zone.pi_min_off_seconds,
    )


def save_control_parameters(
    session: Session,
    zone: Zone,
    values: dict[str, Decimal | int | bool | None],
    *,
    user_id: int | None,
    token_id: int | None = None,
    source: str = "web",
) -> None:
    """Saves zone deviations; ``None`` restores inheritance."""
    complete = {
        name: values.get(name, getattr(zone, name))
        for name in ControlParameters.__dataclass_fields__
    }
    validate_valve_protection(complete)
    for name in ControlParameters.__dataclass_fields__:
        setattr(zone, name, complete[name])
    audit.record(
        session,
        source=source,
        action="update",
        object_type="zone_settings",
        object_id=str(zone.id),
        summary=f"Regelparameter für Zone '{zone.display_name}' geändert",
        user_id=user_id,
        token_id=token_id,
    )


def validate_valve_protection(values: dict[str, Decimal | int | bool | None]) -> None:
    """Reject physically meaningless valve-protection timing for every adapter."""
    interval = int(values["valve_protection_interval_days"] or 0)
    duration = int(values["valve_protection_duration_minutes"] or 0)
    if interval <= 0:
        raise ParameterOutOfRange("Ventilschutz-Abstand muss mindestens 1 Tag betragen.")
    if interval > MAXIMUM_VALVE_PROTECTION_INTERVAL_DAYS:
        raise ParameterOutOfRange(
            f"Ventilschutz-Abstand darf höchstens "
            f"{MAXIMUM_VALVE_PROTECTION_INTERVAL_DAYS} Tage betragen."
        )
    if duration <= 0:
        raise ParameterOutOfRange("Ventilschutz-Dauer muss mindestens 1 Minute betragen.")
    if duration > MAXIMUM_VALVE_PROTECTION_DURATION_MINUTES:
        raise ParameterOutOfRange(
            f"Ventilschutz-Dauer darf höchstens "
            f"{MAXIMUM_VALVE_PROTECTION_DURATION_MINUTES} Minuten betragen."
        )
    if duration > interval * 24 * 60:
        raise ParameterOutOfRange("Ventilschutz-Dauer darf nicht länger als der Abstand sein.")


class UnknownParameter(ValueError):
    """There is no control parameter of this name."""


class ParameterOutOfRange(ValueError):
    """The value lies outside the allowed bounds."""


@dataclass(frozen=True)
class ParameterDescription:
    """What a control parameter means and which values it may take.

    Lives here and not in the adapter, because by now three places need the same
    information: the form in the interface, the REST interface's schema, and the Home
    Assistant registration, which describes a `number` entity per parameter with
    minimum, maximum and step. A bound that comes out differently depending on the
    path is not a bound at all.
    """

    name: str
    label: str
    unit: str | None
    minimum: Decimal
    maximum: Decimal
    step: Decimal

    @property
    def integral(self) -> bool:
        return self.step == self.step.to_integral_value() and self.step >= 1


# The bounds match those of the global defaults (`domain/steuerung.GRENZEN`) -- a zone
# value that the global default would not be allowed to take would be a back door.
# `temperature_offset_k` has no global counterpart: it corrects a miscalibrated
# sensor, and more than ten kelvin off is no longer an offset but a broken device.
PARAMETERS: tuple[ParameterDescription, ...] = (
    ParameterDescription(
        "hysteresis_k", "Hysterese", "K", Decimal("0.1"), Decimal("5.0"), Decimal("0.1")
    ),
    ParameterDescription(
        "min_on_seconds", "Mindest-Einschaltdauer", "s", Decimal(30), Decimal(7200), Decimal(10)
    ),
    ParameterDescription(
        "min_off_seconds", "Mindest-Ausschaltdauer", "s", Decimal(30), Decimal(7200), Decimal(10)
    ),
    ParameterDescription(
        "sensor_timeout_seconds", "Sensorausfall nach", "s",
        Decimal(60), Decimal(86400), Decimal(30),
    ),
    ParameterDescription(
        "temperature_offset_k", "Sensorabgleich", "K",
        Decimal("-10.0"), Decimal("10.0"), Decimal("0.1"),
    ),
    ParameterDescription(
        "window_resume_delay_seconds", "Nachlauf nach Fensterschluss", "s",
        Decimal(0), Decimal(3600), Decimal(10),
    ),
    # No global counterpart under a *different* name here either -- unlike
    # `temperature_offset_k`, this one has one: `setting.default_solar_setback_max_k`.
    # The bound matches `domain.control.LIMITS["default_solar_setback_max_k"]`.
    ParameterDescription(
        "solar_setback_max_k", "Obergrenze Sonnenabsenkung", "K",
        Decimal("0.0"), Decimal("10.0"), Decimal("0.1"),
    ),
    ParameterDescription(
        "valve_protection_enabled", "Ventilschutz eingeschaltet", None,
        Decimal(0), Decimal(1), Decimal(1),
    ),
    ParameterDescription(
        "valve_protection_interval_days", "Ventilschutz-Abstand", "Tage",
        Decimal(1), Decimal(MAXIMUM_VALVE_PROTECTION_INTERVAL_DAYS), Decimal(1),
    ),
    ParameterDescription(
        "valve_protection_duration_minutes", "Ventilschutz-Dauer", "Minuten",
        Decimal(1), Decimal(MAXIMUM_VALVE_PROTECTION_DURATION_MINUTES), Decimal(1),
    ),
)

BY_NAME: dict[str, ParameterDescription] = {p.name: p for p in PARAMETERS}


def set_parameter(
    session: Session,
    zone: Zone,
    name: str,
    value: Decimal,
    *,
    user_id: int | None,
    token_id: int | None = None,
    source: str = "web",
) -> Decimal:
    """Sets **one** control parameter of the zone and leaves the rest as they are.

    `regelparameter_speichern` always takes all fields at once -- right for a form,
    wrong for a single dial in Home Assistant: it knows only its own value and would
    set every other field to whatever the caller happened to have at hand.

    The value gets fixed as a zone deviation, not as inheritance. A `number` entity
    cannot be empty, so there is no "inherits from global default" there; whoever
    wants inheritance back clears the field in the interface.
    """
    description = BY_NAME.get(name)
    if description is None:
        raise UnknownParameter(f"Den Regelparameter '{name}' gibt es nicht.")
    if not description.minimum <= value <= description.maximum:
        raise ParameterOutOfRange(
            f"{description.label} muss zwischen {description.minimum} und "
            f"{description.maximum} liegen."
        )
    rounded = int(value) if description.integral else value
    # Take over the other fields exactly as they stand on the zone -- an inherited
    # None stays inherited. Only this one parameter gets fixed.
    values: dict[str, Decimal | int | bool | None] = {
        field: getattr(zone, field) for field in ControlParameters.__dataclass_fields__
    }
    values[name] = bool(rounded) if name == "valve_protection_enabled" else rounded
    save_control_parameters(
        session, zone, values, user_id=user_id, token_id=token_id, source=source
    )
    return Decimal(rounded)
