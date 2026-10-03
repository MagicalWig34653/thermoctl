"""`domain.temperature_source_health` -- reine Funktion, siehe Moduldocstring.

Deckt Plan 0.11.0 Auftrag 5a ab: keine Zuordnung, eine, mehrere (kälteste
korrigierte Messung gewinnt, Offset beeinflusst die Reihenfolge), alle
veraltet, die Echo-Regel des Bosch-BTH-RA-Gerätevertrags an ihren Grenzen,
ein Thermostat ohne je geschriebene externe Temperatur, und Determinismus.
"""

import dataclasses
from datetime import datetime, timedelta
from decimal import Decimal as D

import pytest

from thermoctl.domain.temperature_source_health import (
    ECHO_INDEPENDENCE_DELAY,
    STAGE_ERSATZQUELLE,
    STAGE_KEINE,
    STAGE_WANDFUEHLER,
    SourceHealth,
    ThermostatCandidate,
    WallProbeReading,
    evaluate_source_health,
)

NOW = datetime(2026, 9, 29, 12, 0, 0)
TIMEOUT_S = 300


def wall(temperature_c: D | None, age_s: int = 0) -> WallProbeReading:
    measured_at = None if temperature_c is None else NOW - timedelta(seconds=age_s)
    return WallProbeReading(temperature_c, measured_at)


def candidate(
    device_id: int,
    *,
    name: str = "TRV",
    temperature_c: D | None = D("22.0"),
    age_s: int = 0,
    offset_k: D = D("0"),
    last_write_at: datetime | None = None,
    measured_at: datetime | None = None,
) -> ThermostatCandidate:
    if measured_at is None:
        measured_at = None if temperature_c is None else NOW - timedelta(seconds=age_s)
    return ThermostatCandidate(
        device_id=device_id,
        device_name=name,
        local_temperature_c=temperature_c,
        measured_at=measured_at,
        offset_k=offset_k,
        last_external_write_at=last_write_at,
    )


# --- Wandfühler brauchbar: Ersatzquelle spielt keine Rolle -----------------


def test_usable_wall_probe_wins_regardless_of_candidates() -> None:
    result = evaluate_source_health(
        wall(D("21.5")),
        [candidate(1, temperature_c=D("18.0"))],
        now=NOW,
        timeout_s=TIMEOUT_S,
    )

    assert result.stage == STAGE_WANDFUEHLER
    assert result.effective_temperature_c == D("21.5")
    assert result.wall_probe_usable is True
    assert result.selected_device_id is None
    # Auch bei brauchbarem Wandfühler wird jeder Kandidat für die
    # Vergleichsprotokollierung (Plan Abschnitt 6, R2) bewertet.
    assert len(result.candidates) == 1


# --- Kein Thermostat zugeordnet ---------------------------------------------


def test_no_thermostat_assigned_and_failed_wall_probe_yields_no_source() -> None:
    result = evaluate_source_health(wall(None), [], now=NOW, timeout_s=TIMEOUT_S)

    assert result.stage == STAGE_KEINE
    assert result.effective_temperature_c is None
    assert result.wall_probe_usable is False
    assert result.candidates == ()
    assert "keine Thermostat-Ersatzquelle" in result.reason


# --- Genau ein Thermostat ----------------------------------------------------


def test_single_usable_candidate_becomes_the_replacement() -> None:
    result = evaluate_source_health(
        wall(None),
        [candidate(23, name="Bad Thermostat", temperature_c=D("23.4"))],
        now=NOW,
        timeout_s=TIMEOUT_S,
    )

    assert result.stage == STAGE_ERSATZQUELLE
    assert result.effective_temperature_c == D("23.4")
    assert result.selected_device_id == 23
    assert result.selected_device_name == "Bad Thermostat"
    assert result.candidates[0].usable is True
    assert result.candidates[0].echo is False


# --- Mehrere Thermostate: kälteste korrigierte Messung gewinnt --------------


def test_multiple_candidates_pick_the_coldest_corrected_reading() -> None:
    result = evaluate_source_health(
        wall(None),
        [
            candidate(1, name="Warm", temperature_c=D("24.0")),
            candidate(2, name="Kalt", temperature_c=D("21.0")),
            candidate(3, name="Mittel", temperature_c=D("22.5")),
        ],
        now=NOW,
        timeout_s=TIMEOUT_S,
    )

    assert result.stage == STAGE_ERSATZQUELLE
    assert result.selected_device_id == 2
    assert result.effective_temperature_c == D("21.0")
    assert len(result.candidates) == 3


def test_offset_can_change_which_candidate_is_coldest_after_correction() -> None:
    # Ohne Ausgleich wäre Gerät 1 (22.0) kälter als Gerät 2 (22.5); der
    # Ausgleich von Gerät 2 (2 K, systematisch zu warm) macht es nach Korrektur
    # kälter (20.5) und damit zum Gewinner.
    result = evaluate_source_health(
        wall(None),
        [
            candidate(1, name="Ohne Ausgleich", temperature_c=D("22.0")),
            candidate(2, name="Mit Ausgleich", temperature_c=D("22.5"), offset_k=D("2.0")),
        ],
        now=NOW,
        timeout_s=TIMEOUT_S,
    )

    assert result.selected_device_id == 2
    assert result.effective_temperature_c == D("20.5")


