"""`audit.read` ist raumbezogen: anlagenweit sieht es alles, auf Räume beschränkt nur deren.

Geprüft werden Bereich (`ZoneScope`), Zuordnung der Audit-Ereignisse zu Räumen, jede Ansicht
(Protokoll, CSV, Schaltprotokoll, Relaisverschleiß, Änderungen samt Rücknahme), REST und MCP,
die Navigation und die Vergabe mit Raum. Maßstab ist immer: ein beschränkter Benutzer erfährt
nichts über Räume außerhalb seines Bereichs -- weder in Einträgen noch in Seitenzählung,
`has_more` oder Auswahllisten.
"""

import csv
import io
from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from tests.helpers import (
    create_all_permissions,
    create_device,
    create_device_command,
    create_settings,
    create_zone,
    role,
    source,
    user_with_permissions,
)
from thermoctl import audit
from thermoctl.auth.csrf import CSRF_HEADER, csrf_token
from thermoctl.auth.sessions import COOKIE_NAME
from thermoctl.auth.tokens import issue_token
from thermoctl.config import get_settings
from thermoctl.db.models.device import ZoneDevice
from thermoctl.db.models.identity import AccessGroup, GroupPermission
from thermoctl.db.models.lookup import Permission
from thermoctl.db.models.operations import AuditEvent
from thermoctl.db.models.sensor_failure import ActuatorDecision
from thermoctl.db.models.state import DeviceCommand, ShadowDecision
from thermoctl.db.models.zone import Zone
from thermoctl.domain import audit_query
from thermoctl.domain import change_log as cl
from thermoctl.domain.authz import (
    Forbidden,
    ZoneScope,
    permission_scope,
    require_scope,
)
from thermoctl.domain.change_log import Actor
from thermoctl.domain.control import arm as control_arm
from thermoctl.domain.device_commands import list_commands
from thermoctl.domain.principal import Principal
from thermoctl.domain.solar_setback_log import setback_transitions, setback_zone_names
from thermoctl.mcp import server
from thermoctl.web.navigation import visible_navigation

Als = Callable[[list[tuple[str, int | None]]], TestClient]
T0 = datetime(2026, 10, 1, 12, 0)


# -- Aufbau -------------------------------------------------------------------------------


def _csrf(client: TestClient) -> dict[str, str]:
    http_session = client.cookies.get(COOKIE_NAME)
    assert http_session is not None
    return {CSRF_HEADER: csrf_token(http_session, get_settings().secret_key.get_secret_value())}


def _event(session: Session, summary: str, object_type: str, object_id: str | None) -> None:
    audit.record(
        session,
        source="web",
        action=f"{object_type}.test",
        object_type=object_type,
        object_id=object_id,
        summary=summary,
    )
    session.flush()


def _assignment(session: Session, zone: Zone, name: str) -> ZoneDevice:
    device = create_device(session, name)
    row = ZoneDevice(
        zone_id=zone.id, device_id=device.id, device_role_id=role(session, "actuator").id
    )
    session.add(row)
    session.flush()
    return row


def _decision(session: Session, zone_name: str, device_name: str, assignment: ZoneDevice | None,
              at: datetime) -> None:
    session.add(
        ActuatorDecision(
            episode_id=None,
            zone_device_id=assignment.id if assignment is not None else None,
            zone_name=zone_name,
            device_name=device_name,
            decided_at=at,
            action="handover",
            reason_code="sensorausfall_uebergabe",
            reason="Testentscheidung.",
            phase=None,
            phase_deadline_at=None,
            simulated=False,
            cycle_source=None,
            on_seconds=None,
            off_seconds=None,
            outdoor_c=None,
            profile_version=1,
        )
    )
    session.flush()


def _setback(session: Session, zone: Zone) -> None:
    for minute, k in enumerate([None, Decimal("1.0"), Decimal("1.0"), None]):
        scheduled = Decimal("21.0")
        session.add(
            ShadowDecision(
                decided_at=T0 + timedelta(minutes=minute),
                zone_id=zone.id,
                setpoint_c=scheduled - (k or Decimal(0)),
                scheduled_setpoint_c=scheduled,
                solar_setback_k=k,
                setpoint_reason="Zeitplan",
                would_heat=False,
                outcome_code="aus",
                reason="Sollwert ist erreicht.",
            )
        )
    session.flush()


