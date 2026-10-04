"""Effective zone temperature: wall probe, or automatic thermostat replacement.

**Reine Funktion.** No clock of its own (`now` is always the caller's), no
database or network access -- every input arrives as a plain data class, exactly
like `domain/fault.py` and `domain/emergency_cycle.py`. The thin, impure lookup
that this module needs but must not perform itself -- "when did thermoctl last
*attempt* to write an external temperature to this device" -- lives in
`services/temperature_source_health.py`; this module only ever receives the
resulting timestamp. Deliberately "attempt", not "confirmed successful write":
see that module's `_NOT_A_WRITE` for why a `failed` outcome still counts.

## Why a thermostat's own reading needs a special rule (Bosch BTH-RA)

Every thermostat eligible as a zone actuator here is a Bosch BTH-RA (confirmed
against two real-plant database dumps, see
`lokal/plaene/0.11.0-geraetevertrag.md` (a)). While thermoctl feeds it an
external room temperature (`remote_temperature`, written by
`domain/self_regulating.py` for a self-regulating valve), the device's own
`local_temperature` is **not an independent measurement** -- it echoes the fed
value back within a few seconds, confirmed against real device-command and
measurement history. Per the manufacturer's own device contract, the device
falls back to its internal probe only after **at least 30 minutes** without a
new `remote_temperature` write -- silence is enough, no explicit switch-over
command exists. Before that delay has elapsed, a `local_temperature` reading
that *looks* current is, at best, a delayed copy of the wall probe that has
just failed -- using it as a replacement would not add a second, independent
source, it would silently keep trusting the same failed one.

`ECHO_INDEPENDENCE_DELAY` is therefore a named, documented device property, not
a value spread across call sites. It is not merely "30 minutes since now": a
candidate only counts as independent once it has a reading whose own
`measured_at` falls **at or after** `last_external_write_at +
ECHO_INDEPENDENCE_DELAY` -- an old, cached reading from before that instant
must not be treated as fresh signal even once the delay has technically
elapsed (see `evaluate_source_health`'s docstring for the boundary tests this
guards against).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import cast

from thermoctl.domain.fault import OK, sensor_state
from thermoctl.domain.number_text import temperature_text

# Bosch BTH-RA Gerätevertrag (belegt, siehe Moduldocstring): das Gerät fällt
# frühestens nach 30 Minuten ohne neuen `remote_temperature`-Schreibvorgang auf
# seinen internen Fühler zurück. Eine benannte Konstante, damit „30 Minuten"
# nicht als Magic Number an mehreren Stellen auftaucht und künftig an einem
# einzigen Ort für ein neues Modell angepasst werden kann.
ECHO_INDEPENDENCE_DELAY = timedelta(minutes=30)

STAGE_WANDFUEHLER = "wandfuehler"
STAGE_ERSATZQUELLE = "ersatzquelle"
STAGE_KEINE = "keine"


@dataclass(frozen=True)
class WallProbeReading:
    """The zone's selected wall probe, exactly as far as this module needs it."""

    temperature_c: Decimal | None
    measured_at: datetime | None


@dataclass(frozen=True)
class ThermostatCandidate:
    """One thermostat-actuator assignment of the zone, as a possible replacement.

    `last_external_write_at` is the moment thermoctl last *attempted* to write
    an external temperature to this exact device -- `None` means no such
    attempt has ever happened (a thermostat that is not run as a
    self-regulating valve, or one thermoctl has simply never fed yet). A
    dry-run is not an attempt: the repository function this comes from
    (`services.temperature_source_health.last_external_temperature_write_at`)
    excludes only the one outcome that provably never left the service --
    a `failed` command still counts, because its outcome at the device
    itself is not actually known (see that function's docstring).
    """

    device_id: int
    device_name: str
    local_temperature_c: Decimal | None
    measured_at: datetime | None
    offset_k: Decimal
    last_external_write_at: datetime | None


@dataclass(frozen=True)
class CandidateAssessment:
    """Per-candidate detail for the comparison log (Plan Abschnitt 6, R2).

    Kept even for a candidate that was not chosen, and even for one that is
    not currently usable at all -- the point of logging every candidate is to
    build up calibration evidence over time (R2), not just to explain the
    single winner.
    """

    device_id: int
    device_name: str
    raw_temperature_c: Decimal | None
    corrected_temperature_c: Decimal | None
    usable: bool
    echo: bool
    reason: str


@dataclass(frozen=True)
class SourceHealth:
    """The effective zone temperature and how it was arrived at."""

    stage: str
    effective_temperature_c: Decimal | None
    wall_probe_usable: bool
    selected_device_id: int | None
    selected_device_name: str | None
    reason: str
    candidates: tuple[CandidateAssessment, ...]


def _wall_probe_usable(wall_probe: WallProbeReading, now: datetime, timeout_s: int) -> bool:
    # Same definition the plan carries over unchanged from `domain/fault.py`:
    # status `ok` *and* a numeric reading present -- see plan 1.2, last
    # paragraph.
    if wall_probe.temperature_c is None:
        return False
    return sensor_state(wall_probe.measured_at, now, timeout_s) == OK


