"""Befund 5 (Codex): Eine Übersteuerung mit Zeitzonenoffset verlor den Offset.

`ZoneOverride.starts_at/ends_at` sind naive UTC-Spalten. Ein Client, der dem MCP-Werkzeug
`override` `2026-11-23T20:00:00+02:00` schickte, bekam 20:00 (UTC) gespeichert -- zwei
Stunden zu spät; die Datenbank verwirft den Offset still. Behoben an der einen Stelle,
durch die Web, REST, MCP, Abwesenheit und Boost laufen: `schedule.create_override`.

Die Tests prüfen den *gespeicherten* Wert (nach `expire` und frischer Abfrage), nicht das
Python-Objekt, das die Sitzung noch hält -- sonst bewiese ein Test nur, was der Code ohnehin
im Speicher hat.
"""

import asyncio
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.helpers import create_settings, create_zone, source, user_with_permissions
from thermoctl.auth.tokens import issue_token
from thermoctl.db.models.override import ZoneOverride
from thermoctl.domain import change_log as change_log_domain
from thermoctl.domain import schedule as schedule_domain
from thermoctl.domain.change_log import Actor
from thermoctl.mcp import server

PLUS_TWO = timezone(timedelta(hours=2))
MINUS_FIVE = timezone(timedelta(hours=-5))

# (Eingabe, gespeicherter Wert als naive UTC)
ENDS = [
    pytest.param(
        datetime(2026, 11, 23, 20, 0, tzinfo=PLUS_TWO), datetime(2026, 11, 23, 18, 0), id="plus-2"
    ),
    pytest.param(
        datetime(2026, 11, 23, 20, 0, tzinfo=MINUS_FIVE),
        datetime(2026, 11, 24, 1, 0),
        id="minus-5-ueber-mitternacht",
    ),
    pytest.param(
        datetime(2026, 11, 23, 20, 0, tzinfo=UTC), datetime(2026, 11, 23, 20, 0), id="utc"
    ),
    pytest.param(
        datetime(2026, 11, 23, 20, 0, tzinfo=timezone(timedelta(hours=5, minutes=45))),
        datetime(2026, 11, 23, 14, 15),
        id="plus-5:45",
    ),
    pytest.param(
        datetime(2026, 11, 23, 20, 0, tzinfo=timezone(timedelta(hours=1, seconds=30))),
        datetime(2026, 11, 23, 18, 59, 30),
        id="offset-mit-sekunden",
    ),
    pytest.param(
        datetime(2026, 11, 23, 20, 0), datetime(2026, 11, 23, 20, 0), id="naiv-gilt-als-utc"
    ),
]


def _stored_ends_at(session: Session, entry: ZoneOverride) -> datetime | None:
    """Der Wert, wie ihn die Datenbank hält (frisch gelesen, nicht aus dem Speicher)."""
    session.flush()
    session.expire(entry)
    return session.scalar(select(ZoneOverride.ends_at).where(ZoneOverride.id == entry.id))


def _stored_starts_at(session: Session, entry: ZoneOverride) -> datetime:
    session.flush()
    session.expire(entry)
    value = session.scalar(select(ZoneOverride.starts_at).where(ZoneOverride.id == entry.id))
    assert value is not None
    return value


@pytest.fixture
def zone_and_source(session: Session):  # type: ignore[no-untyped-def]
    create_settings(session)
    for code in ("web", "api", "mcp"):
        source(session, code)
    return create_zone(session, "befund5")


# -- Domäne direkt -------------------------------------------------------------------------


@pytest.mark.parametrize(("given", "expected"), ENDS)
def test_create_override_stores_ends_at_as_naive_utc(
    session: Session, zone_and_source, given: datetime, expected: datetime
) -> None:
    entry = schedule_domain.create_override(
        session, zone_and_source, Decimal("22.0"), given, now=datetime(2026, 11, 23, 8, 0)
    )

    assert _stored_ends_at(session, entry) == expected
    assert _stored_ends_at(session, entry).tzinfo is None  # type: ignore[union-attr]


def test_create_override_keeps_an_open_end_open(session: Session, zone_and_source) -> None:
    entry = schedule_domain.create_override(session, zone_and_source, Decimal("22.0"), None)

    assert _stored_ends_at(session, entry) is None


def test_create_override_keeps_microseconds_of_an_aware_end(
    session: Session, zone_and_source
) -> None:
    given = datetime(2026, 11, 23, 20, 0, 0, 123456, tzinfo=PLUS_TWO)

    entry = schedule_domain.create_override(
        session, zone_and_source, Decimal("22.0"), given, now=datetime(2026, 11, 23, 8, 0)
    )

    # Vor dem Schreiben, ohne Datenbank: MariaDB schneidet Mikrosekunden ab, das ist Sache
    # der Spalte und nicht dieser Umrechnung.
    assert entry.ends_at == datetime(2026, 11, 23, 18, 0, 0, 123456)


def test_create_override_normalizes_an_aware_now_the_same_way(
    session: Session, zone_and_source
) -> None:
    """`starts_at` liegt in derselben naiven UTC-Spalte wie `ends_at`.

    Heute kommt `now` nur aus `utcnow()` (naiv); die Funktion nimmt aber `now` von
    jedem Aufrufer an, und ein aware-Wert würde den Offset ebenso still verlieren.
    """
    entry = schedule_domain.create_override(
        session,
        zone_and_source,
        Decimal("22.0"),
        datetime(2026, 11, 23, 20, 0),
        now=datetime(2026, 11, 23, 10, 0, tzinfo=PLUS_TWO),
    )

    assert _stored_starts_at(session, entry) == datetime(2026, 11, 23, 8, 0)


