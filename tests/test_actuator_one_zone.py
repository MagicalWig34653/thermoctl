"""Regel „ein Gerät ist höchstens Aktor einer Zone" -- in der Domäne, nicht in den Adaptern.

`assign_device` und `swap_device` lehnen eine NEUE Zuordnung zur Rolle `actuator` ab, wenn das Gerät
schon Aktor einer anderen Zone ist. Bestehende Doppelzuordnungen (Daten aus der Zeit vor der Regel)
bleiben unberührt und bedienbar. REST und MCP besitzen keinen Zuordnungsendpunkt; der Wächter am Ende
sorgt dafür, dass das so bleibt, ohne dass die Regel dort mitgeführt werden müsste.
"""
# ruff: noqa: E501

import dataclasses
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import thermoctl
from tests.helpers import capability, create_device, create_zone, role, source
from thermoctl.auth.csrf import CSRF_HEADER, csrf_token
from thermoctl.auth.sessions import COOKIE_NAME
from thermoctl.config import get_settings
from thermoctl.db.models.device import Device, DeviceCapabilityLink, ZoneDevice
from thermoctl.db.models.lookup import DeviceRole
from thermoctl.db.models.operations import AuditEvent
from thermoctl.db.models.zone import Zone
from thermoctl.domain import device_assignment
from thermoctl.domain.device_assignment import (
    ActuatorInAnotherZone,
    AssignmentAlreadyExists,
    assign_device,
    detach_device,
    set_temperature_source,
    set_thermostat_mode,
    swap_device,
)
from thermoctl.domain.mapping_map import Port, build_mapping_map, can_connect, zone_port_id
from thermoctl.web import mapping_map_views

ClientAls = Callable[[list[tuple[str, int | None]]], TestClient]


def _device(session: Session, name: str, *codes: str) -> Device:
    device = create_device(session, name)
    for code in codes:
        session.add(DeviceCapabilityLink(device_id=device.id, capability_id=capability(session, code).id))
    session.flush()
    return device


def _valve(session: Session, name: str) -> Device:
    return _device(session, name, "thermostat")


def _put(session: Session, zone: Zone, device: Device, code: str) -> ZoneDevice:
    """Schreibt eine Zuordnung am Domänenweg vorbei, so wie sie vor der Regel entstanden sein kann."""
    row = ZoneDevice(zone_id=zone.id, device_id=device.id, device_role_id=role(session, code).id)
    session.add(row)
    session.flush()
    return row


def _zones_of(session: Session, device: Device, code: str = "actuator") -> list[int]:
    return sorted(
        session.scalars(
            select(ZoneDevice.zone_id)
            .join(DeviceRole, DeviceRole.id == ZoneDevice.device_role_id)
            .where(ZoneDevice.device_id == device.id, DeviceRole.code == code)
        )
    )


def _csrf(client: TestClient) -> dict[str, str]:
    cookie = client.cookies.get(COOKIE_NAME)
    assert cookie is not None
    return {CSRF_HEADER: csrf_token(cookie, get_settings().secret_key.get_secret_value())}


def _audit_count(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(AuditEvent)) or 0


# ---- Domäne: assign_device ------------------------------------------------------------------------


def test_an_actuator_of_one_zone_is_refused_as_actuator_of_another(session: Session) -> None:
    source(session)
    bad, flur = create_zone(session, "bad"), create_zone(session, "flur")
    valve = _valve(session, "gemeinsames-ventil")
    actuator = role(session, "actuator")
    assign_device(session, bad, valve, actuator, actor_id=None)
    audit_before = _audit_count(session)

    with pytest.raises(ActuatorInAnotherZone) as refused:
        assign_device(session, flur, valve, actuator, actor_id=None)

    assert refused.value.notice == "'gemeinsames-ventil' ist bereits Aktor von Zone 'Bad'; ein Gerät kann nur eine Zone heizen."
    assert refused.value.zones == ((bad.id, "Bad"),)
    # Nichts geschrieben, kein Audit-Eintrag für eine abgelehnte Zuordnung.
    assert _zones_of(session, valve) == [bad.id]
    assert _audit_count(session) == audit_before


def test_the_refusal_names_every_other_zone(session: Session) -> None:
    source(session)
    a, b, c = create_zone(session, "za"), create_zone(session, "zb"), create_zone(session, "zc")
    valve = _valve(session, "altlast-ventil")
    _put(session, a, valve, "actuator")
    _put(session, b, valve, "actuator")
    with pytest.raises(ActuatorInAnotherZone, match="von Zone 'Za' und von Zone 'Zb'"):
        assign_device(session, c, valve, role(session, "actuator"), actor_id=None)


