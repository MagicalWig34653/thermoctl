"""Verlauf, technische Fassung: Der Wert `scheduled_setpoint_c` ist kein "Zeitplan-Soll".

Die Spalte hält den Sollwert VOR der Sonnenabsenkung (`services/shadow_run.py`,
`resolved_setpoint`). Der folgt außer dem Zeitplan auch einer Übersteuerung, dem Urlaub, der
Abwesenheit und der Betriebsart Aus. Eine Beschriftung mit "Zeitplan" behauptet bei einem Boost
oder einer Abwesenheit etwas, das die Daten nicht belegen.
"""

import dataclasses
from datetime import datetime, timedelta

from thermoctl.domain.zone_history_chart import (
    TECHNICAL,
    Chart,
    Sample,
    build_chart,
)

BERLIN = "Europe/Berlin"
LABEL = "Sollwert vor Sonnenabsenkung"


def _chart(scheduled: float | None) -> Chart:
    start = datetime(2026, 10, 9, 0)
    samples = [
        Sample(start + timedelta(seconds=60 * i), 19.5, 18.5, scheduled, False, 2.0)
        for i in range(30)
    ]
    return build_chart(samples, start, start + timedelta(hours=24), BERLIN, TECHNICAL)


def test_no_technical_text_calls_the_pre_setback_setpoint_a_schedule_value() -> None:
    for field in dataclasses.fields(TECHNICAL):
        value = getattr(TECHNICAL, field.name)
        texts = [text for _, text in value] if isinstance(value, tuple) else [value]
        for text in texts:
            assert "zeitplan" not in text.lower(), f"{field.name}: {text!r}"


def test_the_technical_wording_names_the_value_by_what_it_is() -> None:
    assert TECHNICAL.scheduled_label == LABEL
    assert LABEL in TECHNICAL.last_both
    assert f"({LABEL} unbekannt)" in TECHNICAL.last_effective_only


def test_summary_text_carries_the_new_label_and_no_schedule_word() -> None:
    known = _chart(20.5)
    assert f"Zuletzt {LABEL} 20,5 °C, wirksamer Soll 18,5 °C." in known.summary
    assert "Zeitplan" not in known.summary
    unknown = _chart(None)
    assert f"({LABEL} unbekannt)" in unknown.summary
    assert "Zeitplan" not in unknown.summary