def test_tie_after_correction_is_broken_by_device_id_deterministically() -> None:
    result = evaluate_source_health(
        wall(None),
        [
            candidate(9, temperature_c=D("22.0")),
            candidate(4, temperature_c=D("22.0")),
        ],
        now=NOW,
        timeout_s=TIMEOUT_S,
    )

    assert result.selected_device_id == 4


# --- Alle Thermostate veraltet -----------------------------------------------


def test_all_candidates_stale_yields_no_source() -> None:
    result = evaluate_source_health(
        wall(None),
        [
            candidate(1, temperature_c=D("22.0"), age_s=TIMEOUT_S + 1),
            candidate(2, temperature_c=D("21.0"), age_s=TIMEOUT_S + 100),
        ],
        now=NOW,
        timeout_s=TIMEOUT_S,
    )

    assert result.stage == STAGE_KEINE
    assert result.effective_temperature_c is None
    assert all(not a.usable for a in result.candidates)
    assert "brauchbare Messung" in result.reason


def test_candidate_never_measured_is_reported_as_no_source() -> None:
    result = evaluate_source_health(
        wall(None),
        [candidate(1, temperature_c=None)],
        now=NOW,
        timeout_s=TIMEOUT_S,
    )

    assert result.stage == STAGE_KEINE
    assert result.candidates[0].usable is False
    assert result.candidates[0].raw_temperature_c is None
    assert result.candidates[0].corrected_temperature_c is None


# --- Echo-Regel (Bosch BTH-RA, 30 min) --------------------------------------


def test_echo_within_29_minutes_59_seconds_blocks_replacement() -> None:
    last_write = NOW - (ECHO_INDEPENDENCE_DELAY - timedelta(seconds=1))
    result = evaluate_source_health(
        wall(None),
        [
            candidate(
                1,
                temperature_c=D("23.5"),
                last_write_at=last_write,
                measured_at=NOW,
            )
        ],
        now=NOW,
        timeout_s=TIMEOUT_S,
    )

    assert result.stage == STAGE_KEINE
    assert result.candidates[0].echo is True
    assert result.candidates[0].usable is False
    assert "Echo" in result.candidates[0].reason


def test_echo_exactly_30_minutes_with_a_reading_taken_after_becomes_usable() -> None:
    # `last_write` chosen so the switch-over instant (`last_write + 30 min`)
    # lands exactly at `NOW` -- the reading can then be both "at the boundary"
    # and fresh enough to pass the separate staleness check.
    last_write = NOW - ECHO_INDEPENDENCE_DELAY
    threshold = last_write + ECHO_INDEPENDENCE_DELAY
    assert threshold == NOW
    result = evaluate_source_health(
        wall(None),
        [
            candidate(
                1,
                name="Bad Thermostat",
                temperature_c=D("23.0"),
                last_write_at=last_write,
                measured_at=threshold,
            )
        ],
        now=NOW,
        timeout_s=TIMEOUT_S,
    )

    assert result.stage == STAGE_ERSATZQUELLE
    assert result.candidates[0].echo is False
    assert result.selected_device_id == 1


def test_reading_older_than_the_switchover_instant_stays_blocked_even_if_now_is_later() -> None:
    last_write = NOW - timedelta(hours=2)
    threshold = last_write + ECHO_INDEPENDENCE_DELAY
    # `now` is long past the 30-minute delay, but the *reading itself* was
    # taken before the device's own switch-over instant -- still an echo.
    stale_measurement_before_threshold = threshold - timedelta(seconds=1)
    result = evaluate_source_health(
        wall(None),
        [
            candidate(
                1,
                temperature_c=D("23.0"),
                last_write_at=last_write,
                measured_at=stale_measurement_before_threshold,
            )
        ],
        now=NOW,
        timeout_s=TIMEOUT_S,
    )

    assert result.stage == STAGE_KEINE
    assert result.candidates[0].echo is True


def test_a_reading_with_no_measurement_timestamp_is_not_independent_even_if_written_to() -> None:
    # `local_temperature_c` present but `measured_at` missing: a malformed or
    # incomplete reading. `_independent_of_echo` cannot judge freshness against
    # the switch-over instant without a timestamp, so it must not grant
    # independence just because a write happened -- the conservative direction
    # (Grundsatz 7). Built directly (not through the `candidate()` helper,
    # which always derives `measured_at` from `temperature_c` when not given
    # explicitly as a non-`None` override) so `measured_at` is genuinely
    # `None` here despite a present reading.
    broken_candidate = ThermostatCandidate(
        device_id=1,
        device_name="TRV",
        local_temperature_c=D("22.0"),
        measured_at=None,
        offset_k=D("0"),
        last_external_write_at=NOW - timedelta(hours=2),
    )

    result = evaluate_source_health(wall(None), [broken_candidate], now=NOW, timeout_s=TIMEOUT_S)

    assert result.stage == STAGE_KEINE
    assert result.candidates[0].echo is True


