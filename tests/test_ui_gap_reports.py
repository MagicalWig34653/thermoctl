"""Schließt Testlücken der abgelösten Alt-Oberfläche für Ereignisse, Änderungen, Schaltprotokoll.

Alle Tagesgrenzen sind lokale Kalendertage der Anlage (Europe/Berlin, im August UTC+2), nicht
UTC-Mitternacht: ein Eintrag am 1.8. um 23:30 UTC gehört zum lokalen 2.8.
"""

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.helpers import (
    create_device,
    create_device_command,
    create_settings,
    create_user,
    create_zone,
    source,
)
from thermoctl import audit
from thermoctl.auth.csrf import CSRF_HEADER, csrf_token
from thermoctl.auth.sessions import COOKIE_NAME
from thermoctl.config import get_settings
from thermoctl.db.models.change_log import ChangeLog
from thermoctl.db.models.credential import ApiToken
from thermoctl.db.models.operations import AuditEvent
from thermoctl.domain import change_log as cl
from thermoctl.domain.change_log import Actor

Als = Callable[[list[tuple[str, int | None]]], TestClient]

#: Lokaler Tag 2.8.2026 (Europe/Berlin, UTC+2) = [1.8. 22:00 UTC, 2.8. 22:00 UTC).
VORTAG_LOKAL = datetime(2026, 8, 1, 21, 59, 59)
TAGESANFANG = datetime(2026, 8, 1, 22, 0, 0)
ABEND_UTC = datetime(2026, 8, 1, 23, 30)  # lokal 2.8., 01:30
TAGESENDE = datetime(2026, 8, 2, 21, 59, 59)
FOLGETAG = datetime(2026, 8, 2, 22, 0, 0)


def _csrf(client: TestClient) -> dict[str, str]:
    cookie = client.cookies.get(COOKIE_NAME)
    assert cookie is not None
    return {CSRF_HEADER: csrf_token(cookie, get_settings().secret_key.get_secret_value())}


def _audit_at(session: Session, summary: str, at: datetime, **extra: Any) -> AuditEvent:
    source(session, "web")
    event = audit.record(
        session,
        source="web",
        action="zone.updated",
        object_type="zone",
        summary=summary,
        **extra,
    )
    event.occurred_at = at
    session.flush()
    return event


# -- Ereignisse ---------------------------------------------------------------------------


def test_audit_day_filter_cuts_at_the_local_day_not_at_utc_midnight(
    client_als: Als, session: Session
) -> None:
    create_settings(session)
    _audit_at(session, "Vortag", VORTAG_LOKAL)
    _audit_at(session, "Abend UTC", ABEND_UTC)
    _audit_at(session, "Tagesanfang", TAGESANFANG)
    _audit_at(session, "Tagesende", TAGESENDE)
    _audit_at(session, "Folgetag", FOLGETAG)
    client = client_als([("audit.read", None)])

    def summaries(**params: str) -> set[str]:
        return {e["summary"] for e in client.get("/ui-api/audit", params=params).json()["entries"]}

    # Von: 23:30 UTC am 1.8. gehört zum lokalen 2.8.; der lokale 1.8. 23:59 nicht.
    assert summaries(from_date="2026-08-02") == {
        "Abend UTC",
        "Tagesanfang",
        "Tagesende",
        "Folgetag",
    }
    # Bis, obere Kante: 23:59:59 lokal ist noch drin, 00:00 des Folgetags nicht mehr.
    assert summaries(to_date="2026-08-02") == {"Vortag", "Abend UTC", "Tagesanfang", "Tagesende"}
    assert summaries(from_date="2026-08-02", to_date="2026-08-02") == {
        "Abend UTC",
        "Tagesanfang",
        "Tagesende",
    }
    # Ein Tag, an dem UTC- und Lokaltag auseinanderfallen, mit dem Vortag als Bis-Tag.
    assert summaries(from_date="2026-08-01", to_date="2026-08-01") == {"Vortag"}


