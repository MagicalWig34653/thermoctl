"""`services/emergency_state.py` -- the one read REST/MCP/the control page share
(Auftrag 8b, Grundsatz 6)."""

from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from tests.helpers import capability, create_device, create_settings, create_zone, role
from thermoctl.db.models.device import DeviceCapabilityLink, ZoneDevice
from thermoctl.db.models.sensor_failure import (
    ActuatorEmergencyState,
    SensorFailureEpisode,
    SensorFailureSourceComparison,
    ZoneSensorFailureState,
)
from thermoctl.domain import emergency_operation
from thermoctl.services.emergency_state import entered_notice_text, zone_emergency_view

NOW = datetime(2026, 10, 2, 12, 0, 0)


def _switch_actuator(session: Session, zone_id: int) -> ZoneDevice:
    device = create_device(session, f"zone-{zone_id}-floor")
    session.add(
        DeviceCapabilityLink(device_id=device.id, capability_id=capability(session, "switch").id)
    )
    zone_device = ZoneDevice(
        zone_id=zone_id,
        device_id=device.id,
        device_role_id=role(session, "actuator").id,
        self_regulating=False,
    )
    session.add(zone_device)
    session.flush()
    return zone_device


def test_a_zone_never_touched_by_the_state_machine_reads_as_normal(session: Session) -> None:
    settings = create_settings(session)
    zone = create_zone(session, "Flur")

    view = zone_emergency_view(session, zone, NOW, settings)

    assert view.stage == emergency_operation.STAGE_NORMAL
    assert view.episode_id is None
    assert view.banner is None
    assert view.actuators == ()
    assert view.comparisons == ()


def test_notbetrieb_view_includes_the_switch_actuators_cycle_state(session: Session) -> None:
    settings = create_settings(session)
    zone = create_zone(session, "Flur")
    zone_device = _switch_actuator(session, zone.id)
    episode = SensorFailureEpisode(
        zone_id=zone.id,
        zone_name=zone.display_name,
        started_at=NOW,
        trigger_kind=emergency_operation.TRIGGER_ALLE_QUELLEN,
        profile_version=1,
        fixed_on_seconds=600,
        fixed_off_seconds=1200,
        recovery_seconds=60,
        recovery_samples=2,
        warm_restart_hysteresis_k=Decimal("1"),
        emergency_setpoint_c=Decimal("20"),
        sensor_timeout_seconds=1800,
    )
    session.add(episode)
    session.flush()
    session.add(
        ZoneSensorFailureState(
            zone_id=zone.id,
            episode_id=episode.id,
            stage=emergency_operation.STAGE_NOTBETRIEB,
            recovery_sample_count=0,
        )
    )
    session.add(
        ActuatorEmergencyState(
            zone_device_id=zone_device.id,
            episode_id=episode.id,
            armed_episode_id=episode.id,
            phase="ein",
            phase_deadline_at=NOW + timedelta(minutes=10),
            on_seconds=600,
            off_seconds=1200,
            cycle_source="festtakt",
            warm_locked=False,
            simulated_phase="ein",
            simulated_phase_deadline_at=NOW + timedelta(minutes=10),
            simulated_on_seconds=600,
            simulated_off_seconds=1200,
            simulated_cycle_source="festtakt",
            simulated_warm_locked=False,
            profile_version=1,
        )
    )
    session.flush()

    view = zone_emergency_view(session, zone, NOW, settings)

    assert view.stage == emergency_operation.STAGE_NOTBETRIEB
    assert len(view.actuators) == 1
    actuator = view.actuators[0]
    assert actuator.kind == "switch"
    assert actuator.on_seconds == 600
    assert actuator.off_seconds == 1200
    assert actuator.handover_attempted is False
    assert actuator.handover_status_text == "nicht versucht"
    assert view.banner is not None
    assert "10/20 min" in view.banner.headline

    text = entered_notice_text(view)
    assert "10/20 min" in text


def test_handover_attempted_but_unresolved_is_visibly_distinct(session: Session) -> None:
    settings = create_settings(session)
    zone = create_zone(session, "Flur")
    device = create_device(session, "thermostat-1")
    session.add(
        DeviceCapabilityLink(
            device_id=device.id, capability_id=capability(session, "thermostat").id
        )
    )
    zone_device = ZoneDevice(
        zone_id=zone.id,
        device_id=device.id,
        device_role_id=role(session, "actuator").id,
        self_regulating=True,
    )
    session.add(zone_device)
    session.flush()
    episode = SensorFailureEpisode(
        zone_id=zone.id,
        zone_name=zone.display_name,
        started_at=NOW,
        trigger_kind=emergency_operation.TRIGGER_ALLE_QUELLEN,
        profile_version=1,
        fixed_on_seconds=600,
        fixed_off_seconds=1200,
        recovery_seconds=60,
        recovery_samples=2,
        warm_restart_hysteresis_k=Decimal("1"),
        emergency_setpoint_c=Decimal("20"),
        sensor_timeout_seconds=1800,
    )
    session.add(episode)
    session.flush()
    session.add(
        ZoneSensorFailureState(
            zone_id=zone.id, episode_id=episode.id, stage=emergency_operation.STAGE_NOTBETRIEB
        )
    )
    session.add(
        ActuatorEmergencyState(
            zone_device_id=zone_device.id,
            episode_id=episode.id,
            armed_episode_id=episode.id,
            handover_attempted_at=NOW,
            handover_result=None,
            profile_version=1,
        )
    )
    session.flush()

    view = zone_emergency_view(session, zone, NOW, settings)

    actuator = view.actuators[0]
    assert actuator.handover_attempted is True
    assert actuator.handover_result is None
    assert actuator.handover_status_text == "versucht, Ergebnis unbekannt"

    text = entered_notice_text(view)
    assert "regelt selbst auf einen Notsollwert von 20 °C." in text


