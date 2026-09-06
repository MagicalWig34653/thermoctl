from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.helpers import create_settings
from thermoctl.auth.csrf import csrf_token
from thermoctl.auth.sessions import COOKIE_NAME
from thermoctl.config import get_settings
from thermoctl.db.models.operations import AuditEvent
from thermoctl.db.models.vacation import Vacation


def _csrf(client: TestClient) -> dict[str, str]:
    secret = client.cookies[COOKIE_NAME]
    token = csrf_token(secret, get_settings().secret_key.get_secret_value())
    return {"X-CSRF-Token": token}


def test_the_page_needs_zone_read(client_als) -> None:
    assert client_als([]).get("/vacation").status_code == 403


def test_the_page_shows_no_vacation_by_default(client_als, session: Session) -> None:
    create_settings(session)
    response = client_als([("zone.read", None)]).get("/vacation")
    assert response.status_code == 200
    assert "Kein Urlaub angesetzt" in response.text


def test_the_form_is_hidden_without_the_permission(client_als, session: Session) -> None:
    create_settings(session)
    response = client_als([("zone.read", None)]).get("/vacation")
    assert response.status_code == 200
    assert "vacation.manage" in response.text
    assert "Urlaub ansetzen" not in response.text


def test_the_form_is_shown_with_the_permission(client_als, session: Session) -> None:
    create_settings(session)
    response = client_als([("zone.read", None), ("vacation.manage", None)]).get("/vacation")
    assert response.status_code == 200
    assert "Urlaub ansetzen" in response.text


def test_saving_needs_the_permission(client_als, session: Session) -> None:
    create_settings(session)
    client = client_als([("zone.read", None)])
    response = client.post(
        "/vacation",
        data={
            "start_date": "2030-08-01",
            "end_date": "2030-08-10",
            "setback_temperature_c": "15.0",
        },
        headers=_csrf(client),
    )
    assert response.status_code == 403


def test_a_vacation_is_saved_and_audited(client_als, session: Session) -> None:
    create_settings(session)
    client = client_als([("zone.read", None), ("vacation.manage", None)])
    response = client.post(
        "/vacation",
        data={
            "start_date": "2030-08-01",
            "end_date": "2030-08-10",
            "setback_temperature_c": "15.0",
        },
        headers=_csrf(client),
        follow_redirects=False,
    )
    assert response.status_code == 303
    vacation = session.query(Vacation).one()
    assert vacation.setback_temperature_c == Decimal("15.0")
    assert vacation.created_by_user_id is not None
    assert session.scalar(
        select(AuditEvent).where(
            AuditEvent.object_type == "vacation", AuditEvent.action == "create"
        )
    ) is not None


def test_an_invalid_start_date_returns_to_the_form_with_its_value(
    client_als, session: Session
) -> None:
    create_settings(session)
    client = client_als([("zone.read", None), ("vacation.manage", None)])
    response = client.post(
        "/vacation",
        data={
            "start_date": "nicht-ein-datum",
            "end_date": "2030-08-10",
            "setback_temperature_c": "15.0",
        },
        headers=_csrf(client),
    )
    assert response.status_code == 200
    assert "gültiges Datum" in response.text
    assert 'value="nicht-ein-datum"' in response.text
    assert session.query(Vacation).count() == 0


def test_an_invalid_end_date_returns_to_the_form_with_its_value(
    client_als, session: Session
) -> None:
    create_settings(session)
    client = client_als([("zone.read", None), ("vacation.manage", None)])
    response = client.post(
        "/vacation",
        data={
            "start_date": "2030-08-01",
            "end_date": "auch-kein-datum",
            "setback_temperature_c": "15.0",
        },
        headers=_csrf(client),
    )
    assert response.status_code == 200
    assert "gültiges Datum" in response.text
    assert 'value="auch-kein-datum"' in response.text


def test_an_invalid_temperature_returns_to_the_form_with_its_value(
    client_als, session: Session
) -> None:
    create_settings(session)
    client = client_als([("zone.read", None), ("vacation.manage", None)])
    response = client.post(
        "/vacation",
        data={
            "start_date": "2030-08-01",
            "end_date": "2030-08-10",
            "setback_temperature_c": "keine-zahl",
        },
        headers=_csrf(client),
    )
    assert response.status_code == 200
    assert "gültige Zahl" in response.text
    assert 'value="keine-zahl"' in response.text


def test_an_end_before_the_start_returns_to_the_form(client_als, session: Session) -> None:
    create_settings(session)
    client = client_als([("zone.read", None), ("vacation.manage", None)])
    response = client.post(
        "/vacation",
        data={
            "start_date": "2030-08-10",
            "end_date": "2030-08-01",
            "setback_temperature_c": "15.0",
        },
        headers=_csrf(client),
    )
    assert response.status_code == 200
    assert "nicht vor dem Beginn" in response.text
    assert session.query(Vacation).count() == 0


def test_a_second_overlapping_vacation_is_refused(client_als, session: Session) -> None:
    create_settings(session)
    client = client_als([("zone.read", None), ("vacation.manage", None)])
    client.post(
        "/vacation",
        data={
            "start_date": "2030-08-01",
            "end_date": "2030-08-10",
            "setback_temperature_c": "15.0",
        },
        headers=_csrf(client),
    )
    response = client.post(
        "/vacation",
        data={
            "start_date": "2030-09-01",
            "end_date": "2030-09-10",
            "setback_temperature_c": "15.0",
        },
        headers=_csrf(client),
    )
    assert response.status_code == 200
    assert "laufend" in response.text or "geplant" in response.text
    assert session.query(Vacation).count() == 1


def test_cancelling_needs_the_permission(client_als, session: Session) -> None:
    create_settings(session)
    client = client_als([("zone.read", None)])
    response = client.post("/vacation/cancel", headers=_csrf(client))
    assert response.status_code == 403


def test_cancelling_ends_the_running_vacation(client_als, session: Session) -> None:
    create_settings(session)
    client = client_als([("zone.read", None), ("vacation.manage", None)])
    client.post(
        "/vacation",
        data={
            "start_date": "2030-08-01",
            "end_date": "2030-08-10",
            "setback_temperature_c": "15.0",
        },
        headers=_csrf(client),
    )
    response = client.post(
        "/vacation/cancel", headers=_csrf(client), follow_redirects=False
    )
    assert response.status_code == 303
    vacation = session.query(Vacation).one()
    assert vacation.cancelled_at is not None
    assert session.scalar(
        select(AuditEvent).where(
            AuditEvent.object_type == "vacation", AuditEvent.action == "update"
        )
    ) is not None

    after = client.get("/vacation")
    assert "Kein Urlaub angesetzt" in after.text
