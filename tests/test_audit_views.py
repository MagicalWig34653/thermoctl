from datetime import datetime

from sqlalchemy.orm import Session

from tests.helpers import create_settings, create_user, source
from thermoctl.db.models.operations import AuditEvent


def _entry(
    session: Session,
    summary: str,
    *,
    at: datetime = datetime(2026, 8, 15, 12),
    source_codes: str = "web",
    user_id: int | None = None,
    action: str = "zone.geaendert",
    object_type: str = "zone",
    object_id: str | None = "1",
) -> AuditEvent:
    entry = AuditEvent(
        occurred_at=at,
        source_id=source(session, source_codes).id,
        actor_user_id=user_id,
        action=action,
        object_type=object_type,
        object_id=object_id,
        summary=summary,
    )
    session.add(entry)
    session.flush()
    return entry


def test_audit_requires_audit_read(client_als) -> None:
    assert client_als([("zone.read", None)]).get("/audit").status_code == 403
    assert client_als([("audit.read", None)]).get("/audit").status_code == 200


def test_from_actually_filters(client_als, session: Session) -> None:
    _entry(session, "zu alt", at=datetime(2026, 8, 1, 23, 59))
    _entry(session, "im Zeitraum", at=datetime(2026, 8, 2))
    response = client_als([("audit.read", None)]).get("/audit?from_date=2026-08-02")
    assert "im Zeitraum" in response.text
    assert "zu alt" not in response.text


def test_to_filters_inclusive_of_the_whole_day(client_als, session: Session) -> None:
    _entry(session, "noch enthalten", at=datetime(2026, 8, 2, 23, 59, 59))
    _entry(session, "zu neu", at=datetime(2026, 8, 3))
    response = client_als([("audit.read", None)]).get("/audit?to_date=2026-08-02")
    assert "noch enthalten" in response.text
    assert "zu neu" not in response.text


def test_the_date_filter_uses_the_configured_local_day_not_utc_midnight(
    client_als, session: Session
) -> None:
    """`setting.timezone` defaults to `Europe/Berlin` (UTC+2 in August). Local

    2026-08-02 00:00 is already 2026-08-01 22:00 UTC -- so an entry at
    2026-08-01 23:30 UTC has already happened on the local *second* of August, two and
    a half hours before its own UTC date rolls over. Cutting the filter at UTC
    midnight, as this used to, put that entry in the wrong day on both sides: absent
    from a `from_date=2026-08-02` search that should have found it, and present in a
    `to_date=2026-08-01` search it should not have matched.
    """
    create_settings(session)
    _entry(session, "kurz vor UTC-Mitternacht", at=datetime(2026, 8, 1, 23, 30))
    _entry(session, "klar im Zeitraum", at=datetime(2026, 8, 2, 10, 0))

    from_response = client_als([("audit.read", None)]).get("/audit?from_date=2026-08-02")
    assert "kurz vor UTC-Mitternacht" in from_response.text
    assert "klar im Zeitraum" in from_response.text

    to_response = client_als([("audit.read", None)]).get("/audit?to_date=2026-08-01")
    assert "kurz vor UTC-Mitternacht" not in to_response.text


def test_user_actually_filters(client_als, session: Session) -> None:
    anna = create_user(session, "anna")
    bert = create_user(session, "bert")
    _entry(session, "von Anna", user_id=anna.id)
    _entry(session, "von Bert", user_id=bert.id)
    response = client_als([("audit.read", None)]).get("/audit?user=anna")
    assert "von Anna" in response.text
    assert "von Bert" not in response.text


def test_action_actually_filters(client_als, session: Session) -> None:
    _entry(session, "Anmeldung", action="login")
    _entry(session, "Zone entfernt", action="zone.geloescht")
    response = client_als([("audit.read", None)]).get("/audit?action_code=login")
    assert "Anmeldung" in response.text
    assert "Zone entfernt" not in response.text


def test_object_filters_type_and_optional_id(client_als, session: Session) -> None:
    _entry(session, "Zone eins", object_id="1")
    _entry(session, "Zone zwei", object_id="2")
    _entry(session, "Benutzer eins", object_type="user", object_id="1")
    client = client_als([("audit.read", None)])
    by_type = client.get("/audit?object=zone")
    assert "Zone eins" in by_type.text and "Zone zwei" in by_type.text
    assert "Benutzer eins" not in by_type.text
    by_id = client.get("/audit?object=zone%3A2")
    assert "Zone zwei" in by_id.text
    assert "Zone eins" not in by_id.text


def test_source_actually_filters(client_als, session: Session) -> None:
    _entry(session, "aus dem Web", source_codes="web")
    _entry(session, "aus der API", source_codes="api")
    response = client_als([("audit.read", None)]).get("/audit?source=api")
    assert "aus der API" in response.text
    assert "aus dem Web" not in response.text


def test_pagination_returns_the_second_page(client_als, session: Session) -> None:
    for number in range(51):
        _entry(session, f"Eintrag {number:02}", at=datetime(2026, 8, 15, 12, number))
    response = client_als([("audit.read", None)]).get("/audit?page=2")
    assert "Eintrag 00" in response.text
    assert "Eintrag 50" not in response.text


def test_to_before_from_shows_a_message_and_keeps_the_values(client_als) -> None:
    response = client_als([("audit.read", None)]).get(
        "/audit?from_date=2026-08-20&to_date=2026-08-10"
    )
    assert response.status_code == 200
    assert "darf nicht vor dem Von-Datum liegen" in response.text
    assert 'value="2026-08-20"' in response.text
    assert 'value="2026-08-10"' in response.text


def test_unreadable_filter_values_show_messages_instead_of_422(client_als) -> None:
    response = client_als([("audit.read", None)]).get("/audit?from_date=gestern&page=zwei")
    assert response.status_code == 200
    assert "Bitte ein gültiges Datum eingeben" in response.text
    assert "Seitennummer muss eine ganze Zahl sein" in response.text


def test_detail_is_only_visible_on_request(client_als, session: Session) -> None:
    entry = _entry(session, "mit Einzelheiten")
    entry.detail = "Eine längere technische Erklärung"
    response = client_als([("audit.read", None)]).get("/audit")
    assert "<details" in response.text
    assert "Eine längere technische Erklärung" in response.text
