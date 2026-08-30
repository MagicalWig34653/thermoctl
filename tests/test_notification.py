import asyncio
import json
import logging
from urllib.request import Request

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.helpers import create_zone, create_zone_state, sensor_status_of, source
from thermoctl import app as app_modul
from thermoctl.config import Settings
from thermoctl.db.models.operations import AuditEvent
from thermoctl.domain.fault_notice import FaultNotice
from thermoctl.integrations import notification


class _Response:
    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *args: object) -> None:
        return None


def _settings(**values: str) -> Settings:
    return Settings(
        _env_file=None,
        database_url="sqlite://",
        secret_key="s" * 32,
        **values,
    )


NOTICE = FaultNotice(
    key="sensor:1",
    severity="stoerung",
    title="Sensorstoerung",
    text="Keine aktuellen Werte.",
)


def test_without_a_webhook_no_http_call_happens_at_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Request] = []
    monkeypatch.setattr(
        notification,
        "urlopen",
        lambda request, timeout: calls.append(request),
    )

    asyncio.run(notification.send(_settings(), NOTICE))

    assert calls == []


def test_webhook_sends_the_expected_payload_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[Request, int]] = []

    def _open(request: Request, timeout: int) -> _Response:
        calls.append((request, timeout))
        return _Response()

    monkeypatch.setattr(notification, "urlopen", _open)
    asyncio.run(
        notification.send(
            _settings(notify_webhook="https://example.invalid/meldung"), NOTICE
        )
    )

    assert len(calls) == 1
    request, timeout = calls[0]
    assert timeout == 10
    assert request.full_url == "https://example.invalid/meldung"
    assert request.get_method() == "POST"
    assert json.loads(request.data or b"") == {
        "schluessel": "sensor:1",
        "schwere": "stoerung",
        "titel": "Sensorstoerung",
        "text": "Keine aktuellen Werte.",
    }


def test_an_error_does_not_stop_the_caller_and_the_token_stays_out_of_the_log(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    token = "auffaelliges-webhook-geheimnis"
    seen: list[str | None] = []

    def _broken(request: Request, timeout: int) -> _Response:
        seen.append(request.get_header("Authorization"))
        raise OSError("Gegenstelle nicht erreichbar")

    monkeypatch.setattr(notification, "urlopen", _broken)
    caplog.set_level(logging.WARNING)

    asyncio.run(
        notification.send(
            _settings(
                notify_webhook="https://example.invalid/meldung",
                notify_webhook_token=token,
            ),
            NOTICE,
        )
    )
    afterward = True

    assert afterward is True
    assert seen == [f"Bearer {token}"]
    assert "konnte nicht" in caplog.text
    assert token not in caplog.text


def test_a_sensor_notice_gets_an_audit_entry_with_source_system(
    session: Session,
) -> None:
    source(session, "system")
    zone = create_zone(session, "Meldezone")
    state = create_zone_state(session, zone)
    previous = app_modul._sensor_states(session)
    state.sensor_status_id = sensor_status_of(session, "veraltet").id

    notices = app_modul._sensor_notices(session, previous)

    entry = session.scalar(select(AuditEvent))
    assert len(notices) == 1
    assert entry is not None
    assert entry.action == "notification.sent"
    assert entry.object_id == f"sensor:{zone.id}"
    assert entry.source_id == source(session, "system").id
