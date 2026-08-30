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
