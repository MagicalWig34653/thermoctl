"""The operating pages: what they show and what they tolerate."""

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.helpers import create_settings


def test_an_unknown_period_falls_back_to_the_default(
    angemeldeter_client: TestClient, session: Session
) -> None:
    """The period comes out of the query string, so it can be anything.

    Falling back rather than erroring: a bookmark from an older version, or a
    hand-typed address, should show the statistics page and not a 400.
    """
    create_settings(session)
    session.flush()
    response = angemeldeter_client.get("/statistics?period=irgendwas")
    assert response.status_code == 200
    # The same page the default gives -- the buttons mark seven days as current.
    assert 'aria-current="page"' in response.text


def test_the_operating_page_shows_the_most_recent_decision_per_zone(
    angemeldeter_client: TestClient, session: Session
) -> None:
    """Only the newest one per zone, and only once.

    `/control` resolves this through `domain.zones.latest_decisions_by_zone` --
    the same shared, `LIMIT`-free-history-avoiding query the plant overview uses
    (`web/start_views.py::zone_status_context`); its tiebreak behaviour is proven
    once, in `test_daily_views.py::test_the_overview_shows_the_latest_of_several_decisions`.
    Without a decision in the database the query returns nothing, which is why no
    test had ever exercised this at all -- and the operating page is precisely
    where someone looks to find out what the plant last decided.
    """
    from tests.helpers import create_shadow_decision, create_zone

    zone = create_zone(session, "betriebszone")
    create_settings(session)
    create_shadow_decision(session, zone)
    session.flush()

    response = angemeldeter_client.get("/control")
    assert response.status_code == 200
    assert "betriebszone" in response.text.lower()


def test_the_operating_page_shows_pi_and_its_fallback_reason(
    angemeldeter_client: TestClient, session: Session
) -> None:
    """Specification section 6: whoever looks at a PI zone has to see the wirksamer
    Reglertyp, and if it fell back to hysteresis, why -- not just the ordinary
    hysteresis reason text, which never mentions PI at all."""
    from tests.helpers import create_shadow_decision, create_zone

    create_settings(session)
    running = create_zone(session, "pi-läuft")
    decision = create_shadow_decision(session, running)
    decision.requested_controller = "pi"
    decision.effective_controller = "pi"

    fallen_back = create_zone(session, "pi-zurückgefallen")
    fallback_decision = create_shadow_decision(session, fallen_back)
    fallback_decision.requested_controller = "pi"
    fallback_decision.effective_controller = "hysteresis"
    fallback_decision.controller_fallback_reason = "pi_ungeeignet"
    session.flush()

    response = angemeldeter_client.get("/control")

    assert response.status_code == 200
    assert "PI (Beta)" in response.text
    assert "PI-Rückfall" in response.text
    assert "Die Zone erfüllt die PI-Voraussetzungen nicht (mehr)." in response.text


def test_the_operating_page_shows_notbetrieb_detail_and_comparison(
    angemeldeter_client: TestClient, session: Session
) -> None:
    """Auftrag 8b item 2: Stufe, aktive Quelle, Taktphase, Übergabestatus und die
    Ersatzquelle<->Wandfühler-Auswertung müssen auf der Betriebsseite stehen --
    derselbe geteilte Lesevorgang wie REST/MCP."""
    from datetime import datetime, timedelta
    from decimal import Decimal

    from tests.helpers import capability, create_device, create_zone, role
    from thermoctl.db.models.device import DeviceCapabilityLink, ZoneDevice
    from thermoctl.db.models.sensor_failure import (
        ActuatorEmergencyState,
        SensorFailureEpisode,
        SensorFailureSourceComparison,
        ZoneSensorFailureState,
    )
    from thermoctl.domain import emergency_operation

    create_settings(session)
    zone = create_zone(session, "betriebsnotzone")
    device = create_device(session, "betriebsnotgeraet")
    session.add(
        DeviceCapabilityLink(device_id=device.id, capability_id=capability(session, "switch").id)
    )
    zone_device = ZoneDevice(
        zone_id=zone.id,
        device_id=device.id,
        device_role_id=role(session, "actuator").id,
        self_regulating=False,
    )
    session.add(zone_device)
    session.flush()
    now = datetime(2026, 10, 2, 12, 0, 0)
    episode = SensorFailureEpisode(
        zone_id=zone.id,
        zone_name=zone.display_name,
        started_at=now,
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
            phase="ein",
            phase_deadline_at=now + timedelta(minutes=10),
            on_seconds=600,
            off_seconds=1200,
            cycle_source="festtakt",
            warm_locked=False,
            simulated_phase="ein",
            simulated_phase_deadline_at=now + timedelta(minutes=10),
            simulated_on_seconds=600,
            simulated_off_seconds=1200,
            simulated_cycle_source="festtakt",
            simulated_warm_locked=False,
            profile_version=1,
        )
    )
    session.add(
        SensorFailureSourceComparison(
            zone_id=zone.id,
            zone_name=zone.display_name,
            device_id=device.id,
            device_name=device.display_name,
            measured_at=now,
            wall_probe_c=Decimal("20.0"),
            raw_c=Decimal("19.2"),
            corrected_c=Decimal("19.2"),
            echo=False,
            usable=True,
        )
    )
    session.flush()

    response = angemeldeter_client.get("/control")

    assert response.status_code == 200
    assert "Notbetrieb" in response.text
    assert device.display_name in response.text
    assert "nicht versucht" in response.text
    assert "vorgeschlagener Ausgleichswert" in response.text
    # Außenwertqualität als Klartext, nicht als Rohcode.
    assert "keine Außenquelle eingerichtet" in response.text
    assert "keine_quelle" not in response.text


def test_the_operating_page_shows_nothing_extra_for_a_normal_zone(
    angemeldeter_client: TestClient, session: Session
) -> None:
    from tests.helpers import create_zone

    create_settings(session)
    create_zone(session, "ruhige-zone")

    response = angemeldeter_client.get("/control")

    assert response.status_code == 200
    assert "Ersatzquelle ↔ Wandfühler" not in response.text


def test_the_arming_notes_name_both_command_paths_behind_the_second_latch(
    angemeldeter_client: TestClient, session: Session
) -> None:
    """Der zweite Riegel ist ein Wert für den ganzen Prozess, nicht ein Teil des MQTT-Clients.

    Der Hinweis stand einmal mit „sitzt im MQTT-Client“ da; Meross-Befehle gehen aber
    durch denselben beim Start eingefrorenen Wert (`app.py`: ein `sending_allowed` für
    beide Wege). Die Oberfläche soll das nicht auf einen Weg verengen.
    """
    create_settings(session)
    session.flush()

    response = angemeldeter_client.get("/control")

    assert response.status_code == 200
    assert "Jetzt scharf schalten" in response.text
    text = " ".join(response.text.split())
    assert "für alle Wege, auf denen Befehle hinausgehen (MQTT und Meross)" in text
    assert "sitzt im MQTT-Client" not in text
