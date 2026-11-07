"""Mehrere Eingabefuehler je Zone im Regelpfad (`services/ingest.py`, `services/shadow_run.py`).

Die reine Mittelwertbildung steht in `tests/test_room_temperature.py`. Hier geht es um
das, was der Dienst daraus macht: Zonenzustand, festhaengender Messwert,
Temperatursturz-Fenstererkennung, Notbetrieb (Uebergang und Rueckkehrpruefung) und
Begruendungstexte. Ein Fuehler bleibt bitgenau wie vorher -- das sichern die
unveraenderten Tests in `tests/test_ingest.py` und `tests/test_shadow_run*.py`.
"""

from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from tests.helpers import capability, create_device, create_settings, create_zone, sensor_status_of
from thermoctl.db.models.device import ZoneTemperatureSource
from thermoctl.db.models.lookup import SensorStatus
from thermoctl.db.models.measurement import Measurement
from thermoctl.db.models.operations import Setting
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.sensor_failure import (
    SensorFailureEpisode,
    SensorFailureProfile,
    ZoneSensorFailureState,
)
from thermoctl.db.models.state import ShadowDecision, ZoneState
from thermoctl.db.models.zone import SetpointMode, Zone, ZoneSetpoint
from thermoctl.domain import emergency_operation
from thermoctl.services import shadow_run
from thermoctl.services.ingest import advance_zone_state

T0 = datetime(2026, 9, 29, 12, 0, 0)
TIMEOUT_S = 90


def _plant(session: Session) -> None:
    settings = create_settings(session)
    settings.default_sensor_timeout_seconds = TIMEOUT_S
    for code in ("ok", "veraltet", "keine_quelle"):
        sensor_status_of(session, code)
    capability(session, "temperature")


def _zone(session: Session, name: str, *, emergency: bool = False) -> Zone:
    zone = create_zone(session, name)
    zone.sensor_failure_enabled = emergency
    if emergency:
        # Slow recovery (many samples, long): the tests look at the running count,
        # not at the moment it completes.
        profile = SensorFailureProfile(
            name=f"langsam-{name}",
            version=1,
            fixed_on_seconds=20,
            fixed_off_seconds=30,
            recovery_seconds=3600,
            recovery_samples=50,
            warm_restart_hysteresis_k=Decimal("1.00"),
        )
        session.add(profile)
        session.flush()
        zone.sensor_failure_profile_id = profile.id
    mode = SetpointMode(code=f"h-{name}"[:32], name="Heizen")
    session.add(mode)
    session.flush()
    session.add(
        ZoneSetpoint(zone_id=zone.id, setpoint_mode_id=mode.id, temperature_c=Decimal("21.0"))
    )
    session.add(
        SchedulePoint(
            zone_id=zone.id, weekday=T0.weekday(), minute_of_day=0, setpoint_mode_id=mode.id
        )
    )
    session.flush()
    return zone


def _sensor(session: Session, zone: Zone, name: str, order: int = 0):  # type: ignore[no-untyped-def]
    device = create_device(session, name)
    session.add(ZoneTemperatureSource(zone_id=zone.id, device_id=device.id, sort_order=order))
    session.flush()
    return device


def _reading(session: Session, device_id: int, value: str, at: datetime) -> None:
    session.add(
        Measurement(
            device_id=device_id,
            capability_id=capability(session, "temperature").id,
            value_numeric=Decimal(value),
            measured_at=at,
            received_at=at,
        )
    )
    session.flush()


def _state(session: Session, zone: Zone) -> ZoneState:
    state = session.get(ZoneState, zone.id)
    assert state is not None
    return state


def _status(session: Session, zone: Zone) -> str:
    return session.get(SensorStatus, _state(session, zone).sensor_status_id).code  # type: ignore[union-attr]


def _stage(session: Session, zone: Zone) -> str:
    row = session.get(ZoneSensorFailureState, zone.id)
    assert row is not None
    return row.stage


def _cycle(session: Session, now: datetime, zone: Zone) -> ShadowDecision:
    advance_zone_state(session, now)
    rows = shadow_run.cycle(session, now)
    return next(r for r in rows if r.zone_id == zone.id)


# --- Zonenzustand ---------------------------------------------------------------


