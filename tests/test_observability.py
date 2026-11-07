import asyncio
import logging
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from thermoctl.config import Settings
from thermoctl.db.engine import create_engine_from_settings
from thermoctl.observability import SLOW_REQUEST_MS, monitor_loop_lag


def _records(caplog: pytest.LogCaptureFixture, message: str) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.getMessage() == message]


def test_request_logs_duration_and_route_template_without_query(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="thermoctl.observability"):
        client.get("/healthz?secret=abc")
    (record,) = _records(caplog, "Anfrage")
    assert record.path == "/healthz"  # type: ignore[attr-defined]
    assert record.status == 200  # type: ignore[attr-defined]
    assert isinstance(record.dauer_ms, int)  # type: ignore[attr-defined]
    assert "abc" not in str(record.__dict__)


def test_kiosk_token_never_reaches_the_log(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="thermoctl.observability"):
        client.get("/kiosk/geheimes-token-123", follow_redirects=False)
    (record,) = _records(caplog, "Anfrage")
    assert record.path == "/kiosk/{plaintext}"  # type: ignore[attr-defined]
    assert "geheimes-token-123" not in str(record.__dict__)


def test_unrouted_path_is_cut_to_first_segment(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="thermoctl.observability"):
        client.get("/zufall/token-xyz")
    (record,) = _records(caplog, "Anfrage")
    assert record.path == "/zufall"  # type: ignore[attr-defined]


def test_slow_request_is_a_warning(
    client: TestClient, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("thermoctl.observability.SLOW_REQUEST_MS", -1)
    with caplog.at_level(logging.INFO, logger="thermoctl.observability"):
        client.get("/healthz")
    (record,) = _records(caplog, "Langsame Anfrage")
    assert record.levelno == logging.WARNING
    assert SLOW_REQUEST_MS == 1000


def _run_monitor(block_s: float, threshold: float) -> None:
    async def scenario() -> None:
        task = asyncio.create_task(monitor_loop_lag(None, interval=0.01, threshold=threshold))
        await asyncio.sleep(0.03)
        time.sleep(block_s)  # blockiert die Schleife absichtlich
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())


def test_loop_monitor_reports_artificial_block(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="thermoctl.observability"):
        _run_monitor(0.2, 0.05)
    records = _records(caplog, "Ereignisschleife blockiert")
    assert records
    assert records[0].verzoegerung_ms >= 100  # type: ignore[attr-defined]


def test_loop_monitor_is_silent_without_block(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="thermoctl.observability"):
        _run_monitor(0.0, 0.5)
    assert not _records(caplog, "Ereignisschleife blockiert")


def test_pool_settings_reach_server_databases(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_create_engine(url: str, **kwargs: object):  # type: ignore[no-untyped-def]
        captured.update(kwargs)
        return create_engine("sqlite://")

    monkeypatch.setattr("thermoctl.db.engine.create_engine", fake_create_engine)
    settings = Settings(
        _env_file=None,
        database_url="mysql+pymysql://u:p@localhost/db",
        secret_key="t" * 32,
        db_pool_size=7,
        db_max_overflow=3,
        db_pool_timeout=11,
        db_pool_recycle=99,
    )
    create_engine_from_settings(settings)
    assert (captured["pool_size"], captured["max_overflow"]) == (7, 3)
    assert (captured["pool_timeout"], captured["pool_recycle"]) == (11, 99)


def test_pool_settings_are_not_passed_to_sqlite(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    real = create_engine

    def spy(url: str, **kwargs: object):  # type: ignore[no-untyped-def]
        captured.update(kwargs)
        return real(url, **kwargs)

    monkeypatch.setattr("thermoctl.db.engine.create_engine", spy)
    settings = Settings(_env_file=None, database_url="sqlite://", secret_key="t" * 32)
    create_engine_from_settings(settings)
    assert "pool_size" not in captured