def test_one_zone_may_have_several_actuators(session: Session) -> None:
    source(session)
    zone = create_zone(session, "wohnen")
    first, second = _valve(session, "ventil-1"), _valve(session, "ventil-2")
    actuator = role(session, "actuator")
    assign_device(session, zone, first, actuator, actor_id=None)
    assign_device(session, zone, second, actuator, actor_id=None)
    assert _zones_of(session, first) == _zones_of(session, second) == [zone.id]


def test_the_same_valve_in_the_same_zone_stays_an_ordinary_duplicate(session: Session) -> None:
    source(session)
    zone, other = create_zone(session, "zone-x"), create_zone(session, "zone-y")
    valve = _valve(session, "doppel-ventil")
    actuator = role(session, "actuator")
    assign_device(session, zone, valve, actuator, actor_id=None)
    with pytest.raises(AssignmentAlreadyExists):
        assign_device(session, zone, valve, actuator, actor_id=None)
    # Und auch wenn das Gerät zusätzlich anderswo Aktor ist (Bestand): die eigene Zone gewinnt.
    _put(session, other, valve, "actuator")
    with pytest.raises(AssignmentAlreadyExists):
        assign_device(session, zone, valve, actuator, actor_id=None)


def test_only_the_actuator_role_is_limited(session: Session) -> None:
    """Fühler, Fensterkontakt, Messquelle und Regler dürfen weiterhin in mehreren Zonen stehen."""
    source(session)
    a, b = create_zone(session, "rolle-a"), create_zone(session, "rolle-b")
    sensor = _device(session, "fuehler", "temperature")
    contact = _device(session, "kontakt", "contact")
    knob = create_device(session, "regler")
    set_temperature_source(session, a, sensor, actor_id=None)
    set_temperature_source(session, b, sensor, actor_id=None)
    for zone in (a, b):
        assign_device(session, zone, contact, role(session, "window_contact"), actor_id=None)
        assign_device(session, zone, knob, role(session, "controller"), actor_id=None)
    assert _zones_of(session, contact, "window_contact") == sorted([a.id, b.id])
    assert _zones_of(session, knob, "controller") == sorted([a.id, b.id])
    assert a.temperature_source_device_id == b.temperature_source_device_id == sensor.id


def test_a_device_that_is_only_a_sensor_elsewhere_may_become_an_actuator(session: Session) -> None:
    """Gesperrt ist die Rolle Aktor in einer anderen Zone, nicht jedes andere Vorkommen des Geräts."""
    source(session)
    a, b = create_zone(session, "misch-a"), create_zone(session, "misch-b")
    combo = _device(session, "kombi", "temperature", "switch")
    set_temperature_source(session, a, combo, actor_id=None)
    _put(session, a, combo, "window_contact")
    assign_device(session, b, combo, role(session, "actuator"), actor_id=None)
    assert _zones_of(session, combo) == [b.id]


def test_a_released_actuator_can_move_to_another_zone(session: Session) -> None:
    source(session)
    a, b = create_zone(session, "wandern-a"), create_zone(session, "wandern-b")
    valve = _valve(session, "wanderventil")
    actuator = role(session, "actuator")
    row = assign_device(session, a, valve, actuator, actor_id=None)
    detach_device(session, a, row, actor_id=None)
    assign_device(session, b, valve, actuator, actor_id=None)
    assert _zones_of(session, valve) == [b.id]


def test_the_capability_check_comes_before_the_one_zone_rule(session: Session) -> None:
    """Ein Fühler, der nie schalten kann, bekommt die Fähigkeitsmeldung, nicht die über andere Zonen."""
    source(session)
    a, b = create_zone(session, "fk-a"), create_zone(session, "fk-b")
    sensor = _device(session, "nur-fuehler", "temperature")
    _put(session, a, sensor, "actuator")
    with pytest.raises(device_assignment.CapabilityMissing, match="Schaltausgang"):
        assign_device(session, b, sensor, role(session, "actuator"), actor_id=None)


# ---- Domäne: swap_device --------------------------------------------------------------------------