@pytest.fixture
def welt(session: Session) -> SimpleNamespace:
    """Zwei Räume (eigener/fremder), von jedem Ereignisse, Befehle und Änderungen."""
    create_settings(session)
    create_all_permissions(session)
    for code in ("web", "api", "system"):
        source(session, code)
    own = create_zone(session, "eigen")
    foreign = create_zone(session, "fremd")
    own_assignment = _assignment(session, own, "eigenes-relais")
    foreign_assignment = _assignment(session, foreign, "fremdes-relais")

    actor = Actor(None, None, "web")
    own_change = cl.set_parameter(session, actor, own, "hysteresis_k", Decimal("0.7"))
    foreign_change = cl.set_parameter(session, actor, foreign, "hysteresis_k", Decimal("0.7"))
    control_arm(session, True, reason="von Hand", user_id=None)
    plant_change = cl.force_dry_run(session, actor, reason="Test")
    assert plant_change.change_id is not None
    # Die Änderungen schreiben eigene Audit-Ereignisse; für die Zählungen unten zählt nur,
    # was dieser Aufbau ausdrücklich anlegt.
    session.execute(delete(AuditEvent))

    _event(session, "Raum eigen", "zone", str(own.id))
    _event(session, "Raum fremd", "zone", str(foreign.id))
    _event(session, "Einstellungen eigen", "zone_settings", str(own.id))
    _event(session, "Einstellungen fremd", "zone_settings", str(foreign.id))
    _event(session, "Zeitplan eigen", "schedule", str(own.id))
    _event(session, "Zeitplan fremd", "schedule", str(foreign.id))
    _event(session, "Punkt eigen", "schedule_point", f"{own.id}:5")
    _event(session, "Punkt fremd", "schedule_point", f"{foreign.id}:5")
    _event(session, "Zuordnung eigen", "zone_device", str(own_assignment.id))
    _event(session, "Zuordnung fremd", "zone_device", str(foreign_assignment.id))
    # Ohne eindeutigen Raum: nur Anlagenweite.
    _event(session, "Benutzer angelegt", "user", "1")
    _event(session, "Globale Einstellung", "setting", "1")
    _event(session, "Gruppe", "access_group", "1")
    # Eine Zonen-ID als Präfix einer anderen darf nicht treffen ("1:5" vs "11:5").
    _event(session, "Punkt anderer Raum", "schedule_point", f"{own.id}0:5")
    # Gleiche Zahl, andere Objektart: nur die Zonenarten gehören zu Räumen.
    _event(session, "Token mit gleicher Zahl", "api_token", str(own.id))

    own_device = create_device(session, "eigenes-geraet")
    foreign_device = create_device(session, "fremdes-geraet")
    create_device_command(session, own, own_device, at=T0, command="switch",
                          payload='{"state": "ON"}')
    create_device_command(session, foreign, foreign_device, at=T0 + timedelta(minutes=1),
                          command="switch", payload='{"state": "ON"}')
    orphan = create_device_command(session, foreign, foreign_device,
                                   at=T0 + timedelta(minutes=2))
    # Das Protokoll führt die Räume unter ihrem Anzeigenamen (Momentaufnahme).
    for zone in (own, foreign):
        session.execute(
            update(DeviceCommand)
            .where(DeviceCommand.zone_id == zone.id)
            .values(zone_name=zone.display_name)
        )
    orphan.zone_id = None  # Raum gelöscht: nur der Namensabzug bleibt
    orphan.zone_name = "Gelöscht"
    session.flush()
    _decision(
        session, own.display_name, "eigenes-relais", own_assignment, T0 + timedelta(minutes=3)
    )
    _decision(session, foreign.display_name, "fremdes-relais", foreign_assignment,
              T0 + timedelta(minutes=4))
    _decision(session, "Gelöscht", "altes-relais", None, T0 + timedelta(minutes=5))
    _setback(session, own)
    _setback(session, foreign)

    return SimpleNamespace(
        own=own, foreign=foreign, own_change=own_change.change_id,
        foreign_change=foreign_change.change_id, plant_change=plant_change.change_id,
    )


def _summaries(rows: Any) -> list[str]:
    return [event.summary for event, *_ in rows]


# -- Bereich ------------------------------------------------------------------------------


def _principal(*grants: tuple[str, int | None]) -> Principal:
    return Principal(user_id=1, token_id=None, grants=frozenset(grants))


def test_scope_plant_wide_covers_everything_including_rows_without_a_zone() -> None:
    scope = permission_scope(_principal(("audit.read", None)), "audit.read")
    assert scope == ZoneScope(None) and scope.plant_wide
    assert scope.covers(3) and scope.covers(None)


