import json
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from markupsafe import Markup
from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.auth.dependencies import csrf_protection, current_principal, get_session
from thermoctl.db.base import utcnow
from thermoctl.db.models.device import Device
from thermoctl.db.models.lookup import OperatingMode
from thermoctl.db.models.operations import Setting
from thermoctl.db.models.zone import Zone
from thermoctl.domain.authz import has_permission, require, visible_zones
from thermoctl.domain.data_flow import build_data_flow
from thermoctl.domain.data_flow_mermaid import render_mermaid
from thermoctl.domain.principal import Principal
from thermoctl.domain.zone_history_chart import (
    LAYOUTS,
    TECHNICAL,
    WINDOWS,
    Wording,
    chart_for_zone,
    has_short_bands,
    heat_cells_for,
    hover_payload,
    line_path,
    range_bars,
    range_path,
    solar_bands_for,
    solar_marks_for,
    step_path,
    tick_x,
    ticks_for,
    unknown_bands_for,
    x_position,
    y_position,
)
from thermoctl.domain.zones import (
    ZoneNameTaken,
    create_zone,
    delete_zone,
    update_zone,
    zone_dependencies,
)
from thermoctl.web import templates
from thermoctl.web.forms import FormError, form_again
from thermoctl.web.guards import admin_ui_only
from thermoctl.web.urls import prefixed

# `include_in_schema=False`: the OpenAPI description is the contract of the REST
# interface. These routes deliver HTML for humans, and in the interface under
# /docs there would otherwise be a form route next to every real endpoint whose
# 'Try it out' triggers a real change.
# `admin_ui_only`: diese Seiten gehören zur Anlagensicht. Ein Mieterprofil wird
# hier schon vor der Rechteprüfung abgewiesen -- eine ausgeblendete Verknüpfung
# in der Navigation ist kein Riegel (siehe `web/guards.py`). Die bestehenden
# Rechteprüfungen in den Endpunkten bleiben davon unberührt bestehen.
router = APIRouter(
    dependencies=[Depends(csrf_protection), Depends(admin_ui_only)],
    include_in_schema=False,
)


def _visible_zone(session: Session, principal: Principal, zone_id: int) -> Zone:
    zone = next(
        (zone for zone in visible_zones(session, principal, "zone.manage") if zone.id == zone_id),
        None,
    )
    if zone is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Zone nicht gefunden")
    return zone


def _choice_values(session: Session) -> dict[str, object]:
    operating_modes = session.scalars(select(OperatingMode).order_by(OperatingMode.id)).all()
    devices = session.scalars(select(Device).order_by(Device.display_name, Device.id)).all()
    return {
        "operating_modes": [(kind.id, kind.label) for kind in operating_modes],
        "devices": [(device.id, device.display_name) for device in devices],
    }


def _form_values(form: object) -> dict[str, str]:
    get = form.get  # type: ignore[attr-defined]
    return {
        "name": str(get("name", "")).strip(),
        "display_name": str(get("display_name", "")).strip(),
        "operating_mode": str(get("operating_mode", "")),
        "sort_order": str(get("sort_order", "0")).strip(),
        "temperature_source_device_id": str(get("temperature_source_device_id", "")),
    }


def _checked_values(
    session: Session, values: dict[str, str]
) -> tuple[str, str, int, int, int | None]:
    if not values["name"]:
        raise FormError("name", "Bitte einen technischen Namen eingeben.")
    if not values["display_name"]:
        raise FormError("display_name", "Bitte einen Anzeigenamen eingeben.")
    try:
        operating_mode_id = int(values["operating_mode"])
    except ValueError as exc:
        raise FormError("operating_mode", "Bitte eine Betriebsart auswählen.") from exc
    if session.get(OperatingMode, operating_mode_id) is None:
        raise FormError("operating_mode", "Diese Betriebsart ist nicht bekannt.")
    try:
        sort_order = int(values["sort_order"])
    except ValueError as exc:
        raise FormError("sort_order", "Bitte eine ganze Zahl eingeben.") from exc
    device_id = None
    if values["temperature_source_device_id"]:
        try:
            device_id = int(values["temperature_source_device_id"])
        except ValueError as exc:
            raise FormError(
                "temperature_source_device_id", "Bitte ein bekanntes Gerät auswählen."
            ) from exc
        if session.get(Device, device_id) is None:
            raise FormError("temperature_source_device_id", "Dieses Gerät ist nicht bekannt.")
    return values["name"], values["display_name"], operating_mode_id, sort_order, device_id


def _form_again(
    request: Request,
    session: Session,
    values: dict[str, str],
    errors: FormError,
    *,
    zone: Zone | None,
) -> Response:
    return form_again(
        request,
        "zone_form.html",
        values,
        errors,
        zone=zone,
        **_choice_values(session),
    )