def test_a_swap_refuses_a_new_actuator_that_heats_another_zone_and_writes_nothing(session: Session) -> None:
    source(session)
    a, b = create_zone(session, "tausch-a"), create_zone(session, "tausch-b")
    old, busy = _valve(session, "altes-ventil"), _valve(session, "belegtes-ventil")
    actuator = role(session, "actuator")
    assign_device(session, a, old, actuator, actor_id=None)
    assign_device(session, b, busy, actuator, actor_id=None)
    audit_before = _audit_count(session)

    with pytest.raises(ActuatorInAnotherZone, match="'belegtes-ventil' ist bereits Aktor von Zone 'Tausch-b'"):
        swap_device(session, a, old, busy, actor_id=None)

    session.expire_all()
    assert _zones_of(session, old) == [a.id]
    assert _zones_of(session, busy) == [b.id]
    assert _audit_count(session) == audit_before


def test_a_swap_to_a_free_valve_still_works_and_moves_the_actuator(session: Session) -> None:
    source(session)
    a = create_zone(session, "tausch-frei")
    old, new = _valve(session, "alt"), _valve(session, "neu")
    assign_device(session, a, old, role(session, "actuator"), actor_id=None)
    swap_device(session, a, old, new, actor_id=None)
    assert _zones_of(session, new) == [a.id] and _zones_of(session, old) == []


def test_a_swap_does_not_check_actuator_rows_it_does_not_create(session: Session) -> None:
    """Hat das neue Gerät in dieser Zone schon die Rolle Aktor, entsteht keine Zeile -- ein alter
    Doppelbestand woanders darf den Tausch dann nicht blockieren. Ein Sensor, der als
    Fensterkontakt wandert, ist ohnehin nicht betroffen."""
    source(session)
    a, b = create_zone(session, "bestand-a"), create_zone(session, "bestand-b")
    old, new = _valve(session, "bestand-alt"), _valve(session, "bestand-neu")
    _put(session, a, old, "actuator")
    _put(session, a, new, "actuator")
    _put(session, b, new, "actuator")  # Bestand: neu heizt schon zwei Zonen
    swap_device(session, a, old, new, actor_id=None)
    assert _zones_of(session, new) == sorted([a.id, b.id]) and _zones_of(session, old) == []

    contact_old, contact_new = _device(session, "k-alt", "contact"), _device(session, "k-neu", "contact")
    _put(session, a, contact_old, "window_contact")
    _put(session, b, contact_new, "window_contact")
    swap_device(session, a, contact_old, contact_new, actor_id=None)
    assert _zones_of(session, contact_new, "window_contact") == sorted([a.id, b.id])


# ---- Bestand mit Doppelzuordnung bleibt bedienbar --------------------------------------------------


def test_an_existing_double_assignment_can_still_be_edited_and_released(session: Session) -> None:
    source(session)
    a, b = create_zone(session, "alt-a"), create_zone(session, "alt-b")
    valve = _valve(session, "doppel-bestand")
    row_a, row_b = _put(session, a, valve, "actuator"), _put(session, b, valve, "actuator")

    set_thermostat_mode(session, a, row_a, "self_regulating", actor_id=None)  # Betriebsart
    row_b.sort_order = 3  # Sortierung (die Zeile selbst ist die Änderung, keine Neuzuordnung)
    session.flush()
    assert row_a.thermostat_mode == "self_regulating"

    detach_device(session, a, row_a, actor_id=None)  # Lösen einer Doppelzuordnung
    assert _zones_of(session, valve) == [b.id]


def test_the_map_still_shows_an_existing_double_assignment(session: Session) -> None:
    source(session)
    a, b = create_zone(session, "karte-a"), create_zone(session, "karte-b")
    valve = _valve(session, "karte-doppel")
    _put(session, a, valve, "actuator")
    _put(session, b, valve, "actuator")
    graph = build_mapping_map(session, [a, b], {a.id, b.id})
    assert sorted(c.zone_id for c in graph.connections if c.slot == "actuator") == sorted([a.id, b.id])


# ---- Web: Zuordnungsseite --------------------------------------------------------------------------


def test_the_assignment_page_shows_the_refusal(client_als: ClientAls, session: Session) -> None:
    source(session)
    a, b = create_zone(session, "web-a"), create_zone(session, "web-b")
    valve = _valve(session, "web-ventil")
    actuator = role(session, "actuator")
    assign_device(session, a, valve, actuator, actor_id=None)
    client = client_als([("device.manage", b.id), ("device.read", b.id)])

    response = client.post(
        f"/zones/{b.id}/devices/assign",
        data={"device_id": str(valve.id), "role_id": str(actuator.id)},
        headers=_csrf(client),
    )

    assert response.status_code == 200
    assert "ist bereits Aktor von Zone &#39;Web-a&#39;; ein Gerät kann nur eine Zone heizen." in response.text
    assert _zones_of(session, valve) == [a.id]


