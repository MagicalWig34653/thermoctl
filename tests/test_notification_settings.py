"""The notification preferences and the webhook test button on `/settings`.

`Setting` does not carry the three preference columns or the three delivery-state
columns yet in this worktree -- a parallel task adds them (migration, model, the
domain gate that checks them before a real fault notice goes out). The view code
reads them with `getattr(row, name, default)` so this page keeps working either
way, and every test below sets the attribute directly on the row object it holds,
the same way the view itself would once the columns are real. See STATUS.md.
"""

from collections.abc import Callable
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.helpers import create_settings, source
from thermoctl.auth.csrf import CSRF_HEADER, csrf_token
from thermoctl.auth.sessions import COOKIE_NAME
from thermoctl.config import Settings, get_settings
from thermoctl.db.base import utcnow
from thermoctl.integrations import notification
from thermoctl.web import control_views

ClientBuilder = Callable[[list[tuple[str, int | None]]], TestClient]

ALL_PERMISSIONS: list[tuple[str, int | None]] = [
    ("zone.read", None),
    ("setting.manage", None),
]


def _csrf(client: TestClient) -> dict[str, str]:
    http_session = client.cookies.get(COOKIE_NAME)
    assert http_session is not None
    return {CSRF_HEADER: csrf_token(http_session, get_settings().secret_key.get_secret_value())}