def test_scope_for_zones_covers_only_those_and_never_a_row_without_zone() -> None:
    scope = permission_scope(_principal(("audit.read", 2), ("audit.read", 4)), "audit.read")
    assert scope == ZoneScope(frozenset({2, 4})) and not scope.plant_wide
    assert scope.covers(2) and scope.covers(4)
    assert not scope.covers(3) and not scope.covers(None)


def test_scope_plant_wide_wins_over_zone_grants_and_other_codes_do_not_count() -> None:
    both = _principal(("audit.read", 2), ("audit.read", None))
    assert permission_scope(both, "audit.read") == ZoneScope(None)
    other = _principal(("zone.read", None), ("zone.read", 2))
    assert permission_scope(other, "audit.read") is None


def test_require_scope_raises_without_any_grant() -> None:
    with pytest.raises(Forbidden, match="audit.read"):
        require_scope(_principal(("zone.read", None)), "audit.read")
    assert require_scope(_principal(("audit.read", 1)), "audit.read").zone_ids == {1}


# -- Audit-Abfrage ------------------------------------------------------------------------


def test_audit_query_without_scope_returns_every_event(
    session: Session, welt: SimpleNamespace
) -> None:
    rows, _more = audit_query.list_audit(session)
    assert len(rows) == 15


def test_audit_query_scope_matches_only_events_attributable_to_the_zone(
    session: Session, welt: SimpleNamespace
) -> None:
    rows, more = audit_query.list_audit(session, zone_ids=[welt.own.id])
    assert more is False
    assert sorted(_summaries(rows)) == sorted(
        ["Raum eigen", "Einstellungen eigen", "Zeitplan eigen", "Punkt eigen", "Zuordnung eigen"]
    )


def test_audit_query_scope_with_several_zones_and_exports(
    session: Session, welt: SimpleNamespace
) -> None:
    ids = [welt.own.id, welt.foreign.id]
    rows, truncated = audit_query.export_audit(session, zone_ids=ids)
    assert truncated is False and len(rows) == 10
    nobody, _ = audit_query.export_audit(session, zone_ids=[999_999])
    assert nobody == []


def test_audit_query_scope_combines_with_the_other_filters(
    session: Session, welt: SimpleNamespace
) -> None:
    rows, _ = audit_query.list_audit(session, zone_ids=[welt.own.id], object="schedule")
    assert _summaries(rows) == ["Zeitplan eigen"]
    # Ein Objektfilter auf einen fremden Raum findet im beschränkten Bereich nichts.
    rows, _ = audit_query.list_audit(
        session, zone_ids=[welt.own.id], object=f"zone:{welt.foreign.id}"
    )
    assert rows == []
    rows, _ = audit_query.list_audit(session, zone_ids=[welt.own.id], text="Benutzer")
    assert rows == []


def test_audit_query_scope_paginates_inside_the_query(
    session: Session, welt: SimpleNamespace
) -> None:
    """51 eigene Ereignisse, dazwischen und danach viele fremde: Seiten und `has_more` zählen
    nur eigene (ein Filter nach dem Laden ergäbe kurze Seiten und falsches `has_more`)."""
    for index in range(51):
        _event(session, f"eigen-{index}", "zone", str(welt.own.id))
        _event(session, f"fremd-{index}", "zone", str(welt.foreign.id))
        _event(session, f"fremd-user-{index}", "user", "9")
    first, more = audit_query.list_audit(session, zone_ids=[welt.own.id], page=1)
    assert len(first) == 50 and more is True
    assert all("fremd" not in event.summary for event, *_ in first)
    second, more = audit_query.list_audit(session, zone_ids=[welt.own.id], page=2)
    assert len(second) == 6 and more is False  # 5 aus dem Aufbau + 1


def test_scoped_actions_and_sources_reveal_nothing_about_invisible_events(
    session: Session, welt: SimpleNamespace
) -> None:
    source(session, "kiosk")
    audit.record(session, source="kiosk", action="kiosk.geheim", object_type="user", summary="x")
    session.flush()
    everything = audit_query.scoped_actions(session, None)
    assert "user.test" in everything and "kiosk.geheim" in everything
    # Reihenfolge ist Sache der Datenbankkollation (SQLite und MariaDB sortieren `.`/`_`
    # verschieden); es zählt, welche Aktionen vorkommen.
    assert set(audit_query.scoped_actions(session, [welt.own.id])) == {
        "schedule.test", "schedule_point.test", "zone.test", "zone_device.test",
        "zone_settings.test",
    }
    assert audit_query.scoped_source_codes(session, None) is None
    assert audit_query.scoped_source_codes(session, [welt.own.id]) == {"web"}