def test_the_zone_state_is_the_mean_of_the_usable_sensors_at_the_newest_time(
    session: Session,
) -> None:
    _plant(session)
    zone = _zone(session, "mittel")
    a = _sensor(session, zone, "fuehler-a", 0)
    b = _sensor(session, zone, "fuehler-b", 1)
    _reading(session, a.id, "20.00", T0 - timedelta(seconds=40))
    _reading(session, b.id, "21.00", T0 - timedelta(seconds=10))

    advance_zone_state(session, T0)

    state = _state(session, zone)
    assert state.temperature_c == Decimal("20.50")
    assert state.measured_at == T0 - timedelta(seconds=10)
    assert _status(session, zone) == "ok"


def test_a_stale_sensor_is_left_out_of_the_mean(session: Session) -> None:
    _plant(session)
    zone = _zone(session, "ein-veralteter")
    a = _sensor(session, zone, "fuehler-a", 0)
    b = _sensor(session, zone, "fuehler-b", 1)
    _reading(session, a.id, "10.00", T0 - timedelta(seconds=TIMEOUT_S + 1))
    _reading(session, b.id, "21.00", T0 - timedelta(seconds=10))

    advance_zone_state(session, T0)

    state = _state(session, zone)
    assert state.temperature_c == Decimal("21.00")
    assert state.measured_at == T0 - timedelta(seconds=10)
    assert _status(session, zone) == "ok"


def test_without_a_usable_sensor_the_newest_known_reading_is_flagged_stale(
    session: Session,
) -> None:
    _plant(session)
    zone = _zone(session, "alle-veraltet")
    a = _sensor(session, zone, "fuehler-a", 0)
    b = _sensor(session, zone, "fuehler-b", 1)
    _reading(session, a.id, "19.00", T0 - timedelta(seconds=500))
    _reading(session, b.id, "20.00", T0 - timedelta(seconds=300))

    advance_zone_state(session, T0)

    state = _state(session, zone)
    assert _status(session, zone) == "veraltet"
    # Never a mean of stale values: the newest known reading, flagged by the status.
    assert state.temperature_c == Decimal("20.00")
    assert state.measured_at == T0 - timedelta(seconds=300)


def test_sensors_that_never_reported_give_no_source(session: Session) -> None:
    _plant(session)
    zone = _zone(session, "nie-gemeldet")
    _sensor(session, zone, "fuehler-a", 0)
    _sensor(session, zone, "fuehler-b", 1)

    advance_zone_state(session, T0)

    assert _status(session, zone) == "keine_quelle"
    assert _state(session, zone).temperature_c is None


def test_one_sensor_serving_two_zones_feeds_both(session: Session) -> None:
    _plant(session)
    zone_1 = _zone(session, "zone-eins")
    zone_2 = _zone(session, "zone-zwei")
    shared = _sensor(session, zone_1, "geteilter-fuehler", 0)
    session.add(ZoneTemperatureSource(zone_id=zone_2.id, device_id=shared.id, sort_order=0))
    own = _sensor(session, zone_2, "eigener-fuehler", 1)
    _reading(session, shared.id, "20.00", T0 - timedelta(seconds=5))
    _reading(session, own.id, "22.00", T0 - timedelta(seconds=5))

    advance_zone_state(session, T0)

    assert _state(session, zone_1).temperature_c == Decimal("20.00")
    assert _state(session, zone_2).temperature_c == Decimal("21.00")


# --- festhaengender Messwert -------------------------------------------------------


def _hours_of(session: Session, device_id: int, value: str, hours: int = 3) -> None:
    for step in range(hours * 10 + 1):
        _reading(session, device_id, value, T0 - timedelta(minutes=6 * step))


def test_one_stuck_sensor_flags_the_zone_but_still_counts_in_the_mean(session: Session) -> None:
    _plant(session)
    settings = session.get(Setting, 1)
    assert settings is not None
    settings.stuck_reading_hours = 2
    settings.default_sensor_timeout_seconds = 86400
    zone = _zone(session, "ein-starrer")
    stuck = _sensor(session, zone, "starrer-fuehler", 0)
    moving = _sensor(session, zone, "beweglicher-fuehler", 1)
    _hours_of(session, stuck.id, "20.00")
    for step in range(31):
        _reading(
            session,
            moving.id,
            "22.00" if step % 3 else "22.40",
            T0 - timedelta(minutes=6 * step),
        )

    advance_zone_state(session, T0)

    state = _state(session, zone)
    assert state.sensor_stuck is True
    assert _status(session, zone) == "ok"
    # Reported, but regulation continues with it: 20.00 is part of the mean.
    assert state.temperature_c == Decimal("21.20")


