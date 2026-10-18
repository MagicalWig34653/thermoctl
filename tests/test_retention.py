import json
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from tests.helpers import (
    create_device,
    create_device_command,
    create_settings,
    create_zone,
    create_zone_state,
    source,
)
from thermoctl.db.base import utcnow
from thermoctl.db.models.change_log import ChangeLog, ChangeLogField
from thermoctl.db.models.lookup import DeviceCapability
from thermoctl.db.models.measurement import Measurement
from thermoctl.db.models.oauth import OAuthAuthorizationRequest
from thermoctl.db.models.sensor_failure import ActuatorDecision, SensorFailureSourceComparison
from thermoctl.db.models.state import DeviceCommand, ShadowDecision
from thermoctl.domain.oauth import register_client, start_request
from thermoctl.services.retention import (
    COMMAND_LOG_ROW_OVERHEAD_BYTES,
    delete_expired_oauth,
    delete_old_actuator_decisions,
    delete_old_changes,
    delete_old_device_commands,
    delete_old_measurements,
    delete_old_sensor_failure_source_comparisons,
    delete_old_shadow_decisions,
    estimate_device_command_bytes,
    trim_device_commands_to_size,
)
from thermoctl.services.shadow_run import cycle

NOW = datetime(2026, 8, 29, 12, 0)
DATA_PATH = Path(__file__).parent / "daten" / "anlage-beispiele.json"


def _inventory(session: Session, age_days: list[int]) -> None:
    device_name = json.loads(DATA_PATH.read_text(encoding="utf-8"))["geraete"][0]
    device = create_device(session, device_name)
    capability = DeviceCapability(code="aufbewahrung", label="Aufbewahrung")
    session.add(capability)
    session.flush()
    for age in age_days:
        moment = NOW - timedelta(days=age)
        session.add(
            Measurement(
                device_id=device.id,
                capability_id=capability.id,
                value_numeric=Decimal("1"),
                measured_at=moment,
                received_at=moment,
            )
        )
    session.flush()


def test_old_measurements_are_deleted_completely_in_blocks(session: Session) -> None:
    settings = create_settings(session)
    settings.measurement_retention_days = 30
    _inventory(session, [31, 32, 33, 29])

    assert delete_old_measurements(session, NOW, batch_size=2) == 3
    assert [m.measured_at for m in session.query(Measurement)] == [NOW - timedelta(days=29)]


def test_a_retention_value_of_zero_keeps_the_entire_history(session: Session) -> None:
    settings = create_settings(session)
    settings.measurement_retention_days = 0
    _inventory(session, [100])

    assert delete_old_measurements(session, NOW) == 0
    assert session.query(Measurement).count() == 1


def test_block_size_must_be_positive(session: Session) -> None:
    with pytest.raises(ValueError, match="größer als null"):
        delete_old_measurements(session, NOW, batch_size=0)


def _shadow_inventory(session: Session, zone_id: int, age_days: list[int]) -> None:
    for age in age_days:
        session.add(
            ShadowDecision(
                decided_at=NOW - timedelta(days=age),
                zone_id=zone_id,
                temperature_c=Decimal("20.0"),
                setpoint_c=Decimal("21.0"),
                setpoint_reason="Zeitplan",
                would_heat=False,
                previous_would_heat=False,
                outcome_code="aus",
                reason="Test",
            )
        )
    session.flush()