def test_audit_user_filter_matches_the_username_of_the_actor(
    client_als: Als, session: Session
) -> None:
    create_settings(session)
    anna = create_user(session, "anna")
    ben = create_user(session, "ben")
    owner = create_user(session, "besitzer")
    token = ApiToken(user_id=owner.id, name="anna-token", token_hash="h" * 64, prefix="abc")
    session.add(token)
    session.flush()
    _audit_at(session, "von Anna", datetime(2026, 8, 3, 8), user_id=anna.id)
    _audit_at(session, "von Ben", datetime(2026, 8, 3, 9), user_id=ben.id)
    _audit_at(session, "per Token", datetime(2026, 8, 3, 10), token_id=token.id)
    _audit_at(session, "anonym", datetime(2026, 8, 3, 11))
    client = client_als([("audit.read", None)])

    def summaries(user: str) -> list[str]:
        return [
            e["summary"]
            for e in client.get("/ui-api/audit", params={"user": user}).json()["entries"]
        ]

    assert summaries("anna") == ["von Anna"]
    assert summaries("ben") == ["von Ben"]
    assert summaries("anna-token") == ["per Token"]
    assert summaries("niemand") == []
    # Ganzer Name, kein Teilstring: "ann" trifft weder Benutzer noch Token.
    assert summaries("ann") == []


# -- Änderungsprotokoll -------------------------------------------------------------------


def _set_change_time(session: Session, change_id: int | None, at: datetime) -> None:
    assert change_id is not None
    row = session.get(ChangeLog, change_id)
    assert row is not None
    row.occurred_at = at
    session.flush()


def test_changes_day_filter_cuts_at_the_local_day_with_an_inclusive_upper_edge(
    client_als: Als, session: Session
) -> None:
    create_settings(session)
    source(session, "web")
    zone = create_zone(session, "Tagesraum")
    stamps = {
        "Vortag": VORTAG_LOKAL,
        "Abend": ABEND_UTC,
        "Ende": TAGESENDE,
        "Folgetag": FOLGETAG,
    }
    ids: dict[int, str] = {}
    for index, (name, at) in enumerate(stamps.items()):
        entry = cl.set_parameter(
            session, Actor(None, None, "web"), zone, "hysteresis_k", Decimal(f"0.{index + 1}")
        )
        _set_change_time(session, entry.change_id, at)
        assert entry.change_id is not None
        ids[entry.change_id] = name
    client = client_als([("audit.read", None)])

    def names(**params: str) -> set[str]:
        body = client.get("/ui-api/changes", params=params).json()
        return {ids[e["id"]] for e in body["entries"]}

    assert names(from_date="2026-08-02") == {"Abend", "Ende", "Folgetag"}
    assert names(to_date="2026-08-02") == {"Vortag", "Abend", "Ende"}
    assert names(from_date="2026-08-02", to_date="2026-08-02") == {"Abend", "Ende"}


def test_changes_hide_reverted_drops_the_reverted_entry_only(
    client_als: Als, session: Session
) -> None:
    create_settings(session)
    source(session, "web")
    zone = create_zone(session, "Zurueck")
    first = cl.set_parameter(
        session, Actor(None, None, "web"), zone, "hysteresis_k", Decimal("0.7")
    )
    kept = cl.set_parameter(session, Actor(None, None, "web"), zone, "hysteresis_k", Decimal("0.9"))
    assert first.change_id is not None and kept.change_id is not None
    client = client_als([("audit.read", None), ("zone.manage", zone.id)])
    # Der zweite Eintrag setzt den ersten Wert nicht zurück; zurückgenommen wird der neuere.
    response = client.post(
        f"/ui-api/changes/{kept.change_id}/revert", json={}, headers=_csrf(client)
    )
    assert response.status_code == 200, response.text
    revert_id = response.json()["change"]["id"]

    def ids(**params: str) -> set[int]:
        body = client.get("/ui-api/changes", params=params).json()
        return {e["id"] for e in body["entries"]}

    everything = ids()
    assert everything == {first.change_id, kept.change_id, revert_id}
    hidden = ids(hide_reverted="true")
    assert kept.change_id not in hidden
    assert hidden == {first.change_id, revert_id}
    detail = client.get(f"/ui-api/changes/{kept.change_id}").json()
    assert detail["reverted_at"] is not None and detail["reverted_by_id"] == revert_id


# -- Schaltprotokoll ----------------------------------------------------------------------


def _command(
    session: Session, name: str, at: datetime, zone_name: str = "Wohnen", **extra: Any
) -> None:
    zone = create_zone(session, zone_name) if zone_name not in _ZONES else _ZONES[zone_name]
    _ZONES.setdefault(zone_name, zone)
    device = create_device(session, name)
    create_device_command(session, zone, device, at=at, **extra)


_ZONES: dict[str, Any] = {}


@pytest.fixture(autouse=True)
def _reset_zones() -> None:
    _ZONES.clear()


