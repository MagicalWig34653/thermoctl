"""Zuordnungskarte: Verbinden und Loesen von Anschluessen -- geteilt von Alt-View und /ui-api."""
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.db.models.device import Device, ZoneDevice
from thermoctl.db.models.lookup import DeviceRole
from thermoctl.db.models.zone import Zone
from thermoctl.domain.authz import visible_zones
from thermoctl.domain.device_assignment import (
    TEMPERATURE_SOURCE,
    ActuatorInAnotherZone,
    AssignmentAlreadyExists,
    CapabilityMissing,
    actuator_elsewhere_notice,
    assign_device,
    detach_device,
    set_temperature_source,
    set_thermostat_mode,
)
from thermoctl.domain.mapping_map import (
    CHANNEL_BY_SLOT,
    VALVE_SLOTS,
    Connection,
    MappingMap,
    build_mapping_map,
    can_connect,
    lines_for_mode,
    mode_for_lines,
    role_for_slot,
)
from thermoctl.domain.principal import Principal
from thermoctl.domain.thermostat_mode import MODE_LABELS, MODE_READING_ONLY, MODE_SELF_REGULATING


def connection_title(c: Connection) -> str:
    """Lesbarer Name einer Verbindung in Richtung des Datenflusses (Ausgang → Eingang)."""
    if c.from_zone:
        return f"{c.text} → {c.device_name}"
    return f"{c.device_name} → {c.text}"


# Nach dem Lösen eines Aktors: thermoctl schickt dem Gerät keinen Ausschaltbefehl (`detach_device`
# löscht nur die Zuordnung, die Regelkette kennt das Gerät danach nicht mehr).
ACTUATOR_DETACHED_NOTE = (
    " Das Gerät wird nicht mehr von thermoctl geschaltet und behält seinen letzten Zustand."
)


# Zurückgestuft auf „nur Raumtemperatur": Der Sollwert am Gerät bleibt, wie er zuletzt geschrieben
# wurde (`domain/thermostat_mode.py`: `reading_only` fasst ihn nie an).
DOWNGRADE_NOTE = (
    " Der Sollwert am Gerät bleibt, wie er zuletzt geschrieben wurde; thermoctl fasst ihn in "
    "'Nur Raumtemperatur melden' nicht mehr an (kein Zeitplan, kein Frostschutz bei offenem "
    "Fenster)."
)


@dataclass(frozen=True)
class MapOutcome:
    """Ergebnis von Verbinden/Lösen, unabhängig vom Adapter.

    `code` ist der maschinenlesbare Grund, `reason` der deutsche Satz; `mode` die
    Betriebsart, die dabei gespeichert wurde (nur bei Ventillinien). Die HTML-Karte und
    `/ui-api` übersetzen dasselbe Ergebnis in ihre jeweilige Antwortform.
    """

    ok: bool
    status: int = 200
    reason: str | None = None
    code: str | None = None
    mode: str | None = None
    message: str | None = None


def no_outcome(reason: str, code: str, status: int = 422) -> MapOutcome:
    return MapOutcome(ok=False, status=status, reason=reason, code=code)


def device_lines(graph: MappingMap, zone_id: int, device_id: int) -> set[str]:
    """Die Ventil-Linien (Heizbefehl, Sollwert, Raumtemperatur) dieses Geräts in der Zone."""
    return {
        c.slot
        for c in graph.connections
        if c.zone_id == zone_id and c.device_id == device_id and c.slot in VALVE_SLOTS
    }


STALE_NOTICE = "Die Zuordnung wurde inzwischen geändert, bitte die Seite neu laden."


def fresh_lines(graph: MappingMap, assignment: ZoneDevice) -> set[str]:
    """Die Linien, die die gespeicherte Betriebsart dieser Zeile jetzt hat, gezählt wie im Graphen.

    Eine Zeile ohne gezeichnete Linie (Altbestand) zählt dort als `actuator`.
    """
    ports = [p for p in graph.ports() if p.device_id == assignment.device_id]
    lines = lines_for_mode(
        assignment.thermostat_mode,
        has_setpoint_port=any(p.valve_channel == "setpoint" for p in ports),
        has_actual_port=any(p.valve_channel == "actual" for p in ports),
    )
    return set(lines) or {"actuator"}


def editable_graph(
    session: Session, principal: Principal
) -> tuple[MappingMap, dict[int, Zone]]:
    zones = visible_zones(session, principal, "device.manage")
    graph = build_mapping_map(session, zones, {zone.id for zone in zones})
    return graph, {zone.id: zone for zone in zones}


