"""Verdrahtung der neuen PI-Regeln im Schattenlauf (`services/shadow_run.py`).

Die reine Rechnung (`domain/pi_feedforward.py`, `domain/pi_control.py`) ist dort getestet;
hier steht, dass die Zonenparameter ankommen, dass die Außentemperatur aus der
`measurement`-Tabelle der gewählten Quelle gelesen wird, dass der PLAN-Sollwert (vor der
Sonnenabsenkung) in die Vorsteuerung geht und dass alles bei den Vorgaben und ohne wirksames
PI das bisherige Verhalten lässt.
"""

from datetime import timedelta
from decimal import Decimal

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from tests.helpers import capability, integration
from tests.test_shadow_run_pi import COLD_C, NOW, WARM_C, _pi_zone, _row_for
from thermoctl.db.models.device import Device
from thermoctl.db.models.measurement import Measurement
from thermoctl.db.models.operations import Setting
from thermoctl.db.models.state import ZoneState
from thermoctl.domain.solar_setback import HourlyForecast
from thermoctl.services import shadow_run

ROOM_C = Decimal("20.5")  # 0,5 K unter dem Sollwert 21,0: der P-Anteil bleibt unter 1


def _outdoor_source(session: Session) -> Device:
    device = Device(
        integration_id=integration(session).id,
        external_id="aussen",
        display_name="Außenfühler",
    )
    session.add(device)
    session.flush()
    setting = session.get(Setting, 1)
    assert setting is not None
    setting.outdoor_temperature_source_device_id = device.id
    session.flush()
    return device


def _sample(
    session: Session, device: Device, age: timedelta, value: Decimal | None
) -> None:
    moment = NOW - age
    session.add(
        Measurement(
            device_id=device.id,
            capability_id=capability(session, "temperature").id,
            value_numeric=value,
            value_text=None if value is not None else "unavailable",
            measured_at=moment,
            received_at=moment,
        )
    )
    session.flush()


def _constant_outdoor(session: Session, value: Decimal = Decimal("5.0")) -> Device:
    device = _outdoor_source(session)
    for hours in (6, 4, 2, 1):
        _sample(session, device, timedelta(hours=hours), value)
    return device


def _feedforward_zone(session: Session, name: str, *, measured_c: Decimal = ROOM_C):  # type: ignore[no-untyped-def]
    zone = _pi_zone(session, name, measured_c=measured_c)
    zone.pi_feedforward_enabled = True
    zone.pi_feedforward_gain = Decimal("0.020")
    zone.pi_feedforward_smoothing_hours = 3
    session.flush()
    return zone


def test_default_parameters_add_no_feedforward_and_read_no_outdoor_data(
    session: Session, engine: Engine
) -> None:
    zone = _pi_zone(session, "ff-aus", measured_c=ROOM_C)
    _constant_outdoor(session)
    statements: list[str] = []

    def record(conn, cursor, statement, *args):  # type: ignore[no-untyped-def]
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        row = _row_for(shadow_run.cycle(session, NOW), zone)
    finally:
        event.remove(engine, "before_cursor_execute", record)

    assert row.effective_controller == "pi"
    assert row.pi_feedforward_duty == 0
    assert not [s for s in statements if "FROM measurement" in s and "device_capability" in s]
    state = session.get(ZoneState, zone.id)
    assert state is not None
    # Tastgrad = Kp * Fehler + I, ohne Zuschlag.
    assert row.pi_raw_duty == Decimal("0.25") * Decimal("0.5") + row.pi_integral_after


def test_feedforward_adds_gain_times_setpoint_minus_smoothed_outdoor(
    session: Session, engine: Engine
) -> None:
    zone = _feedforward_zone(session, "ff-an")
    _constant_outdoor(session, Decimal("5.0"))
    statements: list[str] = []

    def record(conn, cursor, statement, *args):  # type: ignore[no-untyped-def]
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        row = _row_for(shadow_run.cycle(session, NOW), zone)
    finally:
        event.remove(engine, "before_cursor_execute", record)

    assert row.pi_feedforward_duty == Decimal("0.32")  # 0,02 * (21,0 - 5,0)
    assert row.pi_raw_duty == (
        Decimal("0.25") * Decimal("0.5") + row.pi_integral_after + Decimal("0.32")
    )
    # Eine Abfrage der Messwerte je Zone und Zyklus.
    assert len([s for s in statements if "FROM measurement" in s and "device_capability" in s]) == 1


