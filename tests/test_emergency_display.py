"""Pure-function tests for `domain/emergency_display.py` (Auftrag 8b).

No database, no session -- every input is a plain value, exactly as the
module itself requires.
"""

from decimal import Decimal

from thermoctl.domain import emergency_actuator_plan, emergency_display, emergency_operation


def test_normal_stage_has_no_banner() -> None:
    assert (
        emergency_display.zone_banner(
            stage=emergency_operation.STAGE_NORMAL,
            actuator_kind=None,
            source_device_name=None,
            emergency_setpoint_c=None,
            cycle_phase=None,
            on_seconds=None,
            off_seconds=None,
            cycle_source=None,
            outdoor_c=None,
            recovery_sample_count=0,
            recovery_samples=None,
        )
        is None
    )


def test_ersatzquelle_banner_names_the_device_without_jargon() -> None:
    banner = emergency_display.zone_banner(
        stage=emergency_operation.STAGE_ERSATZQUELLE,
        actuator_kind=None,
        source_device_name="Thermostat Flur",
        emergency_setpoint_c=None,
        cycle_phase=None,
        on_seconds=None,
        off_seconds=None,
        cycle_source=None,
        outdoor_c=None,
        recovery_sample_count=0,
        recovery_samples=None,
    )
    assert banner is not None
    assert "Thermostat Flur" in banner.headline
    assert "Wandfühler" not in banner.headline
    for forbidden in ("Sensor", "Fühler", "Störung", "Fehler", "!"):
        assert forbidden not in banner.detail


def test_ersatzquelle_banner_falls_back_without_a_device_name() -> None:
    banner = emergency_display.zone_banner(
        stage=emergency_operation.STAGE_ERSATZQUELLE,
        actuator_kind=None,
        source_device_name=None,
        emergency_setpoint_c=None,
        cycle_phase=None,
        on_seconds=None,
        off_seconds=None,
        cycle_source=None,
        outdoor_c=None,
        recovery_sample_count=0,
        recovery_samples=None,
    )
    assert banner is not None
    assert "ein anderes Thermostat" in banner.headline


def test_rueckkehrpruefung_banner_shows_progress() -> None:
    banner = emergency_display.zone_banner(
        stage=emergency_operation.STAGE_RUECKKEHRPRUEFUNG,
        actuator_kind=None,
        source_device_name="Wandfühler",
        emergency_setpoint_c=None,
        cycle_phase=None,
        on_seconds=None,
        off_seconds=None,
        cycle_source=None,
        outdoor_c=None,
        recovery_sample_count=1,
        recovery_samples=2,
    )
    assert banner is not None
    assert "1/2" in banner.headline


def test_rueckkehrpruefung_banner_default_sample_count_without_episode() -> None:
    banner = emergency_display.zone_banner(
        stage=emergency_operation.STAGE_RUECKKEHRPRUEFUNG,
        actuator_kind=None,
        source_device_name=None,
        emergency_setpoint_c=None,
        cycle_phase=None,
        on_seconds=None,
        off_seconds=None,
        cycle_source=None,
        outdoor_c=None,
        recovery_sample_count=0,
        recovery_samples=None,
    )
    assert banner is not None
    assert "0/2" in banner.headline


def test_notbetrieb_thermostat_banner_names_the_notsollwert() -> None:
    banner = emergency_display.zone_banner(
        stage=emergency_operation.STAGE_NOTBETRIEB,
        actuator_kind=emergency_actuator_plan.KIND_THERMOSTAT,
        source_device_name=None,
        emergency_setpoint_c=Decimal("18.5"),
        cycle_phase=None,
        on_seconds=None,
        off_seconds=None,
        cycle_source=None,
        outdoor_c=None,
        recovery_sample_count=0,
        recovery_samples=None,
    )
    assert banner is not None
    assert "18.5" in banner.headline
    assert "regelt selbst" in banner.headline


def test_notbetrieb_switch_banner_shows_cycle_minutes_and_outdoor() -> None:
    banner = emergency_display.zone_banner(
        stage=emergency_operation.STAGE_NOTBETRIEB,
        actuator_kind=emergency_actuator_plan.KIND_SWITCH,
        source_device_name=None,
        emergency_setpoint_c=None,
        cycle_phase=emergency_operation.STAGE_NOTBETRIEB,
        on_seconds=600,
        off_seconds=1200,
        cycle_source="kennlinie",
        outdoor_c=Decimal("-2.5"),
        recovery_sample_count=0,
        recovery_samples=None,
    )
    assert banner is not None
    assert "10/20 min" in banner.headline
    assert "Kennlinie" in banner.detail
    assert "-2.5" in banner.detail


def test_notbetrieb_switch_banner_names_festtakt() -> None:
    banner = emergency_display.zone_banner(
        stage=emergency_operation.STAGE_NOTBETRIEB,
        actuator_kind=emergency_actuator_plan.KIND_SWITCH,
        source_device_name=None,
        emergency_setpoint_c=None,
        cycle_phase="aus",
        on_seconds=0,
        off_seconds=1800,
        cycle_source="festtakt",
        outdoor_c=None,
        recovery_sample_count=0,
        recovery_samples=None,
    )
    assert banner is not None
    assert "Festtakt" in banner.detail
    assert "außen" not in banner.detail


def test_notbetrieb_without_actuator_falls_back_to_generic_text() -> None:
    banner = emergency_display.zone_banner(
        stage=emergency_operation.STAGE_NOTBETRIEB,
        actuator_kind=None,
        source_device_name=None,
        emergency_setpoint_c=None,
        cycle_phase=None,
        on_seconds=None,
        off_seconds=None,
        cycle_source=None,
        outdoor_c=None,
        recovery_sample_count=0,
        recovery_samples=None,
    )
    assert banner is not None
    assert banner.headline == "Notbetrieb aktiv"


def test_handover_status_text_distinguishes_three_states() -> None:
    assert emergency_display.handover_status_text(False, None) == "nicht versucht"
    assert (
        emergency_display.handover_status_text(True, None)
        == "versucht, Ergebnis unbekannt"
    )
    assert emergency_display.handover_status_text(True, "executed") == "erfolgreich"
    assert emergency_display.handover_status_text(True, "failed") == "fehlgeschlagen"


def test_suggested_offset_text_is_none_without_data() -> None:
    assert emergency_display.suggested_offset_text(None) is None


def test_suggested_offset_text_shows_sign_and_value() -> None:
    assert "vorgeschlagener Ausgleichswert" in emergency_display.suggested_offset_text(
        Decimal("0.8")
    )
    assert "+0.8" in emergency_display.suggested_offset_text(Decimal("0.8"))
    assert "-0.3" in emergency_display.suggested_offset_text(Decimal("-0.3"))


def test_handover_status_text_passes_through_an_unknown_result_code() -> None:
    """Forward-compatible: a future/unexpected result string is shown as-is
    rather than silently collapsing into one of the two known labels."""
    assert emergency_display.handover_status_text(True, "decided_no_command") == (
        "decided_no_command"
    )