def test_no_sensor_stuck_means_the_zone_is_not_stuck(session: Session) -> None:
    _plant(session)
    settings = session.get(Setting, 1)
    assert settings is not None
    settings.stuck_reading_hours = 2
    settings.default_sensor_timeout_seconds = 86400
    zone = _zone(session, "keiner-starr")
    a = _sensor(session, zone, "fuehler-a", 0)
    b = _sensor(session, zone, "fuehler-b", 1)
    for device in (a, b):
        for step in range(31):
            value = "21.00" if step % 2 else "21.50"
            _reading(session, device.id, value, T0 - timedelta(minutes=6 * step))

    advance_zone_state(session, T0)

    assert _state(session, zone).sensor_stuck is False


# --- Temperatursturz-Fenstererkennung -----------------------------------------------

WINDOW_MIN = 10
DROP_K = Decimal("1.0")


def _drop_zone(session: Session, name: str) -> Zone:
    _plant(session)
    settings = session.get(Setting, 1)
    assert settings is not None
    settings.default_sensor_timeout_seconds = 86400
    settings.window_temp_drop_window_minutes = WINDOW_MIN
    settings.window_temp_drop_threshold_k = DROP_K
    zone = _zone(session, name)
    zone.window_temp_drop_detection_enabled = True
    return zone


def _history(session: Session, device_id: int, start: str, end: str) -> None:
    _reading(session, device_id, start, T0 - timedelta(minutes=WINDOW_MIN))
    _reading(session, device_id, end, T0)


def test_a_drop_at_any_one_sensor_opens_the_window(session: Session) -> None:
    zone = _drop_zone(session, "sturz-an-einem")
    a = _sensor(session, zone, "fuehler-a", 0)
    b = _sensor(session, zone, "fuehler-b", 1)
    _history(session, a.id, "22.0", "22.0")
    _history(session, b.id, "22.0", "20.5")  # 1.5 K, only this one

    advance_zone_state(session, T0)

    state = _state(session, zone)
    assert state.window_open is True
    assert state.window_open_by_temperature is True


def test_the_mean_dropping_without_a_sensor_dropping_is_no_window(session: Session) -> None:
    """A at 22, B at 18, steady. When A drops out the *mean* falls by 2 K -- but no
    sensor fell, and there is no mean history to compare: no window."""
    zone = _drop_zone(session, "mittel-faellt")
    a = _sensor(session, zone, "fuehler-a", 0)
    b = _sensor(session, zone, "fuehler-b", 1)
    for minute in (WINDOW_MIN, 5, 0):
        _reading(session, a.id, "22.0", T0 - timedelta(minutes=minute + 30))  # old, stale-ish
        _reading(session, b.id, "18.0", T0 - timedelta(minutes=minute))
    # A's last reading is far outside the window and only B changes nothing.
    advance_zone_state(session, T0)

    state = _state(session, zone)
    assert state.window_open is False
    assert state.window_open_by_temperature is False


def test_a_sensor_without_enough_history_does_not_count_even_if_it_looks_dropped(
    session: Session,
) -> None:
    zone = _drop_zone(session, "ohne-historie")
    a = _sensor(session, zone, "fuehler-a", 0)
    b = _sensor(session, zone, "fuehler-b", 1)
    _history(session, a.id, "22.0", "22.0")
    # B just (re)appeared: two readings inside the window, falling, but nothing
    # reaching back to the start of the window.
    _reading(session, b.id, "23.0", T0 - timedelta(minutes=WINDOW_MIN - 3))
    _reading(session, b.id, "21.0", T0)

    advance_zone_state(session, T0)

    assert _state(session, zone).window_open is False


def test_a_single_sensor_drop_behaves_as_before(session: Session) -> None:
    zone = _drop_zone(session, "ein-fuehler-sturz")
    a = _sensor(session, zone, "fuehler-a", 0)
    _history(session, a.id, "22.0", "21.0")  # exactly the threshold

    advance_zone_state(session, T0)

    assert _state(session, zone).window_open is True


# --- Notbetrieb: 3 -> 2 -> 1 -> 0 Fuehler --------------------------------------------


