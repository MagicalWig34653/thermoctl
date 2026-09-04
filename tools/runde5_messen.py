# ruff: noqa: E501, S608
"""Reproduzierbare Messungen für Runde 5 des Komplettreviews.

Aufrufbeispiele stehen in ``docs/runde5-messbericht.md``. Das Werkzeug verändert
nur die explizit übergebene SQLite-Datei.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
import tracemalloc
from collections import Counter
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session

from tests.helpers import (
    capability,
    create_device,
    create_settings,
    create_zone,
    create_zone_state,
)
from thermoctl.db.base import Base
from thermoctl.db.models.state import ShadowDecision
from thermoctl.db.models.zone import ZoneSetpoint
from thermoctl.services.publishing import PublicationState
from thermoctl.services.shadow_run import cycle

NOW = datetime(2026, 9, 1, 12, 0)
MINUTES_PER_YEAR = 365 * 24 * 60


def _query_measurement(
    zone_count: int, history_per_zone: int, baseline_before_index_migration: bool
) -> dict[str, object]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    statements: Counter[str] = Counter()

    with Session(engine) as session:
        if baseline_before_index_migration:
            session.execute(text("DROP INDEX ix_shadow_decision_zone_decided_id"))
            session.execute(text("DROP INDEX ix_shadow_decision_retention"))
            session.execute(
                text(
                    "CREATE INDEX ix_shadow_decision_decided_at "
                    "ON shadow_decision (decided_at)"
                )
            )
            session.commit()
        shadow_indexes = list(
            session.scalars(
                text(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'index' AND tbl_name = 'shadow_decision' ORDER BY name"
                )
            )
        )
        expected_indexes = (
            ["ix_shadow_decision_decided_at"]
            if baseline_before_index_migration
            else [
                "ix_shadow_decision_retention",
                "ix_shadow_decision_zone_decided_id",
            ]
        )
        if shadow_indexes != expected_indexes:
            raise RuntimeError(
                "Unerwarteter Indexbestand für shadow_decision: "
                f"{shadow_indexes!r}"
            )

        settings = create_settings(session)
        for number in range(zone_count):
            zone = create_zone(session, f"zone-{number}")
            state = create_zone_state(session, zone)
            state.temperature_c = Decimal("19.0")
            session.add(
                ZoneSetpoint(
                    zone_id=zone.id,
                    setpoint_mode_id=settings.frost_protection_mode_id,
                    temperature_c=Decimal("16.0"),
                )
            )
            for history in range(history_per_zone):
                session.add(
                    ShadowDecision(
                        decided_at=NOW,
                        zone_id=zone.id,
                        temperature_c=Decimal("19.0"),
                        setpoint_c=Decimal("20.0"),
                        setpoint_reason="Messhistorie",
                        would_heat=bool(history % 2),
                        previous_would_heat=None,
                        outcome_code="messung",
                        reason="Messhistorie",
                    )
                )
        session.commit()

        def count_statement(
            _connection: object,
            _cursor: object,
            statement: str,
            _parameters: object,
            _context: object,
            _executemany: bool,
        ) -> None:
            normalized = " ".join(statement.split())
            statements[normalized] += 1

        event.listen(engine, "before_cursor_execute", count_statement)
        started = time.perf_counter()
        result = cycle(session, NOW)
        session.flush()
        elapsed = time.perf_counter() - started
        event.remove(engine, "before_cursor_execute", count_statement)

    selects = sum(
        count
        for statement, count in statements.items()
        if statement.lstrip().upper().startswith("SELECT")
    )
    writes = sum(
        count
        for statement, count in statements.items()
        if not statement.lstrip().upper().startswith("SELECT")
    )
    engine.dispose()
    return {
        "zones": zone_count,
        "history_per_zone": history_per_zone,
        "baseline_before_index_migration": baseline_before_index_migration,
        "composite_shadow_index_present": (
            "ix_shadow_decision_zone_decided_id" in shadow_indexes
        ),
        "shadow_indexes": shadow_indexes,
        "decisions": len(result),
        "statements": sum(statements.values()),
        "selects": selects,
        "writes": writes,
        "seconds": elapsed,
        "top_repeated": [
            {"count": count, "sql": statement} for statement, count in statements.most_common(12)
        ],
    }


def queries(args: argparse.Namespace) -> None:
    print(
        json.dumps(
            [
                _query_measurement(zones, args.history, args.baseline_before_index_migration)
                for zones in (1, 5, 20)
            ],
            indent=2,
        )
    )


def _insert_measurement(rows: int, with_old_index: bool) -> dict[str, object]:
    """Measure only insertion and commit, after constructing the final schema."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    # This command reproduces the round-5 comparison from before retention existed:
    # composite production index versus the then-removed single-column date index.
    # The new retention index would turn that historical comparison into a different
    # benchmark, so leave it out of this subcommand deliberately.
    with engine.begin() as connection:
        connection.execute(text("DROP INDEX ix_shadow_decision_retention"))
    if with_old_index:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE INDEX ix_shadow_decision_decided_at "
                    "ON shadow_decision (decided_at)"
                )
            )
    with engine.connect() as connection:
        indexes = list(
            connection.execute(
                text(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'index' AND tbl_name = 'shadow_decision' ORDER BY name"
                )
            ).scalars()
        )
    expected_indexes = ["ix_shadow_decision_zone_decided_id"]
    if with_old_index:
        expected_indexes.insert(0, "ix_shadow_decision_decided_at")
    if indexes != expected_indexes:
        raise RuntimeError(
            f"Unerwarteter Indexbestand für INSERT-Messung: {indexes!r}"
        )
    started = time.perf_counter()
    with engine.begin() as connection:
        connection.execute(
            text(
                "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n "
                "WHERE x < :rows) INSERT INTO shadow_decision "
                "(decided_at, zone_id, temperature_c, setpoint_c, setpoint_reason, "
                "would_heat, previous_would_heat, outcome_code, reason) "
                "SELECT '2026-09-01 12:00:00', ((x - 1) % 10) + 1, 19.0, 20.0, "
                "'Messhistorie', x % 2, NULL, 'messung', 'Messhistorie' FROM n"
            ),
            {"rows": rows},
        )
    elapsed = time.perf_counter() - started
    engine.dispose()
    return {
        "rows": rows,
        "with_old_index": with_old_index,
        "shadow_indexes": indexes,
        "seconds": elapsed,
        "rows_per_second": rows / elapsed,
    }