def test_create_override_keeps_a_naive_now_as_utc(session: Session, zone_and_source) -> None:
    entry = schedule_domain.create_override(
        session,
        zone_and_source,
        Decimal("22.0"),
        None,
        now=datetime(2026, 11, 23, 10, 0),
    )

    assert _stored_starts_at(session, entry) == datetime(2026, 11, 23, 10, 0)


def test_an_end_before_or_at_the_start_is_stored_as_given(
    session: Session, zone_and_source
) -> None:
    """Bestand, nicht geändert: die Domäne prüft kein Ende nach dem Beginn.

    Eine solche Übersteuerung gilt nie (`resolved_setpoint` zählt nur Zeilen mit
    `starts_at <= jetzt < ends_at`). Hier festgehalten, damit eine spätere Änderung
    bewusst geschieht; die Umrechnung ändert daran nichts, sie gilt auch für aware-Werte
    (20:00+02:00 = 18:00 UTC liegt vor dem Beginn 19:00 UTC).
    """
    start = datetime(2026, 11, 23, 19, 0)

    before = schedule_domain.create_override(
        session,
        zone_and_source,
        Decimal("22.0"),
        datetime(2026, 11, 23, 20, 0, tzinfo=PLUS_TWO),
        now=start,
    )
    equal = schedule_domain.create_override(
        session, zone_and_source, Decimal("22.0"), start, now=start
    )

    assert _stored_ends_at(session, before) == datetime(2026, 11, 23, 18, 0)
    assert _stored_ends_at(session, equal) == start


# -- Änderungsprotokoll --------------------------------------------------------------------


def test_the_change_log_text_and_the_stored_value_name_the_same_moment(
    session: Session, zone_and_source
) -> None:
    """Protokoll („bis …“, Feld `ends_at`) und Datenbank dürfen nicht auseinanderlaufen.

    Das Protokoll rechnete schon vorher nach UTC um (`canonical`), die Spalte nicht: der
    Eintrag sagte 18:00Z, gespeichert war 20:00.
    """
    recorded = change_log_domain.create_override(
        session,
        Actor(None, None, "mcp"),
        zone_and_source,
        Decimal("22.0"),
        datetime(2026, 11, 23, 20, 0, tzinfo=PLUS_TWO),
    )

    assert _stored_ends_at(session, recorded.result) == datetime(2026, 11, 23, 18, 0)
    from thermoctl.db.models.change_log import ChangeLog

    entry = session.get(ChangeLog, recorded.change_id)
    assert entry is not None
    assert "bis 2026-11-23T18:00:00Z" in entry.summary


# -- MCP ----------------------------------------------------------------------------------


def _mcp_token(session: Session, zone_id: int) -> str:
    permissions: list[tuple[str, int | None]] = [
        ("zone.read", zone_id),
        ("override.create", zone_id),
    ]
    user_record = user_with_permissions(session, "befund5-nutzer", permissions)
    _row, plaintext = issue_token(session, user_record, "befund5", permissions, None)
    return plaintext


@pytest.mark.parametrize(("given", "expected"), ENDS)
def test_mcp_override_stores_ends_at_as_naive_utc(
    session: Session, zone_and_source, given: datetime, expected: datetime
) -> None:
    plaintext = _mcp_token(session, zone_and_source.id)

    result = server.override_zone(session, plaintext, zone_and_source.id, Decimal("22.0"), given)

    entry = session.query(ZoneOverride).one()
    assert _stored_ends_at(session, entry) == expected
    # Die Antwort nennt denselben Augenblick wie die Spalte (naive UTC, wie überall im Modul).
    assert result["ends_at"] == expected.isoformat()


def test_mcp_override_through_the_real_server_parses_iso_strings(
    session: Session, zone_and_source, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ende-zu-Ende über das echte MCP-Paket: `+02:00`, `-05:00`, `Z` und naiv als Text.

    Der Wert geht durch die Argumentprüfung des SDK (pydantic), nicht als fertiges
    `datetime` in die Funktion -- genau dort kommt der Offset des Clients an.
    """
    pytest.importorskip("mcp.server.mcpserver")
    plaintext = _mcp_token(session, zone_and_source.id)
    monkeypatch.setattr(server, "session_scope", lambda _factory: nullcontext(session))
    mcp = server._LoggedServer(server._mcp_server_class()("befund5"))
    inner = mcp._inner
    server._register_tools(mcp, object(), plaintext)  # type: ignore[arg-type]

    cases = [
        ("2026-11-23T20:00:00+02:00", datetime(2026, 11, 23, 18, 0)),
        ("2026-11-23T20:00:00-05:00", datetime(2026, 11, 24, 1, 0)),
        ("2026-11-23T20:00:00Z", datetime(2026, 11, 23, 20, 0)),
        ("2026-11-23T20:00:00", datetime(2026, 11, 23, 20, 0)),
    ]
    for text, expected in cases:
        asyncio.run(
            inner.call_tool(  # type: ignore[attr-defined]
                "override",
                {"zone_id": zone_and_source.id, "temperature_c": "22.0", "ends_at": text},
            )
        )
        entry = session.scalars(select(ZoneOverride).order_by(ZoneOverride.id.desc())).first()
        assert entry is not None
        assert _stored_ends_at(session, entry) == expected, text