def _corrected(candidate: ThermostatCandidate) -> Decimal | None:
    # A thermostatic valve's own sensor sits on (or right next to) the
    # radiator and therefore reads systematically warmer than the room -- see
    # `domain/self_regulating.py`'s module docstring and plan 1.3's reasoning
    # for choosing the *coldest* candidate. `offset_k` is calibrated by the
    # operator as "how many kelvin this device reads too warm"; subtracting it
    # moves the reading back towards the true room temperature. Vorgabe 0 K
    # (plan 1.3) leaves the raw reading untouched until someone calibrates it.
    if candidate.local_temperature_c is None:
        return None
    return candidate.local_temperature_c - candidate.offset_k


def _independent_of_echo(candidate: ThermostatCandidate) -> bool:
    """Whether this candidate's current reading is trusted as its own, not an echo.

    Never written to at all: trivially independent -- there is nothing to
    echo. Otherwise independent only once the reading's own timestamp reaches
    the device's documented switch-over instant (`last_external_write_at +
    ECHO_INDEPENDENCE_DELAY`); a reading measured *before* that instant is a
    stale echo even if `now` is long past it, and a reading with no
    `measured_at` at all cannot be judged fresh.
    """
    if candidate.last_external_write_at is None:
        return True
    if candidate.measured_at is None:
        return False
    threshold = candidate.last_external_write_at + ECHO_INDEPENDENCE_DELAY
    return candidate.measured_at >= threshold


def _candidate_assessment(
    candidate: ThermostatCandidate, now: datetime, timeout_s: int
) -> CandidateAssessment:
    has_reading = candidate.local_temperature_c is not None
    fresh = has_reading and sensor_state(candidate.measured_at, now, timeout_s) == OK
    independent = _independent_of_echo(candidate)
    echo = has_reading and candidate.last_external_write_at is not None and not independent
    usable = fresh and independent

    if not has_reading:
        reason = f"{candidate.device_name}: kein Messwert empfangen."
    elif echo:
        reason = (
            f"{candidate.device_name}: lokale Messung ist ein Echo der eingespeisten "
            f"Ist-Temperatur, seit dem letzten Schreiben sind noch keine "
            f"{int(ECHO_INDEPENDENCE_DELAY.total_seconds() // 60)} Minuten mit einer "
            "unabhängigen Messung vergangen."
        )
    elif not fresh:
        reason = f"{candidate.device_name}: Messwert veraltet."
    else:
        reason = f"{candidate.device_name}: als Ersatzquelle brauchbar."

    return CandidateAssessment(
        device_id=candidate.device_id,
        device_name=candidate.device_name,
        raw_temperature_c=candidate.local_temperature_c,
        corrected_temperature_c=_corrected(candidate),
        usable=usable,
        echo=echo,
        reason=reason,
    )


def evaluate_source_health(
    wall_probe: WallProbeReading,
    candidates: Sequence[ThermostatCandidate],
    *,
    now: datetime,
    timeout_s: int,
) -> SourceHealth:
    """The effective zone temperature right now: wall probe, or replacement.

    Deterministic in every input: no clock of its own, no randomness, and
    candidates are never mutated -- calling this twice with the same
    arguments always returns an equal result. Ties in the corrected
    temperature between several usable candidates are broken by `device_id`
    (ascending), the same "smallest surrogate key wins, not first-seen"
    convention `sensor_failure_policy.migration_default_profile_id` documents
    for a comparable tie -- there is no fachliche Begründung for "first in the
    input list" either.
    """
    wall_usable = _wall_probe_usable(wall_probe, now, timeout_s)
    assessments = tuple(_candidate_assessment(c, now, timeout_s) for c in candidates)

    if wall_usable:
        return SourceHealth(
            stage=STAGE_WANDFUEHLER,
            effective_temperature_c=wall_probe.temperature_c,
            wall_probe_usable=True,
            selected_device_id=None,
            selected_device_name=None,
            reason="Wandfühler ist betriebsbereit.",
            candidates=assessments,
        )

    usable = [a for a in assessments if a.usable]
    if usable:
        chosen = min(
            usable, key=lambda a: (a.corrected_temperature_c, a.device_id)
        )
        return SourceHealth(
            stage=STAGE_ERSATZQUELLE,
            effective_temperature_c=chosen.corrected_temperature_c,
            wall_probe_usable=False,
            selected_device_id=chosen.device_id,
            selected_device_name=chosen.device_name,
            reason=(
                f"Wandfühler ausgefallen — Ersatzquelle {chosen.device_name} "
                f"({temperature_text(cast(Decimal, chosen.corrected_temperature_c))} "
                "korrigiert) aktiv."
            ),
            candidates=assessments,
        )

    if not candidates:
        reason = "Wandfühler ausgefallen — keine Thermostat-Ersatzquelle zugeordnet."
    else:
        reason = "Wandfühler ausgefallen — keine Ersatzquelle liefert eine brauchbare Messung."
    return SourceHealth(
        stage=STAGE_KEINE,
        effective_temperature_c=None,
        wall_probe_usable=False,
        selected_device_id=None,
        selected_device_name=None,
        reason=reason,
        candidates=assessments,
    )


__all__ = [
    "ECHO_INDEPENDENCE_DELAY",
    "STAGE_ERSATZQUELLE",
    "STAGE_KEINE",
    "STAGE_WANDFUEHLER",
    "CandidateAssessment",
    "SourceHealth",
    "ThermostatCandidate",
    "WallProbeReading",
    "evaluate_source_health",
]
