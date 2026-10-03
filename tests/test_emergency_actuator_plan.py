"""`domain.emergency_actuator_plan` -- Unveränderlichkeit der Plan-Ergebnisse.

Die Verhaltenstests der Plan-Funktionen liegen in test_emergency_display.py,
test_shadow_run_sensor_failure.py und test_publishing_notbetrieb_versand.py. Hier
steht nur, was dort nicht auffällt: Die Entscheidungsobjekte sind eingefroren, damit
ein Aufrufer (Schattenlauf, Publisher) einen Plan nicht nachträglich verändern kann,
den ein anderer schon gelesen hat (Mutationslauf, Auftrag 10).
"""

import dataclasses
from datetime import datetime
from typing import Any

import pytest

from thermoctl.domain import emergency_actuator_plan as plan


def test_decisions_are_frozen() -> None:
    now = datetime(2026, 1, 1)
    thermostat = plan.ThermostatDecision(
        action=plan.ACTION_NO_WRITE, reason_code="x", reason="y", handover_attempted_at=now
    )
    restore = plan.RestoreDecision(
        action=plan.ACTION_NO_WRITE, reason_code="x", reason="y", restore_attempted_at=now
    )
    for obj in (thermostat, restore):
        target: Any = obj
        with pytest.raises(dataclasses.FrozenInstanceError):
            target.action = plan.ACTION_RESTORE


def test_switch_decision_is_frozen() -> None:
    target: Any = plan.SwitchDecision.__new__(plan.SwitchDecision)
    with pytest.raises(dataclasses.FrozenInstanceError):
        target.action = plan.ACTION_SWITCH_ON
