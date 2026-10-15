"""Zuordnungskarte (Entwurf): Knoten-Fläche und zwei dünne Schreibendpunkte.

Entscheidungen liegen in `domain/mapping_map.py` (Passt das? Was steht schon?) und in
`domain/device_assignment.py` (Speichern). Die Seite zeigt nur an; die beiden POST-Endpunkte
antworten mit JSON, weil die Karte die Seite nicht neu laden soll, solange der Nutzer noch
zieht. Die bestehende Zuordnungsseite (`/zones/{id}/devices`) bleibt unverändert.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.auth.dependencies import csrf_protection, current_principal, get_session
from thermoctl.db.models.device import Device, ZoneDevice
from thermoctl.db.models.lookup import DeviceRole
from thermoctl.db.models.zone import Zone
from thermoctl.domain.authz import has_permission, visible_zones
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
    SAME_SIDE,
    TECHNICAL_REASON,
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
from thermoctl.web import templates
from thermoctl.web.guards import admin_ui_only

router = APIRouter(
    dependencies=[Depends(csrf_protection), Depends(admin_ui_only)],
    include_in_schema=False,
)


def _rejected(reason: str, status_code: int = 422) -> JSONResponse:
    return JSONResponse({"ok": False, "reason": reason}, status_code=status_code)


@router.get("/devices/map")
async def mapping_map(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    """Gleiches Recht und gleicher Zonenfilter wie die Zuordnungsseite (`device.read`)."""
    zones = visible_zones(session, principal, "device.read")
    editable = {zone.id for zone in zones if has_permission(principal, "device.manage", zone.id)}
    graph = build_mapping_map(session, zones, editable)
    return templates.TemplateResponse(
        request,
        "mapping_map.html",
        {"graph": graph, "payload": _payload(graph), "may_edit": bool(editable)},
    )


def _payload(graph: MappingMap) -> dict[str, object]:
    """Was das Skript braucht: nur Kennungen und fertige Begründungen, keine Regeln."""
    return {
        "reasons": list(graph.reasons),
        "rejections": graph.rejections,
        "same_side": SAME_SIDE,
        "technical_reason": TECHNICAL_REASON,
        "single": {
            p.id: "Dieser Eingang nimmt nur eine Verbindung an."
            for p in graph.ports()
            if p.single and p.node_kind == "device"
        },
        "connections": [
            {
                "id": c.id,
                "out": c.out_port_id,
                "in": c.in_port_id,
                "editable": c.editable,
                "title": _title(c),
            }
            for c in graph.connections
        ],
    }


def _title(c: Connection) -> str:
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


def _device_lines(graph: MappingMap, zone_id: int, device_id: int) -> set[str]:
    """Die Ventil-Linien (Heizbefehl, Sollwert, Raumtemperatur) dieses Geräts in der Zone."""
    return {
        c.slot
        for c in graph.connections
        if c.zone_id == zone_id and c.device_id == device_id and c.slot in VALVE_SLOTS
    }


STALE_NOTICE = "Die Zuordnung wurde inzwischen geändert, bitte die Seite neu laden."


def _fresh_lines(graph: MappingMap, assignment: ZoneDevice) -> set[str]:
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


def _editable_graph(
    session: Session, principal: Principal
) -> tuple[MappingMap, dict[int, Zone]]:
    zones = visible_zones(session, principal, "device.manage")
    graph = build_mapping_map(session, zones, {zone.id for zone in zones})
    return graph, {zone.id: zone for zone in zones}


@router.post("/devices/map/connect")
async def connect(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    """Verbindet zwei Anschlüsse, sofern eine davon ein Zonen-Anschluss ist.

    `from_id` und `to_id` dürfen in beliebiger Reihenfolge stehen (Ziehen geht von jedem Ende
    aus); `can_connect` legt die Richtung fest. Zwei Prüfungen hintereinander, beide aus der
    Domäne: erst `can_connect`, dann die Speicherfunktion mit ihrer eigenen Fähigkeitsprüfung.
    Verbindungen zwischen zwei Geräten gibt es im Datenmodell nicht; solche lehnt der Endpunkt ab,
    statt still etwas anderes zu speichern.
    """
    form = await request.form()
    graph, zones = _editable_graph(session, principal)
    ports = {p.id: p for p in graph.ports()}
    first = ports.get(str(form.get("from_id", "")))
    second = ports.get(str(form.get("to_id", "")))
    if first is None or second is None:
        return _rejected("Anschluss nicht gefunden.", 404)
    zone_port = next((p for p in (first, second) if p.node_kind == "zone"), None)
    if zone_port is None:
        # Erst die Regeln: Unsinn wird als Unsinn begründet, nicht als „nicht gespeichert".
        verdict = can_connect(first, second, graph.connections)
        return _rejected(
            verdict.reason or "Verbindungen zwischen Geräten werden nicht gespeichert."
        )
    if zone_port.zone_id not in zones or zone_port.slot is None:  # pragma: no cover
        # Der Graph ist aus genau diesen Zonen gebaut und jeder Zonenanschluss trägt eine
        # Stelle; nur ein Fehler im Graphaufbau könnte hierher führen.
        return _rejected("Anschluss nicht gefunden.", 404)
    verdict = can_connect(first, second, graph.connections)
    if not verdict.ok or verdict.output is None or verdict.input is None:
        return _rejected(verdict.reason or "Diese Verbindung ist nicht möglich.")
    device_port = verdict.input if verdict.output is zone_port else verdict.output
    zone = zones[zone_port.zone_id]
    device = session.get(Device, device_port.device_id)
    if device is None:  # pragma: no cover - der Anschluss stammt aus derselben Sitzung
        return _rejected("Gerät nicht gefunden.", 404)
    try:
        if zone_port.slot == TEMPERATURE_SOURCE:
            set_temperature_source(session, zone, device, actor_id=principal.user_id)
        else:
            role = session.scalar(
                select(DeviceRole).where(DeviceRole.code == role_for_slot(zone_port.slot))
            )
            if role is None:  # pragma: no cover - die Rollen legt die Migration an
                return _rejected("Diese Rolle gibt es in der Anlage nicht.")
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
                    if _fresh_lines(graph, assignment) != _device_lines(graph, zone.id, device.id):
                        return _rejected(STALE_NOTICE, 409)
                lines = _device_lines(graph, zone.id, device.id) | {zone_port.slot}
                has_actual = device_port.requires_actual or zone_port.slot == "room_temperature"
                mode = mode_for_lines(lines, has_actual_port=has_actual)
                if mode is None:  # pragma: no cover - `can_connect` prüfte dieselbe Menge schon
                    return _rejected("Diese Linien ergeben keine Betriebsart des Ventils.")
                set_thermostat_mode(session, zone, assignment, mode, actor_id=principal.user_id)
    except AssignmentAlreadyExists:
        # `can_connect` kennt jede Doppelzuordnung, die beim Aufbau der Karte schon stand; hier
        # landet, wer gleichzeitig mit einer anderen Anfrage dieselbe Zeile anlegt (-> 409).
        return _rejected("Dieses Gerät ist der Zone in dieser Rolle bereits zugeordnet.", 409)
    except ActuatorInAnotherZone as exc:
        # Maßgeblich ist die Domäne; `can_connect` zeigt dieselbe Regel nur vorab. Hier landet, wer
        # gleichzeitig mit einer anderen Zuordnung dasselbe Gerät belegt. Wie in der Karte: Namen
        # von Zonen, die der Betrachter nicht sehen darf, bleiben verborgen.
        visible = set(zones)
        where = [
            f"von Zone '{name}'" if zone_id in visible else "einer anderen Zone"
            for zone_id, name in exc.zones
        ]
        return _rejected(actuator_elsewhere_notice(device.display_name, where))
    except CapabilityMissing as exc:  # pragma: no cover - `can_connect` prüft dieselbe Fähigkeit
        return _rejected(exc.notice)
    return JSONResponse({"ok": True})


@router.post("/devices/map/disconnect")
async def disconnect(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    """Löst eine gespeicherte Verbindung (Kennung aus `MappingMap.connections`)."""
    form = await request.form()
    graph, zones = _editable_graph(session, principal)
    connection = next(
        (c for c in graph.connections if c.id == str(form.get("connection_id", ""))), None
    )
    if connection is None or connection.zone_id not in zones:
        return _rejected("Verbindung nicht gefunden.", 404)
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
            return _rejected("Verbindung nicht gefunden.", 404)
        remaining = _device_lines(graph, zone.id, connection.device_id) - {connection.slot}
        if connection.slot in VALVE_SLOTS and remaining:
            # Eine Linie von mehreren: Das Ventil bleibt zugeordnet, die Betriebsart ändert sich.
            has_actual = any(
                p.valve_channel == "actual"
                for p in graph.ports()
                if p.device_id == connection.device_id
            )
            mode = mode_for_lines(remaining, has_actual_port=has_actual)
            if mode is None:
                return _rejected(
                    "Der Sollwert wird immer zusammen mit der Raumtemperatur geschrieben: "
                    "thermoctl kennt keine Betriebsart nur mit Sollwert. Lösen Sie zuerst die "
                    "Linie 'Sollwert' (dann meldet das Ventil nur die Raumtemperatur)."
                )
            previous = assignment.thermostat_mode
            set_thermostat_mode(session, zone, assignment, mode, actor_id=principal.user_id)
            note = (
                DOWNGRADE_NOTE
                if previous == MODE_SELF_REGULATING and mode == MODE_READING_ONLY
                else ""
            )
            return JSONResponse(
                {
                    "ok": True,
                    "message": f"Verbindung gelöst: {_title(connection)}. Betriebsart jetzt: "
                    f"'{MODE_LABELS[mode]}'.{note}",
                }
            )
        detach_device(session, zone, assignment, actor_id=principal.user_id)
    note = ACTUATOR_DETACHED_NOTE if connection.slot in VALVE_SLOTS else ""
    return JSONResponse({"ok": True, "message": f"Verbindung gelöst: {_title(connection)}.{note}"})