# -- Schaltprotokoll (Domäne) -------------------------------------------------------------


def test_command_log_without_scope_shows_every_kind_and_orphans(
    session: Session, welt: SimpleNamespace
) -> None:
    entries = list_commands(session)
    assert {e.zone_name.lower() for e in entries} >= {"eigen", "fremd", "gelöscht"}
    assert {e.entry_kind for e in entries} == {"befehl", "entscheidung", "absenkung"}


def test_command_log_scope_filters_commands_decisions_and_setbacks(
    session: Session, welt: SimpleNamespace
) -> None:
    entries = list_commands(session, zone_ids=[welt.own.id])
    assert {e.zone_name.lower() for e in entries} == {"eigen"}
    assert {e.entry_kind for e in entries} == {"befehl", "entscheidung", "absenkung"}


def test_command_log_scope_pages_inside_the_queries(
    session: Session, welt: SimpleNamespace
) -> None:
    device = create_device(session, "viel")
    for minute in range(10, 70):
        create_device_command(session, welt.own, device, at=T0 + timedelta(minutes=minute))
    for minute in range(70, 130):
        create_device_command(session, welt.foreign, device, at=T0 + timedelta(minutes=minute))
    page = list_commands(session, zone_ids=[welt.own.id], limit=51, offset=0)
    assert len(page) == 51 and {e.zone_name.lower() for e in page} == {"eigen"}
    rest = list_commands(session, zone_ids=[welt.own.id], limit=51, offset=51)
    assert {e.zone_name.lower() for e in rest} == {"eigen"}
    # 60 + 1 Befehl + 1 Entscheidung + 2 Absenkungen
    assert len(page) + len(rest) == 64


def test_setback_scope_limits_transitions_and_zone_names(
    session: Session, welt: SimpleNamespace
) -> None:
    kwargs: dict[str, Any] = {"zone_name": None, "from_at": None, "to_at": None, "fetch": 50}
    assert {t.zone_name for t in setback_transitions(session, **kwargs)} == {"eigen", "fremd"}
    only = setback_transitions(session, zone_ids=[welt.own.id], **kwargs)
    assert {t.zone_name for t in only} == {"eigen"}
    assert setback_zone_names(session) == ["Eigen", "Fremd"]
    assert setback_zone_names(session, [welt.own.id]) == ["Eigen"]
    assert setback_transitions(session, zone_ids=[], **kwargs) == []


# -- Änderungsprotokoll (Domäne) ----------------------------------------------------------


def test_change_list_scope_filters_in_the_query(session: Session, welt: SimpleNamespace) -> None:
    everything = cl.list_changes(session)
    assert {v.id for v in everything} == {welt.own_change, welt.foreign_change, welt.plant_change}
    scoped = cl.list_changes(session, zone_ids=[welt.own.id])
    assert [v.id for v in scoped] == [welt.own_change]
    # Ausdrücklich nach einem fremden Raum gefragt: leer statt Fehler und ohne Auskunft.
    assert cl.list_changes(session, zone_ids=[welt.own.id], zone_id=welt.foreign.id) == []
    # Seitenlänge zählt nur sichtbare Einträge.
    assert len(cl.list_changes(session, zone_ids=[welt.own.id], limit=1)) == 1


# -- Weboberfläche: Protokoll -------------------------------------------------------------


def test_web_audit_plant_wide_sees_everything_as_before(
    client_als: Als, welt: SimpleNamespace
) -> None:
    body = client_als([("audit.read", None), ("zone.read", None)]).get("/ui-api/audit").json()
    assert len(body["entries"]) == 15
    assert {z["id"] for z in body["zones"]} == {welt.own.id, welt.foreign.id}
    assert "user.test" in body["actions"]


