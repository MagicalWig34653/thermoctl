import asyncio
import contextlib
import json
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from tests.helpers import (
    create_settings,
    create_zone,
    create_zone_state,
    sensor_status_of,
    source,
)
from thermoctl import app as app_modul
from thermoctl.config import Settings
from thermoctl.db.base import Base
from thermoctl.db.engine import session_factory
from thermoctl.db.models.operations import AuditEvent, Setting
from thermoctl.domain.fault_notice import NOTICE_KIND_SENSOR_FAULT, FaultNotice
from thermoctl.integrations import notification


class _Response:
    def __init__(self, status: int = 200) -> None:
        self.status = status

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *args: object) -> None:
        return None


class _Opener:
    """Stands in for `notification._opener` — the object `_send_webhook` actually
    calls `.open()` on since the redirect fix, not `urlopen` directly."""

    def __init__(self, open_fn: Any) -> None:
        self._open_fn = open_fn

    def open(self, request: Request, timeout: int) -> _Response:
        return self._open_fn(request, timeout)


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
    kind=NOTICE_KIND_SENSOR_FAULT,
)


def test_without_a_webhook_no_http_call_happens_at_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Request] = []
    monkeypatch.setattr(
        notification,
        "_opener",
        _Opener(lambda request, timeout: calls.append(request)),
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

    monkeypatch.setattr(notification, "_opener", _Opener(_open))
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

    monkeypatch.setattr(notification, "_opener", _Opener(_broken))
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


def test_the_opener_carries_the_no_redirect_handler() -> None:
    """`_send_webhook` goes through `_opener`, not the stdlib's default one -- this
    is what makes that matter: the default `HTTPRedirectHandler` follows a redirect
    and keeps `Authorization` across hosts, ours refuses to build the follow-up
    request at all. See `test_offener_webhook_redirect_wird_jetzt_abgelehnt` below
    for the behaviour this wiring produces."""
    assert any(
        isinstance(handler, notification._NoRedirectHandler)
        for handler in notification._opener.handlers
    )


def test_offener_webhook_redirect_wird_jetzt_abgelehnt() -> None:
    """War NOCH NICHT BEHOBEN (siehe Verlauf dieses Tests) -- jetzt behoben.

    Der Standard-`HTTPRedirectHandler` haette aus dieser Antwort eine neue Anfrage
    an `http://127.0.0.1:8080/intern` gebaut und dabei `Authorization` mitgenommen.
    `notification._NoRedirectHandler` baut diese Anfrage gar nicht erst -- der
    Header verlaesst den urspruenglichen Ursprung damit nie, gleich wohin
    umgeleitet wird.
    """
    original = Request(
        "https://webhook.example/meldung",
        data=b"{}",
        headers={"Authorization": "Bearer webhook-geheimnis"},
        method="POST",
    )

    with pytest.raises(HTTPError, match="Webhook-Weiterleitung abgelehnt"):
        notification._NoRedirectHandler().redirect_request(
            original, None, 302, "Found", {}, "http://127.0.0.1:8080/intern"
        )


def test_send_test_without_a_webhook_reports_that_plainly_instead_of_raising() -> None:
    result = asyncio.run(notification.send_test(_settings()))

    assert result.ok is False
    assert result.status_code is None
    assert result.error == "Kein Webhook hinterlegt."


def test_send_test_reports_the_status_code_of_a_successful_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Request] = []

    def _open(request: Request, timeout: int) -> _Response:
        calls.append(request)
        return _Response(status=200)

    monkeypatch.setattr(notification, "_opener", _Opener(_open))

    result = asyncio.run(
        notification.send_test(_settings(notify_webhook="https://example.invalid/meldung"))
    )

    assert len(calls) == 1
    assert json.loads(calls[0].data or b"")["schwere"] == "test"
    assert result.ok is True
    assert result.status_code == 200
    assert result.error is None
    assert result.duration_seconds >= 0.0


def test_send_test_reports_the_status_code_of_a_failed_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _open(request: Request, timeout: int) -> _Response:
        raise HTTPError(request.full_url, 404, "Not Found", None, None)  # type: ignore[arg-type]

    monkeypatch.setattr(notification, "_opener", _Opener(_open))

    result = asyncio.run(
        notification.send_test(_settings(notify_webhook="https://example.invalid/meldung"))
    )

    assert result.ok is False
    assert result.status_code == 404
    assert result.error == "Die Gegenstelle antwortete mit Status 404."


def test_send_test_reports_an_unreachable_host_without_a_status_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from urllib.error import URLError

    def _open(request: Request, timeout: int) -> _Response:
        raise URLError("Name oder Dienst nicht bekannt")

    monkeypatch.setattr(notification, "_opener", _Opener(_open))

    result = asyncio.run(
        notification.send_test(_settings(notify_webhook="https://example.invalid/meldung"))
    )

    assert result.ok is False
    assert result.status_code is None
    assert "nicht bekannt" in (result.error or "")


def test_send_test_scrubs_the_token_and_shortens_an_unfamiliar_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "auffaelliges-webhook-geheimnis"

    def _open(request: Request, timeout: int) -> _Response:
        raise OSError(f"Verbindung fehlgeschlagen, Token war {token}" + "x" * 400)

    monkeypatch.setattr(notification, "_opener", _Opener(_open))

    result = asyncio.run(
        notification.send_test(
            _settings(
                notify_webhook="https://example.invalid/meldung",
                notify_webhook_token=token,
            )
        )
    )

    assert result.ok is False
    assert result.error is not None
    assert token not in result.error
    assert len(result.error) <= 200