def inserts(args: argparse.Namespace) -> None:
    """Compare insert cost of the final schema with the removed single-column index."""
    measurements = {"without_old_index": [], "with_old_index": []}
    for _ in range(args.repetitions):
        measurements["without_old_index"].append(
            _insert_measurement(args.rows, with_old_index=False)
        )
        measurements["with_old_index"].append(
            _insert_measurement(args.rows, with_old_index=True)
        )
    without_seconds = statistics.median(
        entry["seconds"] for entry in measurements["without_old_index"]
    )
    with_seconds = statistics.median(
        entry["seconds"] for entry in measurements["with_old_index"]
    )
    print(
        json.dumps(
            {
                "rows": args.rows,
                "repetitions": args.repetitions,
                "median_seconds_without_old_index": without_seconds,
                "median_seconds_with_old_index": with_seconds,
                "median_time_reduction_percent": (
                    (with_seconds - without_seconds) / with_seconds * 100
                ),
                "measurements": measurements,
            },
            indent=2,
        )
    )


def cache(args: argparse.Namespace) -> None:
    """Measure cache cardinality and memory under accelerated identifier churn."""
    state = PublicationState()
    tracemalloc.start()
    before = tracemalloc.take_snapshot()
    for identifier in range(1, args.cycles * args.devices_per_cycle + 1):
        state.controller_values[identifier] = Decimal("20.0")
        state.valve_commands[identifier] = ("20.0", False, "suppressed")
        state.switch_commands[identifier] = (False, False, "suppressed")
    after = tracemalloc.take_snapshot()
    allocated = sum(stat.size_diff for stat in after.compare_to(before, "lineno"))
    print(
        json.dumps(
            {
                "cycles": args.cycles,
                "devices_per_cycle": args.devices_per_cycle,
                "entries_per_cache": args.cycles * args.devices_per_cycle,
                "allocated_bytes": allocated,
                "bytes_per_identifier_across_three_caches": allocated
                / (args.cycles * args.devices_per_cycle),
            },
            indent=2,
        )
    )


