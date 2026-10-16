"""Räume anlegen, bearbeiten, löschen, Probleme melden, Glossar (`/ui-api`).

Dünner Adapter (Grundsatz 6 und 7): geschrieben wird mit denselben Domänenfunktionen wie in
den HTML-Views (`zone_views`, `report_views`, `glossary_views`) -- `create_zone`,
`update_zone`, `delete_zone`, `zone_dependencies`, `submit_report` (`build_report`) und
`default_glossary`. Hier steht keine Fachregel. Fehler sind `{detail, code[, field]}`.

Rechte wie in den HTML-Views, in jedem Endpunkt neu geprüft:

- Anlegen: `zone.manage` anlagenweit (403), Anlagensicht (`wrong_profile`).
- Formulardaten, Bearbeiten, Löschfolgen, Löschen: `zone.manage` für diese Zone,
  Anlagensicht.
- Problem melden: `report.create` für diese Zone, **beide** Oberflächen (kein Profil-Wächter,
  wie `report_views`); ausdrücklich nicht `zone.read` -- etwas nach außen Auslösendes darf
  nicht aus einem Leserecht folgen.
- Glossar und Meldeoptionen: nur Anmeldung.

Abweichungen von den HTML-Views (bewusst):

- Die HTML-Views antworten für eine Zone ohne `zone.manage` mit 404. Hier gilt wie überall in
  `/ui-api`: nicht mit `zone.read` sichtbar -> 404 (kein Hinweis auf Existenz), sichtbar aber
  ohne `zone.manage` -> 403. Der Unterschied verrät nichts, was `zone.read` nicht ohnehin zeigt.
- Meldet jemand ein Problem für eine Zone, die er weder lesen noch melden darf, kommt dasselbe
  404; darf er sie sehen, aber nicht melden, ein 403 (die HTML-View antwortet immer 404).
- Längen (Name 64, Anzeigename 128) und der Wertebereich der Sortierung (32-Bit) werden hier
  geprüft: die HTML-View ließ sie ungeprüft, auf MariaDB wäre das ein 500.
- Löschen verlangt in der HTML-View eine eigene Bestätigungsseite; die Bestätigung ist hier
  Sache der Oberfläche (`delete-impact` liefert vorher dieselben Zahlen). Das Löschen selbst
  prüft nichts weiter: Kacheln anderer fallen per FK-Cascade, Räume anderer bleiben.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.auth.dependencies import csrf_protection, current_principal, get_session
from thermoctl.db.models.device import Device
from thermoctl.db.models.lookup import OperatingMode
from thermoctl.db.models.zone import Zone
from thermoctl.domain.authz import has_permission, visible_zones
from thermoctl.domain.glossary import AREAS, default_glossary
from thermoctl.domain.modes import DomainError
from thermoctl.domain.principal import Principal
from thermoctl.domain.problem_report import NOTE_LIMIT, REPORT_KINDS
from thermoctl.domain.zones import (
    ZoneNameTaken,
    create_zone,
    delete_zone,
    update_zone,
    zone_dependencies,
)
from thermoctl.web.shared.report import submit_report
from thermoctl.web.ui_api_operations import Number, _forbidden, _need, _need_admin_ui, _text
from thermoctl.web.ui_api_rooms import NamedOut, RoomApiError, _RoomsRoute, _visible_zone

router = APIRouter(
    prefix="/ui-api",
    dependencies=[Depends(csrf_protection)],
    include_in_schema=False,
    route_class=_RoomsRoute,
)

Sess = Annotated[Session, Depends(get_session)]
Prin = Annotated[Principal, Depends(current_principal)]

NAME_LIMIT = 64
DISPLAY_NAME_LIMIT = 128
SORT_ORDER_LIMIT = 2**31 - 1


# -- Ausgabe ------------------------------------------------------------------------------


class RoomFormOut(BaseModel):
    """Die bearbeitbaren Felder eines Raums (die der HTML-Raumform)."""

    id: int
    name: str
    display_name: str
    sort_order: int
    operating_mode: str
    temperature_source_device_id: int | None


class DeviceChoiceOut(BaseModel):
    id: int
    name: str


class RoomFormOptionsOut(BaseModel):
    operating_modes: list[NamedOut]
    devices: list[DeviceChoiceOut]
    name_limit: int
    display_name_limit: int
    #: `null` beim Anlegen.
    room: RoomFormOut | None


class RoomOrderOut(BaseModel):
    id: int
    sort_order: int


class RoomOrdersOut(BaseModel):
    """Sortierwert je sichtbarem Raum, in der Reihenfolge der Räume-Liste."""

    rooms: list[RoomOrderOut]


class DeleteImpactOut(BaseModel):
    """Was mit dem Raum verschwindet -- dieselben Zahlen wie die HTML-Löschseite."""

    id: int
    name: str
    display_name: str
    schedule_points: int
    devices: int
    setpoints: int
    overrides: int
    shadow_decisions: int


class DeletedOut(BaseModel):
    id: int
    deleted: bool


class ReportKindOut(BaseModel):
    code: str
    label: str


class ReportRoomOut(BaseModel):
    id: int
    name: str


class ReportOptionsOut(BaseModel):
    kinds: list[ReportKindOut]
    note_limit: int
    rooms: list[ReportRoomOut]


class ReportResultOut(BaseModel):
    message: str


class GlossaryEntryOut(BaseModel):
    id: str
    term: str
    short: str
    explanation: str
    area: str
    letter: str
    synonyms: list[str]
    see_also: list[str]


class GlossaryOut(BaseModel):
    areas: list[str]
    letters: list[str]
    entries: list[GlossaryEntryOut]


# -- Eingabe ------------------------------------------------------------------------------


class RoomCreateIn(BaseModel):
    name: str = ""
    display_name: str = ""
    operating_mode: str = ""
    sort_order: Number = 0
    temperature_source_device_id: Number = None


class RoomUpdateIn(BaseModel):
    """Nur gesendete Felder ändern etwas; fehlt ein Feld, bleibt es unverändert.

    Abweichung zur HTML-Form, die immer alles sendet: Ein Teil-Aufruf kann so nie
    still etwas überschreiben. `temperature_source_device_id: null` entfernt die Quelle.
    """

    name: str | None = None
    display_name: str | None = None
    operating_mode: str | None = None
    sort_order: Number = None
    temperature_source_device_id: Number = None


class ReportIn(BaseModel):
    kind: str = ""
    note: str = ""


# -- Hilfen -------------------------------------------------------------------------------


def _invalid(field: str, detail: str) -> RoomApiError:
    return RoomApiError(422, detail, "validation", field)


def _checked_name(value: str) -> str:
    text = value.strip()
    if not text:
        raise _invalid("name", "Bitte einen technischen Namen eingeben.")
    if len(text) > NAME_LIMIT:
        raise _invalid("name", f"Der technische Name darf höchstens {NAME_LIMIT} Zeichen haben.")
    return text


def _checked_display_name(value: str) -> str:
    text = value.strip()
    if not text:
        raise _invalid("display_name", "Bitte einen Anzeigenamen eingeben.")
    if len(text) > DISPLAY_NAME_LIMIT:
        raise _invalid(
            "display_name", f"Der Anzeigename darf höchstens {DISPLAY_NAME_LIMIT} Zeichen haben."
        )
    return text


def _checked_operating_mode(session: Session, code: str) -> int:
    text = code.strip()
    if not text:
        raise _invalid("operating_mode", "Bitte eine Betriebsart auswählen.")
    kind = session.scalar(select(OperatingMode).where(OperatingMode.code == text))
    if kind is None:
        raise _invalid("operating_mode", "Diese Betriebsart ist nicht bekannt.")
    return kind.id


def _checked_sort_order(value: Number) -> int:
    text = _text(value)
    try:
        number = int(text)
    except ValueError as exc:
        raise _invalid("sort_order", "Bitte eine ganze Zahl eingeben.") from exc
    if abs(number) > SORT_ORDER_LIMIT:
        raise _invalid("sort_order", "Diese Zahl ist zu groß.")
    return number


def _checked_device(session: Session, value: Number) -> int | None:
    text = _text(value)
    if not text:
        return None
    try:
        device_id = int(text)
    except ValueError as exc:
        raise _invalid(
            "temperature_source_device_id", "Bitte ein bekanntes Gerät auswählen."
        ) from exc
    if session.get(Device, device_id) is None:
        raise _invalid("temperature_source_device_id", "Dieses Gerät ist nicht bekannt.")
    return device_id


def _name_taken() -> RoomApiError:
    return RoomApiError(409, "Dieser Name ist bereits vergeben.", "name_taken", "name")


def _room_form(zone: Zone) -> RoomFormOut:
    return RoomFormOut(
        id=zone.id,
        name=zone.name,
        display_name=zone.display_name,
        sort_order=zone.sort_order,
        operating_mode=zone.operating_mode.code,
        temperature_source_device_id=zone.temperature_source_device_id,
    )


def _manageable_zone(session: Session, principal: Principal, zone_id: int) -> Zone:
    """Sichtbar (`zone.read`, sonst 404), dann `zone.manage` dieser Zone und Anlagensicht."""
    zone = _visible_zone(session, principal, zone_id)
    if not has_permission(principal, "zone.manage", zone.id):
        raise _forbidden()
    _need_admin_ui(principal)
    return zone


# -- Räume --------------------------------------------------------------------------------


@router.get("/room-form")
def room_form(
    principal: Prin, session: Sess, zone_id: int | None = None
) -> RoomFormOptionsOut:
    """Auswahllisten und (mit `zone_id`) die aktuellen Werte für das Raumformular."""
    zone: Zone | None = None
    if zone_id is None:
        _need(principal, "zone.manage")
    else:
        zone = _manageable_zone(session, principal, zone_id)
    return RoomFormOptionsOut(
        operating_modes=[
            NamedOut(code=kind.code, label=kind.label)
            for kind in session.scalars(select(OperatingMode).order_by(OperatingMode.id))
        ],
        devices=[
            DeviceChoiceOut(id=device.id, name=device.display_name)
            for device in session.scalars(
                select(Device).order_by(Device.display_name, Device.id)
            )
        ],
        name_limit=NAME_LIMIT,
        display_name_limit=DISPLAY_NAME_LIMIT,
        room=_room_form(zone) if zone is not None else None,
    )


@router.get("/room-orders")
def room_orders(principal: Prin, session: Sess) -> RoomOrdersOut:
    """Sortierwert der sichtbaren Räume (Anlagensicht, `zone.read`) für die Räume-Liste."""
    _need_admin_ui(principal)
    return RoomOrdersOut(
        rooms=[
            RoomOrderOut(id=zone.id, sort_order=zone.sort_order)
            for zone in visible_zones(session, principal, "zone.read")
        ]
    )


@router.post("/rooms", status_code=201)
def create_room(body: RoomCreateIn, principal: Prin, session: Sess) -> RoomFormOut:
    """Raum anlegen (`domain.zones.create_zone`)."""
    _need(principal, "zone.manage")
    name = _checked_name(body.name)
    display_name = _checked_display_name(body.display_name)
    mode_id = _checked_operating_mode(session, body.operating_mode)
    sort_order = _checked_sort_order(body.sort_order)
    device_id = _checked_device(session, body.temperature_source_device_id)
    try:
        zone = create_zone(
            session,
            principal,
            name=name,
            display_name=display_name,
            operating_mode_id=mode_id,
            sort_order=sort_order,
            temperature_source_device_id=device_id,
        )
    except ZoneNameTaken as exc:
        raise _name_taken() from exc
    return _room_form(zone)


@router.put("/rooms/{zone_id}")
def save_room(zone_id: int, body: RoomUpdateIn, principal: Prin, session: Sess) -> RoomFormOut:
    """Raum speichern (`domain.zones.update_zone`)."""
    zone = _manageable_zone(session, principal, zone_id)
    sent = body.model_fields_set
    name = _checked_name(body.name or "") if "name" in sent else zone.name
    display_name = (
        _checked_display_name(body.display_name or "")
        if "display_name" in sent
        else zone.display_name
    )
    mode_id = (
        _checked_operating_mode(session, body.operating_mode or "")
        if "operating_mode" in sent
        else zone.operating_mode_id
    )
    sort_order = (
        _checked_sort_order(body.sort_order) if "sort_order" in sent else zone.sort_order
    )
    device_id = (
        _checked_device(session, body.temperature_source_device_id)
        if "temperature_source_device_id" in sent
        else zone.temperature_source_device_id
    )
    try:
        update_zone(
            session,
            zone,
            principal,
            name=name,
            display_name=display_name,
            operating_mode_id=mode_id,
            sort_order=sort_order,
            temperature_source_device_id=device_id,
        )
    except ZoneNameTaken as exc:
        raise _name_taken() from exc
    session.refresh(zone)
    return _room_form(zone)


@router.get("/rooms/{zone_id}/delete-impact")
def room_delete_impact(zone_id: int, principal: Prin, session: Sess) -> DeleteImpactOut:
    """Was beim Löschen mitverschwindet (`domain.zones.zone_dependencies`)."""
    zone = _manageable_zone(session, principal, zone_id)
    found = zone_dependencies(session, zone.id)
    return DeleteImpactOut(
        id=zone.id,
        name=zone.name,
        display_name=zone.display_name,
        schedule_points=found.schedule_points,
        devices=found.devices,
        setpoints=found.setpoints,
        overrides=found.overrides,
        shadow_decisions=found.shadow_decisions,
    )


@router.delete("/rooms/{zone_id}")
def delete_room(zone_id: int, principal: Prin, session: Sess) -> DeletedOut:
    """Raum löschen (`domain.zones.delete_zone`); die Bestätigung ist Sache der Oberfläche."""
    zone = _manageable_zone(session, principal, zone_id)
    deleted_id = zone.id
    delete_zone(session, zone, principal)
    return DeletedOut(id=deleted_id, deleted=True)


# -- Probleme melden ------------------------------------------------------------------------


def _reportable_zone(session: Session, principal: Principal, zone_id: int) -> Zone:
    """`report.create` dieser Zone; wer die Zone nur lesen darf, bekommt 403, sonst 404."""
    zone = next(
        (z for z in visible_zones(session, principal, "report.create") if z.id == zone_id), None
    )
    if zone is not None:
        return zone
    if any(z.id == zone_id for z in visible_zones(session, principal, "zone.read")):
        raise _forbidden()
    raise RoomApiError(404, "Raum nicht gefunden.", "not_found")


@router.get("/report-options")
def report_options(principal: Prin, session: Sess) -> ReportOptionsOut:
    """Problemarten und die Räume, für die dieser Principal melden darf."""
    return ReportOptionsOut(
        kinds=[ReportKindOut(code=code, label=label) for code, label in REPORT_KINDS],
        note_limit=NOTE_LIMIT,
        rooms=[
            ReportRoomOut(id=zone.id, name=zone.display_name)
            for zone in visible_zones(session, principal, "report.create")
        ],
    )


@router.post("/rooms/{zone_id}/report")
async def report_problem(
    zone_id: int, body: ReportIn, request: Request, principal: Prin, session: Sess
) -> ReportResultOut:
    """Problem melden (`report_views.submit_report`, derselbe Weg wie die HTML-View).

    Audit-Eintrag und Zustellung wie dort; die Antwort sagt ehrlich, ob die Meldung nur
    aufgenommen oder auch weitergegeben wurde.
    """
    zone = _reportable_zone(session, principal, zone_id)
    try:
        message = await submit_report(request, session, principal, zone, body.kind, body.note)
    except DomainError as exc:
        raise RoomApiError(422, exc.notice, "validation", exc.field) from exc
    return ReportResultOut(message=message)


# -- Glossar --------------------------------------------------------------------------------


@router.get("/glossary")
def glossary(_principal: Prin) -> GlossaryOut:
    """Alle Begriffe (`domain.glossary.default_glossary`); jede Anmeldung genügt."""
    source = default_glossary()
    return GlossaryOut(
        areas=list(AREAS),
        letters=list(source.letters()),
        entries=[
            GlossaryEntryOut(
                id=entry.id,
                term=entry.term,
                short=entry.short,
                explanation=entry.explanation,
                area=entry.area,
                letter=entry.letter,
                synonyms=list(entry.synonyms),
                see_also=list(entry.see_also),
            )
            for entry in source.entries
        ],
    )