def test_a_sensor_notice_gets_an_audit_entry_with_source_system(
    session: Session,
) -> None:
    setting_row = create_settings(session)
    source(session, "system")
    zone = create_zone(session, "Meldezone")
    state = create_zone_state(session, zone)
    previous = app_modul._sensor_states(session)
    state.sensor_status_id = sensor_status_of(session, "veraltet").id

    notices = app_modul._sensor_notices(session, previous, setting_row)

    entry = session.scalar(select(AuditEvent))
    assert len(notices) == 1
    assert entry is not None
    assert entry.action == "notification.sent"
    assert entry.object_id == f"sensor:{zone.id}"
    assert entry.source_id == source(session, "system").id


def _own_database(tmp_path: Path, name: str) -> sessionmaker[Session]:
    """A dedicated, named SQLite file per test -- `deliver()` opens its own session
    via a `session_factory`, which the transaction-isolated `session` fixture used
    elsewhere in this file does not provide."""
    engine = create_engine(f"sqlite:///{tmp_path}/{name}.db", future=True)
    Base.metadata.create_all(engine)
    return session_factory(engine)


def test_deliver_records_a_successful_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fabrik = _own_database(tmp_path, "deliver-erfolg")
    with fabrik() as http_session:
        create_settings(http_session)
        http_session.commit()

    def _open(request: Request, timeout: int) -> _Response:
        return _Response()

    monkeypatch.setattr(notification, "_opener", _Opener(_open))

    asyncio.run(
        notification.deliver(
            fabrik, _settings(notify_webhook="https://example.invalid/meldung"), NOTICE
        )
    )

    with fabrik() as http_session:
        setting = http_session.get(Setting, 1)
        assert setting is not None
        assert setting.notify_last_attempt_at is not None
        assert setting.notify_last_ok is True
        assert setting.notify_last_error is None


def test_deliver_records_a_short_reason_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fabrik = _own_database(tmp_path, "deliver-fehler")
    with fabrik() as http_session:
        create_settings(http_session)
        http_session.commit()

    def _broken(request: Request, timeout: int) -> _Response:
        raise OSError("x" * 400)  # far longer than the stored column allows

    monkeypatch.setattr(notification, "_opener", _Opener(_broken))

    asyncio.run(
        notification.deliver(
            fabrik, _settings(notify_webhook="https://example.invalid/meldung"), NOTICE
        )
    )

    with fabrik() as http_session:
        setting = http_session.get(Setting, 1)
        assert setting is not None
        assert setting.notify_last_ok is False
        assert setting.notify_last_error is not None
        assert len(setting.notify_last_error) <= 200
        # Never the webhook's own address or token -- see `notify_last_error`'s
        # docstring on `Setting`.
        assert "example.invalid" not in setting.notify_last_error


def test_deliver_writes_nothing_without_a_webhook_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fabrik = _own_database(tmp_path, "deliver-kein-webhook")
    with fabrik() as http_session:
        create_settings(http_session)
        http_session.commit()

    calls: list[Request] = []
    monkeypatch.setattr(
        notification, "_opener", _Opener(lambda request, timeout: calls.append(request))
    )

    asyncio.run(notification.deliver(fabrik, _settings(), NOTICE))

    assert calls == []
    with fabrik() as http_session:
        setting = http_session.get(Setting, 1)
        assert setting is not None
        assert setting.notify_last_attempt_at is None
        assert setting.notify_last_ok is None
        assert setting.notify_last_error is None


def test_deliver_tolerates_a_missing_setting_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Before setup finishes there is no `setting` row yet -- `deliver()` must
    still send (and not crash trying to record an outcome nowhere to put it)."""
    fabrik = _own_database(tmp_path, "deliver-ohne-setting")

    def _open(request: Request, timeout: int) -> _Response:
        return _Response()

    monkeypatch.setattr(notification, "_opener", _Opener(_open))

    asyncio.run(
        notification.deliver(
            fabrik, _settings(notify_webhook="https://example.invalid/meldung"), NOTICE
        )
    )


def test_the_webhook_network_call_never_sees_an_open_write_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The property this project has already paid for getting wrong once (an
    HTTP call awaited from inside an open transaction locked the SQLite file for
    the call's whole duration, see `services/publishing.py::_finish_database_work`
    and the analogous Meross test in `tests/test_shadow_run.py`). Tests the
    property itself -- via a tracking wrapper around the exact `session_scope`
    `deliver()` calls -- rather than trusting the source's call order to stay as
    written.
    """
    fabrik = _own_database(tmp_path, "deliver-transaktionsgrenze")
    with fabrik() as http_session:
        create_settings(http_session)
        http_session.commit()

    session_open = False
    violation_seen = False
    real_session_scope = notification.session_scope

    @contextlib.contextmanager
    def _tracking_session_scope(
        factory: sessionmaker[Session],
    ) -> Iterator[Session]:
        nonlocal session_open
        with real_session_scope(factory) as http_session:
            session_open = True
            try:
                yield http_session
            finally:
                session_open = False

    monkeypatch.setattr(notification, "session_scope", _tracking_session_scope)

    def _open(request: Request, timeout: int) -> _Response:
        nonlocal violation_seen
        if session_open:
            violation_seen = True
        return _Response()

    monkeypatch.setattr(notification, "_opener", _Opener(_open))

    asyncio.run(
        notification.deliver(
            fabrik, _settings(notify_webhook="https://example.invalid/meldung"), NOTICE
        )
    )

    assert violation_seen is False
