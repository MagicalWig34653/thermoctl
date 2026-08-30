from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.orm import Session

from thermoctl import audit
from thermoctl.db.models.operations import Setting
from thermoctl.db.models.zone import Zone


@dataclass(frozen=True)
class ControlParameters:
    hysteresis_k: Decimal
    min_on_seconds: int
    min_off_seconds: int
    sensor_timeout_seconds: int
    temperature_offset_k: Decimal
    window_resume_delay_seconds: int


def _or_standard[T](zone_value: T | None, default: T) -> T:
    """Nur None gilt als 'nicht gesetzt' — 0 und 0.0 sind gueltige Zonenwerte."""
    return default if zone_value is None else zone_value


def control_parameters(session: Session, zone: Zone) -> ControlParameters:
    """Die wirksamen Regelparameter einer Zone.

    Leere Zonenfelder heissen 'globaler Standard'. So steht jeder Wert genau einmal
    irgendwo, und eine Aenderung des Standards wirkt auf alle Zonen, die ihn nicht
    ausdruecklich ueberschrieben haben.
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
    )


def save_control_parameters(
    session: Session,
    zone: Zone,
    values: dict[str, Decimal | int | None],
    *,
    user_id: int | None,
    token_id: int | None = None,
    source: str = "web",
) -> None:
    """Speichert Zonenabweichungen; ``None`` stellt die Vererbung wieder her."""
    for name in ControlParameters.__dataclass_fields__:
        setattr(zone, name, values[name])
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


class UnknownParameter(ValueError):
    """Ein Regelparameter dieses Namens gibt es nicht."""


class ParameterOutOfRange(ValueError):
    """Der Wert liegt ausserhalb der erlaubten Grenzen."""


@dataclass(frozen=True)
class ParameterDescription:
    """Was ein Regelparameter bedeutet und welche Werte er annehmen darf.

    Steht hier und nicht im Adapter, weil inzwischen drei Stellen dieselbe Auskunft
    brauchen: das Formular in der Oberflaeche, das Schema der REST-Schnittstelle und die
    Home-Assistant-Anmeldung, die je Parameter eine `number`-Entitaet mit Minimum,
    Maximum und Schrittweite beschreibt. Eine Grenze, die je nach Weg anders ausfaellt,
    ist keine.
    """

    name: str
    label: str
    einheit: str | None
    minimum: Decimal
    maximum: Decimal
    step: Decimal

    @property
    def ganzzahlig(self) -> bool:
        return self.step == self.step.to_integral_value() and self.step >= 1


# Die Grenzen entsprechen denen der globalen Vorgaben (`domain/steuerung.GRENZEN`) --
# ein Zonenwert, den die globale Vorgabe nicht annehmen duerfte, waere eine Hintertuer.
# `temperature_offset_k` hat keine globale Entsprechung: Er gleicht einen falsch
# stehenden Sensor aus, und mehr als zehn Kelvin daneben ist kein Offset mehr, sondern
# ein defektes Geraet.
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
    """Setzt **einen** Regelparameter der Zone und laesst die uebrigen, wie sie sind.

    `regelparameter_speichern` nimmt immer alle Felder auf einmal -- richtig fuer ein
    Formular, falsch fuer einen einzelnen Drehregler in Home Assistant: Der kennt nur
    seinen eigenen Wert und wuerde alle anderen auf das setzen, was der Aufrufer gerade
    zur Hand hat.

    Der Wert wird als Zonenabweichung festgeschrieben, nicht als Vererbung. Eine
    `number`-Entitaet kann nicht leer sein, also gibt es dort kein "erbt vom globalen
    Standard"; wer die Vererbung zurueck will, leert das Feld in der Oberflaeche.
    """
    beschreibung = BY_NAME.get(name)
    if beschreibung is None:
        raise UnknownParameter(f"Den Regelparameter '{name}' gibt es nicht.")
    if not beschreibung.minimum <= value <= beschreibung.maximum:
        raise ParameterOutOfRange(
            f"{beschreibung.label} muss zwischen {beschreibung.minimum} und "
            f"{beschreibung.maximum} liegen."
        )
    gerundet = int(value) if beschreibung.ganzzahlig else value
    # Die uebrigen Felder so uebernehmen, wie sie an der Zone stehen -- ein geerbtes
    # None bleibt geerbt. Nur dieser eine Parameter wird festgeschrieben.
    values: dict[str, Decimal | int | None] = {
        feld: getattr(zone, feld) for feld in ControlParameters.__dataclass_fields__
    }
    values[name] = gerundet
    save_control_parameters(
        session, zone, values, user_id=user_id, token_id=token_id, source=source
    )
    return Decimal(gerundet)