def _env_settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "_env_file": None,
        "database_url": "sqlite://",
        "secret_key": "s" * 32,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _without_webhook(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(control_views, "get_settings", lambda: _env_settings())


def _with_webhook(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        control_views,
        "get_settings",
        lambda: _env_settings(notify_webhook="https://example.invalid/meldung"),
    )


# --- The three toggles -----------------------------------------------------


def test_the_settings_page_shows_the_three_notification_toggles(
    client_als: ClientBuilder, session: Session
) -> None:
    create_settings(session)
    response = client_als(ALL_PERMISSIONS).get("/settings")
    assert response.status_code == 200
    for name in ("notify_sensor_faults", "notify_bridge_faults", "notify_command_failures"):
        assert f'name="{name}"' in response.text


def test_saving_notification_preferences_persists_all_three(
    client_als: ClientBuilder, session: Session
) -> None:
    row = create_settings(session)
    source(session, "web")
    client = client_als(ALL_PERMISSIONS)
    response = client.post(
        "/settings/notifications",
        data={},  # nothing checked
        headers=_csrf(client),
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert row.notify_sensor_faults is False
    assert row.notify_bridge_faults is False
    assert row.notify_command_failures is False


def test_saving_notification_preferences_keeps_a_ticked_box_on(
    client_als: ClientBuilder, session: Session
) -> None:
    row = create_settings(session)
    source(session, "web")
    client = client_als(ALL_PERMISSIONS)
    response = client.post(
        "/settings/notifications",
        data={"notify_sensor_faults": "yes"},
        headers=_csrf(client),
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert row.notify_sensor_faults is True
    assert row.notify_bridge_faults is False


def test_saving_notification_preferences_writes_an_audit_entry(
    client_als: ClientBuilder, session: Session
) -> None:
    from sqlalchemy import select

    from thermoctl.db.models.operations import AuditEvent

    create_settings(session)
    source(session, "web")
    client = client_als(ALL_PERMISSIONS)
    client.post("/settings/notifications", data={}, headers=_csrf(client))
    entry = session.scalar(select(AuditEvent).where(AuditEvent.object_type == "setting"))
    assert entry is not None
    assert entry.action == "update"


def test_without_setting_manage_the_toggles_cannot_be_changed(
    client_als: ClientBuilder, session: Session
) -> None:
    create_settings(session)
    client = client_als([("zone.read", None)])
    response = client.post(
        "/settings/notifications", data={}, headers=_csrf(client)
    )
    assert response.status_code == 403


def test_a_row_without_the_columns_yet_still_renders_with_the_documented_default(
    client_als: ClientBuilder, session: Session
) -> None:
    """Bridges the gap until the parallel migration lands: an untouched row has no
    `notify_*` attributes at all, and the page must still come up -- with the
    documented default (every kind of notice on)."""
    create_settings(session)
    response = client_als(ALL_PERMISSIONS).get("/settings")
    assert response.status_code == 200
    assert "Noch nie versucht" in response.text


# --- The test button --------------------------------------------------------


def test_without_a_webhook_the_button_is_not_offered(
    client_als: ClientBuilder, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    create_settings(session)
    _without_webhook(monkeypatch)
    response = client_als(ALL_PERMISSIONS).get("/settings")
    assert response.status_code == 200
    assert "/settings/notifications/test" not in response.text
    assert "nichts zu testen" in response.text.lower() or "kein webhook" in response.text.lower()


def test_triggering_the_test_without_a_webhook_says_why_it_did_nothing(
    client_als: ClientBuilder, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    create_settings(session)
    _without_webhook(monkeypatch)
    client = client_als(ALL_PERMISSIONS)
    response = client.post(
        "/settings/notifications/test", data={}, headers=_csrf(client)
    )
    assert response.status_code == 200
    assert "nichts zu testen" in response.text.lower()


def test_a_successful_test_shows_status_and_duration_and_updates_the_delivery_state(
    client_als: ClientBuilder, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = create_settings(session)
    _with_webhook(monkeypatch)

    async def _fake_send_test(settings: Settings) -> notification.WebhookTestResult:
        return notification.WebhookTestResult(
            ok=True, status_code=200, duration_seconds=0.042, error=None
        )

    monkeypatch.setattr(notification, "send_test", _fake_send_test)
    client = client_als(ALL_PERMISSIONS)
    response = client.post(
        "/settings/notifications/test", data={}, headers=_csrf(client)
    )
    assert response.status_code == 200
    assert "200" in response.text
    assert "42 ms" in response.text
    assert row.notify_last_ok is True
    assert row.notify_last_error is None
    assert row.notify_last_attempt_at is not None


def test_a_failed_test_shows_the_scrubbed_reason_and_records_it(
    client_als: ClientBuilder, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = create_settings(session)
    _with_webhook(monkeypatch)

    async def _fake_send_test(settings: Settings) -> notification.WebhookTestResult:
        return notification.WebhookTestResult(
            ok=False, status_code=404, duration_seconds=0.01,
            error="Die Gegenstelle antwortete mit Status 404.",
        )

    monkeypatch.setattr(notification, "send_test", _fake_send_test)
    client = client_als(ALL_PERMISSIONS)
    response = client.post(
        "/settings/notifications/test", data={}, headers=_csrf(client)
    )
    assert response.status_code == 200
    assert "404" in response.text
    assert "Die Gegenstelle antwortete mit Status 404." in response.text
    assert row.notify_last_ok is False
    assert row.notify_last_error == "Die Gegenstelle antwortete mit Status 404."


def test_the_error_text_is_escaped_not_trusted(
    client_als: ClientBuilder, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The far end is not a trusted source -- its text must not land in the page
    as live markup."""
    create_settings(session)
    _with_webhook(monkeypatch)

    async def _fake_send_test(settings: Settings) -> notification.WebhookTestResult:
        return notification.WebhookTestResult(
            ok=False, status_code=None, duration_seconds=0.01,
            error="<script>alert(1)</script>",
        )

    monkeypatch.setattr(notification, "send_test", _fake_send_test)
    client = client_als(ALL_PERMISSIONS)
    response = client.post(
        "/settings/notifications/test", data={}, headers=_csrf(client)
    )
    assert "<script>alert(1)</script>" not in response.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in response.text


def test_a_second_attempt_within_the_cooldown_is_refused_without_a_new_call(
    client_als: ClientBuilder, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = create_settings(session)
    _with_webhook(monkeypatch)
    calls = 0

    async def _fake_send_test(settings: Settings) -> notification.WebhookTestResult:
        nonlocal calls
        calls += 1
        return notification.WebhookTestResult(
            ok=True, status_code=200, duration_seconds=0.01, error=None
        )

    monkeypatch.setattr(notification, "send_test", _fake_send_test)
    client = client_als(ALL_PERMISSIONS)
    first = client.post("/settings/notifications/test", data={}, headers=_csrf(client))
    second = client.post("/settings/notifications/test", data={}, headers=_csrf(client))

    assert first.status_code == 200
    assert second.status_code == 200
    assert calls == 1
    assert "kurz warten" in second.text.lower()
    assert row.notify_last_ok is True  # unchanged by the refused second attempt


def test_after_the_cooldown_a_new_attempt_goes_through(
    client_als: ClientBuilder, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = create_settings(session)
    _with_webhook(monkeypatch)
    calls = 0

    async def _fake_send_test(settings: Settings) -> notification.WebhookTestResult:
        nonlocal calls
        calls += 1
        return notification.WebhookTestResult(
            ok=True, status_code=200, duration_seconds=0.01, error=None
        )

    monkeypatch.setattr(notification, "send_test", _fake_send_test)
    row.notify_last_attempt_at = utcnow() - timedelta(seconds=30)
    client = client_als(ALL_PERMISSIONS)
    response = client.post(
        "/settings/notifications/test", data={}, headers=_csrf(client)
    )
    assert response.status_code == 200
    assert calls == 1


def test_without_setting_manage_the_test_button_cannot_be_triggered(
    client_als: ClientBuilder, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    create_settings(session)
    _with_webhook(monkeypatch)
    client = client_als([("zone.read", None)])
    response = client.post(
        "/settings/notifications/test", data={}, headers=_csrf(client)
    )
    assert response.status_code == 403


def test_never_shown_when_notify_last_attempt_at_is_absent(
    client_als: ClientBuilder, session: Session
) -> None:
    """`Setting` here has no `notify_last_attempt_at` column yet -- `getattr`
    falls back to `None`, and `None` renders as "noch nie", not as an error."""
    create_settings(session)
    response = client_als(ALL_PERMISSIONS).get("/settings")
    assert response.status_code == 200
    assert "noch nie" in response.text.lower()