def connect_ports(
    session: Session, principal: Principal, from_id: str, to_id: str
) -> MapOutcome:
    """Verbindet zwei Anschlüsse, sofern eine davon ein Zonen-Anschluss ist.

    `from_id` und `to_id` dürfen in beliebiger Reihenfolge stehen (Ziehen geht von jedem Ende
    aus); `can_connect` legt die Richtung fest. Zwei Prüfungen hintereinander, beide aus der
    Domäne: erst `can_connect`, dann die Speicherfunktion mit ihrer eigenen Fähigkeitsprüfung.
    Verbindungen zwischen zwei Geräten gibt es im Datenmodell nicht; solche lehnt der Endpunkt ab,
    statt still etwas anderes zu speichern.
    """
    graph, zones = editable_graph(session, principal)
    ports = {p.id: p for p in graph.ports()}
    first = ports.get(from_id)
    second = ports.get(to_id)
    if first is None or second is None:
        return no_outcome("Anschluss nicht gefunden.", "not_found", 404)
    zone_port = next((p for p in (first, second) if p.node_kind == "zone"), None)
    if zone_port is None:
        # Erst die Regeln: Unsinn wird als Unsinn begründet, nicht als „nicht gespeichert".
        verdict = can_connect(first, second, graph.connections)
        return no_outcome(
            verdict.reason or "Verbindungen zwischen Geräten werden nicht gespeichert.",
            "connection_rejected",
        )
    if zone_port.zone_id not in zones or zone_port.slot is None:  # pragma: no cover
        # Der Graph ist aus genau diesen Zonen gebaut und jeder Zonenanschluss trägt eine
        # Stelle; nur ein Fehler im Graphaufbau könnte hierher führen.
        return no_outcome("Anschluss nicht gefunden.", "not_found", 404)
    verdict = can_connect(first, second, graph.connections)
    if not verdict.ok or verdict.output is None or verdict.input is None:
        return no_outcome(
            verdict.reason or "Diese Verbindung ist nicht möglich.", "connection_rejected"
        )
    device_port = verdict.input if verdict.output is zone_port else verdict.output
    zone = zones[zone_port.zone_id]
    applied_mode: str | None = None
    device = session.get(Device, device_port.device_id)
    if device is None:  # pragma: no cover - der Anschluss stammt aus derselben Sitzung
        return no_outcome("Gerät nicht gefunden.", "not_found", 404)
    try:
        if zone_port.slot == TEMPERATURE_SOURCE:
            set_temperature_source(session, zone, device, actor_id=principal.user_id)
        else:
            role = session.scalar(
                select(DeviceRole).where(DeviceRole.code == role_for_slot(zone_port.slot))
            )
            if role is None:  # pragma: no cover - die Rollen legt die Migration an
                return no_outcome("Diese Rolle gibt es in der Anlage nicht.", "role_missing")
            if zone_port.slot not in CHANNEL_BY_SLOT:
                assign_device(session, zone, device, role, actor_id=principal.user_id)
            else:
                # Sollwert oder Raumtemperatur: Das Ventil ist vielleicht schon Aktor dieser Zone
                # (über die andere Linie); die Betriebsart folgt aus der Menge der Linien.
                assignment = session.scalar(
                    select(ZoneDevice).where(
                        ZoneDevice.zone_id == zone.id,
                        ZoneDevice.device_id == device.id,
                        ZoneDevice.device_role_id == role.id,
                    )
                )
                if assignment is None:
                    assignment = assign_device(
                        session, zone, device, role, actor_id=principal.user_id
                    )
                else:
                    # Die Linienmenge des Graphen stammt vom Anfang der Anfrage. Eine gleichzeitige
                    # Anfrage kann die Betriebsart seither geändert haben; sie wäre sonst still
                    # überschrieben. Zeile gesperrt neu lesen und gegen den Graphen halten.
                    session.refresh(assignment, with_for_update=True)
                    if fresh_lines(graph, assignment) != device_lines(graph, zone.id, device.id):
                        return no_outcome(STALE_NOTICE, "stale", 409)
                lines = device_lines(graph, zone.id, device.id) | {zone_port.slot}
                has_actual = device_port.requires_actual or zone_port.slot == "room_temperature"
                mode = mode_for_lines(lines, has_actual_port=has_actual)
                if mode is None:  # pragma: no cover - `can_connect` prüfte dieselbe Menge schon
                    return no_outcome(
                        "Diese Linien ergeben keine Betriebsart des Ventils.", "no_valve_mode"
                    )
                set_thermostat_mode(session, zone, assignment, mode, actor_id=principal.user_id)
                applied_mode = mode
    except AssignmentAlreadyExists:
        # `can_connect` kennt jede Doppelzuordnung, die beim Aufbau der Karte schon stand; hier
        # landet, wer gleichzeitig mit einer anderen Anfrage dieselbe Zeile anlegt (-> 409).
        return no_outcome(
            "Dieses Gerät ist der Zone in dieser Rolle bereits zugeordnet.", "already_assigned", 409
        )
    except ActuatorInAnotherZone as exc:
        # Maßgeblich ist die Domäne; `can_connect` zeigt dieselbe Regel nur vorab. Hier landet, wer
        # gleichzeitig mit einer anderen Zuordnung dasselbe Gerät belegt. Wie in der Karte: Namen
        # von Zonen, die der Betrachter nicht sehen darf, bleiben verborgen.
        visible = set(zones)
        where = [
            f"von Zone '{name}'" if zone_id in visible else "einer anderen Zone"
            for zone_id, name in exc.zones
        ]
        return no_outcome(
            actuator_elsewhere_notice(device.display_name, where), "actuator_in_other_zone"
        )
    except CapabilityMissing as exc:  # pragma: no cover - `can_connect` prüft dieselbe Fähigkeit
        return no_outcome(exc.notice, "capability_missing")
    return MapOutcome(ok=True, mode=applied_mode)