def test_emergency_operation_starts_only_when_the_last_usable_sensor_is_gone(
    session: Session,
) -> None:
    _plant(session)
    zone = _zone(session, "drei-nach-null", emergency=True)
    zone.sensor_timeout_seconds = TIMEOUT_S
    ids = [_sensor(session, zone, f"fuehler-{n}", n).id for n in range(3)]
    # Reports at 0 s (a), 40 s (b), 80 s (c) -- each goes stale TIMEOUT_S later.
    _reading(session, ids[0], "20.00", T0)
    _reading(session, ids[1], "21.00", T0 + timedelta(seconds=40))
    _reading(session, ids[2], "22.00", T0 + timedelta(seconds=80))

    # 3 usable.
    row = _cycle(session, T0 + timedelta(seconds=85), zone)
    assert _state(session, zone).temperature_c == Decimal("21.00")
    assert _stage(session, zone) == emergency_operation.STAGE_NORMAL
    assert "Mittelwert aus 3 von 3 Fühlern" in row.reason

    # a is stale (T0 + 91 s), 2 usable.
    row = _cycle(session, T0 + timedelta(seconds=95), zone)
    assert _state(session, zone).temperature_c == Decimal("21.50")
    assert _status(session, zone) == "ok"
    assert _stage(session, zone) == emergency_operation.STAGE_NORMAL
    assert "Mittelwert aus 2 von 3 Fühlern" in row.reason

    # b is stale too (T0 + 131 s), 1 usable: still Normalbetrieb, mean = c alone.
    row = _cycle(session, T0 + timedelta(seconds=135), zone)
    assert _state(session, zone).temperature_c == Decimal("22.00")
    assert _stage(session, zone) == emergency_operation.STAGE_NORMAL
    assert "Mittelwert aus 1 von 3 Fühlern" in row.reason
    assert session.scalar(select(SensorFailureEpisode.id)) is None

    # c is stale (T0 + 171 s): 0 usable -> Notbetrieb.
    _cycle(session, T0 + timedelta(seconds=175), zone)
    assert _status(session, zone) == "veraltet"
    assert _stage(session, zone) == emergency_operation.STAGE_NOTBETRIEB
    episode = session.scalar(select(SensorFailureEpisode))
    assert episode is not None and episode.ended_at is None


def test_the_episode_names_all_sensors_without_a_single_id(session: Session) -> None:
    _plant(session)
    zone = _zone(session, "episode-mehrere", emergency=True)
    zone.sensor_timeout_seconds = TIMEOUT_S
    _sensor(session, zone, "wand-links", 0)
    _sensor(session, zone, "wand-rechts", 1)

    _cycle(session, T0, zone)  # never reported -> no source -> episode

    episode = session.scalar(select(SensorFailureEpisode))
    assert episode is not None
    assert episode.source_device_id is None
    assert episode.source_device_name == "wand-links, wand-rechts"


def test_the_episode_of_a_single_sensor_keeps_its_id_and_name(session: Session) -> None:
    _plant(session)
    zone = _zone(session, "episode-einer", emergency=True)
    zone.sensor_timeout_seconds = TIMEOUT_S
    only = _sensor(session, zone, "wand-allein", 0)

    _cycle(session, T0, zone)

    episode = session.scalar(select(SensorFailureEpisode))
    assert episode is not None
    assert episode.source_device_id == only.id
    assert episode.source_device_name == "wand-allein"


def test_the_reason_text_of_a_single_sensor_has_no_sensor_count(session: Session) -> None:
    _plant(session)
    zone = _zone(session, "text-einer")
    only = _sensor(session, zone, "wand-allein", 0)
    _reading(session, only.id, "20.00", T0)

    row = _cycle(session, T0, zone)

    assert "Fühlern" not in row.reason


# --- Notbetrieb: Rueckkehrpruefung zaehlt Proben an measured_at -----------------------


def _into_emergency(session: Session, zone: Zone, ids: list[int]) -> datetime:
    """Both sensors reported once at T0 and are stale at the returned time."""
    for device_id in ids:
        _reading(session, device_id, "19.00", T0)
    gone = T0 + timedelta(seconds=TIMEOUT_S + 10)
    _cycle(session, gone, zone)
    assert _stage(session, zone) == emergency_operation.STAGE_NOTBETRIEB
    return gone


