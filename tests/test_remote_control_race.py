"""Zwei gleichzeitige Radbefehle für dieselbe Zone (Aktiv-Bereitschafts-Verbund: beide
Instanzen wenden jeden Befehl an).

Unter REPEATABLE READ entsteht der Snapshot einer Sitzung schon beim ersten Lesen
(`session.get(Zone)` in `_execute_command`); eine normale SELECT sieht danach keine Zeile
mehr, die die andere Sitzung inzwischen festgeschrieben hat. Ohne Sperre blieben beide
Übersteuerungen laufen -- und ein „Beenden" nähme nur eine davon weg.

Das lässt sich nur mit echten, gleichzeitigen Transaktionen zeigen, und SQLite kennt weder
Zeilensperren noch diese Isolationsstufe: dort ist nichts abgesichert, der Test wird
übersprungen und sagt das.
"""

import threading
import time
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import Engine, delete, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from tests.helpers import create_mode, create_settings, create_zone, source
from thermoctl.db.models.operations import AuditEvent, Setting
from thermoctl.db.models.override import ZoneOverride
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.sensor_failure import SensorFailureProfile
from thermoctl.db.models.zone import SetpointMode, Zone, ZoneSetpoint
from thermoctl.domain.remote_control import set_temporary_setpoint

NOW = datetime(2026, 8, 31, 8, 0)  # ein Montag


def _committed_zone(engine: Engine) -> int:
    with Session(engine) as setup:
        create_settings(setup).timezone = "UTC"
        source(setup, "system")
        zone = create_zone(setup, "raceradzone")
        day = create_mode(setup, "tag")
        setup.add_all(
            [
                ZoneSetpoint(
                    zone_id=zone.id, setpoint_mode_id=day.id, temperature_c=Decimal("21.0")
                ),
                SchedulePoint(
                    zone_id=zone.id, weekday=1, minute_of_day=1320, setpoint_mode_id=day.id
                ),
            ]
        )
        setup.commit()
        return zone.id


def _remove_committed_data(engine: Engine) -> None:
    """Die Sitzungs-Fixture rollt zurück; hier wurde festgeschrieben, also räumt der Test auf."""
    with Session(engine) as cleanup:
        for model in (ZoneOverride, SchedulePoint, ZoneSetpoint, AuditEvent, Zone, Setting):
            cleanup.execute(delete(model))
        cleanup.execute(delete(SetpointMode))
        cleanup.execute(delete(SensorFailureProfile))
        cleanup.commit()


def test_two_concurrent_dial_commands_leave_exactly_one_running_override(
    engine: Engine,
) -> None:
    if engine.dialect.name not in ("mysql", "mariadb"):
        pytest.skip(
            "braucht MariaDB (THERMOCTL_TEST_DATABASE_URL): SQLite hat keine Zeilensperren "
            "und kein REPEATABLE READ, die Absicherung gegen gleichzeitige Radbefehle "
            "ist dort nicht prüfbar"
        )
    zone_id = _committed_zone(engine)
    first = Session(engine)
    second = Session(engine)
    failures: list[BaseException] = []
    outcome: list[OperationalError] = []
    try:
        first_zone = first.get(Zone, zone_id)
        second_zone = second.get(Zone, zone_id)
        assert first_zone is not None and second_zone is not None
        # Both transactions have taken their snapshot before either commits -- what
        # `_execute_command` does with its `session.get(Zone)`.
        first.execute(select(ZoneOverride)).all()
        second.execute(select(ZoneOverride)).all()

        set_temporary_setpoint(first, first_zone, Decimal("22.0"), NOW, source="system")

        def turn_second() -> None:
            try:
                set_temporary_setpoint(
                    second, second_zone, Decimal("24.0"),
                    NOW + timedelta(seconds=5), source="system",
                )
                second.commit()
            except OperationalError as exc:
                # MariaDB with snapshot isolation (default since 11.6) refuses the
                # locking read of a row the other session committed since this
                # transaction's snapshot: 1020, and the transaction is over. The
                # adapter then repeats the command (`_process_mqtt_message`).
                outcome.append(exc)
                second.rollback()
            except BaseException as exc:  # noqa: BLE001 - reported to the main thread
                failures.append(exc)

        worker = threading.Thread(target=turn_second)
        worker.start()
        # With the lock the second command waits for the first one's commit; without it
        # it has long finished. Either way the first commits now.
        time.sleep(1.5)
        first.commit()
        worker.join(timeout=40)
        assert not worker.is_alive(), "der zweite Befehl hängt (Verklemmung?)"
        assert not failures, failures
        if outcome:
            assert outcome[0].orig is not None and outcome[0].orig.args[0] == 1020, outcome
            # The repeat in a fresh session -- what the adapter does -- now sees the first.
            with Session(engine) as retry:
                retry_zone = retry.get(Zone, zone_id)
                assert retry_zone is not None
                set_temporary_setpoint(
                    retry, retry_zone, Decimal("24.0"),
                    NOW + timedelta(seconds=5), source="system",
                )
                retry.commit()

        with Session(engine) as check:
            later = NOW + timedelta(minutes=1)
            rows = list(check.scalars(select(ZoneOverride).order_by(ZoneOverride.id)))
            running = [
                row for row in rows
                if row.cancelled_at is None
                and row.starts_at <= later
                and (row.ends_at is None or row.ends_at > later)
            ]
            assert len(rows) == 2
            assert [row.temperature_c for row in running] == [Decimal("24.0")]
            assert rows[0].cancelled_at == NOW + timedelta(seconds=5)
    finally:
        first.rollback()
        second.rollback()
        first.close()
        second.close()
        _remove_committed_data(engine)