def _size_after_rows(path: Path, table: str, rows: int) -> int:
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as connection:
        connection.execute(text("PRAGMA journal_mode=DELETE"))
        connection.execute(text("PRAGMA foreign_keys=OFF"))
        connection.execute(text("CREATE TABLE seed(value INTEGER)"))
        connection.execute(text("INSERT INTO seed VALUES (1)"))
        if table == "measurement":
            connection.execute(
                text(
                    "CREATE TABLE target(id INTEGER PRIMARY KEY, device_id INTEGER NOT NULL, capability_id INTEGER NOT NULL, value_numeric NUMERIC(12,3), value_text VARCHAR(32), measured_at DATETIME NOT NULL, received_at DATETIME NOT NULL)"
                )
            )
            values = {"value": "1, 1, 20.125, NULL, '2026-01-01 00:00:00', '2026-01-01 00:00:01'"}
            connection.execute(
                text(
                    "CREATE INDEX ix_measurement_device_capability_measured "
                    "ON target (device_id, capability_id, measured_at)"
                )
            )
        elif table == "shadow_decision":
            connection.execute(
                text(
                    "CREATE TABLE target(id INTEGER PRIMARY KEY, decided_at DATETIME NOT NULL, zone_id INTEGER NOT NULL, temperature_c NUMERIC(5,2), setpoint_c NUMERIC(5,2), setpoint_reason VARCHAR(255) NOT NULL, would_heat BOOLEAN NOT NULL, previous_would_heat BOOLEAN, outcome_code VARCHAR(32) NOT NULL, reason VARCHAR(255) NOT NULL)"
                )
            )
            values = {
                "value": "'2026-01-01 00:00:00', 1, 19.5, 20.0, 'Zeitplan: Modus Tag ab 06:00', 1, 0, 'unter_sollwert', 'Temperatur liegt unter dem Sollwert.'"
            }
            connection.execute(
                text(
                    "CREATE INDEX ix_shadow_decision_zone_decided_id "
                    "ON target (zone_id, decided_at, id)"
                )
            )
        else:
            connection.execute(
                text(
                    "CREATE TABLE target(id INTEGER PRIMARY KEY, sent_at DATETIME NOT NULL, source_id INTEGER NOT NULL, zone_id INTEGER, zone_name VARCHAR(128) NOT NULL, device_id INTEGER, device_name VARCHAR(128) NOT NULL, command VARCHAR(64) NOT NULL, payload TEXT NOT NULL, outcome_id INTEGER NOT NULL, error TEXT, reason TEXT)"
                )
            )
            values = {
                "value": "'2026-01-01 00:00:00', 1, 1, 'Wohnzimmer', 1, 'Heizkörper Wohnzimmer', 'setpoint', '{}', 1, NULL, 'Sollwert aus Zeitplan'"
            }
            connection.execute(text("CREATE INDEX ix_device_command_sent_at ON target (sent_at)"))
        # All fragments above are fixed literals selected by this tool, never input.
        statement = (  # noqa: S608
            "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n "
            "WHERE x < :rows) INSERT INTO target SELECT NULL, "
            f"{values['value']} FROM n"
        )
        connection.execute(text(statement), {"rows": rows})
    engine.dispose()
    return path.stat().st_size