@router.get("/zones")
async def zone_list_view(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    zones = visible_zones(session, principal, "zone.read")
    return templates.TemplateResponse(
        request,
        "zones.html",
        {
            "zones": zones,
            "may_create": has_permission(principal, "zone.manage"),
            "may_edit": {
                zone.id for zone in zones if has_permission(principal, "zone.manage", zone.id)
            },
        },
    )


@router.get("/zones/new")
async def zone_new(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    require(principal, "zone.manage")
    return templates.TemplateResponse(
        request,
        "zone_form.html",
        {"zone": None, "values": {"sort_order": "0"}, "errors": {}, **_choice_values(session)},
    )


@router.post("/zones")
async def create_zone_view(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    require(principal, "zone.manage")
    values = _form_values(await request.form())
    try:
        name, display_name, kind_id, order_value, device_id = _checked_values(session, values)
        create_zone(
            session,
            principal,
            name=name,
            display_name=display_name,
            operating_mode_id=kind_id,
            sort_order=order_value,
            temperature_source_device_id=device_id,
        )
    except ZoneNameTaken:
        return _form_again(
            request,
            session,
            values,
            FormError("name", "Dieser Name ist bereits vergeben."),
            zone=None,
        )
    except FormError as exc:
        return _form_again(request, session, values, exc, zone=None)
    return RedirectResponse(prefixed(request, "/zones"), status_code=status.HTTP_303_SEE_OTHER)


def checked_period(period: str) -> str:
    if period not in WINDOWS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Ungültiger Zeitraum")
    return period


def _attribute_json(payload: object) -> Markup:
    """JSON ohne Leerraum für ein einfach quotiertes `data-`Attribut.

    Das übliche HTML-Escapen machte aus jedem `"` ein `&#34;` und blähte die Daten auf das
    Dreifache auf. Stattdessen stehen `'`, `&`, `<` und `>` als JSON-Escape (`\\u0027` ...) im
    Text, den jeder JSON-Leser zurückübersetzt -- so enthält das Attribut keines dieser Zeichen
    mehr, und mit `"` im Wert ist es auch in einfachen Anführungszeichen eindeutig begrenzt.
    """
    text = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
    for char in "'&<>":
        text = text.replace(char, f"\\u{ord(char):04x}")
    # Von jedem Zeichen befreit, das ein Attribut verlassen könnte; Test: test_zone_history_hover.
    return Markup(text)  # noqa: S704


def history_context(
    session: Session,
    zone_id: int,
    period: str,
    *,
    tile: bool = False,
    wording: Wording = TECHNICAL,
) -> dict[str, object]:
    """Alles, was `zone_history_chart.html` braucht -- für Anlagen- und Wohnungssicht.

    Ohne jede Rechteprüfung: die aufrufende Route hat die Zone vorher gegen ihren eigenen
    Zonenfilter geprüft (`tenant_views.show_temperature_history_tile` mit `visible_zones`).
    `wording` wählt die Beschriftungen, nicht die Daten.
    """
    checked_period(period)
    setting = session.get(Setting, 1)
    timezone_name = setting.timezone if setting is not None else None
    now = datetime.now(UTC).replace(tzinfo=None)
    chart = chart_for_zone(session, zone_id, period, now, timezone_name, wording)
    return {
        "chart": chart,
        "period": period,
        "tile": tile,
        # Jede Karte braucht eine eigene Kennung: auf der Übersichtsseite stehen viele
        # nebeneinander, und SVG-Muster und Beschreibungen werden im Dokument über die Kennung
        # gefunden.
        "chart_id": f"zone-history-{zone_id}" if tile else "zone-history",
        "hover_json": _attribute_json(hover_payload(chart, timezone_name))
        if chart.has_data
        else "",
        "layouts": LAYOUTS,
        "xpos": x_position,
        "ypos": y_position,
        "tickx": tick_x,
        "ticks_for": ticks_for,
        "line": line_path,
        "steps": step_path,
        "range_path": range_path,
        "range_bars": range_bars,
        "solar_bands_for": solar_bands_for,
        "solar_marks_for": solar_marks_for,
        "unknown_bands_for": unknown_bands_for,
        "heat_cells_for": heat_cells_for,
        "short_bands": has_short_bands(chart),
    }


def _data_flow_entries(session: Session, zones: list[Zone]) -> list[dict[str, object]]:
    """Je Zone der Graph, sein Mermaid-Quelltext und die Kanten als Text."""
    now = utcnow()
    entries: list[dict[str, object]] = []
    for zone in zones:
        graph = build_data_flow(session, zone, now)
        entries.append(
            {
                "zone": zone,
                "graph": graph,
                "mermaid": render_mermaid(graph),
                "edge_texts": [graph.edge_text(edge) for edge in graph.edges],
            }
        )
    return entries


@router.get("/zones/data-flow")
async def data_flow_overview(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    """Datenfluss aller Zonen -- gleicher Zonenfilter und gleiches Recht wie `/zones/history`."""
    zones = visible_zones(session, principal, "zone.manage")
    return templates.TemplateResponse(
        request,
        "data_flow.html",
        {"entries": _data_flow_entries(session, zones), "single": False},
    )


@router.get("/zones/{zone_id}/data-flow")
async def zone_data_flow(
    zone_id: int,
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    zone = _visible_zone(session, principal, zone_id)
    return templates.TemplateResponse(
        request,
        "data_flow.html",
        {"entries": _data_flow_entries(session, [zone]), "single": True, "zone": zone},
    )


@router.get("/zones/history")
async def zone_history_overview(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
    period: str = "24h",
) -> Response:
    """Alle Verläufe auf einer Seite -- die Kacheln laden einzeln nach.

    Die Seite selbst enthält keine Messdaten, nur Platzhalter. Welche Zonen darauf stehen,
    entscheidet derselbe Zonenfilter wie bei der Einzelkarte (`_visible_zone`: `zone.manage`);
    jede Kachel holt ihre Karte über `zone_history`, das die Zone noch einmal prüft. Ohne
    Recht ist die Seite leer, wie die Zonenliste.
    """
    checked_period(period)
    zones = visible_zones(session, principal, "zone.manage")
    # `page_path`/`tile_path`: die Adressen dieser Oberfläche. Seitenkörper und Kachel sind frei
    # von Rechten und festen Adressen, damit eine andere Oberfläche (Wohnungssicht) sie mit ihrem
    # eigenen Zonenfilter und ihren eigenen Routen wiederverwenden kann.
    return templates.TemplateResponse(
        request,
        "zones_history.html",
        {
            "zones": zones,
            "period": period,
            "page_path": "/zones/history",
            "tile_path": "/zones/{zone_id}/history",
        },
    )


@router.get("/zones/{zone_id}/history")
async def zone_history(
    zone_id: int,
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
    period: str = "24h",
    tile: bool = False,
) -> Response:
    zone = _visible_zone(session, principal, zone_id)
    return templates.TemplateResponse(
        request,
        "zone_history_chart.html",
        {
            "zone": zone,
            "zone_href": f"/zones/{zone.id}" if tile else None,
            **history_context(session, zone_id, period, tile=tile),
        },
    )


@router.get("/zones/{zone_id}")
async def edit_zone(
    zone_id: int,
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    zone = _visible_zone(session, principal, zone_id)
    values = {
        "name": zone.name,
        "display_name": zone.display_name,
        "operating_mode": str(zone.operating_mode_id),
        "sort_order": str(zone.sort_order),
        "temperature_source_device_id": str(zone.temperature_source_device_id or ""),
    }
    return templates.TemplateResponse(
        request,
        "zone_form.html",
        {
            "zone": zone,
            "values": values,
            "errors": {},
            **_choice_values(session),
            **history_context(session, zone_id, "24h"),
        },
    )


@router.post("/zones/{zone_id}")
async def save_zone(
    zone_id: int,
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    zone = _visible_zone(session, principal, zone_id)
    values = _form_values(await request.form())
    try:
        name, display_name, kind_id, order_value, device_id = _checked_values(session, values)
        update_zone(
            session,
            zone,
            principal,
            name=name,
            display_name=display_name,
            operating_mode_id=kind_id,
            sort_order=order_value,
            temperature_source_device_id=device_id,
        )
    except ZoneNameTaken:
        return _form_again(
            request,
            session,
            values,
            FormError("name", "Dieser Name ist bereits vergeben."),
            zone=zone,
        )
    except FormError as exc:
        return _form_again(request, session, values, exc, zone=zone)
    return RedirectResponse(prefixed(request, "/zones"), status_code=status.HTTP_303_SEE_OTHER)


@router.get("/zones/{zone_id}/delete")
async def confirm_zone_delete(
    zone_id: int,
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    zone = _visible_zone(session, principal, zone_id)
    return templates.TemplateResponse(
        request,
        "zone_delete.html",
        {"zone": zone, "dependencies": zone_dependencies(session, zone.id)},
    )


@router.post("/zones/{zone_id}/delete")
async def execute_zone_delete(
    zone_id: int,
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    zone = _visible_zone(session, principal, zone_id)
    delete_zone(session, zone, principal)
    return RedirectResponse(prefixed(request, "/zones"), status_code=status.HTTP_303_SEE_OTHER)
