from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.orm import Session

from thermoctl import audit
from thermoctl.db.models.operations import Setting
from thermoctl.db.models.zone import Zone


@dataclass(frozen=True)
class Regelparameter:
    hysteresis_k: Decimal
    min_on_seconds: int
    min_off_seconds: int
    sensor_timeout_seconds: int
    temperature_offset_k: Decimal
    window_resume_delay_seconds: int


def _oder_standard[T](zonenwert: T | None, standard: T) -> T:
    """Nur None gilt als 'nicht gesetzt' — 0 und 0.0 sind gueltige Zonenwerte."""
    return standard if zonenwert is None else zonenwert


def regelparameter(session: Session, zone: Zone) -> Regelparameter:
    """Die wirksamen Regelparameter einer Zone.

    Leere Zonenfelder heissen 'globaler Standard'. So steht jeder Wert genau einmal
    irgendwo, und eine Aenderung des Standards wirkt auf alle Zonen, die ihn nicht
    ausdruecklich ueberschrieben haben.
    """
    e = session.get(Setting, 1)
    assert e is not None, "setting-Zeile fehlt — Einrichtung unvollstaendig"
    return Regelparameter(
        hysteresis_k=_oder_standard(zone.hysteresis_k, e.default_hysteresis_k),
        min_on_seconds=_oder_standard(zone.min_on_seconds, e.default_min_on_seconds),
        min_off_seconds=_oder_standard(zone.min_off_seconds, e.default_min_off_seconds),
        sensor_timeout_seconds=_oder_standard(
            zone.sensor_timeout_seconds, e.default_sensor_timeout_seconds
        ),
        temperature_offset_k=_oder_standard(zone.temperature_offset_k, Decimal("0.00")),
        window_resume_delay_seconds=_oder_standard(
            zone.window_resume_delay_seconds, e.default_window_resume_delay_seconds
        ),
    )


def regelparameter_speichern(
    session: Session,
    zone: Zone,
    werte: dict[str, Decimal | int | None],
    *,
    user_id: int | None,
    token_id: int | None = None,
    quelle: str = "web",
) -> None:
    """Speichert Zonenabweichungen; ``None`` stellt die Vererbung wieder her."""
    for name in Regelparameter.__dataclass_fields__:
        setattr(zone, name, werte[name])
    audit.record(
        session,
        source=quelle,
        action="update",
        object_type="zone_settings",
        object_id=str(zone.id),
        summary=f"Regelparameter für Zone '{zone.display_name}' geändert",
        user_id=user_id,
        token_id=token_id,
    )


class Parameterunbekannt(ValueError):
    """Ein Regelparameter dieses Namens gibt es nicht."""


class Parametergrenze(ValueError):
    """Der Wert liegt ausserhalb der erlaubten Grenzen."""


@dataclass(frozen=True)
class Parameterbeschreibung:
    """Was ein Regelparameter bedeutet und welche Werte er annehmen darf.

    Steht hier und nicht im Adapter, weil inzwischen drei Stellen dieselbe Auskunft
    brauchen: das Formular in der Oberflaeche, das Schema der REST-Schnittstelle und die
    Home-Assistant-Anmeldung, die je Parameter eine `number`-Entitaet mit Minimum,
    Maximum und Schrittweite beschreibt. Eine Grenze, die je nach Weg anders ausfaellt,
    ist keine.
    """

    name: str
    beschriftung: str
    einheit: str | None
    minimum: Decimal
    maximum: Decimal
    schritt: Decimal

    @property
    def ganzzahlig(self) -> bool:
        return self.schritt == self.schritt.to_integral_value() and self.schritt >= 1


# Die Grenzen entsprechen denen der globalen Vorgaben (`domain/steuerung.GRENZEN`) --
# ein Zonenwert, den die globale Vorgabe nicht annehmen duerfte, waere eine Hintertuer.
# `temperature_offset_k` hat keine globale Entsprechung: Er gleicht einen falsch
# stehenden Sensor aus, und mehr als zehn Kelvin daneben ist kein Offset mehr, sondern
# ein defektes Geraet.
PARAMETER: tuple[Parameterbeschreibung, ...] = (
    Parameterbeschreibung(
        "hysteresis_k", "Hysterese", "K", Decimal("0.1"), Decimal("5.0"), Decimal("0.1")
    ),
    Parameterbeschreibung(
        "min_on_seconds", "Mindest-Einschaltdauer", "s", Decimal(30), Decimal(7200), Decimal(10)
    ),
    Parameterbeschreibung(
        "min_off_seconds", "Mindest-Ausschaltdauer", "s", Decimal(30), Decimal(7200), Decimal(10)
    ),
    Parameterbeschreibung(
        "sensor_timeout_seconds", "Sensorausfall nach", "s",
        Decimal(60), Decimal(86400), Decimal(30),
    ),
    Parameterbeschreibung(
        "temperature_offset_k", "Sensorabgleich", "K",
        Decimal("-10.0"), Decimal("10.0"), Decimal("0.1"),
    ),
    Parameterbeschreibung(
        "window_resume_delay_seconds", "Nachlauf nach Fensterschluss", "s",
        Decimal(0), Decimal(3600), Decimal(10),
    ),
)

NACH_NAME: dict[str, Parameterbeschreibung] = {p.name: p for p in PARAMETER}


def parameter_setzen(
    session: Session,
    zone: Zone,
    name: str,
    wert: Decimal,
    *,
    user_id: int | None,
    token_id: int | None = None,
    quelle: str = "web",
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
    beschreibung = NACH_NAME.get(name)
    if beschreibung is None:
        raise Parameterunbekannt(f"Den Regelparameter '{name}' gibt es nicht.")
    if not beschreibung.minimum <= wert <= beschreibung.maximum:
        raise Parametergrenze(
            f"{beschreibung.beschriftung} muss zwischen {beschreibung.minimum} und "
            f"{beschreibung.maximum} liegen."
        )
    gerundet = int(wert) if beschreibung.ganzzahlig else wert
    # Die uebrigen Felder so uebernehmen, wie sie an der Zone stehen -- ein geerbtes
    # None bleibt geerbt. Nur dieser eine Parameter wird festgeschrieben.
    werte: dict[str, Decimal | int | None] = {
        feld: getattr(zone, feld) for feld in Regelparameter.__dataclass_fields__
    }
    werte[name] = gerundet
    regelparameter_speichern(
        session, zone, werte, user_id=user_id, token_id=token_id, quelle=quelle
    )
    return Decimal(gerundet)
