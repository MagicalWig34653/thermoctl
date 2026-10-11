"""The change log web route uses real domain writes and permissions."""

import re
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.helpers import create_settings, create_user, create_zone
from thermoctl.auth.csrf import csrf_token
from thermoctl.auth.sessions import COOKIE_NAME
from thermoctl.config import get_settings
from thermoctl.db.models.change_log import ChangeLog
from thermoctl.domain.change_log import Actor, set_parameter


def test_changes_page_requires_audit_read(client_als) -> None:
    client = client_als([("zone.read", None)])
    assert client.get("/changes").status_code == 403
    assert 'href="/changes"' not in client.get("/").text


def test_changes_page_shows_before_and_after(client_als, session: Session) -> None:
    create_settings(session)
    zone = create_zone(session, "Webprotokoll")
    set_parameter(session, Actor(None, None, "web"), zone, "hysteresis_k", Decimal("0.7"))
    response = client_als([("audit.read", None)]).get("/changes")
    assert response.status_code == 200
    assert "Webprotokoll" in response.text
    assert "0.7" in response.text
    assert "Zurücknehmen" in response.text


def _csrf(client) -> dict[str, str]:  # type: ignore[no-untyped-def]
    return {
        "X-CSRF-Token": csrf_token(
            client.cookies[COOKIE_NAME], get_settings().secret_key.get_secret_value()
        )
    }


def _entry(session: Session, name: str = "Webprotokoll") -> tuple[int, object]:
    create_settings(session)
    zone = create_zone(session, name)
    entry = set_parameter(session, Actor(None, None, "web"), zone, "hysteresis_k", Decimal("0.7"))
    assert entry.change_id is not None
    return entry.change_id, zone


def test_confirmation_and_success_restore_the_value(client_als, session: Session) -> None:
    entry_id, zone = _entry(session)
    client = client_als([("audit.read", None), ("zone.manage", zone.id)])
    confirmation = client.get(f"/changes/{entry_id}/revert")
    assert confirmation.status_code == 200
    assert "hysteresis_k" in confirmation.text
    assert "0.7" in confirmation.text
    assert zone.hysteresis_k == Decimal("0.7")
    response = client.post(f"/changes/{entry_id}/revert", headers=_csrf(client))
    assert response.status_code == 200
    session.refresh(zone)
    assert zone.hysteresis_k is None
    assert "Zurückgenommen am" in response.text


def test_conflict_needs_second_confirmation(client_als, session: Session) -> None:
    entry_id, zone = _entry(session)
    set_parameter(session, Actor(None, None, "web"), zone, "hysteresis_k", Decimal("0.9"))
    client = client_als([("audit.read", None), ("zone.manage", zone.id)])
    response = client.post(f"/changes/{entry_id}/revert", headers=_csrf(client))
    assert "aktuell 0.9" in response.text
    assert "erwartet 0.7" in response.text
    assert zone.hysteresis_k == Decimal("0.9")
    assert (
        client.post(
            f"/changes/{entry_id}/revert", data={"force": "1"}, headers=_csrf(client)
        ).status_code
        == 403
    )
    token = re.search(r'name="force_token" value="([a-f0-9]+)"', response.text)
    assert token is not None
    forced = client.post(
        f"/changes/{entry_id}/revert",
        data={"force": "1", "force_token": token.group(1)},
        headers=_csrf(client),
    )
    assert forced.status_code == 200
    session.refresh(zone)
    assert zone.hysteresis_k is None
    assert "Erzwungen" in forced.text


def test_revert_requires_csrf_and_change_permission(client_als, session: Session) -> None:
    entry_id, zone = _entry(session)
    client = client_als([("audit.read", None)])
    assert client.post(f"/changes/{entry_id}/revert").status_code == 403
    denied = client.post(f"/changes/{entry_id}/revert", headers=_csrf(client))
    assert denied.status_code == 403
    assert "0.7" not in denied.text
    assert zone.hysteresis_k == Decimal("0.7")


def test_unknown_and_repeated_revert_are_friendly(client_als, session: Session) -> None:
    entry_id, zone = _entry(session)
    client = client_als([("audit.read", None), ("zone.manage", zone.id)])
    assert client.get("/changes/999999/revert").status_code == 404
    assert client.post("/changes/999999/revert", headers=_csrf(client)).status_code == 404
    client.post(f"/changes/{entry_id}/revert", headers=_csrf(client))
    repeated = client.post(f"/changes/{entry_id}/revert", headers=_csrf(client))
    assert repeated.status_code == 200
    assert "bereits zurückgenommen" in repeated.text