def test_old_shadow_decisions_are_deleted_in_real_blocks_at_an_exclusive_boundary(
    session: Session,
) -> None:
    settings = create_settings(session)
    settings.shadow_decision_retention_days = 365
    zone = create_zone(session, "schatten-aufbewahrung")
    _shadow_inventory(session, zone.id, [367, 366, 365, 364, 368])

    deletes = 0

    def count_deletes(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        nonlocal deletes
        if statement.lstrip().upper().startswith("DELETE FROM SHADOW_DECISION"):
            deletes += 1

    engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", count_deletes)
    try:
        assert delete_old_shadow_decisions(session, NOW, batch_size=2) == 3
    finally:
        event.remove(engine, "before_cursor_execute", count_deletes)

    assert deletes == 2
    remaining = session.query(ShadowDecision).order_by(ShadowDecision.id)
    assert [row.decided_at for row in remaining] == [
        NOW - timedelta(days=365),
        NOW - timedelta(days=364),
    ]


def test_shadow_retention_keeps_the_valve_protection_marker_authoritative(
    session: Session,
) -> None:
    settings = create_settings(session)
    settings.shadow_decision_retention_days = 365
    zone = create_zone(session, "ventilschutz-marker")
    zone.created_at = NOW - timedelta(days=90)
    zone.valve_protection_enabled = True
    zone.min_on_seconds = 0
    zone.min_off_seconds = 0
    state = create_zone_state(session, zone)
    state.temperature_c = Decimal("21.0")
    state.measured_at = NOW
    state.updated_at = NOW
    state.last_regular_heat_at = NOW - timedelta(days=29)
    state.regular_heat_history_compacted = True
    _shadow_inventory(session, zone.id, [366])

    before = cycle(session, NOW)[0]
    assert before.outcome_code != "ventilschutz"
    assert delete_old_shadow_decisions(session, NOW) == 1
    assert state.last_regular_heat_at == NOW - timedelta(days=29)

    after = cycle(session, NOW + timedelta(minutes=1))[0]
    assert after.outcome_code != "ventilschutz"
    assert state.last_regular_heat_at == NOW - timedelta(days=29)


def test_shadow_block_size_must_be_positive(session: Session) -> None:
    with pytest.raises(ValueError, match="größer als null"):
        delete_old_shadow_decisions(session, NOW, batch_size=-1)


def _comparison_inventory(
    session: Session, zone_id: int, device_id: int, age_days: list[int]
) -> None:
    for age in age_days:
        moment = NOW - timedelta(days=age)
        session.add(
            SensorFailureSourceComparison(
                zone_id=zone_id,
                zone_name="Vergleichszone",
                device_id=device_id,
                device_name="Vergleichsgerät",
                measured_at=moment,
                wall_probe_c=Decimal("20.00"),
                raw_c=Decimal("19.50"),
                corrected_c=Decimal("19.50"),
                echo=False,
                usable=True,
            )
        )
    session.flush()


def test_old_source_comparisons_are_deleted_using_the_shadow_decision_retention(
    session: Session,
) -> None:
    settings = create_settings(session)
    settings.shadow_decision_retention_days = 365
    zone = create_zone(session, "quellenvergleich-aufbewahrung")
    device = create_device(session, json.loads(DATA_PATH.read_text(encoding="utf-8"))["geraete"][0])
    _comparison_inventory(session, zone.id, device.id, [367, 366, 364])

    assert delete_old_sensor_failure_source_comparisons(session, NOW, batch_size=2) == 2
    remaining = session.query(SensorFailureSourceComparison).all()
    assert [row.measured_at for row in remaining] == [NOW - timedelta(days=364)]


def test_source_comparison_block_size_must_be_positive(session: Session) -> None:
    with pytest.raises(ValueError, match="größer als null"):
        delete_old_sensor_failure_source_comparisons(session, NOW, batch_size=0)


def _actuator_decision_inventory(session: Session, age_days: list[int]) -> None:
    for age in age_days:
        moment = NOW - timedelta(days=age)
        session.add(
            ActuatorDecision(
                zone_name="Taktzone",
                device_name="Ventil",
                decided_at=moment,
                action="switch_on",
                reason_code="festtakt",
                reason="Takt",
                simulated=True,
                profile_version=1,
            )
        )
    session.flush()


def test_old_actuator_decisions_are_deleted_using_the_shadow_decision_retention(
    session: Session,
) -> None:
    settings = create_settings(session)
    settings.shadow_decision_retention_days = 365
    _actuator_decision_inventory(session, [367, 366, 364])

    assert delete_old_actuator_decisions(session, NOW, batch_size=2) == 2
    remaining = session.query(ActuatorDecision).all()
    assert [row.decided_at for row in remaining] == [NOW - timedelta(days=364)]


def test_actuator_decision_block_size_must_be_positive(session: Session) -> None:
    with pytest.raises(ValueError, match="größer als null"):
        delete_old_actuator_decisions(session, NOW, batch_size=0)


def test_expired_oauth_rows_are_removed_by_the_retention_run(session: Session) -> None:
    client = register_client(session, "Aufräumen", ["https://client.example/cb"])
    start_request(
        session,
        client,
        "https://client.example/cb",
        "x" * 43,
        "https://mcp.example",
        None,
        "https://mcp.example",
    )
    frische = session.query(OAuthAuthorizationRequest).one()
    alt = OAuthAuthorizationRequest(
        request_hash="h" * 64,
        client_id=client.id,
        redirect_uri="https://client.example/cb",
        code_challenge="x" * 43,
        resource="https://mcp.example",
        expires_at=utcnow() - timedelta(days=3),
    )
    session.add(alt)
    session.flush()

    assert delete_expired_oauth(session) == 1
    assert session.query(OAuthAuthorizationRequest).all() == [frische]


def _change(session: Session, age_days: int, source_id: int) -> ChangeLog:
    entry = ChangeLog(
        occurred_at=NOW - timedelta(days=age_days),
        source_id=source_id,
        change_type="control_parameter",
        summary=f"vor {age_days} Tagen",
        revertible=True,
    )
    session.add(entry)
    session.flush()
    session.add(ChangeLogField(change_id=entry.id, field="hysteresis_k", after_value="0.5"))
    session.flush()
    return entry


def test_old_changes_are_deleted_with_their_fields_and_young_ones_stay(
    session: Session,
) -> None:
    mcp = source(session, "mcp")
    old = [_change(session, age, mcp.id) for age in (366, 400, 900)]
    young = [_change(session, age, mcp.id) for age in (364, 1)]
    boundary = _change(session, 365, mcp.id)
    # Eine alte Rücknahme zeigt auf einen jungen Eintrag: Er bleibt, nur der Verweis fällt.
    young[0].reverted_by_id = old[0].id
    session.flush()

    deleted = delete_old_changes(session, NOW, batch_size=2)

    assert deleted == 3
    session.expire_all()
    remaining = {e.id for e in session.query(ChangeLog)}
    assert remaining == {young[0].id, young[1].id, boundary.id}
    assert session.query(ChangeLogField).count() == 3
    assert session.get(ChangeLog, young[0].id).reverted_by_id is None  # type: ignore[union-attr]


def test_changes_keep_days_is_configurable_and_validated(session: Session) -> None:
    mcp = source(session, "mcp")
    _change(session, 40, mcp.id)
    _change(session, 10, mcp.id)

    assert delete_old_changes(session, NOW, keep_days=30) == 1
    assert session.query(ChangeLog).count() == 1
    with pytest.raises(ValueError, match="keep_days"):
        delete_old_changes(session, NOW, keep_days=0)
    with pytest.raises(ValueError, match="Blockgröße"):
        delete_old_changes(session, NOW, batch_size=0)


# --- Schaltprotokoll: Alter und Größe -----------------------------------------


def _commands(session: Session, ages_days: list[int], payload: str = "{}") -> None:
    zone = create_zone(session, "protokollzone")
    device = create_device(session, "protokollgeraet")
    for age in ages_days:
        create_device_command(session, zone, device, at=NOW - timedelta(days=age), payload=payload)


def _remaining_ages(session: Session) -> list[int]:
    rows = session.query(DeviceCommand.sent_at).order_by(DeviceCommand.sent_at.desc()).all()
    return [(NOW - sent_at).days for (sent_at,) in rows]


def test_the_command_log_defaults_are_three_years_and_two_gib(session: Session) -> None:
    settings = create_settings(session)
    session.flush()
    session.refresh(settings)
    assert settings.command_log_retention_days == 1095
    assert settings.command_log_max_mb == 2048


def test_old_device_commands_are_deleted_in_blocks_at_an_exclusive_boundary(
    session: Session,
) -> None:
    settings = create_settings(session)
    settings.command_log_retention_days = 30
    _commands(session, [29, 30, 31, 40, 500])

    # Genau 30 Tage alt bleibt (Grenze exklusiv); drei Blöcke zu je zwei Zeilen.
    assert delete_old_device_commands(session, NOW, batch_size=2) == 3
    assert _remaining_ages(session) == [29, 30]


def test_device_commands_within_the_age_limit_are_untouched(session: Session) -> None:
    settings = create_settings(session)
    settings.command_log_retention_days = 1095
    _commands(session, [0, 1, 1094])

    assert delete_old_device_commands(session, NOW) == 0
    assert session.query(DeviceCommand).count() == 3


def test_device_command_block_size_must_be_positive(session: Session) -> None:
    with pytest.raises(ValueError, match="größer als null"):
        delete_old_device_commands(session, NOW, batch_size=0)
    with pytest.raises(ValueError, match="größer als null"):
        trim_device_commands_to_size(session, batch_size=0)


def test_the_size_estimate_counts_text_lengths_plus_a_constant_per_row(
    session: Session,
) -> None:
    create_settings(session)
    assert estimate_device_command_bytes(session) == 0

    zone = create_zone(session, "z")
    device = create_device(session, "g")
    entry = create_device_command(session, zone, device, payload="x" * 1000)
    entry.error = "fehler"
    entry.reason_code = "abc"
    session.flush()

    text = (
        entry.zone_name,
        entry.device_name,
        entry.command,
        entry.payload,
        entry.error,
        entry.reason,
        entry.reason_code,
    )
    expected = COMMAND_LOG_ROW_OVERHEAD_BYTES + sum(len(t) for t in text if t)
    assert estimate_device_command_bytes(session) == expected


def test_nothing_is_trimmed_while_the_log_is_below_the_size_limit(session: Session) -> None:
    settings = create_settings(session)
    settings.command_log_max_mb = 10
    _commands(session, [3, 2, 1], payload="x" * 1000)

    assert trim_device_commands_to_size(session) == 0
    assert session.query(DeviceCommand).count() == 3


def test_an_empty_log_is_not_trimmed(session: Session) -> None:
    settings = create_settings(session)
    settings.command_log_max_mb = 10
    assert trim_device_commands_to_size(session) == 0


def test_only_the_oldest_commands_fall_away_until_the_log_fits(session: Session) -> None:
    settings = create_settings(session)
    settings.command_log_max_mb = 10
    limit = 10 * 1024 * 1024
    payload = "x" * 60_000  # bleibt unter der TEXT-Grenze von MariaDB
    # Neueste zuerst eingefügt: das Alter entscheidet, nicht die Id.
    _commands(session, list(range(1, 201)), payload=payload)
    total = estimate_device_command_bytes(session)
    assert total > limit
    row_size = total // 200

    removed = trim_device_commands_to_size(session, batch_size=7)

    assert removed > 0
    remaining = _remaining_ages(session)
    assert len(remaining) == 200 - removed
    # Es bleiben genau die jüngsten Einträge.
    assert remaining == list(range(1, 201 - removed))
    assert estimate_device_command_bytes(session) <= limit
    # Und es wurde nicht mehr gelöscht als nötig.
    assert estimate_device_command_bytes(session) + row_size > limit


def test_a_log_exactly_at_the_size_limit_is_kept(session: Session) -> None:
    settings = create_settings(session)
    settings.command_log_max_mb = 10
    limit = 10 * 1024 * 1024
    # Payloads bleiben unter der TEXT-Grenze von MariaDB (65.535 Byte).
    _commands(session, list(range(2, 176)), payload="x" * 60_000)
    first = session.query(DeviceCommand).first()
    assert first is not None
    gap = limit - estimate_device_command_bytes(session)
    filler = gap - COMMAND_LOG_ROW_OVERHEAD_BYTES - 3  # Namen "a", "b", Befehl "c"
    assert 0 < filler < 60_000
    session.add(
        DeviceCommand(
            sent_at=NOW - timedelta(days=1),
            source_id=first.source_id,
            zone_name="a",
            device_name="b",
            command="c",
            payload="x" * filler,
            outcome_id=first.outcome_id,
        )
    )
    session.flush()
    # Genau auf der Grenze: Überhang null, nichts fällt weg.
    assert estimate_device_command_bytes(session) == limit
    assert trim_device_commands_to_size(session) == 0
    assert session.query(DeviceCommand).count() == 175

    # Ein einziges Byte darüber: genau der älteste Eintrag fällt weg.
    settings.command_log_max_mb = 10
    first.payload = first.payload + "x"
    session.flush()
    assert trim_device_commands_to_size(session) == 1
    assert session.query(DeviceCommand).count() == 174
