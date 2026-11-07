"""Room temperature of a zone from one or more input sensors: the mean of the usable ones.

**Reine Funktion.** No clock of its own (`now` is the caller's), no database access --
like `domain/fault.py`, whose `sensor_state` decides, per sensor, whether a reading is
current. This module only combines those per-sensor verdicts into one zone verdict.

Fachentscheidungen (Projektinhaber):

- Each sensor is judged on its own by `fault.sensor_state`; only sensors with status
  `ok` enter the mean. A sensor without a value counts as `keine_quelle`, like one
  that never reported.
- One usable sensor is enough. The zone status is `ok` if at least one sensor is `ok`,
  else `veraltet` if sensors exist that did report once, else `keine_quelle`. With
  exactly one sensor this is bit-for-bit what `sensor_state` says for it.
- `measured_at` of the room is the newest measurement time among the usable sensors.
- No smoothing: when a sensor drops out, the mean jumps at once. That is intended --
  an averaged-in stale value would be worse than an honest step.
- Rounding: the mean of several sensors is rounded half-up to 0.01 K (the scale of
  `ZoneState.temperature_c`, `Numeric(5, 2)`). A single usable sensor's value is
  passed through unchanged.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from thermoctl.domain.fault import NO_SOURCE, OK, VERALTET, sensor_state
from thermoctl.domain.number_text import temperature_text

MEAN_QUANTUM = Decimal("0.01")


@dataclass(frozen=True)
class SensorInput:
    device_id: int
    value: Decimal | None
    measured_at: datetime | None


@dataclass(frozen=True)
class SensorVerdict:
    device_id: int
    status: str


@dataclass(frozen=True)
class RoomTemperature:
    mean: Decimal | None
    measured_at: datetime | None
    status: str
    usable_count: int
    total_count: int
    sensors: tuple[SensorVerdict, ...]
    reason: str


def _sensor_status(sensor: SensorInput, now: datetime, timeout_s: int) -> str:
    if sensor.value is None:
        return NO_SOURCE
    return sensor_state(sensor.measured_at, now, timeout_s)


def room_temperature(
    sensors: Sequence[SensorInput],
    now: datetime,
    timeout_s: int,
) -> RoomTemperature:
    verdicts = tuple(SensorVerdict(s.device_id, _sensor_status(s, now, timeout_s)) for s in sensors)
    usable = [s for s, v in zip(sensors, verdicts, strict=True) if v.status == OK]
    total = len(sensors)

    if not usable:
        status = VERALTET if any(v.status == VERALTET for v in verdicts) else NO_SOURCE
        if total == 0:
            reason = "Keine Temperaturfuehler zugeordnet — keine Sensorquelle verfuegbar."
        elif status == VERALTET:
            reason = f"Kein brauchbarer Temperaturfuehler (0 von {total}) — Messwerte veraltet."
        else:
            reason = (
                f"Kein brauchbarer Temperaturfuehler (0 von {total}) — "
                "noch nie einen Messwert empfangen."
            )
        return RoomTemperature(None, None, status, 0, total, verdicts, reason)

    values = [s.value for s in usable if s.value is not None]
    if len(values) == 1:
        mean = values[0]
    else:
        mean = (sum(values, Decimal(0)) / len(values)).quantize(
            MEAN_QUANTUM, rounding=ROUND_HALF_UP
        )
    newest = max(s.measured_at for s in usable if s.measured_at is not None)

    if total == 1:
        reason = f"Raumtemperatur {temperature_text(mean)} von einem Fuehler."
    else:
        reason = (
            f"Raumtemperatur {temperature_text(mean)} als Mittelwert von "
            f"{len(usable)} von {total} Fuehlern"
        )
        if len(usable) < total:
            skipped = ", ".join(str(v.device_id) for v in verdicts if v.status != OK)
            reason += f" (nicht brauchbar: Geraet {skipped})"
        reason += "."
    return RoomTemperature(mean, newest, OK, len(usable), total, verdicts, reason)
