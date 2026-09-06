import json
import logging
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.helpers import (
    create_device,
    create_settings,
    create_zone,
    integration,
    role,
    sensor_status_of,
)
from thermoctl.db.models.device import (
    Device,
    DeviceCapabilityLink,
    DeviceProperty,
    ZoneDevice,
)
from thermoctl.db.models.lookup import DeviceCapability, SensorStatus
from thermoctl.db.models.measurement import DeviceHealth, Measurement
from thermoctl.db.models.operations import Setting
from thermoctl.db.models.state import ZoneState
from thermoctl.db.models.zone import Zone
from thermoctl.services.ingest import advance_zone_state, process_message

DATENPFAD = Path(__file__).parent / "daten" / "anlage-beispiele.json"
BASIS = "test-basis"
EMPFANGEN_AM = datetime(2026, 8, 29, 7, 0)


def _capability(session: Session, code: str) -> DeviceCapability:
    capability = DeviceCapability(code=code, label=code)
    session.add(capability)
    session.flush()
    return capability


def _example_state() -> tuple[str, bytes]:
    data = json.loads(DATENPFAD.read_text(encoding="utf-8"))
    name = next(name for name, state in data["zustaende"].items() if "humidity" in state)
    return name, json.dumps(data["zustaende"][name]).encode()


def _device_names() -> list[str]:
    return json.loads(DATENPFAD.read_text(encoding="utf-8"))["geraete"]


def test_a_real_message_writes_history_and_a_sign_of_life(session: Session) -> None:
    integration(session)
    for code in ("battery", "humidity", "link_quality", "temperature"):
        _capability(session, code)
    name, payload = _example_state()

    process_message(
        session, f"{BASIS}/{name}", payload, base=BASIS, received_at=EMPFANGEN_AM
    )
    process_message(
        session, f"{BASIS}/{name}", payload, base=BASIS, received_at=EMPFANGEN_AM
    )
    session.flush()

    assert session.query(Measurement).count() == 8
    device = session.scalar(select(Device).where(Device.external_id == name))
    assert device is not None
    assert device.last_seen_at == EMPFANGEN_AM
    healthy = session.get(DeviceHealth, device.id)
    assert healthy is not None
    assert healthy.payload_count == 2


def test_an_unknown_device_is_created_without_a_zone(session: Session) -> None:
    integration(session)
    _capability(session, "temperature")
    name, _payload = _example_state()

    process_message(
        session,
        f"{BASIS}/{name}",
        b'{"temperature": 21}',
        base=BASIS,
        received_at=EMPFANGEN_AM,
    )
    session.flush()

    device = session.scalar(select(Device).where(Device.external_id == name))
    assert device is not None
    assert device.is_enabled is True
    assert not any(zone.temperature_source_device_id == device.id for zone in session.query(Zone))


