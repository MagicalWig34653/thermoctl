import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from thermoctl.db.base import Base
from tools import runde5_messen


def test_queries_report_the_real_shadow_index_state(
    capsys: pytest.CaptureFixture[str],
) -> None:
    runde5_messen.main(["queries", "--history", "1"])
    production = json.loads(capsys.readouterr().out)
    assert all(entry["composite_shadow_index_present"] for entry in production)
    assert all(
        entry["shadow_indexes"]
        == ["ix_shadow_decision_retention", "ix_shadow_decision_zone_decided_id"]
        for entry in production
    )

    runde5_messen.main(
        ["queries", "--history", "1", "--baseline-vor-indexmigration"]
    )
    baseline = json.loads(capsys.readouterr().out)
    assert all(not entry["composite_shadow_index_present"] for entry in baseline)
    assert all(
        entry["shadow_indexes"] == ["ix_shadow_decision_decided_at"]
        for entry in baseline
    )


def test_the_other_measurement_subcommands_run_with_tiny_samples(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(runde5_messen, "MINUTES_PER_YEAR", 2)
    database = tmp_path / "year.sqlite"
    engine = create_engine(f"sqlite:///{database}")
    Base.metadata.create_all(engine)
    engine.dispose()

    commands = [
        ["inserts", "--rows", "2", "--repetitions", "1"],
        ["cache", "--cycles", "1", "--devices-per-cycle", "1"],
        ["growth", "--sample-rows", "2", "--directory", str(tmp_path / "growth")],
        ["build-year", "--database", str(database), "--zones", "1"],
    ]
    for command in commands:
        runde5_messen.main(command)
        assert json.loads(capsys.readouterr().out)