def test_the_swap_page_shows_the_refusal_and_keeps_the_old_valve(client_als: ClientAls, session: Session) -> None:
    source(session)
    a, b = create_zone(session, "web-sa"), create_zone(session, "web-sb")
    old, busy = _valve(session, "web-alt"), _valve(session, "web-belegt")
    actuator = role(session, "actuator")
    assign_device(session, a, old, actuator, actor_id=None)
    assign_device(session, b, busy, actuator, actor_id=None)
    client = client_als([("device.manage", a.id), ("device.read", a.id)])

    response = client.post(
        f"/zones/{a.id}/devices/swap",
        data={"old_device_id": str(old.id), "new_device_id": str(busy.id)},
        headers=_csrf(client),
    )

    assert response.status_code == 200
    assert "ist bereits Aktor von Zone &#39;Web-sb&#39;" in response.text
    assert _zones_of(session, old) == [a.id]


def test_editing_an_existing_double_assignment_on_the_page_is_not_blocked(client_als: ClientAls, session: Session) -> None:
    source(session)
    a, b = create_zone(session, "web-ba"), create_zone(session, "web-bb")
    valve = _valve(session, "web-bestand")
    row = _put(session, a, valve, "actuator")
    _put(session, b, valve, "actuator")
    client = client_als([("device.manage", a.id), ("device.read", a.id)])

    mode = client.post(
        f"/zones/{a.id}/devices/regulation",
        data={"assignment_id": str(row.id), "thermostat_mode": "reading_only"},
        headers=_csrf(client),
        follow_redirects=False,
    )
    assert mode.status_code == 303
    session.refresh(row)
    assert row.thermostat_mode == "reading_only"

    detach = client.post(
        f"/zones/{a.id}/devices/detach",
        data={"assignment_id": str(row.id)},
        headers=_csrf(client),
        follow_redirects=False,
    )
    assert detach.status_code == 303
    assert _zones_of(session, valve) == [b.id]


# ---- Karte -----------------------------------------------------------------------------------------


def _connect(client: TestClient, first: str, second: str) -> Any:
    return client.post("/devices/map/connect", headers=_csrf(client), data={"from_id": first, "to_id": second})


def test_the_map_and_the_domain_give_the_same_reason(client_als: ClientAls, session: Session) -> None:
    """Die Karte zeigt die Regel der Domäne vorab; derselbe Wortlaut, dieselbe Entscheidung."""
    source(session)
    a, b = create_zone(session, "kw-a"), create_zone(session, "kw-b")
    valve = _valve(session, "kw-ventil")
    assign_device(session, a, valve, role(session, "actuator"), actor_id=None)
    with pytest.raises(ActuatorInAnotherZone) as domain:
        assign_device(session, b, valve, role(session, "actuator"), actor_id=None)
    client = client_als([("device.manage", a.id), ("device.manage", b.id)])
    refused = _connect(client, zone_port_id(b.id, "actuator"), f"dev-{valve.id}-thermostat")
    assert refused.status_code == 422
    assert refused.json()["reason"] == domain.value.notice


def _blind_can_connect(real: Callable[..., Any]) -> Callable[..., Any]:
    """Eine Karte, die vom Stand beim Laden nichts mehr weiß: kennt weder Verbindungen noch die
    Aktoren anderer Zonen. So sieht die zweite von zwei gleichzeitigen Anfragen die Welt."""

    def blind(a: Port, b: Port, existing: Any = ()) -> Any:
        verdict = real(
            dataclasses.replace(a, actuator_zones=()),
            dataclasses.replace(b, actuator_zones=()),
            [],
        )
        if not verdict.ok:
            return verdict
        # Der Endpunkt vergleicht die Anschlüsse mit `is`: die Originale zurückgeben, keine Kopien.
        by_id = {a.id: a, b.id: b}
        return dataclasses.replace(verdict, output=by_id[verdict.output.id], input=by_id[verdict.input.id])

    return blind