def growth(args: argparse.Namespace) -> None:
    results: dict[str, object] = {"minutes_per_year": MINUTES_PER_YEAR, "zones": args.zones}
    rows = MINUTES_PER_YEAR * args.zones
    results["rows_per_table_per_year"] = rows
    sizes: dict[str, object] = {}
    for table in ("measurement", "shadow_decision", "device_command"):
        path = args.directory / f"row-size-{table}.sqlite"
        if path.exists():
            path.unlink()
        size = _size_after_rows(path, table, args.sample_rows)
        empty = 8192
        per_row = (size - empty) / args.sample_rows
        sizes[table] = {
            "sample_rows": args.sample_rows,
            "file_bytes": size,
            "bytes_per_row": per_row,
            "projected_year_bytes": per_row * rows,
        }
    results["sizes"] = sizes
    print(json.dumps(results, indent=2))


def build_year(args: argparse.Namespace) -> None:
    """Populate an already migrated SQLite database with one value/minute/zone."""
    engine = create_engine(f"sqlite:///{args.database}")
    with Session(engine) as session:
        create_settings(session)
        temperature = capability(session, "temperature")
        device_ids: list[int] = []
        for number in range(args.zones):
            zone = create_zone(session, f"year-zone-{number}")
            create_zone_state(session, zone)
            device = create_device(session, f"year-device-{number}")
            zone.temperature_source_device_id = device.id
            device_ids.append(device.id)
        session.commit()
        started = time.perf_counter()
        values = ",".join(f"({identifier})" for identifier in device_ids)
        statement = text(  # noqa: S608
            "WITH RECURSIVE minute(n) AS ("
            "SELECT 0 UNION ALL SELECT n+1 FROM minute WHERE n < :last_minute), "
            f"devices(id) AS (VALUES {values}) "
            "INSERT INTO measurement "
            "(device_id, capability_id, value_numeric, value_text, measured_at, received_at) "
            "SELECT devices.id, :capability, 20.125, NULL, "
            "datetime('2025-09-01 00:00:00', '+' || minute.n || ' minutes'), "
            "datetime('2025-09-01 00:00:01', '+' || minute.n || ' minutes') "
            "FROM minute CROSS JOIN devices"
        )
        session.execute(
            statement,
            {"last_minute": MINUTES_PER_YEAR - 1, "capability": temperature.id},
        )
        session.commit()
        elapsed = time.perf_counter() - started
    engine.dispose()
    print(
        json.dumps(
            {
                "database": str(args.database),
                "rows": MINUTES_PER_YEAR * args.zones,
                "seconds": elapsed,
                "file_bytes": args.database.stat().st_size,
            },
            indent=2,
        )
    )


def main(arguments: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(required=True)
    query_parser = subparsers.add_parser("queries")
    query_parser.add_argument("--history", type=int, default=30)
    query_parser.add_argument(
        "--baseline-vor-indexmigration",
        dest="baseline_before_index_migration",
        action="store_true",
    )
    query_parser.set_defaults(run=queries)
    insert_parser = subparsers.add_parser("inserts")
    insert_parser.add_argument("--rows", type=int, default=100000)
    insert_parser.add_argument("--repetitions", type=int, default=5)
    insert_parser.set_defaults(run=inserts)
    cache_parser = subparsers.add_parser("cache")
    cache_parser.add_argument("--cycles", type=int, default=1440)
    cache_parser.add_argument("--devices-per-cycle", type=int, default=10)
    cache_parser.set_defaults(run=cache)
    growth_parser = subparsers.add_parser("growth")
    growth_parser.add_argument("--zones", type=int, default=10)
    growth_parser.add_argument("--sample-rows", type=int, default=100000)
    growth_parser.add_argument("--directory", type=Path, required=True)
    growth_parser.set_defaults(run=growth)
    year_parser = subparsers.add_parser("build-year")
    year_parser.add_argument("--database", type=Path, required=True)
    year_parser.add_argument("--zones", type=int, default=10)
    year_parser.set_defaults(run=build_year)
    args = parser.parse_args(arguments)
    if hasattr(args, "directory"):
        args.directory.mkdir(parents=True, exist_ok=True)
    args.run(args)


if __name__ == "__main__":
    main()
