"""Das Änderungsprotokoll über MCP: schreibende Werkzeuge, `list_changes`, `revert_change`.

Die Domäne (`tests/test_change_log.py`) ist dort geprüft. Hier geht es um das, was der
Adapter selbst verantwortet: dass jedes schreibende Werkzeug den Token des Aufrufers ins
Protokoll bringt, dass ein Fehler keinen Eintrag hinterlässt, dass die Meldungen der
Domäne den Client lesbar erreichen und dass eine gegenstandslose Rücknahme ihre
Markierung behält (die Sitzung darf dafür nicht zurückgerollt werden).
"""

from collections.abc import Callable
from contextlib import nullcontext
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session, sessionmaker

from tests.helpers import (
    create_settings,
    create_zone,
    source,
    user_with_permissions,
    zone_with_schedule,
)
from thermoctl.auth.tokens import issue_token
from thermoctl.db.base import Base
from thermoctl.db.models.change_log import ChangeLog
from thermoctl.db.models.credential import ApiToken
from thermoctl.db.models.identity import User
from thermoctl.db.models.lookup import ActorSource
from thermoctl.db.models.operations import AuditEvent, Setting
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.zone import Zone
from thermoctl.domain.authz import Forbidden
from thermoctl.domain.control import arm
from thermoctl.domain.modes import DomainError
from thermoctl.domain.sensor_failure_policy import ProfileValues, save_profile
from thermoctl.domain.zone_settings import ParameterOutOfRange
from thermoctl.mcp import server

ToolError = pytest.importorskip("mcp.server.mcpserver.exceptions").ToolError

type Tools = dict[str, Callable[..., Any]]


def _issue(
    session: Session, name: str, permissions: list[tuple[str, int | None]]
) -> tuple[ApiToken, str]:
    owner = user_with_permissions(session, name, permissions)
    return issue_token(session, owner, name, permissions, None)


def _tools(session: Session, monkeypatch: pytest.MonkeyPatch, plaintext: str) -> Tools:
    """Die registrierten Werkzeuge, wie ein Client sie ruft (mit Fehlerumwandlung)."""
    tools: Tools = {}

    class RecordingServer:
        def tool(self, name: str | None = None):  # type: ignore[no-untyped-def]
            def decorate(function):  # type: ignore[no-untyped-def]
                tools[str(name)] = function
                return function

            return decorate

        def run(self, transport: str = "stdio", **kwargs: object) -> None:
            raise AssertionError("Der Test startet keinen Transport")

    monkeypatch.setattr(server, "session_scope", lambda _factory: nullcontext(session))
    server._register_tools(RecordingServer(), object(), plaintext)  # type: ignore[arg-type]
    return tools


def _entries(session: Session) -> list[ChangeLog]:
    return list(session.scalars(select(ChangeLog).order_by(ChangeLog.id)))


def _count(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(ChangeLog)) or 0


ALL_RIGHTS = [
    ("zone.read", None),
    ("audit.read", None),
    ("override.create", None),
    ("override.cancel", None),
    ("zone.manage", None),
    ("schedule.manage", None),
    ("vacation.manage", None),
    ("setting.manage", None),
    ("control.disarm", None),
    ("device.manage", None),
]


# --- schreibende Werkzeuge: Token im Eintrag, change_id in der Antwort -----------------