def test_thermostat_never_fed_an_external_temperature_is_immediately_independent() -> None:
    result = evaluate_source_health(
        wall(None),
        [candidate(1, temperature_c=D("22.0"), last_write_at=None)],
        now=NOW,
        timeout_s=TIMEOUT_S,
    )

    assert result.stage == STAGE_ERSATZQUELLE
    assert result.candidates[0].echo is False


# --- Wandfühler-Rückkehr während Ersatzquelle: die Funktion liefert nur den
#     Zustand des Wandfühlers, entscheidet aber nicht selbst (das macht 5b). --


def test_wall_probe_usability_is_reported_even_while_a_replacement_is_active() -> None:
    stale_wall = wall(D("20.0"), age_s=TIMEOUT_S + 1)
    result = evaluate_source_health(
        stale_wall,
        [candidate(1, temperature_c=D("21.0"))],
        now=NOW,
        timeout_s=TIMEOUT_S,
    )

    assert result.stage == STAGE_ERSATZQUELLE
    assert result.wall_probe_usable is False


# --- Determinismus -----------------------------------------------------------


def test_same_input_always_yields_an_equal_result() -> None:
    args = (
        wall(D("19.0")),
        [
            candidate(1, temperature_c=D("22.0")),
            candidate(2, temperature_c=D("21.5"), offset_k=D("0.5")),
        ],
    )

    first = evaluate_source_health(*args, now=NOW, timeout_s=TIMEOUT_S)
    second = evaluate_source_health(*args, now=NOW, timeout_s=TIMEOUT_S)

    assert first == second


@pytest.mark.parametrize("timeout_s", [0, 300, 3600])
def test_evaluate_never_raises_for_a_zone_with_no_data_at_all(timeout_s: int) -> None:
    result = evaluate_source_health(wall(None), [], now=NOW, timeout_s=timeout_s)

    assert result.stage == STAGE_KEINE


# --- Mutationslauf: feste Werte statt der Konstante ---------------------------
# Die Tests oben leiten die Grenze aus ECHO_INDEPENDENCE_DELAY ab und fangen daher
# nicht, wenn die Konstante selbst verschoben wird. Hier steht die dokumentierte
# Umschaltzeit des Geräts (30 Minuten) als Literal.


def test_echo_delay_is_exactly_thirty_minutes() -> None:
    assert ECHO_INDEPENDENCE_DELAY == timedelta(minutes=30)


def _echo_probe(measured_after_write: timedelta) -> SourceHealth:
    last_write = NOW - timedelta(minutes=45)
    return evaluate_source_health(
        wall(None),
        [
            candidate(
                1,
                last_write_at=last_write,
                measured_at=last_write + measured_after_write,
                age_s=0,
            )
        ],
        now=NOW,
        timeout_s=3600,
    )


def test_reading_29_min_59_s_after_the_write_is_still_an_echo() -> None:
    result = _echo_probe(timedelta(minutes=29, seconds=59))
    assert result.candidates[0].echo is True
    assert result.stage == STAGE_KEINE


def test_reading_exactly_30_min_after_the_write_is_independent() -> None:
    result = _echo_probe(timedelta(minutes=30))
    assert result.candidates[0].echo is False
    assert result.stage == STAGE_ERSATZQUELLE


def test_reason_texts_name_the_actual_situation() -> None:
    echo = _echo_probe(timedelta(minutes=10)).candidates[0].reason
    assert echo == (
        "TRV: lokale Messung ist ein Echo der eingespeisten Ist-Temperatur, seit dem "
        "letzten Schreiben sind noch keine 30 Minuten mit einer unabhängigen Messung "
        "vergangen."
    )
    stale = evaluate_source_health(
        wall(None),
        [candidate(1, age_s=TIMEOUT_S + 60)],
        now=NOW,
        timeout_s=TIMEOUT_S,
    ).candidates[0]
    assert stale.reason == "TRV: Messwert veraltet."
    ok = evaluate_source_health(
        wall(None), [candidate(1)], now=NOW, timeout_s=TIMEOUT_S
    ).candidates[0]
    assert ok.reason == "TRV: als Ersatzquelle brauchbar."


def test_all_result_types_are_immutable() -> None:
    result = evaluate_source_health(wall(D("21.0")), [candidate(1)], now=NOW, timeout_s=TIMEOUT_S)
    for obj, attr in (
        (wall(D("21.0")), "temperature_c"),
        (candidate(1), "device_name"),
        (result.candidates[0], "usable"),
        (result, "stage"),
    ):
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(obj, attr, None)


def test_evaluate_source_health_takes_now_and_timeout_by_keyword_only() -> None:
    with pytest.raises(TypeError):
        evaluate_source_health(wall(None), [], NOW, TIMEOUT_S)  # type: ignore[misc]