def test_source_comparison_ignores_a_row_with_no_device(session: Session) -> None:
    """A comparison row can survive its device's deletion (SET NULL, like
    every other history table in this module) -- it has nothing to average
    into a per-device suggestion any more, but must not crash the read."""
    settings = create_settings(session)
    zone = create_zone(session, "Flur")
    session.add(
        SensorFailureSourceComparison(
            zone_id=zone.id,
            zone_name=zone.display_name,
            device_id=None,
            device_name="gelöschtes-gerät",
            measured_at=NOW,
            wall_probe_c=Decimal("20.0"),
            raw_c=Decimal("19.0"),
            corrected_c=Decimal("19.0"),
            echo=False,
            usable=True,
        )
    )
    session.flush()

    view = zone_emergency_view(session, zone, NOW, settings)

    assert view.comparisons == ()


def test_source_comparison_average_excludes_echo_and_unusable_rows(session: Session) -> None:
    settings = create_settings(session)
    zone = create_zone(session, "Flur")
    device = create_device(session, "thermostat-2")

    def _row(days_ago: int, wall: Decimal, raw: Decimal, usable: bool, echo: bool) -> None:
        session.add(
            SensorFailureSourceComparison(
                zone_id=zone.id,
                zone_name=zone.display_name,
                device_id=device.id,
                device_name=device.display_name,
                measured_at=NOW - timedelta(days=days_ago),
                wall_probe_c=wall,
                raw_c=raw,
                corrected_c=raw,
                echo=echo,
                usable=usable,
            )
        )

    # Thermostat misst zu warm (raw > wall_probe) -- die Konvention aus
    # `domain/temperature_source_health.py::_corrected` ist `corrected = raw -
    # offset_k`, also muss der Vorschlag hier positiv sein: `offset_k = raw -
    # wall_probe`, damit `raw - offset_k` wieder auf den Wandfühler fällt.
    _row(1, Decimal("20.0"), Decimal("21.0"), True, False)  # raw 1.0 K zu warm
    _row(2, Decimal("20.0"), Decimal("20.5"), True, False)  # raw 0.5 K zu warm
    _row(3, Decimal("30.0"), Decimal("10.0"), True, True)  # echo, excluded
    _row(4, Decimal("30.0"), Decimal("10.0"), False, False)  # unusable, excluded
    session.flush()

    view = zone_emergency_view(session, zone, NOW, settings)

    assert len(view.comparisons) == 1
    comparison = view.comparisons[0]
    assert comparison.sample_count == 2
    assert comparison.mean_deviation_k == Decimal("0.75")
    assert comparison.suggested_offset_text is not None
    assert "+0.75" in comparison.suggested_offset_text


def test_applying_the_suggested_offset_corrects_back_to_the_wall_probe(
    session: Session,
) -> None:
    """Proves the sign is the right way round, not just that some number comes
    out: taking the suggested offset and actually applying it the way
    `domain/temperature_source_health.py::_corrected` does
    (`corrected = raw - offset_k`) must land back on the wall probe's value --
    the whole point of the calibration hint (Entscheidung R2)."""
    settings = create_settings(session)
    zone = create_zone(session, "Flur")
    device = create_device(session, "thermostat-warm")
    wall = Decimal("20.0")
    raw = Decimal("21.0")  # thermostat reads 1 K too warm
    session.add(
        SensorFailureSourceComparison(
            zone_id=zone.id,
            zone_name=zone.display_name,
            device_id=device.id,
            device_name=device.display_name,
            measured_at=NOW,
            wall_probe_c=wall,
            raw_c=raw,
            corrected_c=raw,
            echo=False,
            usable=True,
        )
    )
    session.flush()

    view = zone_emergency_view(session, zone, NOW, settings)

    comparison = view.comparisons[0]
    assert comparison.mean_deviation_k == Decimal("1.0")
    corrected = raw - comparison.mean_deviation_k
    assert corrected == wall


def test_source_comparison_is_empty_without_any_history(session: Session) -> None:
    settings = create_settings(session)
    zone = create_zone(session, "Flur")

    view = zone_emergency_view(session, zone, NOW, settings)

    assert view.comparisons == ()


def test_ersatzquelle_notice_text_names_the_device(session: Session) -> None:
    settings = create_settings(session)
    zone = create_zone(session, "Flur")
    device = create_device(session, "thermostat-3")
    session.add(
        ZoneSensorFailureState(
            zone_id=zone.id,
            stage=emergency_operation.STAGE_ERSATZQUELLE,
            active_source_device_id=device.id,
        )
    )
    session.flush()

    view = zone_emergency_view(session, zone, NOW, settings)

    assert view.stage == emergency_operation.STAGE_ERSATZQUELLE
    text = entered_notice_text(view)
    assert device.display_name in text


def test_entered_notice_text_notes_a_missing_actuator(session: Session) -> None:
    """Notbetrieb can be reached before any actuator assignment exists yet
    (right after activation, before the first cycle runs) -- the notice text
    must say so plainly instead of silently listing nothing."""
    settings = create_settings(session)
    zone = create_zone(session, "Flur")
    session.add(
        ZoneSensorFailureState(zone_id=zone.id, stage=emergency_operation.STAGE_NOTBETRIEB)
    )
    session.flush()

    view = zone_emergency_view(session, zone, NOW, settings)

    assert view.actuators == ()
    assert "noch kein Aktor zugeordnet" in entered_notice_text(view)