def test_web_audit_scoped_shows_only_own_rooms(client_als: Als, welt: SimpleNamespace) -> None:
    client = client_als([("audit.read", welt.own.id), ("zone.read", None)])
    body = client.get("/ui-api/audit").json()
    assert sorted(e["summary"] for e in body["entries"]) == sorted(
        ["Raum eigen", "Einstellungen eigen", "Zeitplan eigen", "Punkt eigen", "Zuordnung eigen"]
    )
    # Auswahllisten verraten nichts über unsichtbare Räume oder Ereignisse.
    assert body["zones"] == [{"id": welt.own.id, "name": "eigen"}]
    assert "user.test" not in body["actions"] and "setting.test" not in body["actions"]
    assert {s["code"] for s in body["sources"]} == {"web"}
    # Auch ein ausdrücklicher Filter auf einen fremden Raum ergibt nichts.
    filtered = client.get("/ui-api/audit", params={"object": f"zone:{welt.foreign.id}"}).json()
    assert filtered["entries"] == [] and filtered["has_more"] is False


def test_web_audit_scoped_grant_without_zone_read_still_filters(
    client_als: Als, welt: SimpleNamespace
) -> None:
    body = client_als([("audit.read", welt.own.id)]).get("/ui-api/audit").json()
    assert len(body["entries"]) == 5
    assert body["zones"] == []  # die Raumliste kommt aus `zone.read`, nie aus dem Protokoll


def test_web_audit_scoped_pagination_and_has_more(client_als: Als, session: Session,
                                                  welt: SimpleNamespace) -> None:
    for index in range(46):
        _event(session, f"eigen-{index}", "zone", str(welt.own.id))
    for index in range(60):
        _event(session, f"fremd-{index}", "zone", str(welt.foreign.id))
    client = client_als([("audit.read", welt.own.id)])
    first = client.get("/ui-api/audit").json()
    assert len(first["entries"]) == 50 and first["has_more"] is True
    second = client.get("/ui-api/audit", params={"page": "2"}).json()
    assert len(second["entries"]) == 1 and second["has_more"] is False
    third = client.get("/ui-api/audit", params={"page": "3"}).json()
    assert third["entries"] == [] and third["has_more"] is False
    everything = [e["summary"] for e in first["entries"] + second["entries"]]
    assert not any(summary.startswith("fremd") for summary in everything)


def test_web_audit_csv_is_filtered_identically(client_als: Als, welt: SimpleNamespace) -> None:
    scoped = client_als([("audit.read", welt.own.id)]).get("/ui-api/audit.csv")
    rows = list(csv.reader(io.StringIO(scoped.text)))
    assert len(rows) == 1 + 5
    assert not any("fremd" in cell or "Benutzer" in cell for row in rows for cell in row)
    wide = client_als([("audit.read", None)]).get("/ui-api/audit.csv")
    assert len(list(csv.reader(io.StringIO(wide.text)))) == 1 + 15


def test_web_audit_without_the_right_is_still_403(client_als: Als, welt: SimpleNamespace) -> None:
    for path in ("/ui-api/audit", "/ui-api/audit.csv", "/ui-api/changes", "/ui-api/relay-wear",
                 "/ui-api/device-commands"):
        response = client_als([("zone.read", None)]).get(path)
        assert response.status_code == 403, path


# -- Weboberfläche: Schaltprotokoll und Verschleiß -----------------------------------------


def test_web_device_commands_plant_wide_sees_orphans_and_all_zone_names(
    client_als: Als, welt: SimpleNamespace
) -> None:
    body = client_als([("audit.read", None)]).get("/ui-api/device-commands").json()
    assert {e["zone_name"].lower() for e in body["entries"]} == {"eigen", "fremd", "gelöscht"}
    assert body["zone_names"] == ["Eigen", "Fremd", "Gelöscht"]


def test_web_device_commands_scoped_hides_other_rooms_orphans_and_names(
    client_als: Als, welt: SimpleNamespace
) -> None:
    client = client_als([("audit.read", welt.own.id)])
    body = client.get("/ui-api/device-commands").json()
    assert {e["zone_name"].lower() for e in body["entries"]} == {"eigen"}
    assert {e["kind"] for e in body["entries"]} == {"befehl", "entscheidung", "absenkung"}
    assert body["zone_names"] == ["Eigen"]
    # Namensfilter auf einen fremden Raum: leer, ohne Auskunft.
    other = client.get("/ui-api/device-commands", params={"zone": "Fremd"}).json()
    assert other["entries"] == [] and other["has_more"] is False


