"""Vollständige Ausgabewerte (Zustand *und* Ereignisse) je Übergang von
`thermoctl.domain.emergency_operation.advance`.

Ergänzung zu test_emergency_operation.py aus dem Mutationslauf (Auftrag 10): Die
dortigen Tests prüfen Stufe und einzelne Signale; hier wird jeder Übergang mit **allen**
Feldern von `ZoneEmergencyState` und `StageEvents` und dem Begründungstext festgenagelt.
Ein vertauschtes `episode_ended`, `pi_neutralized`, `handover_due` oder ein falsch
vorbelegter Sperrmerker (`handover_due_signalled`) ist in der Regelkette ein echter Fehler
(PI läuft weiter gegen einen ausgefallenen Fühler, Übergabe doppelt oder gar nicht).
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta

import pytest

from thermoctl.domain.emergency_operation import (
    KIND_ERSATZQUELLE,
    KIND_WANDFUEHLER,
    REASON_DEAKTIVIERT,
    REASON_ERSATZQUELLE_EINTRITT,
    REASON_ERSATZQUELLE_HAELT,
    REASON_NORMAL,
    REASON_NOTBETRIEB_EINTRITT,
    REASON_NOTBETRIEB_HAELT,
    REASON_RUECKFALL,
    REASON_RUECKKEHR_ABGESCHLOSSEN,
    REASON_RUECKKEHR_LAEUFT,
    REASON_RUECKKEHR_NEUSTART,
    REASON_RUECKKEHR_STARTET,
    STAGE_ERSATZQUELLE,
    STAGE_NORMAL,
    STAGE_NOTBETRIEB,
    STAGE_RUECKKEHRPRUEFUNG,
    TRIGGER_ALLE_QUELLEN,
    TRIGGER_WANDFUEHLER,
    SourceReading,
    StageEvents,
    StageInput,
    ZoneEmergencyState,
    advance,
)

NOW = datetime(2026, 1, 10, 12, 0, 0, tzinfo=UTC)
SECONDS = 60
SAMPLES = 3


def _src(
    usable: bool,
    measured_at: datetime | None = NOW,
    device_id: int | None = None,
    name: str | None = None,
) -> SourceReading:
    return SourceReading(usable, measured_at, device_id, name)


DOWN = _src(False, None)
WALL_OK = _src(True)
TRV = _src(True, NOW, 7, "TRV")


def _inp(wall: SourceReading, repl: SourceReading, *, enabled: bool = True) -> StageInput:
    return StageInput(NOW, enabled, wall, repl, SECONDS, SAMPLES)


def _events(
    *,
    code: str,
    reason: str,
    started: bool = False,
    ended: bool = False,
    trigger: str | None = None,
    pi: bool = True,
    handover: bool = False,
) -> StageEvents:
    return StageEvents(started, ended, trigger, pi, handover, code, reason)


def test_all_types_are_immutable() -> None:
    out = advance(None, _inp(WALL_OK, DOWN))
    for obj in (WALL_OK, _inp(WALL_OK, DOWN), out.state, out.events, out):
        field = dataclasses.fields(obj)[0].name  # type: ignore[arg-type]
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(obj, field, None)


def test_state_defaults_describe_no_open_episode() -> None:
    s = ZoneEmergencyState()
    assert (s.stage, s.tracked_kind, s.tracked_device_id) == (STAGE_NORMAL, None, None)
    assert (s.source_measured_at, s.recovery_started_at, s.last_counted_measurement_at) == (
        None,
        None,
        None,
    )
    assert s.recovery_sample_count == 0
    assert s.episode_open is False
    assert s.handover_due_signalled is False


def test_normal_with_usable_wall_probe() -> None:
    out = advance(None, _inp(WALL_OK, TRV))
    assert out.state == ZoneEmergencyState()
    assert out.events == _events(
        code=REASON_NORMAL, reason="Normalbetrieb: Wandfühler betriebsbereit.", pi=False
    )


def test_normal_to_ersatzquelle() -> None:
    out = advance(ZoneEmergencyState(), _inp(DOWN, TRV))
    assert out.state == ZoneEmergencyState(stage=STAGE_ERSATZQUELLE, episode_open=True)
    assert out.events == _events(
        code=REASON_ERSATZQUELLE_EINTRITT,
        reason="Wandfühler ausgefallen — Ersatzquelle TRV übernimmt, PI ausgesetzt.",
        started=True,
        trigger=TRIGGER_WANDFUEHLER,
    )


def test_replacement_without_name_is_called_ersatzquelle() -> None:
    out = advance(None, _inp(DOWN, _src(True, NOW, 7, None)))
    assert out.events.reason == (
        "Wandfühler ausgefallen — Ersatzquelle Ersatzquelle übernimmt, PI ausgesetzt."
    )


def test_normal_to_notbetrieb_when_nothing_is_usable() -> None:
    out = advance(None, _inp(DOWN, DOWN))
    assert out.state == ZoneEmergencyState(
        stage=STAGE_NOTBETRIEB, episode_open=True, handover_due_signalled=True
    )
    assert out.events == _events(
        code=REASON_NOTBETRIEB_EINTRITT,
        reason="Wandfühler und Ersatzquelle unbrauchbar — Notbetrieb, Thermostat-Übergabe fällig.",
        started=True,
        trigger=TRIGGER_ALLE_QUELLEN,
        handover=True,
    )


def _ersatz(*, signalled: bool = False, **kw: object) -> ZoneEmergencyState:
    return ZoneEmergencyState(
        stage=STAGE_ERSATZQUELLE,
        episode_open=True,
        handover_due_signalled=signalled,
        **kw,  # type: ignore[arg-type]
    )


def test_ersatzquelle_holds_and_keeps_the_handover_latch() -> None:
    out = advance(_ersatz(signalled=True), _inp(DOWN, TRV))
    assert out.state == _ersatz(signalled=True)
    assert out.events == _events(
        code=REASON_ERSATZQUELLE_HAELT,
        reason="Ersatzquelle TRV weiterhin aktiv, Wandfühler nicht brauchbar.",
    )


@pytest.mark.parametrize(
    ("signalled", "tail"),
    [
        (False, "Notbetrieb, Thermostat-Übergabe fällig."),
        (True, "Notbetrieb (gleiche Störung)."),
    ],
)
def test_ersatzquelle_to_notbetrieb_signals_handover_only_once(signalled: bool, tail: str) -> None:
    out = advance(_ersatz(signalled=signalled), _inp(DOWN, DOWN))
    assert out.state == ZoneEmergencyState(
        stage=STAGE_NOTBETRIEB, episode_open=True, handover_due_signalled=True
    )
    assert out.events == _events(
        code=REASON_NOTBETRIEB_EINTRITT,
        reason=f"Auch die Ersatzquelle ist unbrauchbar geworden — {tail}",
        handover=not signalled,
    )


def test_ersatzquelle_recovery_against_wall_probe_starts_counting() -> None:
    out = advance(_ersatz(signalled=True), _inp(WALL_OK, TRV))
    assert out.state == ZoneEmergencyState(
        stage=STAGE_ERSATZQUELLE,
        tracked_kind=KIND_WANDFUEHLER,
        source_measured_at=NOW,
        recovery_started_at=NOW,
        last_counted_measurement_at=NOW,
        recovery_sample_count=1,
        episode_open=True,
        handover_due_signalled=True,
    )
    assert out.events == _events(
        code=REASON_RUECKKEHR_LAEUFT,
        reason=(
            "Rückkehrprüfung gegen Wandfühler: 1/3 neue Messwerte, 0/60 s durchgehend brauchbar."
        ),
    )


def test_ersatzquelle_recovery_against_wall_probe_completes_and_ends_episode() -> None:
    cur = _ersatz(
        signalled=True,
        tracked_kind=KIND_WANDFUEHLER,
        recovery_started_at=NOW - timedelta(seconds=120),
        last_counted_measurement_at=NOW - timedelta(seconds=30),
        recovery_sample_count=2,
    )
    out = advance(cur, _inp(WALL_OK, TRV))
    assert out.state == ZoneEmergencyState()
    assert out.events == _events(
        code=REASON_RUECKKEHR_ABGESCHLOSSEN,
        reason=(
            "Rückkehrprüfung gegen Wandfühler abgeschlossen (3 Messwerte, 120 s) — "
            "Normalbetrieb wieder aufgenommen."
        ),
        ended=True,
        pi=False,
    )


def _notbetrieb() -> ZoneEmergencyState:
    return ZoneEmergencyState(
        stage=STAGE_NOTBETRIEB, episode_open=True, handover_due_signalled=True
    )


def test_notbetrieb_holds_without_any_source() -> None:
    out = advance(_notbetrieb(), _inp(DOWN, DOWN))
    assert out.state == _notbetrieb()
    assert out.events == _events(
        code=REASON_NOTBETRIEB_HAELT,
        reason="Notbetrieb hält an — keine Quelle liefert eine Messung.",
    )


def test_notbetrieb_starts_recovery_against_wall_probe() -> None:
    out = advance(_notbetrieb(), _inp(WALL_OK, DOWN))
    assert out.state == ZoneEmergencyState(
        stage=STAGE_RUECKKEHRPRUEFUNG,
        tracked_kind=KIND_WANDFUEHLER,
        source_measured_at=NOW,
        recovery_started_at=NOW,
        last_counted_measurement_at=NOW,
        recovery_sample_count=1,
        episode_open=True,
        handover_due_signalled=True,
    )
    assert out.events == _events(
        code=REASON_RUECKKEHR_STARTET, reason="Rückkehrprüfung gegen Wandfühler gestartet."
    )


def test_notbetrieb_starts_recovery_against_replacement() -> None:
    out = advance(_notbetrieb(), _inp(DOWN, TRV))
    assert out.state.tracked_kind == KIND_ERSATZQUELLE
    assert out.state.tracked_device_id == 7
    assert out.events.reason == "Rückkehrprüfung gegen TRV gestartet."
    assert out.events.pi_neutralized is True
    assert out.events.handover_due is False
    assert out.events.episode_started is False


def test_recovery_without_a_measurement_timestamp_counts_zero() -> None:
    out = advance(_notbetrieb(), _inp(_src(True, None), DOWN))
    assert out.state.recovery_sample_count == 0
    assert out.state.last_counted_measurement_at is None


def _checking(kind: str, device_id: int | None, **kw: object) -> ZoneEmergencyState:
    return ZoneEmergencyState(
        stage=STAGE_RUECKKEHRPRUEFUNG,
        tracked_kind=kind,
        tracked_device_id=device_id,
        episode_open=True,
        handover_due_signalled=True,
        **kw,  # type: ignore[arg-type]
    )


def test_recovery_falls_back_into_the_same_episode() -> None:
    out = advance(_checking(KIND_ERSATZQUELLE, 7), _inp(DOWN, DOWN))
    assert out.state == _notbetrieb()
    assert out.events == _events(
        code=REASON_RUECKFALL,
        reason=(
            "Rückkehrprüfung gegen Ersatzquelle: Quelle erneut ausgefallen, "
            "zurück in Notbetrieb (gleiche Störung)."
        ),
    )


def test_recovery_restarts_when_the_replacement_device_changes() -> None:
    other = _src(True, NOW, 8, "Bad-TRV")
    out = advance(_checking(KIND_ERSATZQUELLE, 7), _inp(DOWN, other))
    assert out.state == _checking(
        KIND_ERSATZQUELLE,
        8,
        source_measured_at=NOW,
        recovery_started_at=NOW,
        last_counted_measurement_at=NOW,
        recovery_sample_count=1,
    )
    assert out.events == _events(
        code=REASON_RUECKKEHR_NEUSTART,
        reason="Rückkehrprüfung neu gestartet: Ersatzquelle gewechselt zu Bad-TRV.",
    )


def test_recovery_against_replacement_completes_but_keeps_episode_open() -> None:
    cur = _checking(
        KIND_ERSATZQUELLE,
        7,
        recovery_started_at=NOW - timedelta(seconds=120),
        last_counted_measurement_at=NOW - timedelta(seconds=30),
        recovery_sample_count=2,
    )
    out = advance(cur, _inp(DOWN, TRV))
    assert out.state == ZoneEmergencyState(
        stage=STAGE_ERSATZQUELLE, episode_open=True, handover_due_signalled=True
    )
    assert out.events == _events(
        code=REASON_RUECKKEHR_ABGESCHLOSSEN,
        reason=(
            "Rückkehrprüfung gegen TRV abgeschlossen (3 Messwerte, 120 s) — "
            "Ersatzquelle TRV aktiv, PI weiter ausgesetzt."
        ),
    )


@pytest.mark.parametrize("was_open", [True, False])
def test_disabled_closes_an_open_episode_and_resets_everything(was_open: bool) -> None:
    cur = ZoneEmergencyState(
        stage=STAGE_NOTBETRIEB, episode_open=was_open, handover_due_signalled=True
    )
    out = advance(cur, _inp(DOWN, DOWN, enabled=False))
    assert out.state == ZoneEmergencyState()
    assert out.events == _events(
        code=REASON_DEAKTIVIERT,
        reason="Notbetrieb deaktiviert — normale Regelung angenommen.",
        ended=was_open,
        pi=False,
    )
