"""Proves, at the actual publisher boundary, the constraint the Hauptsession
review of 88bc87a placed on Auftrag 7a of `lokal/plaene/0.11.0-notbetrieb.md`:
a zone in `notbetrieb`/`rueckkehrpruefung` sends **exactly** what the same
zone would send if none of the sensor-failure machinery existed -- because
`services/publishing.py::_send_actuator_switches` reads only
`ShadowDecision.would_heat`/`.reason` (`_latest_decision`), and
`services/shadow_run.py` never writes anything else there for such a zone
(see `tests/test_shadow_run_sensor_failure.py` for that half of the proof).

This test runs a **scharf geschaltete** installation (`setting.control_armed
= True`, no cluster claim so this process leads by default) against a fake
MQTT transport and counts write calls: a zone that reaches Notbetrieb (no
wall probe, no replacement candidate at all -- straight to `notbetrieb` on
its very first cycle) must send the identical single switch command an
otherwise plain zone with no sensor at all sends for the same reason
(`REASON_CODE_NO_SOURCE`, `would_heat=False`) -- not zero (Notbetrieb
silently suppressing the existing publisher would be its own, different
bug), not more than one (the actuator plan's own Takt/handover bookkeeping
leaking into the real switch path would be exactly the regression this test
guards against).
"""

from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from tests.helpers import (
    capability,
    create_all_command_outcomes,
    create_settings,
    create_zone,
    integration,
    role,
)
from thermoctl.config import get_settings
from thermoctl.db.models.device import Device, DeviceCapabilityLink, ZoneDevice
from thermoctl.db.models.sensor_failure import SensorFailureProfile, ZoneSensorFailureState
from thermoctl.services import publishing, shadow_run
from thermoctl.services.publishing import PublicationState

NOW = datetime(2026, 9, 30, 12, 0)


class _FakeClient:
    """A minimal `MqttPublisher` -- records every message, always succeeds.

    Same shape as `tests/test_publishing.py::Mitschrift`, kept local and
    minimal here so this file's one concern (call counting) is not entangled
    with that file's own (payload/registration behaviour).
    """

    def __init__(self) -> None:
        self.switch_messages: list[tuple[str, str]] = []

    async def publishing(
        self, topic: str, payload: str, *, switches: bool, retained: bool = False
    ) -> bool:
        if switches:
            self.switch_messages.append((topic, payload))
        return True


@pytest.mark.anyio
async def test_notbetrieb_zone_sends_exactly_what_an_ordinary_no_source_zone_sends(
    session: Session,
) -> None:
    settings = create_settings(session)
    settings.control_armed = True
    create_all_command_outcomes(session)

    profile = SensorFailureProfile(
        name="Testprofil",
        version=1,
        fixed_on_seconds=600,
        fixed_off_seconds=1200,
        recovery_seconds=60,
        recovery_samples=2,
        warm_restart_hysteresis_k=Decimal("1.00"),
    )
    session.add(profile)
    session.flush()

    notbetrieb_zone = create_zone(session, "notbetrieb-scharf")
    notbetrieb_zone.sensor_failure_enabled = True
    notbetrieb_zone.sensor_failure_profile_id = profile.id
    notbetrieb_relais = Device(
        integration_id=integration(session, "zigbee2mqtt").id,
        external_id="notbetrieb-relais",
        display_name="notbetrieb-relais",
    )
    session.add(notbetrieb_relais)
    session.flush()
    session.add(
        DeviceCapabilityLink(
            device_id=notbetrieb_relais.id, capability_id=capability(session, "switch").id
        )
    )
    session.add(
        ZoneDevice(
            zone_id=notbetrieb_zone.id,
            device_id=notbetrieb_relais.id,
            device_role_id=role(session, "actuator").id,
            self_regulating=False,
        )
    )

    ordinary_zone = create_zone(session, "ohne-sensor-scharf")
    ordinary_relais = Device(
        integration_id=integration(session, "zigbee2mqtt").id,
        external_id="ordinary-relais",
        display_name="ordinary-relais",
    )
    session.add(ordinary_relais)
    session.flush()
    session.add(
        DeviceCapabilityLink(
            device_id=ordinary_relais.id, capability_id=capability(session, "switch").id
        )
    )
    session.add(
        ZoneDevice(
            zone_id=ordinary_zone.id,
            device_id=ordinary_relais.id,
            device_role_id=role(session, "actuator").id,
            self_regulating=False,
        )
    )
    session.flush()

    # Neither zone has a `ZoneState` row at all -- both are, from `decide()`'s
    # own point of view, an ordinary "keine Quelle" zone. `notbetrieb_zone`
    # additionally has no wall probe and no thermostat candidate assigned, so
    # its sensor-failure state machine also lands on `notbetrieb` on this
    # very first cycle (both sources unusable from the start) -- the exact
    # scenario the Hauptsession review's constraint is about.
    rows = shadow_run.cycle(session, NOW)

    sf_state = session.get(ZoneSensorFailureState, notbetrieb_zone.id)
    assert sf_state is not None
    assert sf_state.stage == "notbetrieb"

    notbetrieb_row = next(r for r in rows if r.zone_id == notbetrieb_zone.id)
    ordinary_row = next(r for r in rows if r.zone_id == ordinary_zone.id)
    assert notbetrieb_row.would_heat == ordinary_row.would_heat is False
    assert notbetrieb_row.outcome_code == ordinary_row.outcome_code == "keine_quelle"
    assert notbetrieb_row.reason == ordinary_row.reason

    client = _FakeClient()
    sent = await publishing.cycle(session, client, PublicationState(), "thermoctl", NOW)
    assert sent > 0  # sanity: the fake client did receive messages this cycle

    base = get_settings().mqtt_base_topic
    notbetrieb_topic = f"{base}/{notbetrieb_relais.external_id}/set"
    ordinary_topic = f"{base}/{ordinary_relais.external_id}/set"
    notbetrieb_messages = [m for m in client.switch_messages if m[0] == notbetrieb_topic]
    ordinary_messages = [m for m in client.switch_messages if m[0] == ordinary_topic]

    assert len(notbetrieb_messages) == len(ordinary_messages) == 1
    assert notbetrieb_messages[0][1] == ordinary_messages[0][1] == '{"state": "OFF"}'