def test_web_device_commands_scoped_has_more_counts_only_visible_rows(
    client_als: Als, session: Session, welt: SimpleNamespace
) -> None:
    device = create_device(session, "masse")
    for minute in range(10, 60):  # 50 eigene -> mit den 4 aus dem Aufbau 54
        create_device_command(session, welt.own, device, at=T0 + timedelta(minutes=minute))
    for minute in range(60, 200):
        create_device_command(session, welt.foreign, device, at=T0 + timedelta(minutes=minute))
    client = client_als([("audit.read", welt.own.id)])
    first = client.get("/ui-api/device-commands").json()
    assert len(first["entries"]) == 50 and first["has_more"] is True
    second = client.get("/ui-api/device-commands", params={"page": "2"}).json()
    assert len(second["entries"]) == 4 and second["has_more"] is False
    assert {e["zone_name"].lower() for e in first["entries"] + second["entries"]} == {"eigen"}


def test_web_relay_wear_scoped_shows_only_devices_of_the_zones(
    client_als: Als, session: Session, welt: SimpleNamespace
) -> None:
    base = (datetime.now() - timedelta(days=2)).replace(hour=12, minute=0, second=0, microsecond=0)
    for zone, device_name in ((welt.own, "eigen-r"), (welt.foreign, "fremd-r")):
        device = create_device(session, device_name)
        for minutes, state in ((0, "ON"), (10, "OFF"), (20, "ON")):
            create_device_command(
                session, zone, device, at=base + timedelta(minutes=minutes), command="switch",
                payload=f'{{"state": "{state}"}}',
            )
    wide = client_als([("audit.read", None), ("zone.read", None)]).get("/ui-api/relay-wear").json()
    assert {d["device_name"] for d in wide["devices"]} == {"eigen-r", "fremd-r"}

    client = client_als([("audit.read", welt.own.id), ("zone.read", None)])
    scoped = client.get("/ui-api/relay-wear").json()
    assert {d["device_name"] for d in scoped["devices"]} == {"eigen-r"}
    response = client.get("/ui-api/relay-wear", params={"zone_id": welt.foreign.id})
    assert response.status_code == 404 and response.json()["code"] == "not_found"
    assert client.get("/ui-api/relay-wear", params={"zone_id": welt.own.id}).status_code == 200


def test_web_relay_wear_scoped_needs_zone_read_for_the_zone_too(
    client_als: Als, welt: SimpleNamespace
) -> None:
    body = client_als([("audit.read", welt.own.id), ("zone.read", welt.foreign.id)]).get(
        "/ui-api/relay-wear"
    ).json()
    assert body["devices"] == []


# -- Weboberfläche: Änderungen und Rücknahme -----------------------------------------------


def test_web_changes_plant_wide_sees_all_including_plant_changes(
    client_als: Als, welt: SimpleNamespace
) -> None:
    body = client_als([("audit.read", None)]).get("/ui-api/changes").json()
    assert {e["id"] for e in body["entries"]} == {
        welt.own_change, welt.foreign_change, welt.plant_change
    }


def test_web_changes_scoped_lists_and_opens_only_own_rooms(
    client_als: Als, welt: SimpleNamespace
) -> None:
    client = client_als([("audit.read", welt.own.id)])
    body = client.get("/ui-api/changes").json()
    assert [e["id"] for e in body["entries"]] == [welt.own_change] and body["has_more"] is False
    assert {s["code"] for s in body["sources"]} >= {"web"}
    assert client.get(f"/ui-api/changes/{welt.own_change}").status_code == 200
    for hidden in (welt.foreign_change, welt.plant_change, 999_999):
        response = client.get(f"/ui-api/changes/{hidden}")
        assert response.status_code == 404 and response.json()["code"] == "not_found", hidden
    asked = client.get("/ui-api/changes", params={"zone_id": welt.foreign.id}).json()
    assert asked["entries"] == [] and asked["has_more"] is False


def test_web_revert_scoped_works_inside_the_scope_with_the_write_right(
    client_als: Als, session: Session, welt: SimpleNamespace
) -> None:
    client = client_als([("audit.read", welt.own.id), ("zone.manage", welt.own.id)])
    response = client.post(
        f"/ui-api/changes/{welt.own_change}/revert", json={}, headers=_csrf(client)
    )
    assert response.status_code == 200, response.text
    assert response.json()["outcome"] == "reverted"


def test_web_revert_scoped_outside_the_scope_is_404_even_with_the_write_right(
    client_als: Als, session: Session, welt: SimpleNamespace
) -> None:
    client = client_als(
        [("audit.read", welt.own.id), ("zone.manage", None), ("control.disarm", None)]
    )
    for hidden in (welt.foreign_change, welt.plant_change, 999_999):
        response = client.post(
            f"/ui-api/changes/{hidden}/revert", json={}, headers=_csrf(client)
        )
        assert response.status_code == 404, hidden
        assert response.json()["code"] == "not_found"
    session.expire_all()
    assert session.get(Zone, welt.foreign.id).hysteresis_k == Decimal("0.7")  # type: ignore[union-attr]