def test_an_unavailable_latest_reading_keeps_the_last_valid_value(session: Session) -> None:
    zone = _feedforward_zone(session, "ff-unavailable")
    device = _constant_outdoor(session, Decimal("5.0"))
    _sample(session, device, timedelta(minutes=1), None)  # "unavailable"

    row = _row_for(shadow_run.cycle(session, NOW), zone)

    assert row.pi_feedforward_duty == Decimal("0.32")


def test_a_stale_outdoor_value_switches_the_feedforward_off(session: Session) -> None:
    zone = _feedforward_zone(session, "ff-timeout")
    device = _outdoor_source(session)
    _sample(session, device, timedelta(hours=5), Decimal("5.0"))  # älter als das Timeout

    row = _row_for(shadow_run.cycle(session, NOW), zone)

    assert row.effective_controller == "pi"
    assert row.pi_feedforward_duty == 0


def test_without_an_outdoor_source_there_is_no_feedforward(session: Session) -> None:
    zone = _feedforward_zone(session, "ff-ohne-quelle")

    row = _row_for(shadow_run.cycle(session, NOW), zone)

    assert row.pi_feedforward_duty == 0


def test_the_plan_setpoint_not_the_solar_lowered_one_goes_into_the_feedforward(
    session: Session,
) -> None:
    zone = _feedforward_zone(session, "ff-plan-sollwert")
    zone.solar_gain_factor = Decimal("1.0")
    setting = session.get(Setting, 1)
    assert setting is not None
    setting.default_solar_setback_max_k = Decimal("2.0")
    session.flush()
    _constant_outdoor(session, Decimal("5.0"))
    forecast = [HourlyForecast(NOW + timedelta(hours=1), Decimal(20), Decimal(300), Decimal(300))]

    row = _row_for(shadow_run.cycle(session, NOW, forecast), zone)

    assert row.setpoint_c == Decimal("19.0")  # abgesenkt
    assert row.scheduled_setpoint_c == Decimal("21.0")
    assert row.pi_feedforward_duty == Decimal("0.32")  # 0,02 * (21,0 - 5,0), nicht 0,28


def test_a_zone_without_effective_pi_records_no_feedforward(session: Session) -> None:
    zone = _pi_zone(session, "ff-ohne-pi", measured_c=ROOM_C, pi_enabled=False)
    zone.pi_feedforward_enabled = True
    session.flush()
    _constant_outdoor(session)

    row = _row_for(shadow_run.cycle(session, NOW), zone)

    assert row.effective_controller == "hysteresis"
    assert row.pi_feedforward_duty is None


def test_the_surplus_decay_factor_reaches_the_controller(session: Session) -> None:
    held = _pi_zone(session, "decay-0", measured_c=WARM_C)
    default = _pi_zone(session, "decay-1", measured_c=WARM_C)
    held.pi_surplus_decay_factor = Decimal("0.00")
    for zone in (held, default):
        state = session.get(ZoneState, zone.id)
        assert state is not None
        state.pi_integral = Decimal("0.30")
    session.flush()

    rows = shadow_run.cycle(session, NOW)

    assert _row_for(rows, held).pi_integral_after == Decimal("0.30")  # kein Abbau
    assert _row_for(rows, default).pi_integral_after < Decimal("0.30")  # wie bisher


def test_the_integral_separation_reaches_the_controller(session: Session) -> None:
    separated = _pi_zone(session, "trennung", measured_c=COLD_C)  # 3 K unter Soll
    default = _pi_zone(session, "ohne-trennung", measured_c=COLD_C)
    separated.pi_integral_separation_k = Decimal("0.5")
    session.flush()

    rows = shadow_run.cycle(session, NOW)

    row_separated = _row_for(rows, separated)
    row_default = _row_for(rows, default)
    assert row_separated.pi_integral_after == row_separated.pi_integral_before
    assert row_default.pi_integral_after > row_default.pi_integral_before