def test_every_writing_tool_records_an_entry_for_the_token_of_the_caller(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    zone = zone_with_schedule(
        session,
        "protokollzone",
        [(1, 360, "tag-prot", Decimal("21.0")), (1, 1320, "nacht-prot", Decimal("17.0"))],
    )
    source(session, "mcp")
    save_profile(session, ProfileValues("Notbetrieb Vorgabe", 600, 1200, 60, 2, Decimal("1"), ()))
    arm(session, True, reason="von Hand", user_id=None)
    token, plaintext = _issue(session, "protokollant", ALL_RIGHTS)
    point = session.scalars(
        select(SchedulePoint).where(
            SchedulePoint.zone_id == zone.id, SchedulePoint.minute_of_day == 360
        )
    ).one()
    tools = _tools(session, monkeypatch, plaintext)

    calls: list[tuple[str, str, Callable[[], dict[str, object]]]] = [
        (
            "set_control_parameters",
            "control_parameter",
            lambda: tools["set_control_parameters"](zone.id, "hysteresis_k", Decimal("0.7")),
        ),
        (
            "move_schedule_point",
            "schedule_point",
            lambda: tools["move_schedule_point"](zone.id, point.id, 2, 400),
        ),
        ("override", "override", lambda: tools["override"](zone.id, Decimal("22.0"))),
        ("cancel_override", "override_cancel", lambda: tools["cancel_override"](zone.id)),
        ("boost", "boost", lambda: tools["boost"](zone.id)),
        (
            "vacation",
            "vacation",
            lambda: tools["vacation"](date(2027, 1, 1), date(2027, 1, 10), Decimal("15.0")),
        ),
        ("cancel_vacation", "vacation_cancel", lambda: tools["cancel_vacation"]()),
        ("force_dry_run", "force_dry_run", lambda: tools["force_dry_run"]("Test")),
        (
            "set_sensor_failure_defaults",
            "sensor_failure_defaults",
            lambda: tools["set_sensor_failure_defaults"](
                300, 900, 120, 3, Decimal("1"), Decimal("15")
            ),
        ),
        (
            "set_sensor_failure_policy",
            "sensor_failure_policy",
            lambda: tools["set_sensor_failure_policy"](zone.id, True),
        ),
    ]
    for tool, change_type, call in calls:
        before = _count(session)
        result = call()
        change_id = result["change_id"]
        assert isinstance(change_id, int), f"{tool}: keine change_id in {result}"
        entry = session.get(ChangeLog, change_id)
        assert entry is not None and _count(session) == before + 1, tool
        assert entry.change_type == change_type, tool
        assert entry.actor_token_id == token.id, f"{tool}: Token fehlt im Eintrag"
        assert entry.actor_user_id == token.user_id, tool
        source_row = session.get(ActorSource, entry.source_id)
        assert source_row is not None and source_row.code == "mcp", tool


def test_the_formerly_missing_token_of_set_control_parameters_is_now_recorded(
    session: Session,
) -> None:
    """Die Lücke aus der Spec: `set_control_parameters` gab den Token nie weiter."""
    create_settings(session)
    source(session, "mcp")
    zone = create_zone(session, "tokenzone")
    token, plaintext = _issue(session, "regler", [("zone.read", zone.id), ("zone.manage", zone.id)])

    result = server.set_control_parameters(
        session, plaintext, zone.id, "hysteresis_k", Decimal("0.6")
    )

    entry = session.get(ChangeLog, result["change_id"])
    assert entry is not None and entry.actor_token_id == token.id
    # Auch das Audit-Log der Domäne kennt den Token jetzt.
    audit = session.scalars(select(AuditEvent).order_by(AuditEvent.id.desc())).first()
    assert audit is not None and audit.actor_token_id == token.id


def test_writing_tools_keep_their_response_format_and_add_change_id(session: Session) -> None:
    zone = zone_with_schedule(session, "formzone", [(1, 0, "tag-form", Decimal("20.0"))])
    source(session, "mcp")
    _token_row, plaintext = _issue(session, "former", ALL_RIGHTS)

    assert set(server.override_zone(session, plaintext, zone.id, Decimal("21.0"))) == {
        "zone",
        "temperature_c",
        "ends_at",
        "change_id",
    }
    assert set(server.cancel_override(session, plaintext, zone.id)) == {
        "zone",
        "cancelled",
        "change_id",
    }
    assert set(server.boost(session, plaintext, zone.id)) == {
        "zone",
        "mode",
        "temperature_c",
        "valid_until",
        "change_id",
    }
    assert set(
        server.set_control_parameters(session, plaintext, zone.id, "hysteresis_k", Decimal("0.5"))
    ) == {
        "zone",
        "name",
        "value",
        "change_id",
    }


# --- keine Änderung, kein Eintrag --------------------------------------------------------


def test_a_refused_change_leaves_no_entry(session: Session) -> None:
    zone = zone_with_schedule(session, "abgewiesen", [(1, 0, "tag-abg", Decimal("20.0"))])
    source(session, "mcp")
    _token_row, plaintext = _issue(session, "abweiser", ALL_RIGHTS)

    with pytest.raises(DomainError):
        server.override_zone(session, plaintext, zone.id, Decimal("99"))
    with pytest.raises(ParameterOutOfRange):
        server.set_control_parameters(session, plaintext, zone.id, "hysteresis_k", Decimal("99"))
    with pytest.raises(ValueError):
        server.set_sensor_failure_policy(
            session, plaintext, zone.id, enabled=True, backup_offsets={999_999: Decimal("1")}
        )

    assert _count(session) == 0


def test_a_change_without_effect_has_no_change_id(session: Session) -> None:
    zone = zone_with_schedule(session, "wirkungslos", [(1, 0, "tag-wl", Decimal("20.0"))])
    source(session, "mcp")
    _token_row, plaintext = _issue(session, "wiederholer", ALL_RIGHTS)

    first = server.set_control_parameters(
        session, plaintext, zone.id, "hysteresis_k", Decimal("0.5")
    )
    again = server.set_control_parameters(
        session, plaintext, zone.id, "hysteresis_k", Decimal("0.5")
    )
    nothing_to_cancel = server.cancel_override(session, plaintext, zone.id)
    no_vacation = server.cancel_vacation(session, plaintext)
    already_dry = server.force_dry_run(session, plaintext, "schon aus")

    assert isinstance(first["change_id"], int)
    assert again["change_id"] is None
    assert nothing_to_cancel == {"zone": zone.name, "cancelled": False, "change_id": None}
    assert no_vacation == {"cancelled": False, "change_id": None}
    assert already_dry == {"armed": False, "changed": False, "change_id": None}
    assert _count(session) == 1


# --- list_changes ------------------------------------------------------------------------


def _two_zones_with_changes(session: Session) -> tuple[Zone, Zone, str]:
    first = zone_with_schedule(session, "wohnen", [(1, 0, "tag-wohnen", Decimal("20.0"))])
    second = create_zone(session, "schlafen")
    source(session, "mcp")
    source(session, "web")
    _row, plaintext = _issue(session, "protokollierer", ALL_RIGHTS)
    server.set_control_parameters(session, plaintext, first.id, "hysteresis_k", Decimal("0.6"))
    server.set_control_parameters(session, plaintext, second.id, "hysteresis_k", Decimal("0.8"))
    server.override_zone(session, plaintext, first.id, Decimal("22.0"))
    return first, second, plaintext


def test_list_changes_returns_the_entries_with_before_and_after(session: Session) -> None:
    first, _second, plaintext = _two_zones_with_changes(session)

    listing = server.list_changes(
        session, plaintext, zone=first.name, change_type="control_parameter"
    )

    assert len(listing) == 1
    entry = listing[0]
    assert set(entry) == {
        "id",
        "at",
        "source",
        "user",
        "token",
        "change_type",
        "zone",
        "summary",
        "fields",
        "revertible",
        "not_revertible_reason",
        "reverted",
        "reverted_at",
        "reverted_by",
        "reverts",
        "forced",
    }
    assert entry["source"] == "mcp"
    owner = session.scalars(select(User).where(User.username == "protokollierer")).one()
    assert entry["user"] == owner.display_name
    assert entry["token"] == "protokollierer"
    assert entry["zone"] == first.display_name
    assert entry["fields"] == [{"field": "hysteresis_k", "before": None, "after": "0.6"}]
    assert entry["revertible"] is True and entry["reverted"] is False
    assert entry["reverted_at"] is None and entry["reverts"] is None and entry["forced"] is False
    assert str(entry["at"]).endswith("+00:00")


def test_list_changes_filters(session: Session) -> None:
    first, second, plaintext = _two_zones_with_changes(session)

    everything = server.list_changes(session, plaintext)
    assert [e["change_type"] for e in everything] == [
        "override",
        "control_parameter",
        "control_parameter",
    ]  # neueste zuerst
    assert len(server.list_changes(session, plaintext, zone=first.name)) == 2
    assert len(server.list_changes(session, plaintext, zone=second.display_name)) == 1
    assert len(server.list_changes(session, plaintext, change_type="override")) == 1
    assert len(server.list_changes(session, plaintext, source="mcp")) == 3
    assert server.list_changes(session, plaintext, source="web") == []
    assert len(server.list_changes(session, plaintext, limit=2)) == 2
    now = datetime.now(UTC)
    assert len(server.list_changes(session, plaintext, from_at=now - timedelta(hours=1))) == 3
    assert server.list_changes(session, plaintext, from_at=now + timedelta(hours=1)) == []
    assert server.list_changes(session, plaintext, to_at=now - timedelta(hours=1)) == []


def test_list_changes_can_hide_reverted_entries(session: Session) -> None:
    first, _second, plaintext = _two_zones_with_changes(session)
    target = server.list_changes(
        session, plaintext, zone=first.name, change_type="control_parameter"
    )[0]
    server.revert_change(session, plaintext, int(target["id"]))  # type: ignore[call-overload]

    shown = server.list_changes(session, plaintext, zone=first.name, include_reverted=False)
    all_of_zone = server.list_changes(session, plaintext, zone=first.name)

    assert target["id"] not in {e["id"] for e in shown}
    assert target["id"] in {e["id"] for e in all_of_zone}
    original = next(e for e in all_of_zone if e["id"] == target["id"])
    assert original["reverted"] is True and original["reverted_by"] is not None
    assert str(original["reverted_at"]).endswith("+00:00")
    rollback = next(e for e in all_of_zone if e["reverts"] == target["id"])
    assert rollback["revertible"] is False and rollback["not_revertible_reason"]


def test_list_changes_needs_audit_read(session: Session) -> None:
    zone = zone_with_schedule(session, "lesesperre", [(1, 0, "tag-ls", Decimal("20.0"))])
    _row, no_audit = _issue(session, "ohne-audit", [("zone.read", None)])
    _row, zone_only = _issue(
        session, "audit-nur-zone", [("zone.read", None), ("audit.read", zone.id)]
    )

    with pytest.raises(Forbidden, match="audit.read"):
        server.list_changes(session, no_audit)
    with pytest.raises(Forbidden, match="audit.read"):
        server.list_changes(session, zone_only)  # nur anlagenweit, wie das Schaltprotokoll


@pytest.mark.parametrize("limit", [0, -1, 501])
def test_list_changes_refuses_a_nonsensical_limit_with_a_readable_message(
    session: Session, monkeypatch: pytest.MonkeyPatch, limit: int
) -> None:
    _row, plaintext = _issue(session, f"limit{limit}", [("zone.read", None), ("audit.read", None)])
    tools = _tools(session, monkeypatch, plaintext)

    with pytest.raises(ToolError, match="limit muss zwischen 1 und 500"):
        tools["list_changes"](limit=limit)
    assert tools["list_changes"](limit=500) == []
    assert tools["list_changes"](limit=1) == []


def test_list_changes_with_an_unknown_zone_is_an_error_not_an_empty_list(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _two_zones_with_changes(session)
    _row, plaintext = _issue(session, "sucher", [("zone.read", None), ("audit.read", None)])
    tools = _tools(session, monkeypatch, plaintext)

    with pytest.raises(ToolError, match="Zone nicht gefunden"):
        tools["list_changes"](zone="gibt-es-nicht")


def test_entries_of_a_deleted_zone_stay_visible_without_a_zone_filter(session: Session) -> None:
    doomed = create_zone(session, "verschwindet")
    create_settings(session)
    source(session, "mcp")
    _row, plaintext = _issue(
        session, "chronist", [("zone.read", None), ("zone.manage", None), ("audit.read", None)]
    )
    server.set_control_parameters(session, plaintext, doomed.id, "hysteresis_k", Decimal("0.6"))
    from sqlalchemy import delete

    session.execute(delete(Zone).where(Zone.id == doomed.id))
    session.expire_all()

    listing = server.list_changes(session, plaintext)

    assert [e["zone"] for e in listing] == [doomed.display_name]
    with pytest.raises(LookupError, match="Zone nicht gefunden"):
        server.list_changes(session, plaintext, zone=doomed.name)


# --- revert_change -----------------------------------------------------------------------


def _changed_parameter(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> tuple[Zone, int, str, Tools]:
    create_settings(session)
    source(session, "mcp")
    zone = create_zone(session, "ruecknahme")
    _row, writer = _issue(
        session, "schreiber", [("zone.read", None), ("zone.manage", None), ("audit.read", None)]
    )
    result = server.set_control_parameters(session, writer, zone.id, "hysteresis_k", Decimal("0.7"))
    return zone, int(result["change_id"]), writer, _tools(session, monkeypatch, writer)  # type: ignore[call-overload]


def _hysteresis(session: Session, zone: Zone) -> Decimal | None:
    session.refresh(zone)
    return zone.hysteresis_k


def test_revert_change_restores_the_parameter_and_returns_the_new_entry(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    zone, change_id, _writer, tools = _changed_parameter(session, monkeypatch)
    assert _hysteresis(session, zone) == Decimal("0.7")
    _row, rollback_token = _issue(
        session, "zuruecknehmer", [("zone.read", None), ("zone.manage", None)]
    )
    rolling = _tools(session, monkeypatch, rollback_token)

    result = rolling["revert_change"](change_id)

    assert _hysteresis(session, zone) is None  # wieder geerbt
    assert result["status"] == "reverted"
    assert result["reverts"] == change_id
    assert result["token"] == "zuruecknehmer"
    assert result["fields"] == [{"field": "hysteresis_k", "before": "0.7", "after": None}]
    assert result["revertible"] is False and result["forced"] is False
    original = session.get(ChangeLog, change_id)
    assert original is not None and original.reverted_by_id == result["id"]
    assert tools["list_changes"]()[0]["id"] == result["id"]


def test_revert_change_rights_are_those_of_the_change(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    zone, change_id, _writer, _tools_ = _changed_parameter(session, monkeypatch)
    _row, reader = _issue(session, "nurleser-rn", [("zone.read", None), ("audit.read", None)])
    reading = _tools(session, monkeypatch, reader)

    with pytest.raises(ToolError, match="zone.manage"):
        reading["revert_change"](change_id)

    assert _hysteresis(session, zone) == Decimal("0.7")
    assert [e.reverted_at for e in _entries(session)] == [None]


def test_revert_change_conflict_names_current_and_expected_value(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    zone, change_id, writer, tools = _changed_parameter(session, monkeypatch)
    server.set_control_parameters(session, writer, zone.id, "hysteresis_k", Decimal("0.9"))

    with pytest.raises(ToolError) as caught:
        tools["revert_change"](change_id)

    message = str(caught.value)
    assert "hysteresis_k" in message and "0.9" in message and "0.7" in message
    assert "force" in message
    assert _hysteresis(session, zone) == Decimal("0.9")  # nichts verändert
    assert session.get(ChangeLog, change_id).reverted_at is None  # type: ignore[union-attr]


def test_revert_change_with_force_overrides_the_conflict_and_says_so(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    zone, change_id, writer, tools = _changed_parameter(session, monkeypatch)
    server.set_control_parameters(session, writer, zone.id, "hysteresis_k", Decimal("0.9"))

    result = tools["revert_change"](change_id, force=True)

    assert result["forced"] is True and result["status"] == "reverted"
    assert _hysteresis(session, zone) is None


def test_revert_change_twice_is_refused_readably(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _zone, change_id, _writer, tools = _changed_parameter(session, monkeypatch)
    first = tools["revert_change"](change_id)

    with pytest.raises(ToolError, match="bereits zurückgenommen"):
        tools["revert_change"](change_id)

    assert _count(session) == 2  # das Original und genau eine Rücknahme
    assert first["status"] == "reverted"


def test_a_rollback_is_not_rolled_back_again(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _zone, change_id, _writer, tools = _changed_parameter(session, monkeypatch)
    rollback = tools["revert_change"](change_id)

    with pytest.raises(ToolError, match="nicht erneut zurückgenommen"):
        tools["revert_change"](rollback["id"])


def test_an_irreversible_kind_is_refused_readably(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    create_settings(session)
    source(session, "mcp")
    arm(session, True, reason="von Hand", user_id=None)
    _row, plaintext = _issue(
        session,
        "notaus-rn",
        [("zone.read", None), ("control.disarm", None), ("audit.read", None)],
    )
    tools = _tools(session, monkeypatch, plaintext)
    change_id = tools["force_dry_run"]("Test")["change_id"]
    assert tools["list_changes"]()[0]["revertible"] is False

    with pytest.raises(ToolError, match="lässt sich nicht zurücknehmen"):
        tools["revert_change"](change_id)

    assert session.get(Setting, 1).control_armed is False  # type: ignore[union-attr]


def test_an_unknown_change_is_refused_readably(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _row, plaintext = _issue(session, "sucher-rn", [("zone.read", None), ("zone.manage", None)])
    tools = _tools(session, monkeypatch, plaintext)

    with pytest.raises(ToolError, match="gibt es nicht"):
        tools["revert_change"](999_999)


def test_the_missing_right_is_reported_before_any_state_is_disclosed(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ein Token ohne Recht erfährt weder den aktuellen noch den erwarteten Wert."""
    zone, change_id, writer, _tools_ = _changed_parameter(session, monkeypatch)
    server.set_control_parameters(session, writer, zone.id, "hysteresis_k", Decimal("0.9"))
    _row, reader = _issue(session, "neugierig", [("zone.read", None), ("audit.read", None)])
    reading = _tools(session, monkeypatch, reader)

    with pytest.raises(ToolError) as caught:
        reading["revert_change"](change_id)
    with pytest.raises(ToolError) as forced:
        reading["revert_change"](change_id, force=True)

    for error in (caught, forced):
        message = str(error.value)
        assert "zone.manage" in message
        assert "0.9" not in message and "0.7" not in message
        assert "ist jetzt" not in message
    # ... und auch nicht, ob der Eintrag schon zurückgenommen ist:
    writing = _tools(session, monkeypatch, writer)
    writing["revert_change"](change_id, force=True)
    with pytest.raises(ToolError) as after:
        reading["revert_change"](change_id)
    assert "zone.manage" in str(after.value) and "zurückgenommen" not in str(after.value)


def test_revert_change_is_logged_as_rejected_with_the_domain_message(
    session: Session, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    _zone, change_id, _writer, tools = _changed_parameter(session, monkeypatch)
    tools["revert_change"](change_id)
    caplog.clear()
    caplog.set_level(logging.INFO)

    with pytest.raises(ToolError):
        tools["revert_change"](change_id)

    records = [r for r in caplog.records if getattr(r, "tool", "") == "revert_change"]
    assert [r.outcome for r in records] == ["rejected"]  # type: ignore[attr-defined]
    assert "bereits zurückgenommen" in records[0].reason  # type: ignore[attr-defined]


# --- gegenstandslose Rücknahme: Markierung muss committet werden -------------------------


def test_an_already_effective_revert_is_an_answer_and_keeps_its_mark(tmp_path: Path) -> None:
    """Mit echter Transaktion: eine Ausnahme hätte die Markierung zurückgerollt."""
    engine = create_engine(f"sqlite:///{tmp_path / 'wirkungslos.db'}", connect_args={"timeout": 30})

    @event.listens_for(engine, "connect")
    def _pragma(dbapi_connection, _record) -> None:  # type: ignore[no-untyped-def]
        dbapi_connection.execute("PRAGMA journal_mode=WAL")

    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as db:
        zone = zone_with_schedule(db, "gegenstandslos", [(1, 0, "tag-gs", Decimal("20.0"))])
        source(db, "mcp")
        _row, plaintext = _issue(db, "revertierer", ALL_RIGHTS)
        zone_id = zone.id
        db.commit()

    tools: Tools = {}

    class RecordingServer:
        def tool(self, name: str | None = None):  # type: ignore[no-untyped-def]
            def decorate(function):  # type: ignore[no-untyped-def]
                tools[str(name)] = function
                return function

            return decorate

    server._register_tools(RecordingServer(), factory, plaintext)  # type: ignore[arg-type]

    created = tools["override"](zone_id, Decimal("22.0"))
    tools["cancel_override"](zone_id)

    result = tools["revert_change"](created["change_id"])

    assert result["status"] == "already_effective"
    assert result["change_id"] == created["change_id"]
    assert "beendet" in str(result["message"])
    with factory() as check:
        original = check.get(ChangeLog, created["change_id"])
        assert original is not None
        assert original.reverted_at is not None  # die Markierung ist festgeschrieben
        assert original.reverted_by_id is None
        assert (
            check.scalar(select(func.count()).select_from(ChangeLog)) == 2
        )  # kein Rücknahme-Eintrag
    with pytest.raises(ToolError, match="wirkungslos"):
        tools["revert_change"](created["change_id"])
    engine.dispose()