def test_web_revert_scoped_in_scope_still_needs_the_write_right_of_the_change(
    client_als: Als, welt: SimpleNamespace
) -> None:
    """`audit.read` für den Raum genügt nicht: die Rücknahme verlangt `zone.manage` für ihn."""
    only_read = client_als([("audit.read", welt.own.id)])
    response = only_read.post(
        f"/ui-api/changes/{welt.own_change}/revert", json={}, headers=_csrf(only_read)
    )
    assert response.status_code == 403 and response.json()["code"] == "forbidden"
    elsewhere = client_als([("audit.read", welt.own.id), ("zone.manage", welt.foreign.id)])
    response = elsewhere.post(
        f"/ui-api/changes/{welt.own_change}/revert", json={}, headers=_csrf(elsewhere)
    )
    assert response.status_code == 403


def test_web_revert_plant_wide_audit_read_keeps_the_old_rules(
    client_als: Als, welt: SimpleNamespace
) -> None:
    client = client_als([("audit.read", None), ("zone.manage", welt.foreign.id)])
    ok = client.post(
        f"/ui-api/changes/{welt.foreign_change}/revert", json={}, headers=_csrf(client)
    )
    assert ok.status_code == 200
    denied = client.post(
        f"/ui-api/changes/{welt.own_change}/revert", json={}, headers=_csrf(client)
    )
    assert denied.status_code == 403


# -- Navigation und Rechtevergabe ---------------------------------------------------------


@pytest.mark.parametrize("path", ["/audit", "/device-commands", "/changes", "/relay-wear"])
def test_navigation_shows_the_protocols_for_a_zone_scoped_audit_read(path: str) -> None:
    assert path in {i.path for i in visible_navigation(_principal(("audit.read", 3)))}
    assert path in {i.path for i in visible_navigation(_principal(("audit.read", None)))}
    assert path not in {i.path for i in visible_navigation(_principal(("zone.read", None)))}


def test_bootstrap_navigation_lists_the_protocols_for_a_scoped_grant(
    client_als: Als, welt: SimpleNamespace
) -> None:
    body = client_als([("audit.read", welt.own.id)]).get("/ui-api/bootstrap").json()
    paths = {item["path"] for item in body["navigation"]}
    assert {"/audit", "/device-commands", "/changes", "/relay-wear"} <= paths


def test_audit_read_is_zone_scoped_in_the_permission_list(
    session: Session, welt: SimpleNamespace
) -> None:
    permission = session.scalar(select(Permission).where(Permission.code == "audit.read"))
    assert permission is not None and permission.is_zone_scoped is True


def test_a_group_can_now_be_granted_audit_read_for_a_zone(
    client_als: Als, session: Session, welt: SimpleNamespace
) -> None:
    client = client_als([(code, None) for code in ("group.manage", "user.manage", "zone.read")])
    group = AccessGroup(name="Hausmeister")
    session.add(group)
    session.flush()
    wanted = [{"code": "audit.read", "zone_id": welt.own.id}]
    response = client.put(
        f"/ui-api/access/groups/{group.id}/permissions",
        json={"permissions": wanted},
        headers=_csrf(client),
    )
    assert response.status_code == 200, response.text
    rows = session.scalars(
        select(GroupPermission).where(GroupPermission.access_group_id == group.id)
    ).all()
    assert [(r.zone_id) for r in rows] == [welt.own.id]


def test_the_permission_matrix_offers_rooms_for_audit_read(
    client_als: Als, welt: SimpleNamespace
) -> None:
    """Die Rechteliste der Gruppenseite kommt aus der Datenbank: `audit.read` trägt jetzt das
    Raumkennzeichen, die Oberfläche bietet also Räume dafür an."""
    client = client_als([(code, None) for code in ("group.manage", "zone.read")])
    areas = client.get("/ui-api/access/groups").json()["areas"]
    flags = {p["code"]: p["zone_scoped"] for area in areas for p in area["permissions"]}
    assert flags["audit.read"] is True
    assert flags["user.manage"] is False  # Gegenprobe: andere Anlagenrechte bleiben anlagenweit


# -- REST ---------------------------------------------------------------------------------