def test_a_concurrent_assignment_to_another_zone_is_refused_by_the_domain_on_the_map(
    client_als: ClientAls, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    source(session)
    mine, other = create_zone(session, "rs-mine"), create_zone(session, "rs-other")
    valve = _valve(session, "rs-ventil")
    _put(session, other, valve, "actuator")
    monkeypatch.setattr(mapping_map_views, "can_connect", _blind_can_connect(can_connect))
    client = client_als([("device.manage", mine.id), ("device.manage", other.id)])

    refused = _connect(client, zone_port_id(mine.id, "actuator"), f"dev-{valve.id}-thermostat")

    assert refused.status_code == 422
    assert refused.json()["reason"] == "'rs-ventil' ist bereits Aktor von Zone 'Rs-other'; ein Gerät kann nur eine Zone heizen."
    assert _zones_of(session, valve) == [other.id]


def test_that_refusal_keeps_an_unseen_zone_unnamed(
    client_als: ClientAls, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    source(session)
    mine, hidden = create_zone(session, "rh-mine"), create_zone(session, "rh-hidden")
    valve = _valve(session, "rh-ventil")
    _put(session, hidden, valve, "actuator")
    monkeypatch.setattr(mapping_map_views, "can_connect", _blind_can_connect(can_connect))
    client = client_als([("device.manage", mine.id)])

    refused = _connect(client, zone_port_id(mine.id, "actuator"), f"dev-{valve.id}-thermostat")

    assert refused.status_code == 422
    assert refused.json()["reason"] == "'rh-ventil' ist bereits Aktor einer anderen Zone; ein Gerät kann nur eine Zone heizen."


def test_a_concurrent_identical_assignment_gives_409_not_500(
    client_als: ClientAls, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Zwei Anfragen legen gleichzeitig dieselbe Zeile an: die zweite besteht die Vorprüfung (sie sah
    die erste noch nicht), und erst der Unique-Constraint `rolle_je_zuordnung` hält sie auf. Das ist
    ein 409, kein 500 -- und die Sitzung bleibt danach benutzbar."""
    source(session)
    zone = create_zone(session, "rc-zone")
    valve = _valve(session, "rc-ventil")
    _put(session, zone, valve, "actuator")  # die Zeile der „ersten Anfrage"
    real_exists = device_assignment._assignment_exists
    calls: list[bool] = []

    def blind_first(*args: Any, **kwargs: Any) -> bool:
        calls.append(True)
        return False if len(calls) == 1 else real_exists(*args, **kwargs)

    monkeypatch.setattr(device_assignment, "_assignment_exists", blind_first)
    monkeypatch.setattr(mapping_map_views, "can_connect", _blind_can_connect(can_connect))
    client = client_als([("device.manage", zone.id)])

    refused = _connect(client, zone_port_id(zone.id, "actuator"), f"dev-{valve.id}-thermostat")

    assert refused.status_code == 409
    assert refused.json()["reason"] == "Dieses Gerät ist der Zone in dieser Rolle bereits zugeordnet."
    assert len(calls) == 2  # Vorprüfung blind, danach der Blick nach dem Constraint-Fehler
    assert session.scalar(select(func.count()).select_from(ZoneDevice).where(ZoneDevice.device_id == valve.id)) == 1


def test_an_integrity_error_that_is_not_the_duplicate_stays_an_error(session: Session) -> None:
    """Nur der Doppeleintrag wird zu `AssignmentAlreadyExists`; ein anderer Fehler beim Einfügen
    (hier: die Zone gibt es nicht mehr) darf nicht als „bereits zugeordnet" durchgehen."""
    from sqlalchemy.exc import IntegrityError

    source(session)
    zone = create_zone(session, "gone")
    valve = _valve(session, "gone-ventil")
    ghost = Zone(name="ghost", display_name="Ghost")
    ghost.id = zone.id + 1000
    with pytest.raises(IntegrityError):
        assign_device(session, ghost, valve, role(session, "actuator"), actor_id=None)


# ---- Wächter: REST/MCP/Import umgehen die Regel nicht -----------------------------------------------


def test_nothing_but_the_domain_creates_assignments() -> None:
    """REST und MCP haben keinen Zuordnungsendpunkt, die Web-Adapter rufen `assign_device`/
    `swap_device`. Entstünde irgendwo ein weiterer Schreibweg (`ZoneDevice(...)`), liefe er an der
    Regel „ein Aktor, eine Zone" vorbei -- dieser Test hält den Schreibweg an einer Stelle."""
    root = Path(thermoctl.__file__).parent
    writers = sorted(
        str(path.relative_to(root))
        for path in root.rglob("*.py")
        if re.search(r"(?<!class )\bZoneDevice\(", path.read_text(encoding="utf-8"))
    )
    assert writers == ["domain/device_assignment.py"]