def test_device_commands_day_filter_cuts_at_the_local_day_with_an_inclusive_upper_edge(
    client_als: Als, session: Session
) -> None:
    create_settings(session)
    _command(session, "vortag", VORTAG_LOKAL)
    _command(session, "abend", ABEND_UTC)
    _command(session, "ende", TAGESENDE)
    _command(session, "folgetag", FOLGETAG)
    client = client_als([("audit.read", None)])

    def devices(**params: str) -> set[str]:
        body = client.get("/ui-api/device-commands", params=params).json()
        return {e["device_name"] for e in body["entries"]}

    assert devices(from_date="2026-08-02") == {"abend", "ende", "folgetag"}
    assert devices(to_date="2026-08-02") == {"vortag", "abend", "ende"}
    assert devices(from_date="2026-08-02", to_date="2026-08-02") == {"abend", "ende"}


def test_device_commands_show_error_and_reason(client_als: Als, session: Session) -> None:
    create_settings(session)
    zone = create_zone(session, "Fehlerraum")
    device = create_device(session, "stoerventil")
    entry = create_device_command(
        session, zone, device, at=datetime(2026, 8, 3, 9), outcome_code="failed"
    )
    entry.error = "Gerät antwortet nicht"
    entry.reason = "Sollwert aus dem Zeitplan"
    session.flush()
    client = client_als([("audit.read", None)])

    (shown,) = client.get("/ui-api/device-commands").json()["entries"]
    assert shown["error"] == "Gerät antwortet nicht"
    assert shown["reason"] == "Sollwert aus dem Zeitplan"
    assert shown["outcome"] == "failed" and shown["kind"] == "befehl"


def _absenkung(session: Session, name: str) -> Any:
    """Eine Zone mit zwei Absenkungswechseln (beginnt, endet) im Entscheidungsprotokoll."""
    from thermoctl.db.models.state import ShadowDecision

    zone = create_zone(session, name)
    for minute, k in ((0, Decimal("2.0")), (10, None)):
        session.add(
            ShadowDecision(
                decided_at=datetime(2026, 8, 15, 12, minute),
                zone_id=zone.id,
                setpoint_c=Decimal("20.5") - (k or 0),
                scheduled_setpoint_c=Decimal("20.5"),
                solar_setback_k=k,
                setpoint_reason="Zeitplan",
                would_heat=False,
                outcome_code="aus",
                reason="Sollwert ist erreicht.",
            )
        )
    session.flush()
    return zone


def test_setback_entries_carry_kind_source_and_outcome_labels(
    client_als: Als, session: Session
) -> None:
    create_settings(session)
    zone = _absenkung(session, "sonnenzimmer")
    client = client_als([("audit.read", None)])

    body = client.get("/ui-api/device-commands", params={"zone": zone.display_name}).json()

    entries = body["entries"]
    assert len(entries) == 2
    assert {e["kind"] for e in entries} == {"absenkung"}
    assert len({e["source"] for e in entries}) == 1
    assert body["source_labels"][entries[0]["source"]] == "Regelung (Sonnenabsenkung)"
    assert {e["device_name"] for e in entries} <= {None, ""}
    for entry in entries:
        assert entry["outcome"] in body["outcome_labels"]
        assert entry["simulated"] is not None
    assert any("Sonnenabsenkung" in (e["reason"] or "") for e in entries)


def test_zone_filter_offers_a_zone_that_only_has_setback_entries(
    client_als: Als, session: Session
) -> None:
    create_settings(session)
    zone = _absenkung(session, "nur-sonne")
    _command(session, "anderes-ventil", datetime(2026, 8, 3, 9), zone_name="Wohnzimmer")
    client = client_als([("audit.read", None)])

    body = client.get("/ui-api/device-commands").json()

    assert zone.display_name in body["zone_names"]
    assert "Wohnzimmer" in body["zone_names"]
    filtered = client.get("/ui-api/device-commands", params={"zone": zone.display_name}).json()
    assert filtered["entries"] and {e["kind"] for e in filtered["entries"]} == {"absenkung"}


def test_zone_filter_lists_a_zone_with_commands_and_setback_only_once_and_sorted(
    client_als: Als, session: Session
) -> None:
    create_settings(session)
    zone = _absenkung(session, "beides")
    device = create_device(session, "beides-ventil")
    create_device_command(session, zone, device, at=datetime(2026, 8, 3, 9))
    _command(session, "ventil-a", datetime(2026, 8, 3, 9), zone_name="Ärmel")
    _command(session, "ventil-z", datetime(2026, 8, 3, 9), zone_name="zelt")
    client = client_als([("audit.read", None)])

    names = client.get("/ui-api/device-commands").json()["zone_names"]

    assert names.count(zone.display_name) == 1
    assert names == sorted(set(names), key=lambda n: (n.casefold(), n))
    assert {"Ärmel", "zelt", zone.display_name} <= set(names)