def disconnect_connection(
    session: Session, principal: Principal, connection_id: str
) -> MapOutcome:
    """Löst eine gespeicherte Verbindung (Kennung aus `MappingMap.connections`)."""
    graph, zones = editable_graph(session, principal)
    connection = next((c for c in graph.connections if c.id == connection_id), None)
    if connection is None or connection.zone_id not in zones:
        return no_outcome("Verbindung nicht gefunden.", "not_found", 404)
    zone = zones[connection.zone_id]
    if connection.slot == TEMPERATURE_SOURCE:
        set_temperature_source(session, zone, None, actor_id=principal.user_id)
    else:
        assignment = session.scalar(
            select(ZoneDevice)
            .join(DeviceRole, DeviceRole.id == ZoneDevice.device_role_id)
            .where(
                ZoneDevice.zone_id == zone.id,
                ZoneDevice.device_id == connection.device_id,
                DeviceRole.code == role_for_slot(connection.slot),
            )
        )
        if assignment is None:  # pragma: no cover - die Verbindung stammt aus derselben Sitzung
            return no_outcome("Verbindung nicht gefunden.", "not_found", 404)
        remaining = device_lines(graph, zone.id, connection.device_id) - {connection.slot}
        if connection.slot in VALVE_SLOTS and remaining:
            # Eine Linie von mehreren: Das Ventil bleibt zugeordnet, die Betriebsart ändert sich.
            has_actual = any(
                p.valve_channel == "actual"
                for p in graph.ports()
                if p.device_id == connection.device_id
            )
            mode = mode_for_lines(remaining, has_actual_port=has_actual)
            if mode is None:
                return no_outcome(
                    "Der Sollwert wird immer zusammen mit der Raumtemperatur geschrieben: "
                    "thermoctl kennt keine Betriebsart nur mit Sollwert. Lösen Sie zuerst die "
                    "Linie 'Sollwert' (dann meldet das Ventil nur die Raumtemperatur).",
                    "no_valve_mode",
                )
            previous = assignment.thermostat_mode
            set_thermostat_mode(session, zone, assignment, mode, actor_id=principal.user_id)
            note = (
                DOWNGRADE_NOTE
                if previous == MODE_SELF_REGULATING and mode == MODE_READING_ONLY
                else ""
            )
            return MapOutcome(
                ok=True,
                mode=mode,
                message=f"Verbindung gelöst: {connection_title(connection)}. Betriebsart jetzt: "
                f"'{MODE_LABELS[mode]}'.{note}",
            )
        detach_device(session, zone, assignment, actor_id=principal.user_id)
    note = ACTUATOR_DETACHED_NOTE if connection.slot in VALVE_SLOTS else ""
    return MapOutcome(ok=True, message=f"Verbindung gelöst: {connection_title(connection)}.{note}")