def _recovery_count(session: Session, zone: Zone) -> int:
    row = session.get(ZoneSensorFailureState, zone.id)
    assert row is not None
    return row.recovery_sample_count


def test_a_returning_sensor_counts_one_sample_while_another_one_fails(
    session: Session,
) -> None:
    _plant(session)
    zone = _zone(session, "rueckkehr-eins", emergency=True)
    zone.sensor_timeout_seconds = TIMEOUT_S
    a = _sensor(session, zone, "fuehler-a", 0)
    b = _sensor(session, zone, "fuehler-b", 1)
    gone = _into_emergency(session, zone, [a.id, b.id])

    # a returns; b stays silent (its last reading is long stale).
    back = gone + timedelta(seconds=20)
    _reading(session, a.id, "20.00", back)
    _cycle(session, back, zone)
    assert _stage(session, zone) == emergency_operation.STAGE_RUECKKEHRPRUEFUNG
    assert _recovery_count(session, zone) == 1

    # Same reading seen again next cycle (nothing new): not counted twice.
    _cycle(session, back + timedelta(seconds=5), zone)
    assert _recovery_count(session, zone) == 1

    # b returns in the very cycle in which a's reading has just gone stale:
    # exactly one new sample, the one of b.
    later = back + timedelta(seconds=TIMEOUT_S + 5)
    _reading(session, b.id, "20.50", later)
    _cycle(session, later, zone)
    assert _recovery_count(session, zone) == 2


def test_a_timestamp_stepping_back_is_not_counted_a_second_time(session: Session) -> None:
    """The room's `measured_at` is the newest of the usable sensors. When the sensor
    that delivered it leaves the zone, it falls back to an older reading that was
    already part of the count."""
    _plant(session)
    zone = _zone(session, "rueckkehr-zurueck", emergency=True)
    zone.sensor_timeout_seconds = TIMEOUT_S
    a = _sensor(session, zone, "fuehler-a", 0)
    b = _sensor(session, zone, "fuehler-b", 1)
    gone = _into_emergency(session, zone, [a.id, b.id])

    first = gone + timedelta(seconds=10)
    _reading(session, a.id, "20.00", first)
    _cycle(session, first, zone)
    assert _recovery_count(session, zone) == 1

    second = first + timedelta(seconds=10)
    _reading(session, b.id, "20.20", second)
    _cycle(session, second, zone)
    assert _recovery_count(session, zone) == 2

    # b is taken out of the zone: the room's newest reading is a's again.
    session.execute(
        delete(ZoneTemperatureSource).where(ZoneTemperatureSource.device_id == b.id)
    )
    session.flush()
    _cycle(session, second + timedelta(seconds=5), zone)
    assert _state(session, zone).measured_at == first
    assert _recovery_count(session, zone) == 2


def test_two_zones_sharing_a_sensor_recover_independently(session: Session) -> None:
    _plant(session)
    zone_1 = _zone(session, "geteilt-eins", emergency=True)
    zone_2 = _zone(session, "geteilt-zwei", emergency=True)
    for zone in (zone_1, zone_2):
        zone.sensor_timeout_seconds = TIMEOUT_S
    shared = _sensor(session, zone_1, "geteilter-fuehler", 0)
    session.add(ZoneTemperatureSource(zone_id=zone_2.id, device_id=shared.id, sort_order=0))
    own = _sensor(session, zone_2, "eigener-fuehler", 1)
    for device in (shared, own):
        _reading(session, device.id, "19.00", T0)
    gone = T0 + timedelta(seconds=TIMEOUT_S + 10)
    advance_zone_state(session, gone)
    shadow_run.cycle(session, gone)
    assert _stage(session, zone_1) == emergency_operation.STAGE_NOTBETRIEB
    assert _stage(session, zone_2) == emergency_operation.STAGE_NOTBETRIEB

    # The shared sensor returns: zone 1 recovers on it, zone 2 on shared + own mean.
    back = gone + timedelta(seconds=10)
    _reading(session, shared.id, "20.00", back)
    advance_zone_state(session, back)
    shadow_run.cycle(session, back)

    assert _recovery_count(session, zone_1) == 1
    assert _recovery_count(session, zone_2) == 1
    assert _state(session, zone_1).temperature_c == Decimal("20.00")
    assert _state(session, zone_2).temperature_c == Decimal("20.00")