def test_filters_and_escapes_free_text(client_als, session: Session) -> None:
    first_id, first_zone = _entry(session, "<img src=x onerror=alert(1)>")
    second_zone = create_zone(session, "Andere Zone")
    second = set_parameter(
        session, Actor(None, None, "web"), second_zone, "hysteresis_k", Decimal("0.8")
    )
    entry = session.get(ChangeLog, first_id)
    assert entry is not None
    entry.summary = '<script>alert("x")</script>'
    user = create_user(session, "xss-actor")
    user.display_name = "<svg onload=alert(1)>"
    entry.actor_user_id = user.id
    session.flush()
    client = client_als([("audit.read", None)])
    page = client.get("/changes")
    assert "&lt;img" in page.text and "<img src=x" not in page.text
    assert "&lt;script&gt;" in page.text and "<script>alert" not in page.text
    assert "&lt;svg" in page.text and "<svg onload" not in page.text
    filtered = client.get(
        "/changes",
        params={"zone": str(first_zone.id), "change_type": "control_parameter", "source": "web"},
    )
    assert 'id="change-' + str(first_id) + '"' in filtered.text
    assert second.change_id is not None
    assert 'id="change-' + str(second.change_id) + '"' not in filtered.text


def test_date_and_reverted_filters(client_als, session: Session) -> None:
    old_id, zone = _entry(session)
    later = set_parameter(session, Actor(None, None, "web"), zone, "hysteresis_k", Decimal("0.8"))
    old = session.get(ChangeLog, old_id)
    new = session.get(ChangeLog, later.change_id)
    assert old is not None and new is not None
    old.occurred_at = datetime(2026, 8, 1, 23, 59)
    new.occurred_at = datetime(2026, 8, 2, 23, 59)
    session.flush()
    client = client_als([("audit.read", None), ("zone.manage", zone.id)])
    by_date = client.get("/changes", params={"from_date": "2026-08-02", "to_date": "2026-08-02"})
    assert f'id="change-{old_id}"' not in by_date.text
    assert f'id="change-{later.change_id}"' in by_date.text
    client.post(f"/changes/{later.change_id}/revert", headers=_csrf(client))
    hidden = client.get("/changes", params={"hide_reverted": "1"})
    assert f'id="change-{later.change_id}"' not in hidden.text
    assert "Rücknahme von Eintrag" in hidden.text


def test_page_two_and_bad_filters(client_als, session: Session) -> None:
    _, zone = _entry(session)
    first = session.scalar(select(ChangeLog.id).order_by(ChangeLog.id))
    for number in range(50):
        value = Decimal("0.6") if number % 2 == 0 else Decimal("0.7")
        set_parameter(session, Actor(None, None, "web"), zone, "hysteresis_k", value)
    client = client_als([("audit.read", None)])
    page = client.get("/changes?page=2")
    assert f'id="change-{first}"' in page.text
    invalid = client.get("/changes?from_date=gestern&zone=ungültig&page=abc")
    assert "Bitte ein gültiges Datum" in invalid.text
    assert "Bitte eine gültige Zone" in invalid.text
    assert "Seitennummer muss eine ganze Zahl" in invalid.text


def test_revert_needs_change_right_but_not_audit_read(client_als, session: Session) -> None:
    entry_id, zone = _entry(session)
    client = client_als([("zone.manage", zone.id)])
    assert client.get("/changes").status_code == 403
    response = client.post(
        f"/changes/{entry_id}/revert", headers=_csrf(client), follow_redirects=False
    )
    assert response.status_code == 303
    session.refresh(zone)
    assert zone.hysteresis_k is None


def test_tenant_profile_cannot_open_change_log(tenant_client, session: Session) -> None:
    _entry(session)
    client = tenant_client([("audit.read", None)])
    assert client.get("/changes").status_code == 403
    assert 'href="/changes"' not in client.get("/account").text


def test_invalid_filter_choices_keep_the_form_usable(client_als) -> None:
    client = client_als([("audit.read", None)])
    response = client.get(
        "/changes",
        params={
            "from_date": "2026-08-20",
            "to_date": "2026-08-10",
            "zone": "0",
            "change_type": "unbekannt",
            "source": "unbekannt",
        },
    )
    assert response.status_code == 200
    assert "Das Bis-Datum darf nicht vor dem Von-Datum liegen" in response.text
    assert "Bitte eine gültige Zone wählen" in response.text
    assert "Bitte eine gültige Änderungsart wählen" in response.text
    assert "Bitte eine gültige Quelle wählen" in response.text