def test_a_missing_capability_does_not_discard_the_other_values(
    session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    integration(session)
    _capability(session, "temperature")
    name, _payload = _example_state()
    with caplog.at_level(logging.WARNING, logger="thermoctl.services.ingest"):
        process_message(
            session,
            f"{BASIS}/{name}",
            b'{"temperature": 20.5, "battery": 75}',
            base=BASIS,
            received_at=EMPFANGEN_AM,
        )
    session.flush()

    assert [m.value_numeric for m in session.query(Measurement)] == [Decimal("20.500")]
    assert "Messwertfähigkeit fehlt" in caplog.text


def test_the_device_list_updates_the_device_and_sets_known_capabilities(
    session: Session,
) -> None:
    integration(session)
    temperature = _capability(session, "temperature")
    name, _payload = _example_state()
    items = [
        {
            "friendly_name": name,
            "definition": {
                "model": "testmodell",
                "exposes": [
                    {"type": "numeric", "property": "temperature"},
                    {"type": "numeric", "property": "humidity"},
                ],
            },
        }
    ]

    process_message(
        session,
        f"{BASIS}/bridge/devices",
        json.dumps(items).encode(),
        base=BASIS,
        received_at=EMPFANGEN_AM,
    )
    session.flush()

    device = session.scalar(select(Device).where(Device.external_id == name))
    assert device is not None
    assert device.model == "testmodell"
    assert session.scalars(
        select(DeviceCapabilityLink.capability_id).where(
            DeviceCapabilityLink.device_id == device.id
        )
    ).all() == [temperature.id]


def test_a_broken_payload_leaves_no_database_row(session: Session) -> None:
    integration(session)
    process_message(
        session,
        f"{BASIS}/bridge/devices",
        b"{kaputt",
        base=BASIS,
        received_at=EMPFANGEN_AM,
    )
    process_message(
        session, f"{BASIS}/geraet", b"{kaputt", base=BASIS, received_at=EMPFANGEN_AM
    )
    assert session.query(Device).count() == 0


def test_availability_is_carried_forward_on_the_one_device_state(
    session: Session,
) -> None:
    integration(session)
    name = _device_names()[0]
    process_message(
        session,
        f"{BASIS}/{name}/availability",
        b'{"state": "online"}',
        base=BASIS,
        received_at=EMPFANGEN_AM,
    )
    session.flush()

    device = session.scalar(select(Device).where(Device.external_id == name))
    assert device is not None
    healthy = session.get(DeviceHealth, device.id)
    assert healthy is not None
    assert healthy.availability == "online"


def test_zone_state_accounts_for_source_age_and_the_zone_timeout(
    session: Session,
) -> None:
    create_settings(session)
    for code in ("ok", "veraltet", "keine_quelle"):
        sensor_status_of(session, code)
    temperature = _capability(session, "temperature")
    device_names = _device_names()
    fresh_device = create_device(session, device_names[0])
    old_device = create_device(session, device_names[1])
    frisch = create_zone(session, "frisch-zone")
    alt = create_zone(session, "alt-zone")
    ohne = create_zone(session, "ohne-zone")
    frisch.temperature_source_device_id = fresh_device.id
    alt.temperature_source_device_id = old_device.id
    alt.sensor_timeout_seconds = 30
    for device, age in ((fresh_device, 60), (old_device, 31)):
        session.add(
            Measurement(
                device_id=device.id,
                capability_id=temperature.id,
                value_numeric=Decimal("20.5"),
                measured_at=EMPFANGEN_AM - timedelta(seconds=age),
                received_at=EMPFANGEN_AM,
            )
        )

    advance_zone_state(session, EMPFANGEN_AM)
    session.flush()
    codes = {status.id: status.code for status in session.query(SensorStatus)}
    states = {z.zone_id: codes[z.sensor_status_id] for z in session.query(ZoneState)}
    assert states == {frisch.id: "ok", alt.id: "veraltet", ohne.id: "keine_quelle"}


# --- sensor_stuck --------------------------------------------------------------


def _identical_history(
    session: Session, temperature: DeviceCapability, device: Device, *, value: Decimal
) -> None:
    """Ten readings, six minutes apart, spanning three hours -- the same cadence
    the real occurrence that prompted this feature reported at."""
    for step in range(31):
        session.add(
            Measurement(
                device_id=device.id,
                capability_id=temperature.id,
                value_numeric=value,
                measured_at=EMPFANGEN_AM - timedelta(hours=3) + timedelta(minutes=6 * step),
                received_at=EMPFANGEN_AM,
            )
        )


def test_a_reading_unchanged_for_longer_than_the_configured_duration_is_stuck(
    session: Session,
) -> None:
    settings = create_settings(session)
    settings.stuck_reading_hours = 2
    for code in ("ok", "veraltet", "keine_quelle"):
        sensor_status_of(session, code)
    temperature = _capability(session, "temperature")
    device = create_device(session, _device_names()[0])
    zone = create_zone(session, "starre-zone")
    zone.temperature_source_device_id = device.id
    _identical_history(session, temperature, device, value=Decimal("22.70"))

    advance_zone_state(session, EMPFANGEN_AM)

    state = session.get(ZoneState, zone.id)
    assert state is not None and state.sensor_stuck is True


def test_a_sensor_oscillating_between_two_resolution_steps_is_not_stuck(
    session: Session,
) -> None:
    """The exact motivating counter-example: a sensor with 0.1 K resolution that
    keeps toggling between two adjacent values must not be flagged, even though it
    never leaves that pair for hours."""
    settings = create_settings(session)
    settings.stuck_reading_hours = 2
    for code in ("ok", "veraltet", "keine_quelle"):
        sensor_status_of(session, code)
    temperature = _capability(session, "temperature")
    device = create_device(session, _device_names()[0])
    zone = create_zone(session, "pendel-zone")
    zone.temperature_source_device_id = device.id
    for step in range(31):
        value = Decimal("22.70") if step % 2 == 0 else Decimal("22.80")
        session.add(
            Measurement(
                device_id=device.id,
                capability_id=temperature.id,
                value_numeric=value,
                measured_at=EMPFANGEN_AM - timedelta(hours=3) + timedelta(minutes=6 * step),
                received_at=EMPFANGEN_AM,
            )
        )

    advance_zone_state(session, EMPFANGEN_AM)

    state = session.get(ZoneState, zone.id)
    assert state is not None and state.sensor_stuck is False


def test_history_shorter_than_the_configured_duration_is_not_yet_stuck(
    session: Session,
) -> None:
    """A zone whose source was only just assigned has not 'held still for the
    configured duration' -- it has simply never been asked that question yet."""
    settings = create_settings(session)
    settings.stuck_reading_hours = 12
    for code in ("ok", "veraltet", "keine_quelle"):
        sensor_status_of(session, code)
    temperature = _capability(session, "temperature")
    device = create_device(session, _device_names()[0])
    zone = create_zone(session, "junge-zone")
    zone.temperature_source_device_id = device.id
    # Only reaches back one hour, far short of the configured twelve.
    _identical_history(session, temperature, device, value=Decimal("22.70"))

    advance_zone_state(session, EMPFANGEN_AM)

    state = session.get(ZoneState, zone.id)
    assert state is not None and state.sensor_stuck is False


def test_a_stale_source_is_never_checked_for_being_stuck(session: Session) -> None:
    """Task instructions, section 4: a zone with an already-failed sensor is not a
    case for this check at all -- the existing staleness indicator already covers
    it, and the two must not be conflated."""
    settings = create_settings(session)
    settings.stuck_reading_hours = 1
    for code in ("ok", "veraltet", "keine_quelle"):
        sensor_status_of(session, code)
    temperature = _capability(session, "temperature")
    device = create_device(session, _device_names()[0])
    zone = create_zone(session, "abgelaufene-zone")
    zone.temperature_source_device_id = device.id
    zone.sensor_timeout_seconds = 30
    # Identical readings, but the most recent one is already older than the timeout
    # -- `sensor_state()` reads `veraltet`, not `ok`.
    for step in range(20):
        session.add(
            Measurement(
                device_id=device.id,
                capability_id=temperature.id,
                value_numeric=Decimal("22.70"),
                measured_at=EMPFANGEN_AM - timedelta(hours=2) + timedelta(minutes=5 * step),
                received_at=EMPFANGEN_AM,
            )
        )

    advance_zone_state(session, EMPFANGEN_AM)

    codes = {status.id: status.code for status in session.query(SensorStatus)}
    state = session.get(ZoneState, zone.id)
    assert state is not None
    assert codes[state.sensor_status_id] == "veraltet"
    assert state.sensor_stuck is False


def test_a_zone_without_a_temperature_source_is_never_checked_for_being_stuck(
    session: Session,
) -> None:
    create_settings(session)
    for code in ("ok", "veraltet", "keine_quelle"):
        sensor_status_of(session, code)
    zone = create_zone(session, "quellenlose-zone")

    advance_zone_state(session, EMPFANGEN_AM)

    state = session.get(ZoneState, zone.id)
    assert state is not None and state.sensor_stuck is False


@pytest.mark.parametrize(("contact_value", "expected"), [("true", False), ("false", True)])
def test_zone_state_inverts_the_zigbee_contact_value_exactly_once(
    session: Session, contact_value: str, expected: bool
) -> None:
    create_settings(session)
    sensor_status_of(session, "keine_quelle")
    contact = _capability(session, "contact")
    zone = create_zone(session, "kontakt-zone")
    device = create_device(session, _device_names()[0])
    session.add(
        ZoneDevice(
            zone_id=zone.id,
            device_id=device.id,
            device_role_id=role(session, "window_contact").id,
        )
    )
    session.add(
        Measurement(
            device_id=device.id,
            capability_id=contact.id,
            value_text=contact_value,
            measured_at=EMPFANGEN_AM,
            received_at=EMPFANGEN_AM,
        )
    )

    advance_zone_state(session, EMPFANGEN_AM)

    state = session.get(ZoneState, zone.id)
    assert state is not None and state.window_open is expected


def test_the_zone_counts_as_open_as_soon_as_one_of_two_contacts_is_open(
    session: Session,
) -> None:
    create_settings(session)
    sensor_status_of(session, "keine_quelle")
    contact = _capability(session, "contact")
    zone = create_zone(session, "zwei-kontakte-zone")
    window_role = role(session, "window_contact")
    for name, value in zip(_device_names()[:2], ("true", "false"), strict=True):
        device = create_device(session, name)
        session.add(
            ZoneDevice(
                zone_id=zone.id,
                device_id=device.id,
                device_role_id=window_role.id,
            )
        )
        session.add(
            Measurement(
                device_id=device.id,
                capability_id=contact.id,
                value_text=value,
                measured_at=EMPFANGEN_AM,
                received_at=EMPFANGEN_AM,
            )
        )

    advance_zone_state(session, EMPFANGEN_AM)

    state = session.get(ZoneState, zone.id)
    assert state is not None and state.window_open is True


def test_a_missing_or_stale_window_contact_stays_unknown(
    session: Session,
) -> None:
    create_settings(session)
    sensor_status_of(session, "keine_quelle")
    contact = _capability(session, "contact")
    ohne = create_zone(session, "ohne-kontakt-zone")
    alt = create_zone(session, "alter-kontakt-zone")
    alt.sensor_timeout_seconds = 30
    device = create_device(session, _device_names()[0])
    session.add(
        ZoneDevice(
            zone_id=alt.id,
            device_id=device.id,
            device_role_id=role(session, "window_contact").id,
        )
    )
    session.add(
        Measurement(
            device_id=device.id,
            capability_id=contact.id,
            value_text="false",
            measured_at=EMPFANGEN_AM - timedelta(seconds=31),
            received_at=EMPFANGEN_AM,
        )
    )

    advance_zone_state(session, EMPFANGEN_AM)

    without_state = session.get(ZoneState, ohne.id)
    old_state = session.get(ZoneState, alt.id)
    assert without_state is not None and without_state.window_open is None
    assert old_state is not None and old_state.window_open is None


def _open_window_zone(session: Session, name: str) -> tuple[Zone, Device]:
    _capability(session, "contact")
    zone = create_zone(session, name)
    device = create_device(session, f"{name}-kontakt")
    session.add(
        ZoneDevice(
            zone_id=zone.id,
            device_id=device.id,
            device_role_id=role(session, "window_contact").id,
        )
    )
    return zone, device


def _set_contact(
    session: Session, device: Device, value: str, at: datetime
) -> None:
    contact = session.query(DeviceCapability).filter_by(code="contact").one()
    session.add(
        Measurement(
            device_id=device.id,
            capability_id=contact.id,
            value_text=value,
            measured_at=at,
            received_at=at,
        )
    )


def test_window_open_since_starts_when_the_window_first_opens(session: Session) -> None:
    setting_row = create_settings(session)
    # Long enough that the artificial time jumps below do not make the contact
    # reading itself count as stale (`sensor_state`) -- this test is about
    # `window_open_since`, not about the sensor-timeout interaction.
    setting_row.default_sensor_timeout_seconds = 86400
    sensor_status_of(session, "keine_quelle")
    zone, device = _open_window_zone(session, "fenster-seit-zone")
    _set_contact(session, device, "true", EMPFANGEN_AM)

    advance_zone_state(session, EMPFANGEN_AM)
    state = session.get(ZoneState, zone.id)
    assert state is not None and state.window_open is False
    assert state.window_open_since is None

    opened_at = EMPFANGEN_AM + timedelta(minutes=1)
    _set_contact(session, device, "false", opened_at)
    advance_zone_state(session, opened_at)
    state = session.get(ZoneState, zone.id)
    assert state is not None and state.window_open is True
    assert state.window_open_since == opened_at

    # A later cycle where the window is still open must not reset the clock --
    # otherwise the window alarm's "open for longer than X minutes" could never
    # trigger, since every cycle would restart the count from zero.
    later = opened_at + timedelta(minutes=40)
    advance_zone_state(session, later)
    state = session.get(ZoneState, zone.id)
    assert state is not None and state.window_open_since == opened_at


def test_window_open_since_clears_when_the_window_closes(session: Session) -> None:
    create_settings(session)
    sensor_status_of(session, "keine_quelle")
    zone, device = _open_window_zone(session, "fenster-schliesst-zone")
    opened_at = EMPFANGEN_AM
    _set_contact(session, device, "false", opened_at)
    advance_zone_state(session, opened_at)
    assert session.get(ZoneState, zone.id).window_open_since == opened_at  # type: ignore[union-attr]

    closed_at = opened_at + timedelta(minutes=10)
    _set_contact(session, device, "true", closed_at)
    advance_zone_state(session, closed_at)
    state = session.get(ZoneState, zone.id)
    assert state is not None and state.window_open is False
    assert state.window_open_since is None


# --- Fenster-Erkennung aus einem Temperatursturz -------------------------------

# Small, easy-to-reason-about values instead of the production defaults -- the
# defaults themselves are covered by `tests/test_window_temperature_drop.py` and
# `tests/test_control.py`; these tests are about the wiring in `services/ingest.py`.
_DROP_WINDOW_MINUTES = 10
_DROP_THRESHOLD_K = Decimal("1.0")
_DROP_HOLD_MINUTES = 20


def _temp_drop_zone(session: Session, name: str) -> tuple[Zone, Setting, DeviceCapability]:
    settings = create_settings(session)
    settings.default_sensor_timeout_seconds = 86400
    settings.window_temp_drop_window_minutes = _DROP_WINDOW_MINUTES
    settings.window_temp_drop_threshold_k = _DROP_THRESHOLD_K
    settings.window_temp_drop_hold_minutes = _DROP_HOLD_MINUTES
    for code in ("ok", "veraltet", "keine_quelle"):
        sensor_status_of(session, code)
    temperature = _capability(session, "temperature")
    device = create_device(session, _device_names()[0])
    zone = create_zone(session, name)
    zone.temperature_source_device_id = device.id
    zone.window_temp_drop_detection_enabled = True
    return zone, settings, temperature


def _add_reading(
    session: Session, device_id: int, capability_id: int, value: Decimal, at: datetime
) -> None:
    session.add(
        Measurement(
            device_id=device_id,
            capability_id=capability_id,
            value_numeric=value,
            measured_at=at,
            received_at=at,
        )
    )


def test_a_drop_at_exactly_the_threshold_opens_the_window(session: Session) -> None:
    zone, _settings, temperature = _temp_drop_zone(session, "sturz-an-schwelle-zone")
    device_id = zone.temperature_source_device_id
    assert device_id is not None
    start = EMPFANGEN_AM - timedelta(minutes=_DROP_WINDOW_MINUTES)
    _add_reading(session, device_id, temperature.id, Decimal("22.0"), start)
    _add_reading(
        session, device_id, temperature.id, Decimal("22.0") - _DROP_THRESHOLD_K, EMPFANGEN_AM
    )

    advance_zone_state(session, EMPFANGEN_AM)

    state = session.get(ZoneState, zone.id)
    assert state is not None
    assert state.window_open is True
    assert state.window_open_by_temperature is True
    assert state.window_open_since == EMPFANGEN_AM


def test_a_drop_just_under_the_threshold_does_not_open_the_window(session: Session) -> None:
    zone, _settings, temperature = _temp_drop_zone(session, "sturz-knapp-darunter-zone")
    device_id = zone.temperature_source_device_id
    assert device_id is not None
    start = EMPFANGEN_AM - timedelta(minutes=_DROP_WINDOW_MINUTES)
    _add_reading(session, device_id, temperature.id, Decimal("22.0"), start)
    _add_reading(
        session,
        device_id,
        temperature.id,
        Decimal("22.0") - _DROP_THRESHOLD_K + Decimal("0.01"),
        EMPFANGEN_AM,
    )

    advance_zone_state(session, EMPFANGEN_AM)

    state = session.get(ZoneState, zone.id)
    assert state is not None
    assert state.window_open is False
    assert state.window_open_by_temperature is False
    assert state.window_open_since is None


def test_a_slow_cooldown_does_not_open_the_window(session: Session) -> None:
    """The room drifting down a few tenths of a Kelvin once heating stops must
    never be mistaken for an opened window."""
    zone, _settings, temperature = _temp_drop_zone(session, "langsames-auskuehlen-zone")
    device_id = zone.temperature_source_device_id
    assert device_id is not None
    start = EMPFANGEN_AM - timedelta(minutes=_DROP_WINDOW_MINUTES)
    for step, value in enumerate(("21.00", "20.95", "20.90", "20.85")):
        _add_reading(
            session,
            device_id,
            temperature.id,
            Decimal(value),
            start + timedelta(minutes=step * (_DROP_WINDOW_MINUTES // 3)),
        )

    advance_zone_state(session, EMPFANGEN_AM)

    state = session.get(ZoneState, zone.id)
    assert state is not None and state.window_open is False


def test_the_end_of_a_heating_phase_does_not_open_the_window(session: Session) -> None:
    """Temperature climbing towards the setpoint while heating was on, then
    levelling off as the heater switches off -- the room never actually falls,
    so this must not read as a dropped, opened-window temperature."""
    zone, _settings, temperature = _temp_drop_zone(session, "heizphasenende-zone")
    device_id = zone.temperature_source_device_id
    assert device_id is not None
    start = EMPFANGEN_AM - timedelta(minutes=_DROP_WINDOW_MINUTES)
    for step, value in enumerate(("20.50", "20.90", "21.00", "21.00")):
        _add_reading(
            session,
            device_id,
            temperature.id,
            Decimal(value),
            start + timedelta(minutes=step * (_DROP_WINDOW_MINUTES // 3)),
        )

    advance_zone_state(session, EMPFANGEN_AM)

    state = session.get(ZoneState, zone.id)
    assert state is not None and state.window_open is False


def test_too_few_readings_in_the_history_does_not_open_the_window(session: Session) -> None:
    zone, _settings, temperature = _temp_drop_zone(session, "zu-wenig-messwerte-zone")
    device_id = zone.temperature_source_device_id
    assert device_id is not None
    # Only reaches back a couple of minutes, far short of the configured window.
    _add_reading(
        session,
        device_id,
        temperature.id,
        Decimal("22.0"),
        EMPFANGEN_AM - timedelta(minutes=2),
    )
    _add_reading(
        session, device_id, temperature.id, Decimal("22.0") - _DROP_THRESHOLD_K, EMPFANGEN_AM
    )

    advance_zone_state(session, EMPFANGEN_AM)

    state = session.get(ZoneState, zone.id)
    assert state is not None
    assert state.window_open is False
    assert state.window_open_by_temperature is False


def test_the_switch_on_without_a_temperature_source_never_opens_the_window(
    session: Session,
) -> None:
    """The switch alone is not enough -- a zone still needs an actual temperature
    source to reason about, exactly like `sensor_stuck` and the sensor-fault
    detection already require one."""
    zone, _settings, _temperature = _temp_drop_zone(session, "ohne-quelle-mit-schalter-zone")
    zone.temperature_source_device_id = None

    advance_zone_state(session, EMPFANGEN_AM)

    state = session.get(ZoneState, zone.id)
    assert state is not None
    assert state.window_open is False
    assert state.window_open_by_temperature is False


def test_a_reporting_gap_mid_window_still_yields_a_correct_result(session: Session) -> None:
    """Distinct from the short-history case above: there is plenty of history
    here (readings well before the gap, and `history_covers_duration` is true),
    just none *during* a stretch of the window -- a real Zigbee reporting
    outage, not a sensor only just assigned. Only a handful of readings end up
    inside the window either side of the gap; the query must work with exactly
    those, without needing anything from inside the gap itself, and still reach
    the correct verdict."""
    zone, _settings, temperature = _temp_drop_zone(session, "meldeluecke-zone")
    device_id = zone.temperature_source_device_id
    assert device_id is not None
    start = EMPFANGEN_AM - timedelta(minutes=_DROP_WINDOW_MINUTES)
    _add_reading(session, device_id, temperature.id, Decimal("22.0"), start)
    _add_reading(
        session, device_id, temperature.id, Decimal("22.05"), start + timedelta(minutes=1)
    )
    # A reporting gap follows -- nothing stored for several minutes -- before a
    # single reading right at "now" shows the drop.
    _add_reading(
        session, device_id, temperature.id, Decimal("22.0") - _DROP_THRESHOLD_K, EMPFANGEN_AM
    )

    advance_zone_state(session, EMPFANGEN_AM)

    state = session.get(ZoneState, zone.id)
    assert state is not None
    assert state.window_open is True
    assert state.window_open_by_temperature is True


def test_a_triggered_suspicion_holds_without_a_fresh_drop_and_then_lapses(
    session: Session,
) -> None:
    zone, _settings, temperature = _temp_drop_zone(session, "vermutung-haelt-zone")
    device_id = zone.temperature_source_device_id
    assert device_id is not None
    start = EMPFANGEN_AM - timedelta(minutes=_DROP_WINDOW_MINUTES)
    _add_reading(session, device_id, temperature.id, Decimal("22.0"), start)
    _add_reading(
        session, device_id, temperature.id, Decimal("22.0") - _DROP_THRESHOLD_K, EMPFANGEN_AM
    )

    advance_zone_state(session, EMPFANGEN_AM)
    state = session.get(ZoneState, zone.id)
    assert state is not None and state.window_open is True
    opened_at = EMPFANGEN_AM

    # A later cycle, still inside the hold, with a flat reading (no fresh drop) --
    # the suspicion must still hold, and the clock must not restart.
    still_holding_at = opened_at + timedelta(minutes=_DROP_HOLD_MINUTES - 1)
    _add_reading(
        session, device_id, temperature.id, Decimal("21.0") - _DROP_THRESHOLD_K, still_holding_at
    )
    advance_zone_state(session, still_holding_at)
    state = session.get(ZoneState, zone.id)
    assert state is not None
    assert state.window_open is True
    assert state.window_open_by_temperature is True
    assert state.window_open_since == opened_at

    # Once the hold has fully elapsed and nothing in the trailing window shows a
    # fresh drop (the reading has been flat since), the suspicion lapses on its
    # own -- see `domain.window_temperature_drop`'s module docstring for why a
    # bounded hold, not a recovery signal, is the release used here.
    lapsed_at = opened_at + timedelta(minutes=_DROP_HOLD_MINUTES)
    _add_reading(session, device_id, temperature.id, Decimal("21.0") - _DROP_THRESHOLD_K, lapsed_at)
    advance_zone_state(session, lapsed_at)
    state = session.get(ZoneState, zone.id)
    assert state is not None
    assert state.window_open is False
    assert state.window_open_by_temperature is False
    assert state.window_open_since is None


def test_the_cap_forces_a_silence_once_a_streak_runs_too_long(session: Session) -> None:
    """Cross-review finding: the hold alone lets the detection re-trigger off its
    own withheld heat indefinitely, since a room the detection itself has kept
    unheated can keep cooling steeply enough to cross the threshold again right
    where each hold lapses. The cap (`setting.window_temp_drop_max_suspected_
    minutes`) has to force a silence once one uninterrupted streak has run for
    too long, even though a fresh drop is, on its own merits, still present."""
    zone, settings, temperature = _temp_drop_zone(session, "obergrenze-zone")
    cap_minutes = 25
    silence_minutes = 15
    settings.window_temp_drop_max_suspected_minutes = cap_minutes
    settings.window_temp_drop_silence_minutes = silence_minutes
    device_id = zone.temperature_source_device_id
    assert device_id is not None
    start = EMPFANGEN_AM - timedelta(minutes=_DROP_WINDOW_MINUTES)
    _add_reading(session, device_id, temperature.id, Decimal("22.0"), start)
    _add_reading(
        session, device_id, temperature.id, Decimal("22.0") - _DROP_THRESHOLD_K, EMPFANGEN_AM
    )
    advance_zone_state(session, EMPFANGEN_AM)
    state = session.get(ZoneState, zone.id)
    assert state is not None and state.window_open is True
    opened_at = EMPFANGEN_AM

    # A fresh drop keeps appearing right at the cap boundary -- exactly the
    # feedback loop the cap exists to break -- yet detection must now stand
    # down instead of trusting it.
    at_cap = opened_at + timedelta(minutes=cap_minutes)
    _add_reading(
        session,
        device_id,
        temperature.id,
        Decimal("22.0"),
        at_cap - timedelta(minutes=_DROP_WINDOW_MINUTES),
    )
    _add_reading(
        session, device_id, temperature.id, Decimal("22.0") - _DROP_THRESHOLD_K, at_cap
    )
    advance_zone_state(session, at_cap)
    state = session.get(ZoneState, zone.id)
    assert state is not None
    assert state.window_open is False
    assert state.window_open_by_temperature is False
    assert state.window_open_since is None
    assert state.window_temp_drop_silence_until == at_cap + timedelta(minutes=silence_minutes)


def test_silence_blocks_a_fresh_drop_then_lapses_and_trusts_one_again(
    session: Session,
) -> None:
    zone, settings, temperature = _temp_drop_zone(session, "zwangspause-zone")
    cap_minutes = 25
    silence_minutes = 15
    settings.window_temp_drop_max_suspected_minutes = cap_minutes
    settings.window_temp_drop_silence_minutes = silence_minutes
    device_id = zone.temperature_source_device_id
    assert device_id is not None
    start = EMPFANGEN_AM - timedelta(minutes=_DROP_WINDOW_MINUTES)
    _add_reading(session, device_id, temperature.id, Decimal("22.0"), start)
    _add_reading(
        session, device_id, temperature.id, Decimal("22.0") - _DROP_THRESHOLD_K, EMPFANGEN_AM
    )
    advance_zone_state(session, EMPFANGEN_AM)

    at_cap = EMPFANGEN_AM + timedelta(minutes=cap_minutes)
    _add_reading(
        session,
        device_id,
        temperature.id,
        Decimal("22.0"),
        at_cap - timedelta(minutes=_DROP_WINDOW_MINUTES),
    )
    _add_reading(
        session, device_id, temperature.id, Decimal("22.0") - _DROP_THRESHOLD_K, at_cap
    )
    advance_zone_state(session, at_cap)
    state = session.get(ZoneState, zone.id)
    assert state is not None
    silence_until = state.window_temp_drop_silence_until
    assert silence_until == at_cap + timedelta(minutes=silence_minutes)

    # Still inside the silence: an even steeper fresh drop must not reopen it,
    # and the deadline itself must not move.
    mid_silence = at_cap + timedelta(minutes=5)
    _add_reading(
        session,
        device_id,
        temperature.id,
        Decimal("22.0"),
        mid_silence - timedelta(minutes=_DROP_WINDOW_MINUTES),
    )
    _add_reading(
        session,
        device_id,
        temperature.id,
        Decimal("22.0") - _DROP_THRESHOLD_K - Decimal("2"),
        mid_silence,
    )
    advance_zone_state(session, mid_silence)
    state = session.get(ZoneState, zone.id)
    assert state is not None
    assert state.window_open is False
    assert state.window_open_by_temperature is False
    assert state.window_temp_drop_silence_until == silence_until

    # Once the silence has lapsed, a fresh drop is trusted again.
    after_silence = silence_until + timedelta(minutes=1)
    _add_reading(
        session,
        device_id,
        temperature.id,
        Decimal("22.0"),
        after_silence - timedelta(minutes=_DROP_WINDOW_MINUTES),
    )
    _add_reading(
        session, device_id, temperature.id, Decimal("22.0") - _DROP_THRESHOLD_K, after_silence
    )
    advance_zone_state(session, after_silence)
    state = session.get(ZoneState, zone.id)
    assert state is not None
    assert state.window_open is True
    assert state.window_open_by_temperature is True
    assert state.window_temp_drop_silence_until is None
    assert state.window_open_since == after_silence


def test_a_real_window_contact_governs_exclusively_even_when_it_reads_unknown(
    session: Session,
) -> None:
    """Task instruction: once a zone has a window contact assigned, it decides
    exclusively -- even while its own current reading is stale or missing. The
    temperature guess must not step in during that outage, however steep the
    zone's own recent temperature drop looks."""
    zone, _settings, temperature = _temp_drop_zone(session, "kontakt-und-temperatur-zone")
    device_id = zone.temperature_source_device_id
    assert device_id is not None
    start = EMPFANGEN_AM - timedelta(minutes=_DROP_WINDOW_MINUTES)
    _add_reading(session, device_id, temperature.id, Decimal("22.0"), start)
    _add_reading(
        session,
        device_id,
        temperature.id,
        Decimal("22.0") - _DROP_THRESHOLD_K - Decimal("2"),
        EMPFANGEN_AM,
    )
    # A window contact assigned to the same zone, but stale -- old enough to read
    # as unknown under the (very short) sensor timeout below.
    contact = _capability(session, "contact")
    contact_device = create_device(session, "kontakt-und-temperatur-zone-kontakt")
    zone.sensor_timeout_seconds = 30
    session.add(
        ZoneDevice(
            zone_id=zone.id,
            device_id=contact_device.id,
            device_role_id=role(session, "window_contact").id,
        )
    )
    session.add(
        Measurement(
            device_id=contact_device.id,
            capability_id=contact.id,
            value_text="false",
            measured_at=EMPFANGEN_AM - timedelta(seconds=31),
            received_at=EMPFANGEN_AM,
        )
    )

    advance_zone_state(session, EMPFANGEN_AM)

    state = session.get(ZoneState, zone.id)
    assert state is not None
    assert state.window_open is None
    assert state.window_open_by_temperature is False


def test_the_switch_off_never_opens_the_window_from_temperature_alone(
    session: Session,
) -> None:
    zone, _settings, temperature = _temp_drop_zone(session, "schalter-aus-zone")
    zone.window_temp_drop_detection_enabled = False
    device_id = zone.temperature_source_device_id
    assert device_id is not None
    start = EMPFANGEN_AM - timedelta(minutes=_DROP_WINDOW_MINUTES)
    _add_reading(session, device_id, temperature.id, Decimal("22.0"), start)
    _add_reading(
        session,
        device_id,
        temperature.id,
        Decimal("22.0") - _DROP_THRESHOLD_K - Decimal("2"),
        EMPFANGEN_AM,
    )

    advance_zone_state(session, EMPFANGEN_AM)

    state = session.get(ZoneState, zone.id)
    assert state is not None
    assert state.window_open is None
    assert state.window_open_by_temperature is False


def test_the_cold_window_alarm_applies_to_a_temperature_inferred_window_too(
    session: Session,
) -> None:
    """Task instruction: verify, rather than assume, that `domain.window_alarm`
    treats a temperature-inferred window exactly like a real one -- it reads only
    `window_open`/`window_open_since`, which `advance_zone_state` sets identically
    regardless of source, so this is expected to hold without any change there."""
    zone, setting_row, temperature = _temp_drop_zone(session, "temperatur-fenster-alarm-zone")
    setting_row.window_alarm_open_minutes = 30
    setting_row.window_alarm_outdoor_threshold_c = Decimal("5.0")
    setting_row.window_temp_drop_hold_minutes = 120
    outdoor_device = create_device(session, "aussenfuehler-temp-erkennung")
    setting_row.outdoor_temperature_source_device_id = outdoor_device.id
    session.add(
        Measurement(
            device_id=outdoor_device.id,
            capability_id=temperature.id,
            value_numeric=Decimal("-2.0"),
            measured_at=EMPFANGEN_AM,
            received_at=EMPFANGEN_AM,
        )
    )
    device_id = zone.temperature_source_device_id
    assert device_id is not None
    start = EMPFANGEN_AM - timedelta(minutes=_DROP_WINDOW_MINUTES)
    _add_reading(session, device_id, temperature.id, Decimal("22.0"), start)
    _add_reading(
        session, device_id, temperature.id, Decimal("22.0") - _DROP_THRESHOLD_K, EMPFANGEN_AM
    )

    # Just triggered -- not yet long enough for the alarm.
    advance_zone_state(session, EMPFANGEN_AM)
    state = session.get(ZoneState, zone.id)
    assert state is not None
    assert state.window_open is True
    assert state.window_open_by_temperature is True
    assert state.window_alarm is False

    # 40 minutes later, still cold outside and still held (hold set to 120
    # minutes above so the suspicion has not lapsed on its own by then).
    later = EMPFANGEN_AM + timedelta(minutes=40)
    _add_reading(session, outdoor_device.id, temperature.id, Decimal("-2.0"), later)
    advance_zone_state(session, later)
    state = session.get(ZoneState, zone.id)
    assert state is not None and state.window_alarm is True


def test_window_alarm_is_unknown_without_an_outdoor_source(session: Session) -> None:
    """No outdoor source configured is an unknown state, not "no alarm" --
    `domain.window_alarm.window_alarm_state`'s own contract."""
    create_settings(session)
    sensor_status_of(session, "keine_quelle")
    zone, device = _open_window_zone(session, "kein-aussenwert-zone")
    opened_at = EMPFANGEN_AM
    _set_contact(session, device, "false", opened_at)

    advance_zone_state(session, opened_at + timedelta(hours=1))
    state = session.get(ZoneState, zone.id)
    assert state is not None and state.window_alarm is None


def test_window_alarm_becomes_true_once_both_conditions_hold(session: Session) -> None:
    setting_row = create_settings(session)
    setting_row.window_alarm_open_minutes = 30
    setting_row.window_alarm_outdoor_threshold_c = Decimal("5.0")
    # Long enough that the artificial time jumps below do not make the window
    # contact's or the outdoor sensor's own reading count as stale.
    setting_row.default_sensor_timeout_seconds = 86400
    sensor_status_of(session, "keine_quelle")
    temperature = _capability(session, "temperature")
    outdoor_device = create_device(session, "aussenfuehler")
    setting_row.outdoor_temperature_source_device_id = outdoor_device.id
    session.add(
        Measurement(
            device_id=outdoor_device.id,
            capability_id=temperature.id,
            value_numeric=Decimal("-2.0"),
            measured_at=EMPFANGEN_AM,
            received_at=EMPFANGEN_AM,
        )
    )
    zone, device = _open_window_zone(session, "fenster-alarm-zone")
    opened_at = EMPFANGEN_AM
    _set_contact(session, device, "false", opened_at)

    # Window just opened -- not yet long enough.
    advance_zone_state(session, opened_at)
    state = session.get(ZoneState, zone.id)
    assert state is not None and state.window_alarm is False

    # 40 minutes later, still open and still cold outside.
    later = opened_at + timedelta(minutes=40)
    advance_zone_state(session, later)
    state = session.get(ZoneState, zone.id)
    assert state is not None and state.window_alarm is True

    # The window closes -- the alarm clears in the same cycle.
    closed_at = later + timedelta(minutes=1)
    _set_contact(session, device, "true", closed_at)
    advance_zone_state(session, closed_at)
    state = session.get(ZoneState, zone.id)
    assert state is not None and state.window_alarm is False


def test_a_stale_window_contact_mid_alarm_turns_unknown_not_all_clear(
    session: Session,
) -> None:
    """The cross-review finding: an active alarm's window contact going stale
    (sensor timeout, not a real closing) must not be read as "window closed" --
    that used to clear `window_open_since` and make the alarm silently drop to
    `False`, exactly the false all-clear this test guards against. The correct
    answer while the contact is unknown is `None`, and once the contact reports
    fresh again -- still open, the whole time -- the alarm must reassert itself
    from the *original* opening time, not from a clock that was reset in
    between.
    """
    setting_row = create_settings(session)
    setting_row.window_alarm_open_minutes = 30
    setting_row.window_alarm_outdoor_threshold_c = Decimal("5.0")
    # Short enough that the contact measurement below actually goes stale
    # partway through the test -- this is exactly what is under test here,
    # unlike the long timeout the test above deliberately avoids it with.
    setting_row.default_sensor_timeout_seconds = 1800
    sensor_status_of(session, "keine_quelle")
    temperature = _capability(session, "temperature")
    outdoor_device = create_device(session, "aussenfuehler-stale-kontakt")
    setting_row.outdoor_temperature_source_device_id = outdoor_device.id
    session.add(
        Measurement(
            device_id=outdoor_device.id,
            capability_id=temperature.id,
            value_numeric=Decimal("-2.0"),
            measured_at=EMPFANGEN_AM,
            received_at=EMPFANGEN_AM,
        )
    )
    # Keep the outdoor reading fresh throughout -- this test is about the
    # window contact going stale, not the outdoor source.
    zone, device = _open_window_zone(session, "stale-kontakt-zone")
    opened_at = EMPFANGEN_AM
    _set_contact(session, device, "false", opened_at)
    # Establishes `window_open_since == opened_at` on this very first cycle --
    # without it, the *next* call below would be the first one to ever see the
    # window open and would stamp `window_open_since` with its own `now`
    # instead of `opened_at`.
    advance_zone_state(session, opened_at)
    assert session.get(ZoneState, zone.id).window_open_since == opened_at  # type: ignore[union-attr]

    # 40 minutes later: open long enough, cold enough -- alarm active. The
    # contact itself is refreshed here too (still open) so that this cycle
    # alone is not already what makes it stale -- the clock's *origin*,
    # `window_open_since`, is what must stay at `opened_at`, not the contact's
    # own freshness.
    active_at = opened_at + timedelta(minutes=40)
    _set_contact(session, device, "false", active_at)
    session.add(
        Measurement(
            device_id=outdoor_device.id,
            capability_id=temperature.id,
            value_numeric=Decimal("-2.0"),
            measured_at=active_at,
            received_at=active_at,
        )
    )
    advance_zone_state(session, active_at)
    state = session.get(ZoneState, zone.id)
    assert state is not None and state.window_alarm is True
    assert state.window_open_since == opened_at

    # An hour later, without a fresh contact reading: the contact is now
    # stale (over the 1800s timeout), so the window's own state is unknown.
    stale_at = active_at + timedelta(hours=1)
    session.add(
        Measurement(
            device_id=outdoor_device.id,
            capability_id=temperature.id,
            value_numeric=Decimal("-2.0"),
            measured_at=stale_at,
            received_at=stale_at,
        )
    )
    advance_zone_state(session, stale_at)
    state = session.get(ZoneState, zone.id)
    assert state is not None
    assert state.window_open is None, "the contact itself must read as unknown"
    assert state.window_alarm is None, (
        "an unknown contact must never be read as an all-clear for an "
        "already-active alarm"
    )
    assert state.window_open_since == opened_at, (
        "the clock must survive an unknown cycle unchanged -- it is not "
        "confirmed closed, so it must not be cleared"
    )

    # The contact reports again -- still open, the whole time.
    recovered_at = stale_at + timedelta(minutes=1)
    _set_contact(session, device, "false", recovered_at)
    session.add(
        Measurement(
            device_id=outdoor_device.id,
            capability_id=temperature.id,
            value_numeric=Decimal("-2.0"),
            measured_at=recovered_at,
            received_at=recovered_at,
        )
    )
    advance_zone_state(session, recovered_at)
    state = session.get(ZoneState, zone.id)
    assert state is not None
    assert state.window_open is True
    assert state.window_open_since == opened_at, (
        "the clock must resume from the original opening, not restart -- the "
        "unknown interval never actually meant the window closed"
    )
    assert state.window_alarm is True


def test_a_broken_availability_message_has_no_effect(session: Session) -> None:
    """The third message path needs the same protection as the other two.

    Zigbee2MQTT is known to send an empty payload to `.../availability` when the
    bridge restarts. An exception there would halt ingest for every other device.
    """
    for payload in (b"", b"{kaputt", b"\xff\xfe", b'"nur ein Text"', b"{}"):
        process_message(
            session,
            "zigbee2mqtt/Ein Geraet/availability",
            payload,
            base="zigbee2mqtt",
            received_at=datetime(2026, 8, 29, 12, 0, 0),
        )
    session.flush()
    states = list(session.scalars(select(DeviceHealth)))
    assert all(z.availability is None for z in states), (
        "An unusable availability message must not set a state."
    )


def test_the_first_sighting_survives_a_second_device_list(session: Session) -> None:
    """`first_seen_at` is the first sighting, not the last.

    Zigbee2MQTT resends the device list on every connection. If it overwrote the
    value, it would say today after every bridge restart -- making the question
    'since when have we known this device?' unanswerable.
    """
    items = json.dumps(
        [
            {
                "friendly_name": "Ein Multisensor",
                "ieee_address": "0x0000000000000001",
                "type": "EndDevice",
                "definition": {"model": "M1", "vendor": "V", "exposes": []},
            }
        ]
    ).encode()
    integration(session, "zigbee2mqtt")
    frueher = datetime(2026, 8, 1, 8, 0, 0)
    later = datetime(2026, 8, 29, 8, 0, 0)
    process_message(
        session, "zigbee2mqtt/bridge/devices", items, base="zigbee2mqtt", received_at=frueher
    )
    process_message(
        session, "zigbee2mqtt/bridge/devices", items, base="zigbee2mqtt", received_at=later
    )
    session.flush()
    device = session.scalar(select(Device).where(Device.external_id == "Ein Multisensor"))
    assert device is not None
    assert device.first_seen_at == frueher


def test_message_kinds_that_are_not_processed_have_no_consequences(
    session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    """Bridge and foreign messages are logged, not silently dropped and not
    processed -- logged so an unexpected topic stands out while debugging."""
    before = len(list(session.scalars(select(Device))))
    with caplog.at_level(logging.INFO):
        process_message(
            session, "zigbee2mqtt/bridge/state", b'{"state": "online"}',
            base="zigbee2mqtt", received_at=datetime(2026, 8, 29, 12, 0, 0),
        )
        process_message(
            session, "ganz/woanders/her", b"{}",
            base="zigbee2mqtt", received_at=datetime(2026, 8, 29, 12, 0, 0),
        )
    session.flush()
    assert len(list(session.scalars(select(Device)))) == before
    assert "nicht verarbeitet" in caplog.text


def test_the_first_sighting_is_filled_in_for_a_hand_created_device(
    session: Session,
) -> None:
    """Starting with subproject 3, the interface also creates devices -- without a
    sighting there.

    When the first message comes in, the timestamp should be filled in retroactively
    instead of staying empty. Otherwise the overview would permanently show 'never'.
    """
    db_connection = integration(session, "zigbee2mqtt")
    session.add(
        Device(
            integration_id=db_connection.id,
            external_id="Von Hand angelegt",
            display_name="Von Hand angelegt",
            is_enabled=True,
            first_seen_at=None,
        )
    )
    session.flush()
    items = json.dumps(
        [
            {
                "friendly_name": "Von Hand angelegt",
                "ieee_address": "0x0000000000000002",
                "type": "EndDevice",
                "definition": {"model": "M2", "vendor": "V", "exposes": []},
            }
        ]
    ).encode()
    gesehen = datetime(2026, 8, 29, 9, 30, 0)
    process_message(
        session, "zigbee2mqtt/bridge/devices", items, base="zigbee2mqtt", received_at=gesehen
    )
    session.flush()
    device = session.scalar(select(Device).where(Device.external_id == "Von Hand angelegt"))
    assert device is not None and device.first_seen_at == gesehen


def test_a_property_named_twice_does_not_break_the_whole_device_list(
    session: Session,
) -> None:
    """A real bridge list repeats a property name -- and that took down the ingest.

    Zigbee2MQTT names the same property in more than one branch when a device has
    several endpoints, or when its `switch` and `light` exposes both carry
    `execute_if_off`. `device_property` holds one row per `(device_id, name)`, so the
    second insert hit the UNIQUE constraint. Since `_process_device_list` handles the
    whole message in one transaction, that did not just drop one property: the entire
    `bridge/devices` message failed, and **no** device on the bridge was updated any
    more. Taken from a real installation, where it appeared as

        IntegrityError: UNIQUE constraint failed:
        device_property.device_id, device_property.name

    The first occurrence wins; the point of the test is that the ingest survives at
    all and the other devices still arrive.
    """
    integration(session)
    items = [
        {
            "ieee_address": "0x1111",
            "friendly_name": "steckdose",
            "definition": {
                "model": "doppelt",
                "exposes": [
                    {
                        "type": "switch",
                        "features": [
                            {"type": "binary", "property": "state", "access": 7},
                            {
                                "type": "binary",
                                "property": "execute_if_off",
                                "access": 2,
                            },
                        ],
                    },
                    {
                        "type": "light",
                        "features": [
                            {
                                "type": "binary",
                                "property": "execute_if_off",
                                "access": 2,
                            },
                        ],
                    },
                ],
            },
        },
        {
            "ieee_address": "0x2222",
            "friendly_name": "fuehler",
            "definition": {
                "model": "einfach",
                "exposes": [{"type": "numeric", "property": "temperature", "access": 1}],
            },
        },
    ]

    process_message(
        session,
        f"{BASIS}/bridge/devices",
        json.dumps(items).encode(),
        base=BASIS,
        received_at=EMPFANGEN_AM,
    )
    session.flush()

    device = session.scalar(select(Device).where(Device.external_id == "steckdose"))
    assert device is not None
    names = session.scalars(
        select(DeviceProperty.name).where(DeviceProperty.device_id == device.id)
    ).all()
    assert sorted(names) == ["execute_if_off", "state"]

    # The decisive part: the device behind the duplicate arrived as well. Before the
    # fix the exception aborted the message and this row did not exist.
    assert session.scalar(select(Device).where(Device.external_id == "fuehler")) is not None


def _regler_mit_merkmalen(session: Session, name: str, *properties: str) -> None:
    """A device with the given properties -- plus `temperature`, deliberately.

    The property values are only written when the message also carries a recognised
    measurement; without one the whole block is skipped. A test built without it would
    pass for a reason that has nothing to do with what it claims to check.
    """
    _capability(session, "temperature")
    exposes: list[dict[str, object]] = [
        {"type": "numeric", "property": "temperature", "access": 1}
    ]
    exposes += [{"type": "binary", "property": name_, "access": 3} for name_ in properties]
    process_message(
        session,
        f"{BASIS}/bridge/devices",
        json.dumps(
            [{"friendly_name": name, "definition": {"model": "regler", "exposes": exposes}}]
        ).encode(),
        base=BASIS,
        received_at=EMPFANGEN_AM,
    )


def test_a_broken_property_payload_leaves_the_stored_values_alone(
    session: Session,
) -> None:
    """A device state that is not readable JSON must not clear what is known.

    The reading path already logs the parse failure; here the point is the second
    consequence: the stored property values stay as they were instead of being wiped
    to "unknown" by a single garbled message.
    """
    integration(session)
    _regler_mit_merkmalen(session, "wandregler", "child_lock")
    process_message(
        session, f"{BASIS}/wandregler", b'{"temperature": 20.0, "child_lock": true}',
        base=BASIS, received_at=EMPFANGEN_AM,
    )
    session.flush()
    prop = session.scalar(select(DeviceProperty).where(DeviceProperty.name == "child_lock"))
    assert prop is not None and prop.last_value_text == "true"

    process_message(
        session, f"{BASIS}/wandregler", b"{kaputt",
        base=BASIS, received_at=EMPFANGEN_AM + timedelta(minutes=1),
    )
    session.flush()
    session.refresh(prop)
    assert prop.last_value_text == "true"


def test_a_state_that_is_not_an_object_changes_nothing(session: Session) -> None:
    """Valid JSON, wrong shape -- a list carries no property values."""
    integration(session)
    _regler_mit_merkmalen(session, "listenregler", "child_lock")
    process_message(
        session, f"{BASIS}/listenregler", b'["kein", "objekt"]',
        base=BASIS, received_at=EMPFANGEN_AM,
    )
    session.flush()
    prop = session.scalar(select(DeviceProperty).where(DeviceProperty.name == "child_lock"))
    assert prop is not None and prop.last_value_text is None


def test_only_the_properties_present_in_the_message_are_updated(
    session: Session,
) -> None:
    """Zigbee2MQTT sends what changed, not the whole device.

    A property missing from the message keeps its last value -- treating absence as
    "no longer known" would make a display flicker to empty on every partial update.
    """
    integration(session)
    _regler_mit_merkmalen(session, "zweiwertig", "child_lock")
    process_message(
        session, f"{BASIS}/zweiwertig", b'{"temperature": 21.5, "child_lock": false}',
        base=BASIS, received_at=EMPFANGEN_AM,
    )
    session.flush()

    process_message(
        session, f"{BASIS}/zweiwertig", b'{"temperature": 22.0}',
        base=BASIS, received_at=EMPFANGEN_AM + timedelta(minutes=1),
    )
    session.flush()
    lock = session.scalar(select(DeviceProperty).where(DeviceProperty.name == "child_lock"))
    assert lock is not None and lock.last_value_text == "false"