def _bearer(session: Session, name: str, grants: list[tuple[str, int | None]]) -> dict[str, str]:
    owner = user_with_permissions(session, name, grants)
    _token, plaintext = issue_token(session, owner, name, grants, None)
    return {"Authorization": f"Bearer {plaintext}"}


def test_rest_device_commands_plant_wide_token_sees_all(
    client: TestClient, session: Session, welt: SimpleNamespace
) -> None:
    head = _bearer(session, "rest-weit", [("audit.read", None)])
    body = client.get("/api/v1/device-commands?limit=100", headers=head).json()
    assert {e["zone"].lower() for e in body} == {"eigen", "fremd", "gelöscht"}


def test_rest_device_commands_zone_scoped_token_sees_only_its_zone(
    client: TestClient, session: Session, welt: SimpleNamespace
) -> None:
    head = _bearer(session, "rest-eng", [("audit.read", welt.own.id)])
    body = client.get("/api/v1/device-commands?limit=100", headers=head).json()
    assert body and {e["zone"].lower() for e in body} == {"eigen"}
    asked = client.get("/api/v1/device-commands?zone=Fremd", headers=head).json()
    assert asked == []


def test_rest_device_commands_token_without_audit_read_is_403(
    client: TestClient, session: Session, welt: SimpleNamespace
) -> None:
    head = _bearer(session, "rest-ohne", [("zone.read", None)])
    assert client.get("/api/v1/device-commands", headers=head).status_code == 403


def test_rest_a_token_cannot_widen_a_zone_scoped_audit_read_of_its_owner(
    session: Session, welt: SimpleNamespace
) -> None:
    """Ein Token ist höchstens so weit wie sein Besitzer: nur für den Raum vergeben, kein
    anlagenweites `audit.read` (und damit keine Auskunft über andere Räume)."""
    owner = user_with_permissions(session, "rest-besitzer", [("audit.read", welt.own.id)])
    with pytest.raises(Forbidden, match="audit.read"):
        issue_token(session, owner, "breit", [("audit.read", None)], None)
    issue_token(session, owner, "eng", [("audit.read", welt.own.id)], None)


# -- MCP ----------------------------------------------------------------------------------


def _mcp_token(session: Session, name: str, grants: list[tuple[str, int | None]]) -> str:
    owner = user_with_permissions(session, name, grants)
    return issue_token(session, owner, name, grants, None)[1]


def test_mcp_device_commands_scoped_token_sees_only_its_zone(
    session: Session, welt: SimpleNamespace
) -> None:
    wide = _mcp_token(session, "mcp-weit", [("audit.read", None)])
    assert {e["zone"].lower() for e in server.device_commands(session, wide)} == {
        "eigen", "fremd", "gelöscht"
    }
    narrow = _mcp_token(session, "mcp-eng", [("audit.read", welt.own.id)])
    assert {e["zone"].lower() for e in server.device_commands(session, narrow)} == {"eigen"}
    assert server.device_commands(session, narrow, zone="Fremd") == []


def test_mcp_device_commands_without_audit_read_is_forbidden(
    session: Session, welt: SimpleNamespace
) -> None:
    plain = _mcp_token(session, "mcp-ohne", [("zone.read", None)])
    with pytest.raises(Forbidden, match="audit.read"):
        server.device_commands(session, plain)


def test_mcp_list_changes_scoped_token_sees_only_its_zone(
    session: Session, welt: SimpleNamespace
) -> None:
    wide = _mcp_token(session, "mcp-chg-weit", [("audit.read", None)])
    assert {e["id"] for e in server.list_changes(session, wide)} == {
        welt.own_change, welt.foreign_change, welt.plant_change
    }
    narrow = _mcp_token(session, "mcp-chg-eng", [("audit.read", welt.own.id)])
    assert [e["id"] for e in server.list_changes(session, narrow)] == [welt.own_change]
    assert [e["id"] for e in server.list_changes(session, narrow, zone="eigen")] == [
        welt.own_change
    ]
    with pytest.raises(LookupError):
        server.list_changes(session, narrow, zone="fremd")


def test_mcp_revert_does_not_need_audit_read_but_the_write_right(
    session: Session, welt: SimpleNamespace
) -> None:
    """Die MCP-Rücknahme verlangt wie bisher nur die Rechte der Änderung, nicht `audit.read`."""
    token = _mcp_token(session, "mcp-revert", [("zone.manage", welt.own.id)])
    result = server.revert_change(session, token, welt.own_change)
    assert result["status"] == "reverted"
    with pytest.raises(Forbidden):
        server.revert_change(session, token, welt.foreign_change)
