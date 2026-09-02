import base64
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.helpers import create_mode, create_settings, create_zone, source
from thermoctl.auth.csrf import csrf_token
from thermoctl.auth.sessions import COOKIE_NAME
from thermoctl.config import get_settings
from thermoctl.db.models.operations import AuditEvent
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.domain.schedule import (
    ScheduleError,
    change_schedule_point_mode,
    copy_schedule_day,
    move_schedule_point,
    paint_schedule_interval,
    schedule_snapshot,
    undo_schedule_gesture,
)
from thermoctl.web import schedule_views


def _snapshot(session: Session, zone_id: int) -> tuple[tuple[int, int, int], ...]:
    return schedule_snapshot(list(session.scalars(
        select(SchedulePoint).where(SchedulePoint.zone_id == zone_id)
    )))


def test_painting_an_empty_schedule_returns_to_frost_protection(
    session: Session,
) -> None:
    settings = create_settings(session)
    source(session)
    zone = create_zone(session, "erste-malerei")
    comfort = create_mode(session, "komfort", "Komfort")

    result = paint_schedule_interval(
        session, zone, weekday=1, start_minute=390, end_minute=480,
        mode_id=comfort.id, user_id=None,
    )

    assert result is not None
    assert _snapshot(session, zone.id) == (
        (1, 390, comfort.id), (1, 480, settings.frost_protection_mode_id)
    )


def test_painting_over_a_whole_existing_section_minimizes_the_points(
    session: Session,
) -> None:
    create_settings(session)
    source(session)
    zone = create_zone(session, "uebermalen")
    night = create_mode(session, "nacht-ueber", "Nacht")
    day = create_mode(session, "tag-ueber", "Tag")
    away = create_mode(session, "weg-ueber", "Abwesend")
    _point(session, zone.id, 1, 0, night.id)
    _point(session, zone.id, 1, 360, day.id)
    _point(session, zone.id, 1, 420, away.id)
    _point(session, zone.id, 1, 600, night.id)

    paint_schedule_interval(
        session, zone, weekday=1, start_minute=330, end_minute=660,
        mode_id=day.id, user_id=None,
    )

    assert _snapshot(session, zone.id) == ((1, 330, day.id), (1, 660, night.id))


def test_painting_exactly_the_mode_already_there_is_a_no_op(session: Session) -> None:
    create_settings(session)
    source(session)
    zone = create_zone(session, "gleich")
    night = create_mode(session, "nacht-gleich", "Nacht")
    day = create_mode(session, "tag-gleich", "Tag")
    _point(session, zone.id, 1, 0, night.id)
    _point(session, zone.id, 1, 360, day.id)
    _point(session, zone.id, 1, 480, night.id)
    before = _snapshot(session, zone.id)

    assert paint_schedule_interval(
        session, zone, weekday=1, start_minute=360, end_minute=480,
        mode_id=day.id, user_id=None,
    ) is None
    assert _snapshot(session, zone.id) == before
    entries = session.scalars(
        select(AuditEvent).where(AuditEvent.object_type == "schedule")
    ).all()
    assert entries == []


def test_painting_across_midnight_is_rejected(session: Session) -> None:
    create_settings(session)
    zone = create_zone(session, "mitternacht")
    mode = create_mode(session, "nacht-mitternacht", "Nacht")
    with pytest.raises(ScheduleError, match="Mitternacht"):
        paint_schedule_interval(
            session, zone, weekday=1, start_minute=1380, end_minute=60,
            mode_id=mode.id, user_id=None,
        )


@pytest.mark.parametrize(
    ("weekday", "start", "end", "mode_id"),
    [(0, 60, 120, 1), (1, -1, 120, 1), (1, 60, 1441, 1), (1, 60, 120, 999999)],
)
def test_painting_rejects_invalid_domain_boundaries(
    session: Session, weekday: int, start: int, end: int, mode_id: int,
) -> None:
    create_settings(session)
    zone = create_zone(session, f"grenze-{weekday}-{start}-{end}-{mode_id}")
    with pytest.raises(ScheduleError):
        paint_schedule_interval(
            session, zone, weekday=weekday, start_minute=start, end_minute=end,
            mode_id=mode_id, user_id=None,
        )


def test_painting_to_end_of_sunday_restores_monday_mode(session: Session) -> None:
    create_settings(session)
    source(session)
    zone = create_zone(session, "sonntag-ende")
    night = create_mode(session, "sonntag-nacht", "Nacht")
    comfort = create_mode(session, "sonntag-komfort", "Komfort")
    _point(session, zone.id, 1, 0, night.id)
    paint_schedule_interval(
        session, zone, weekday=7, start_minute=1380, end_minute=1440,
        mode_id=comfort.id, user_id=None,
    )
    assert (7, 1380, comfort.id) in _snapshot(session, zone.id)


def test_copying_a_day_and_undoing_are_each_one_audited_gesture(session: Session) -> None:
    create_settings(session)
    source(session)
    zone = create_zone(session, "tag-kopieren")
    night = create_mode(session, "nacht-kopie", "Nacht")
    day = create_mode(session, "tag-kopie", "Tag")
    _point(session, zone.id, 1, 0, night.id)
    _point(session, zone.id, 1, 360, day.id)
    _point(session, zone.id, 1, 480, night.id)
    snapshots = copy_schedule_day(
        session, zone, source_weekday=1, target_weekdays=[1, 2, 3, 4, 5],
        user_id=None,
    )
    assert snapshots is not None
    before, after, revision = snapshots
    assert all((day_number, 360, day.id) in after for day_number in range(1, 6))
    undo_schedule_gesture(
        session, zone, before=before, expected_after=after,
        expected_revision=revision, user_id=None
    )
    assert _snapshot(session, zone.id) == before
    entries = session.scalars(
        select(AuditEvent).where(AuditEvent.object_type == "schedule")
    ).all()
    assert len(entries) == 2


def test_undo_refuses_to_overwrite_a_later_edit(session: Session) -> None:
    create_settings(session)
    source(session)
    zone = create_zone(session, "altes-undo")
    mode = create_mode(session, "undo-mode", "Tag")
    result = paint_schedule_interval(
        session, zone, weekday=1, start_minute=360, end_minute=480,
        mode_id=mode.id, user_id=None,
    )
    assert result is not None
    before, after, revision = result
    _point(session, zone.id, 2, 600, mode.id)
    with pytest.raises(ScheduleError, match="inzwischen"):
        undo_schedule_gesture(
            session, zone, before=before, expected_after=after,
            expected_revision=revision, user_id=None
        )


def test_copying_an_empty_or_only_source_day_is_a_no_op(session: Session) -> None:
    create_settings(session)
    zone = create_zone(session, "leere-kopie")
    assert copy_schedule_day(
        session, zone, source_weekday=1, target_weekdays=[2], user_id=None
    ) is None
    mode = create_mode(session, "nur-quelle", "Tag")
    _point(session, zone.id, 1, 360, mode.id)
    assert copy_schedule_day(
        session, zone, source_weekday=1, target_weekdays=[1], user_id=None
    ) is None
    with pytest.raises(ScheduleError):
        copy_schedule_day(
            session, zone, source_weekday=8, target_weekdays=[1], user_id=None
        )


def test_copying_an_already_copied_pattern_is_idempotent(session: Session) -> None:
    create_settings(session)
    zone = create_zone(session, "idempotent-copy")
    day = create_mode(session, "idempotent-copy-day", "Tag")
    night = create_mode(session, "idempotent-copy-night", "Nacht")
    _point(session, zone.id, 1, 540, day.id)
    _point(session, zone.id, 1, 1080, night.id)

    first = copy_schedule_day(
        session, zone, source_weekday=1, target_weekdays=list(range(1, 8)), user_id=None
    )
    second = copy_schedule_day(
        session, zone, source_weekday=1, target_weekdays=list(range(1, 8)), user_id=None
    )

    assert first is not None
    assert second is None


def test_paint_copy_and_undo_routes_use_normal_csrf_protected_forms(
    client_als, session: Session,
) -> None:
    settings = create_settings(session)
    zone = create_zone(session, "mal-routen")
    comfort = create_mode(session, "komfort-routen", "Komfort")
    client = client_als([("zone.read", zone.id), ("schedule.manage", zone.id)])

    page = client.get(f"/zones/{zone.id}/schedule")
    assert 'id="schedule-paint"' in page.text
    assert 'name="csrf_token"' in page.text
    painted = client.post(
        f"/zones/{zone.id}/schedule/paint",
        data={"weekday": "1", "start_time": "06:30", "end_time": "08:00",
              "mode_id": str(comfort.id)}, headers=_csrf(client),
    )
    assert painted.status_code == 200 and "Rückgängig" in painted.text
    assert _snapshot(session, zone.id) == (
        (1, 390, comfort.id), (1, 480, settings.frost_protection_mode_id)
    )
    token = re.search(r'name="undo_token" value="([^"]+)"', painted.text)
    assert token is not None
    undone = client.post(
        f"/zones/{zone.id}/schedule/undo",
        data={"undo_token": token.group(1)}, headers=_csrf(client),
    )
    assert undone.status_code == 200 and _snapshot(session, zone.id) == ()


def test_rendered_palette_defaults_to_painting_and_explains_move_only_gestures(
    client_als, session: Session,
) -> None:
    create_settings(session)
    zone = create_zone(session, "paint-default")
    create_mode(session, "paint-default-mode", "Komfort")
    client = client_als([("zone.read", zone.id), ("schedule.manage", zone.id)])

    page = client.get(f"/zones/{zone.id}/schedule")
    form = re.search(
        rf'<form[^>]*action="/zones/{zone.id}/schedule/paint"[^>]*>(.*?)</form>',
        page.text,
        re.S,
    )

    assert form is not None
    checked = re.findall(r'<input[^>]*name="paint_tool"[^>]*checked[^>]*>', form.group(1))
    assert len(checked) == 1
    assert 'value="move"' not in checked[0]
    assert "Ziehen im Raster malt mit dem gewählten Modus" in form.group(1)


def test_paint_mode_keeps_draggable_bars_movable_and_paints_everywhere_else(
    client_als, session: Session,
) -> None:
    create_settings(session)
    zone = create_zone(session, "fixed-area-feedback")
    client = client_als([("zone.read", zone.id), ("schedule.manage", zone.id)])

    page = client.get(f"/zones/{zone.id}/schedule")
    script = Path("thermoctl/web/static/schedule.js").read_text()
    stylesheet = Path("thermoctl/web/static/thermoctl.css").read_text()

    assert 'data-paint-tool-hint aria-live="polite"' in page.text
    assert 'if (event.target.closest(".schedule-draggable"))' in script
    assert 'if (!event.target.closest(".schedule-draggable"))' in script
    assert "explainMoveTool();" in script
    assert ".schedule-day {\n    cursor: not-allowed;" in stylesheet
    assert ".schedule-draggable {\n    cursor: grab;" in stylesheet
    assert ".schedule-painting .schedule-bar:not(.schedule-draggable) {" in stylesheet
    assert ".schedule-painting .schedule-draggable {\n    cursor: grab;" in stylesheet


def test_schedule_gestures_only_write_fields_that_are_present() -> None:
    script = Path("thermoctl/web/static/schedule.js").read_text()

    assert 'form.elements.namedItem("point_id")' in script
    assert 'form.elements.namedItem("weekday")' in script
    assert 'form.elements.namedItem("time_of_day")' in script
    assert "if (!pointField || !weekdayField || !timeField)" in script
    assert 'form.elements.namedItem("start_time")' in script
    assert 'form.elements.namedItem("end_time")' in script
    assert 'form.elements.namedItem("end_boundary")' in script
    assert "form.elements.weekday.value" not in script


def test_selected_schedule_tool_survives_htmx_page_replacements() -> None:
    script = Path("thermoctl/web/static/schedule.js").read_text()

    assert "rememberPaintTool(tool.value);" in script
    assert "const remembered = rememberedPaintTool();" in script
    assert "candidate.value === remembered" in script
    assert "tool.checked = true" in script


def test_no_op_painting_reports_that_the_gesture_changed_nothing(
    client_als, session: Session,
) -> None:
    create_settings(session)
    zone = create_zone(session, "paint-no-op")
    mode = create_mode(session, "paint-no-op-mode", "Komfort")
    _point(session, zone.id, 1, 360, mode.id)
    client = client_als([("zone.read", zone.id), ("schedule.manage", zone.id)])

    response = client.post(
        f"/zones/{zone.id}/schedule/paint",
        data={"weekday": "1", "start_time": "06:00", "end_time": "07:00",
              "mode_id": str(mode.id)},
        headers=_csrf(client),
    )

    assert response.status_code == 200
    assert 'data-gesture-notice' in response.text
    assert "bereits genauso aus; es wurde nichts geändert" in response.text


def test_the_rendered_paint_form_works_without_javascript(
    client_als, session: Session,
) -> None:
    settings = create_settings(session)
    zone = create_zone(session, "malen-ohne-javascript")
    night = create_mode(session, "nacht-ohne-javascript", "Nacht")
    client = client_als([("zone.read", zone.id), ("schedule.manage", zone.id)])

    page = client.get(f"/zones/{zone.id}/schedule")
    form = re.search(
        rf'<form[^>]*action="/zones/{zone.id}/schedule/paint"[^>]*>(.*?)</form>',
        page.text,
        re.S,
    )
    assert form is not None
    body = form.group(1)
    fields = dict(re.findall(r'name="([^"]+)"[^>]*value="([^"]*)"', body))
    # Unchecked checkboxes are not successful controls in a native form submission.
    fields.pop("end_boundary", None)
    night_tool = re.search(
        rf'<input[^>]*name="([^"]+)"[^>]*value="({night.id})"[^>]*>', body
    )
    assert night_tool is not None
    assert "mode_id" not in fields, "mode_id must not hide a JavaScript dependency"
    controls = {
        element_id: re.search(
            rf'<(?:input|select)[^>]*id="{element_id}"[^>]*name="([^"]+)"', body
        )
        for element_id in ("paint-weekday", "paint-start", "paint-end")
    }
    assert all(control is not None for control in controls.values())
    fields[night_tool.group(1)] = night_tool.group(2)
    fields[controls["paint-weekday"].group(1)] = "3"  # type: ignore[union-attr]
    fields[controls["paint-start"].group(1)] = "09:00"  # type: ignore[union-attr]
    fields[controls["paint-end"].group(1)] = "11:30"  # type: ignore[union-attr]

    response = client.post(f"/zones/{zone.id}/schedule/paint", data=fields)

    assert response.status_code == 200
    assert "gültigen Zeitraum" not in response.text
    assert _snapshot(session, zone.id) == (
        (3, 540, night.id),
        (3, 690, settings.frost_protection_mode_id),
    )


def test_painting_until_midnight_is_selectable_without_javascript(
    client_als, session: Session,
) -> None:
    settings = create_settings(session)
    zone = create_zone(session, "midnight-without-javascript")
    night = create_mode(session, "midnight-form-mode", "Nacht")
    client = client_als([("zone.read", zone.id), ("schedule.manage", zone.id)])

    page = client.get(f"/zones/{zone.id}/schedule")
    control = re.search(r'<input[^>]*id="paint-end-boundary"[^>]*>', page.text)
    assert control is not None
    assert 'name="end_boundary"' in control.group(0)
    assert 'type="checkbox"' in control.group(0)
    assert 'value="1440"' in control.group(0)
    assert 'for="paint-end-boundary">Bis Tagesende (24:00)</label>' in page.text

    response = client.post(
        f"/zones/{zone.id}/schedule/paint",
        data={
            "weekday": "1", "start_time": "23:00", "end_time": "23:45",
            "end_boundary": "1440", "paint_tool": str(night.id),
        },
        headers=_csrf(client),
    )

    assert response.status_code == 200
    assert _snapshot(session, zone.id) == (
        (1, 1380, night.id), (2, 0, settings.frost_protection_mode_id)
    )


def test_copy_controls_are_natively_operable_without_javascript(
    client_als, session: Session,
) -> None:
    create_settings(session)
    zone = create_zone(session, "kopieren-ohne-javascript")
    day = create_mode(session, "copy-html-day", "Tag")
    night = create_mode(session, "copy-html-night", "Nacht")
    _point(session, zone.id, 3, 540, day.id)
    _point(session, zone.id, 3, 1080, night.id)
    client = client_als([("zone.read", zone.id), ("schedule.manage", zone.id)])

    page = client.get(f"/zones/{zone.id}/schedule")
    wednesday = re.search(
        r'<h2[^>]*>Mittwoch</h2>\s*<details[^>]*>(.*?)</details>', page.text, re.S
    )
    assert wednesday is not None
    assert "<summary" in wednesday.group(1)
    assert ">Übertragen</summary>" in wednesday.group(1)
    assert "dropdown-menu" not in wednesday.group(1)
    assert "data-bs-toggle" not in wednesday.group(1)
    forms = re.findall(
        rf'<form[^>]*action="/zones/{zone.id}/schedule/copy-day"[^>]*>(.*?)</form>',
        wednesday.group(1),
        re.S,
    )
    fields = next(
        dict(re.findall(r'name="([^"]+)"[^>]*value="([^"]*)"', body))
        for body in forms
        if 'name="weekday" value="3"' in body and 'name="scope" value="all"' in body
    )

    response = client.post(f"/zones/{zone.id}/schedule/copy-day", data=fields)

    assert response.status_code == 200
    assert "Der Tag wurde auf alle Tage übertragen" in response.text
    snapshot = _snapshot(session, zone.id)
    assert all((weekday, 540, day.id) in snapshot for weekday in range(1, 8))
    assert all((weekday, 1080, night.id) in snapshot for weekday in range(1, 8))


def test_painting_upwards_submits_the_lower_pointer_boundary() -> None:
    script = Path("thermoctl/web/static/schedule.js").read_text()

    assert "finish = current === start" in script
    assert "const low = Math.min(start, finish);" in script
    assert "const high = Math.max(start, finish);" in script
    assert "finish = high === low" not in script


def test_copying_to_identical_targets_reports_the_no_op(
    client_als, session: Session,
) -> None:
    create_settings(session)
    zone = create_zone(session, "copy-no-op")
    day = create_mode(session, "copy-no-op-day", "Tag")
    night = create_mode(session, "copy-no-op-night", "Nacht")
    _point(session, zone.id, 1, 540, day.id)
    _point(session, zone.id, 1, 1080, night.id)
    client = client_als([("zone.read", zone.id), ("schedule.manage", zone.id)])
    client.post(
        f"/zones/{zone.id}/schedule/copy-day",
        data={"weekday": "1", "scope": "all"}, headers=_csrf(client),
    )

    response = client.post(
        f"/zones/{zone.id}/schedule/copy-day",
        data={"weekday": "1", "scope": "all"}, headers=_csrf(client),
    )

    assert response.status_code == 200
    notice = re.search(r'data-gesture-notice[^>]*>([^<]+)', response.text)
    assert notice is not None
    assert "Zieltage sehen bereits genauso aus; es wurde nichts geändert" in notice.group(1)


def test_the_rendered_undo_form_works_without_javascript(
    client_als, session: Session,
) -> None:
    create_settings(session)
    zone = create_zone(session, "undo-ohne-javascript")
    mode = create_mode(session, "undo-modus-ohne-javascript", "Tag")
    client = client_als([("zone.read", zone.id), ("schedule.manage", zone.id)])
    painted = client.post(
        f"/zones/{zone.id}/schedule/paint",
        data={
            "csrf_token": csrf_token(
                client.cookies[COOKIE_NAME],
                get_settings().secret_key.get_secret_value(),
            ),
            "weekday": "1",
            "start_time": "06:30",
            "end_time": "08:00",
            "paint_tool": str(mode.id),
        },
    )
    changed = _snapshot(session, zone.id)
    form = re.search(
        rf'<form[^>]*action="/zones/{zone.id}/schedule/undo"[^>]*>(.*?)</form>',
        painted.text,
        re.S,
    )
    assert form is not None
    fields = dict(
        re.findall(r'name="([^"]+)"[^>]*value="([^"]*)"', form.group(1))
    )

    response = client.post(f"/zones/{zone.id}/schedule/undo", data=fields)

    assert changed
    assert response.status_code == 200
    assert _snapshot(session, zone.id) == ()


def test_copy_route_and_invalid_gesture_input_are_reported(client_als, session: Session) -> None:
    create_settings(session)
    zone = create_zone(session, "kopier-route")
    mode = create_mode(session, "kopiermodus", "Tag")
    night = create_mode(session, "kopiermodus-nacht", "Nacht")
    _point(session, zone.id, 1, 360, mode.id)
    _point(session, zone.id, 1, 480, night.id)
    client = client_als([("zone.read", zone.id), ("schedule.manage", zone.id)])
    invalid = client.post(
        f"/zones/{zone.id}/schedule/paint",
        data={"weekday": "x"}, headers=_csrf(client),
    )
    copied = client.post(
        f"/zones/{zone.id}/schedule/copy-day",
        data={"weekday": "1", "scope": "workdays"}, headers=_csrf(client),
    )
    assert invalid.status_code == 200 and "gültigen Zeitraum" in invalid.text
    assert copied.status_code == 200
    assert all((day, 360, mode.id) in _snapshot(session, zone.id) for day in range(1, 6))


def test_an_invalid_undo_token_is_rejected(client_als, session: Session) -> None:
    create_settings(session)
    zone = create_zone(session, "undo-signatur")
    client = client_als([("schedule.manage", zone.id)])
    response = client.post(
        f"/zones/{zone.id}/schedule/undo",
        data={"undo_token": "manipulated"}, headers=_csrf(client),
    )
    assert response.status_code == 400


def test_gesture_routes_reject_invalid_copy_and_domain_values(client_als, session: Session) -> None:
    create_settings(session)
    zone = create_zone(session, "ungueltige-gesten")
    mode = create_mode(session, "ungueltiger-modus", "Tag")
    client = client_als([("schedule.manage", zone.id)])
    paint = client.post(
        f"/zones/{zone.id}/schedule/paint",
        data={"weekday": "9", "start_time": "06:00", "end_time": "07:00",
              "mode_id": str(mode.id)}, headers=_csrf(client),
    )
    bad_day = client.post(
        f"/zones/{zone.id}/schedule/copy-day",
        data={"weekday": "not-a-day", "scope": "all"}, headers=_csrf(client),
    )
    bad_scope = client.post(
        f"/zones/{zone.id}/schedule/copy-day",
        data={"weekday": "1", "scope": "weekend"}, headers=_csrf(client),
    )
    domain_day = client.post(
        f"/zones/{zone.id}/schedule/copy-day",
        data={"weekday": "9", "scope": "all"}, headers=_csrf(client),
    )
    assert paint.status_code == 200 and "Wochentag" in paint.text
    assert bad_day.status_code == bad_scope.status_code == 400
    assert domain_day.status_code == 200 and "Wochentag" in domain_day.text


def test_undo_token_parser_rejects_a_bad_signature_and_non_mapping(monkeypatch) -> None:
    valid_shape = schedule_views._undo_token(1, ((), (), 1))
    encoded, _signature = valid_shape.split(".", 1)
    with pytest.raises(ValueError):
        schedule_views._undo_payload(encoded + ".bad")

    encoded_list = base64.urlsafe_b64encode(b"[]").decode().rstrip("=")
    monkeypatch.setattr(schedule_views.hmac, "compare_digest", lambda _a, _b: True)
    with pytest.raises(ValueError):
        schedule_views._undo_payload(encoded_list + ".accepted-for-shape-test")


def test_undo_route_rejects_another_zone_and_reports_a_later_edit(
    client_als, session: Session,
) -> None:
    create_settings(session)
    first = create_zone(session, "undo-erste-zone")
    second = create_zone(session, "undo-zweite-zone")
    mode = create_mode(session, "undo-route-mode", "Tag")
    client = client_als([("schedule.manage", None)])
    painted = client.post(
        f"/zones/{first.id}/schedule/paint",
        data={"weekday": "1", "start_time": "06:00", "end_time": "07:00",
              "mode_id": str(mode.id)}, headers=_csrf(client),
    )
    token_match = re.search(r'name="undo_token" value="([^"]+)"', painted.text)
    assert token_match is not None
    token = token_match.group(1)
    wrong_zone = client.post(
        f"/zones/{second.id}/schedule/undo",
        data={"undo_token": token}, headers=_csrf(client),
    )
    _point(session, first.id, 2, 600, mode.id)
    stale = client.post(
        f"/zones/{first.id}/schedule/undo",
        data={"undo_token": token}, headers=_csrf(client),
    )
    assert wrong_zone.status_code == 400
    assert stale.status_code == 200 and "inzwischen geändert" in stale.text


def _csrf(client: TestClient) -> dict[str, str]:
    secret = client.cookies[COOKIE_NAME]
    return {
        "X-CSRF-Token": csrf_token(
            secret, get_settings().secret_key.get_secret_value()
        )
    }


def _point(session: Session, zone_id: int, day: int, minute: int, mode_id: int) -> SchedulePoint:
    point = SchedulePoint(
        zone_id=zone_id,
        weekday=day,
        minute_of_day=minute,
        setpoint_mode_id=mode_id,
    )
    session.add(point)
    session.flush()
    return point


def test_the_week_view_shows_the_wraparound_from_sunday_to_monday(
    client_als, session: Session
) -> None:
    zone = create_zone(session, "bad")
    day = create_mode(session, "tag", "Tag")
    night = create_mode(session, "nacht", "Nacht")
    _point(session, zone.id, 1, 360, day.id)
    _point(session, zone.id, 7, 1320, night.id)

    response = client_als([("zone.read", zone.id)]).get(f"/zones/{zone.id}/schedule")

    assert response.status_code == 200
    assert "Montag" in response.text and "Sonntag" in response.text
    assert 'title="Nacht ab 00:00"' in response.text
    assert 'title="Tag ab 06:00"' in response.text
    assert 'title="Nacht ab 22:00"' in response.text
    assert "Schaltpunkt anlegen" not in response.text


def test_creating_a_point_and_reporting_a_double_booking_understandably(
    client_als, session: Session
) -> None:
    source(session)
    zone = create_zone(session, "bad")
    mode = create_mode(session, "tag", "Tag")
    client = client_als([("schedule.manage", zone.id), ("zone.read", zone.id)])
    data = {"weekday": "2", "time_of_day": "03:15", "mode_id": str(mode.id)}

    angelegt = client.post(
        f"/zones/{zone.id}/schedule/points",
        data=data,
        headers=_csrf(client),
        follow_redirects=False,
    )
    doppelt = client.post(
        f"/zones/{zone.id}/schedule/points", data=data, headers=_csrf(client)
    )

    point = session.scalar(select(SchedulePoint).where(SchedulePoint.zone_id == zone.id))
    assert angelegt.status_code == 303
    assert point is not None and (point.weekday, point.minute_of_day) == (2, 195)
    assert doppelt.status_code == 200
    assert "Zu diesem Zeitpunkt gibt es bereits einen Punkt." in doppelt.text
    assert 'value="03:15"' in doppelt.text
    assert session.scalar(
        select(AuditEvent).where(AuditEvent.object_type == "schedule_point")
    ) is not None


def test_invalid_points_stay_in_the_form_with_their_input(
    client_als, session: Session
) -> None:
    zone = create_zone(session, "bad")
    client = client_als([("schedule.manage", zone.id)])
    response = client.post(
        f"/zones/{zone.id}/schedule/points",
        data={"weekday": "2", "time_of_day": "25:61", "mode_id": "unbekannt"},
        headers=_csrf(client),
    )
    assert response.status_code == 200
    assert "gültige Uhrzeit" in response.text
    assert 'value="25:61"' in response.text


def test_confirming_and_carrying_out_a_point_deletion(client_als, session: Session) -> None:
    source(session)
    zone = create_zone(session, "bad")
    mode = create_mode(session, "tag", "Tag")
    point = _point(session, zone.id, 2, 180, mode.id)
    point_id = point.id
    client = client_als([("schedule.manage", zone.id)])

    form = client.get(
        f"/zones/{zone.id}/schedule/points/{point_id}/delete"
    )
    response = client.post(
        f"/zones/{zone.id}/schedule/points/{point_id}/delete",
        headers=_csrf(client),
        follow_redirects=False,
    )

    assert form.status_code == 200 and "Dienstag" in form.text
    assert response.status_code == 303
    assert session.get(SchedulePoint, point_id) is None


def test_adopting_a_schedule_copies_exactly_and_leaves_the_source_unchanged(
    client_als, session: Session
) -> None:
    source(session)
    source_zone = create_zone(session, "quelle")
    target = create_zone(session, "ziel")
    day = create_mode(session, "tag", "Tag")
    night = create_mode(session, "nacht", "Nacht")
    _point(session, source_zone.id, 1, 360, day.id)
    _point(session, source_zone.id, 7, 1320, night.id)
    client = client_als(
        [("schedule.manage", target.id), ("zone.read", source_zone.id)]
    )

    response = client.post(
        f"/zones/{target.id}/schedule/adopt",
        data={"source_id": str(source_zone.id)},
        headers=_csrf(client),
        follow_redirects=False,
    )

    source_points = session.scalars(
        select(SchedulePoint).where(SchedulePoint.zone_id == source_zone.id)
    ).all()
    target_points = session.scalars(
        select(SchedulePoint).where(SchedulePoint.zone_id == target.id)
    ).all()
    assert response.status_code == 303
    assert [(p.weekday, p.minute_of_day, p.setpoint_mode_id) for p in target_points] == [
        (p.weekday, p.minute_of_day, p.setpoint_mode_id) for p in source_points
    ]
    assert len(source_points) == 2
    assert session.scalar(
        select(AuditEvent).where(
            AuditEvent.object_type == "schedule", AuditEvent.object_id == str(target.id)
        )
    ) is not None


def test_adopting_onto_an_existing_schedule_asks_first(
    client_als, session: Session
) -> None:
    source(session)
    source_zone = create_zone(session, "quelle")
    target = create_zone(session, "ziel")
    mode = create_mode(session, "tag", "Tag")
    old_point = _point(session, target.id, 2, 180, mode.id)
    _point(session, source_zone.id, 1, 360, mode.id)
    client = client_als(
        [("schedule.manage", target.id), ("zone.read", source_zone.id)]
    )
    pfad = f"/zones/{target.id}/schedule/adopt"

    nachfrage = client.post(
        pfad,
        data={"source_id": str(source_zone.id)},
        headers=_csrf(client),
    )
    assert nachfrage.status_code == 200
    assert "ersetzt ihn vollständig" in nachfrage.text
    assert session.get(SchedulePoint, old_point.id) is old_point

    bestaetigt = client.post(
        pfad,
        data={"source_id": str(source_zone.id), "confirmed": "yes"},
        headers=_csrf(client),
        follow_redirects=False,
    )
    assert bestaetigt.status_code == 303
    assert session.get(SchedulePoint, old_point.id) is None


def test_permissions_and_foreign_zones_yield_404(client_als, session: Session) -> None:
    eigene = create_zone(session, "eigene")
    fremde = create_zone(session, "fremde")
    mode = create_mode(session, "tag", "Tag")
    leser = client_als([("zone.read", eigene.id)])
    assert leser.get(f"/zones/{fremde.id}/schedule").status_code == 404
    assert (
        leser.post(
            f"/zones/{eigene.id}/schedule/points",
            data={"weekday": "1", "time_of_day": "06:00", "mode_id": str(mode.id)},
            headers=_csrf(leser),
        ).status_code
        == 404
    )

    administrator = client_als([("schedule.manage", eigene.id), ("zone.read", eigene.id)])
    assert (
        administrator.get(f"/zones/{fremde.id}/schedule/adopt").status_code == 404
    )


def test_the_adoption_form_and_a_faulty_selection(client_als, session: Session) -> None:
    target = create_zone(session, "ziel")
    client = client_als([("schedule.manage", target.id)])
    pfad = f"/zones/{target.id}/schedule/adopt"
    assert client.get(pfad).status_code == 200
    response = client.post(pfad, data={}, headers=_csrf(client))
    assert response.status_code == 200
    assert "Bitte eine Quellzone auswählen." in response.text


def test_a_nonsensical_selection_when_creating_a_point(client_als, session: Session) -> None:
    """Weekday and mode come from select fields -- a request still does not have to
    stick to that. Both paths are deliberately bypassed here."""
    source(session)
    zone = create_zone(session, "zone-unsinn")
    mode = create_mode(session, "unsinn-tag", "Tag")
    client = client_als([("schedule.manage", None), ("zone.read", None)])
    head = _csrf(client)

    for data, expected in (
        ({"weekday": "Montag", "time_of_day": "06:00", "mode_id": str(mode.id)}, "Wochentag"),
        ({"weekday": "1", "time_of_day": "06:00", "mode_id": "kein Modus"}, "Modus"),
    ):
        response = client.post(
            f"/zones/{zone.id}/schedule/points", data=data, headers=head
        )
        assert response.status_code == 200, data
        assert expected in response.text, data
    assert session.scalar(
        select(SchedulePoint).where(SchedulePoint.zone_id == zone.id)
    ) is None


def test_a_foreign_schedule_point_yields_404(client_als, session: Session) -> None:
    """A point belonging to another zone cannot be deleted via one's own."""
    source(session)
    eigene = create_zone(session, "eigene-zeitplan")
    fremde = create_zone(session, "fremde-zeitplan")
    mode = create_mode(session, "fremd-tag", "Tag")
    fremder = _point(session, fremde.id, 1, 360, mode.id)
    client = client_als([("schedule.manage", None), ("zone.read", None)])

    assert client.get(
        f"/zones/{eigene.id}/schedule/points/{fremder.id}/delete"
    ).status_code == 404
    assert client.post(
        f"/zones/{eigene.id}/schedule/points/{fremder.id}/delete", headers=_csrf(client)
    ).status_code == 404
    assert session.get(SchedulePoint, fremder.id) is not None


def test_adopting_from_itself_yields_404(client_als, session: Session) -> None:
    """A zone cannot adopt its schedule from itself -- that would be an operation
    with no effect that would look as if it had one."""
    source(session)
    zone = create_zone(session, "zone-selbstuebernahme")
    client = client_als([("schedule.manage", None), ("zone.read", None)])
    response = client.post(
        f"/zones/{zone.id}/schedule/adopt",
        data={"source_id": str(zone.id)},
        headers=_csrf(client),
    )
    assert response.status_code == 404


def test_an_unknown_schedule_point_yields_404(client_als, session: Session) -> None:
    source(session)
    zone = create_zone(session, "zone-unbekannter-punkt")
    client = client_als([("schedule.manage", None), ("zone.read", None)])
    assert client.get(f"/zones/{zone.id}/schedule/points/999999/delete").status_code == 404


# --- Moving (the target of dragging in the week view) -----------------------


def test_punkt_verschieben(client_als, session: Session) -> None:
    zone = create_zone(session, "bad")
    source(session, "web")
    day = create_mode(session, "tag", "Tag")
    point = _point(session, zone.id, 1, 360, day.id)
    mandant = client_als([("zone.read", None), ("schedule.manage", None)])

    response = mandant.post(
        f"/zones/{zone.id}/schedule/points/move",
        data={"point_id": str(point.id), "weekday": "3", "time_of_day": "07:15"},
        headers=_csrf(mandant),
        follow_redirects=False,
    )
    assert response.status_code == 303
    session.refresh(point)
    assert (point.weekday, point.minute_of_day) == (3, 435)


def test_moving_keeps_the_identifier_and_logs_where_from_and_to(
    client_als, session: Session
) -> None:
    """Deleting and recreating would be functionally the same, but would produce two
    unrelated audit entries and a gap in the schedule in between."""
    zone = create_zone(session, "bad")
    source(session, "web")
    day = create_mode(session, "tag", "Tag")
    point = _point(session, zone.id, 1, 360, day.id)
    previous_identifier = point.id
    mandant = client_als([("zone.read", None), ("schedule.manage", None)])

    mandant.post(
        f"/zones/{zone.id}/schedule/points/move",
        data={"point_id": str(point.id), "weekday": "2", "time_of_day": "22:30"},
        headers=_csrf(mandant),
    )
    session.refresh(point)
    assert point.id == previous_identifier
    entry = session.scalars(
        select(AuditEvent).where(AuditEvent.object_type == "schedule_point")
    ).one()
    assert entry.detail == "Mo 06:00 → Di 22:30"


def test_moving_onto_an_occupied_moment_is_refused(
    client_als, session: Session
) -> None:
    zone = create_zone(session, "bad")
    source(session, "web")
    day = create_mode(session, "tag", "Tag")
    night = create_mode(session, "nacht", "Nacht")
    beweglich = _point(session, zone.id, 1, 360, day.id)
    _point(session, zone.id, 1, 1320, night.id)
    mandant = client_als([("zone.read", None), ("schedule.manage", None)])

    response = mandant.post(
        f"/zones/{zone.id}/schedule/points/move",
        data={"point_id": str(beweglich.id), "weekday": "1", "time_of_day": "22:00"},
        headers=_csrf(mandant),
    )
    assert response.status_code == 200
    # The message belongs on the week view, not on the time-of-day field of the
    # create form: both paths report the same sentence, and in the first version
    # it ended up on a form the user had not even touched.
    assert "data-move-error" in response.text
    assert "wurde nicht verschoben" in response.text
    session.refresh(beweglich)
    assert beweglich.minute_of_day == 360


def test_moving_onto_its_own_place_is_not_an_error(
    client_als, session: Session
) -> None:
    """When dragging, a bar can easily land right back where it was. That must not
    fail as a collision with itself."""
    zone = create_zone(session, "bad")
    source(session, "web")
    day = create_mode(session, "tag", "Tag")
    point = _point(session, zone.id, 1, 360, day.id)
    mandant = client_als([("zone.read", None), ("schedule.manage", None)])

    response = mandant.post(
        f"/zones/{zone.id}/schedule/points/move",
        data={"point_id": str(point.id), "weekday": "1", "time_of_day": "06:00"},
        headers=_csrf(mandant),
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert not session.scalars(
        select(AuditEvent).where(AuditEvent.object_type == "schedule_point")
    ).all()


def test_verschieben_braucht_schedule_manage(client_als, session: Session) -> None:
    zone = create_zone(session, "bad")
    day = create_mode(session, "tag", "Tag")
    point = _point(session, zone.id, 1, 360, day.id)
    mandant = client_als([("zone.read", None)])
    response = mandant.post(
        f"/zones/{zone.id}/schedule/points/move",
        data={"point_id": str(point.id), "weekday": "2", "time_of_day": "07:00"},
        headers=_csrf(mandant),
    )
    assert response.status_code == 404
    session.refresh(point)
    assert point.weekday == 1


def test_nonsensical_target_data_is_refused(client_als, session: Session) -> None:
    zone = create_zone(session, "bad")
    day = create_mode(session, "tag", "Tag")
    point = _point(session, zone.id, 1, 360, day.id)
    mandant = client_als([("zone.read", None), ("schedule.manage", None)])
    for data in (
        {"weekday": "9", "time_of_day": "07:00"},
        {"weekday": "kein Tag", "time_of_day": "07:00"},
        {"weekday": "2", "time_of_day": "25:00"},
        {"weekday": "2", "time_of_day": ""},
    ):
        response = mandant.post(
            f"/zones/{zone.id}/schedule/points/move",
            data=data | {"point_id": str(point.id)},
            headers=_csrf(mandant),
        )
        assert response.status_code == 200, data
    session.refresh(point)
    assert (point.weekday, point.minute_of_day) == (1, 360)


def test_the_week_view_carries_the_point_identifier_for_dragging(
    client_als, session: Session
) -> None:
    """Without it, the bar has nothing it could move -- and dragging would be silently
    ineffective instead of visibly broken."""
    zone = create_zone(session, "bad")
    day = create_mode(session, "tag", "Tag")
    point = _point(session, zone.id, 1, 360, day.id)
    response = client_als([("zone.read", None), ("schedule.manage", None)]).get(
        f"/zones/{zone.id}/schedule"
    )
    assert f'data-point="{point.id}"' in response.text
    assert "schedule-draggable" in response.text


def test_carried_segments_are_visibly_marked_as_not_draggable(
    client_als, session: Session,
) -> None:
    zone = create_zone(session, "carried-segment")
    day = create_mode(session, "carried-segment-mode", "Tag")
    _point(session, zone.id, 1, 360, day.id)
    response = client_als([("zone.read", None), ("schedule.manage", None)]).get(
        f"/zones/{zone.id}/schedule"
    )

    carried = re.findall(
        r'<div class="[^"]*schedule-carried[^"]*"[^>]*>(.*?)</div>',
        response.text,
        re.S,
    )
    assert len(carried) == 7
    assert all("Vom Vortag übernommen" in segment for segment in carried)
    assert all("schedule-draggable" not in segment for segment in carried)


def test_without_schedule_manage_no_bar_can_be_dragged(
    client_als, session: Session
) -> None:
    """Counter-check: otherwise the test above would also be satisfied by a version
    that makes every bar draggable for everyone."""
    zone = create_zone(session, "bad")
    day = create_mode(session, "tag", "Tag")
    _point(session, zone.id, 1, 360, day.id)
    response = client_als([("zone.read", None)]).get(f"/zones/{zone.id}/schedule")
    assert response.status_code == 200
    assert "schedule-draggable" not in response.text


def test_the_create_route_still_reports_at_the_field(client_als, session: Session) -> None:
    """Counter-check to the dedicated channel for move errors: the path via the form
    should still get its message right where the input field is."""
    zone = create_zone(session, "bad")
    source(session, "web")
    day = create_mode(session, "tag", "Tag")
    _point(session, zone.id, 1, 360, day.id)
    mandant = client_als([("zone.read", None), ("schedule.manage", None)])

    response = mandant.post(
        f"/zones/{zone.id}/schedule/points",
        data={"weekday": "1", "time_of_day": "06:00", "mode_id": str(day.id)},
        headers=_csrf(mandant),
    )
    assert response.status_code == 200
    assert "bereits einen Punkt" in response.text
    assert "data-move-error" not in response.text


def test_moving_a_point_with_a_nonsensical_id_is_a_not_found(
    angemeldeter_client: TestClient, session: Session
) -> None:
    """The drag script fills this field; anything can be posted by hand.

    404 rather than 400, for the same reason as elsewhere: an unparsable id must not
    be distinguishable from an id that simply is not there.
    """
    zone = create_zone(session, "verschiebezone")
    session.flush()
    response = angemeldeter_client.post(
        f"/zones/{zone.id}/schedule/points/move",
        data={"point_id": "keine Zahl", "weekday": "1", "time_of_day": "06:00"},
        headers=_csrf(angemeldeter_client),
        follow_redirects=False,
    )
    assert response.status_code == 404


def test_moving_a_point_to_an_impossible_time_is_refused(
    angemeldeter_client: TestClient, session: Session
) -> None:
    """1439 is the last minute of a day; 24:00 belongs to the next one.

    The drag script snaps to 23:45 for exactly this reason, but the route is reachable
    without it -- the browser is not the only caller.
    """
    zone = create_zone(session, "minutenzone")
    mode = create_mode(session, "tag")
    session.flush()
    angemeldeter_client.post(
        f"/zones/{zone.id}/schedule/points",
        data={"weekday": "1", "time_of_day": "06:00", "mode_id": str(mode.id)},
        headers=_csrf(angemeldeter_client),
    )
    point_id = session.scalar(select(SchedulePoint.id).where(SchedulePoint.zone_id == zone.id))
    assert point_id is not None

    response = angemeldeter_client.post(
        f"/zones/{zone.id}/schedule/points/move",
        data={"point_id": str(point_id), "weekday": "1", "time_of_day": "24:00"},
        headers=_csrf(angemeldeter_client),
        follow_redirects=True,
    )
    assert "gültige Uhrzeit" in response.text


def test_moving_a_point_onto_an_occupied_time_names_the_conflict(
    angemeldeter_client: TestClient, session: Session
) -> None:
    """Two points at the same minute would make the schedule ambiguous.

    The database says so through its unique constraint; the view has to turn that into
    a sentence rather than letting an IntegrityError become a 500.
    """
    zone = create_zone(session, "kollisionszone")
    mode = create_mode(session, "tag")
    session.flush()
    for time_of_day in ("06:00", "07:00"):
        angemeldeter_client.post(
            f"/zones/{zone.id}/schedule/points",
            data={"weekday": "1", "time_of_day": time_of_day, "mode_id": str(mode.id)},
            headers=_csrf(angemeldeter_client),
        )
    first = session.scalar(
        select(SchedulePoint.id)
        .where(SchedulePoint.zone_id == zone.id, SchedulePoint.minute_of_day == 360)
    )
    assert first is not None

    response = angemeldeter_client.post(
        f"/zones/{zone.id}/schedule/points/move",
        data={"point_id": str(first), "weekday": "1", "time_of_day": "07:00"},
        headers=_csrf(angemeldeter_client),
        follow_redirects=True,
    )
    assert "bereits einen Punkt" in response.text


def _first_point(session: Session, zone_id: int, minute: int) -> SchedulePoint:
    point = session.scalar(
        select(SchedulePoint).where(
            SchedulePoint.zone_id == zone_id, SchedulePoint.minute_of_day == minute
        )
    )
    assert point is not None
    return point


def test_moving_a_point_beyond_the_end_of_the_day_is_refused(
    angemeldeter_client: TestClient, session: Session
) -> None:
    """1439 is the last minute of a day; 1440 would be the next one's midnight.

    Called through the domain rather than the form: the form's own time parsing
    rejects "24:00" earlier, so the guard behind it would never be reached from
    there -- and it is the one that also protects the REST and MCP adapters.
    """
    zone = create_zone(session, "minutenzone")
    mode = create_mode(session, "tag")
    session.flush()
    angemeldeter_client.post(
        f"/zones/{zone.id}/schedule/points",
        data={"weekday": "1", "time_of_day": "06:00", "mode_id": str(mode.id)},
        headers=_csrf(angemeldeter_client),
    )
    point = _first_point(session, zone.id, 360)

    with pytest.raises(ScheduleError) as fehler:
        move_schedule_point(session, zone, point, weekday=1, minute=1440, user_id=None)
    assert fehler.value.field == "time_of_day"


def test_moving_a_point_onto_an_occupied_minute_names_the_field(
    angemeldeter_client: TestClient, session: Session
) -> None:
    """Two points at the same minute would make the schedule ambiguous.

    The database says so through its unique constraint; the domain turns that into a
    sentence. The field name matters as much as the sentence: keyed under anything the
    form does not have, the message is rendered nowhere and the user sees a page that
    simply did not do what they asked. It used to be keyed `uhrzeit` while the form
    field had long been `time_of_day`.
    """
    zone = create_zone(session, "kollisionszone")
    mode = create_mode(session, "tag")
    session.flush()
    for time_of_day in ("06:00", "07:00"):
        angemeldeter_client.post(
            f"/zones/{zone.id}/schedule/points",
            data={"weekday": "1", "time_of_day": time_of_day, "mode_id": str(mode.id)},
            headers=_csrf(angemeldeter_client),
        )
    point = _first_point(session, zone.id, 360)

    with pytest.raises(ScheduleError) as fehler:
        move_schedule_point(session, zone, point, weekday=1, minute=420, user_id=None)
    assert fehler.value.field == "time_of_day"
    assert "bereits einen Punkt" in fehler.value.notice


# --- Changing the mode -----------------------------------------------------


def test_changing_a_point_mode_keeps_its_identifier_and_records_both_modes(
    client_als, session: Session
) -> None:
    source(session, "web")
    zone = create_zone(session, "moduswechsel")
    comfort = create_mode(session, "komfort", "Komfort")
    economy = create_mode(session, "sparen", "Sparen")
    point = _point(session, zone.id, 1, 390, comfort.id)
    point_id = point.id
    client = client_als([("zone.read", zone.id), ("schedule.manage", zone.id)])

    response = client.post(
        f"/zones/{zone.id}/schedule/points/mode",
        data={"point_id": str(point.id), "mode_id": str(economy.id)},
        headers=_csrf(client),
        follow_redirects=False,
    )

    assert response.status_code == 303
    session.refresh(point)
    assert point.id == point_id
    assert point.setpoint_mode_id == economy.id
    event = session.scalars(
        select(AuditEvent).where(AuditEvent.object_type == "schedule_point")
    ).one()
    assert "Modus" in event.summary and "geändert" in event.summary
    assert event.detail == "Komfort → Sparen"


def test_an_unknown_mode_is_rejected_by_the_domain(session: Session) -> None:
    zone = create_zone(session, "unbekannter-modus")
    comfort = create_mode(session, "komfort", "Komfort")
    point = _point(session, zone.id, 1, 390, comfort.id)

    with pytest.raises(ScheduleError) as error:
        change_schedule_point_mode(
            session, zone, point, mode_id=999999, user_id=None
        )

    assert error.value.field == "mode_id"
    assert point.setpoint_mode_id == comfort.id


def test_changing_to_the_current_mode_does_not_write_an_audit_event(
    session: Session,
) -> None:
    source(session, "web")
    zone = create_zone(session, "modus-unveraendert")
    comfort = create_mode(session, "komfort", "Komfort")
    point = _point(session, zone.id, 1, 390, comfort.id)

    returned = change_schedule_point_mode(
        session, zone, point, mode_id=comfort.id, user_id=None
    )

    assert returned is point
    assert not session.scalars(
        select(AuditEvent).where(AuditEvent.object_type == "schedule_point")
    ).all()


def test_a_point_from_another_zone_cannot_have_its_mode_changed(
    client_als, session: Session
) -> None:
    own_zone = create_zone(session, "eigene-moduszone")
    foreign_zone = create_zone(session, "fremde-moduszone")
    comfort = create_mode(session, "komfort", "Komfort")
    economy = create_mode(session, "sparen", "Sparen")
    point = _point(session, foreign_zone.id, 1, 390, comfort.id)
    client = client_als([("zone.read", own_zone.id), ("schedule.manage", own_zone.id)])

    response = client.post(
        f"/zones/{own_zone.id}/schedule/points/mode",
        data={"point_id": str(point.id), "mode_id": str(economy.id)},
        headers=_csrf(client),
    )

    assert response.status_code == 404
    session.refresh(point)
    assert point.setpoint_mode_id == comfort.id


def test_changing_a_point_mode_requires_schedule_manage(client_als, session: Session) -> None:
    zone = create_zone(session, "modus-ohne-recht")
    comfort = create_mode(session, "komfort", "Komfort")
    economy = create_mode(session, "sparen", "Sparen")
    point = _point(session, zone.id, 1, 390, comfort.id)
    client = client_als([("zone.read", zone.id)])

    response = client.post(
        f"/zones/{zone.id}/schedule/points/mode",
        data={"point_id": str(point.id), "mode_id": str(economy.id)},
        headers=_csrf(client),
    )

    assert response.status_code == 404
    session.refresh(point)
    assert point.setpoint_mode_id == comfort.id


def test_changing_a_point_mode_without_csrf_is_rejected(client_als, session: Session) -> None:
    zone = create_zone(session, "modus-ohne-csrf")
    comfort = create_mode(session, "komfort", "Komfort")
    economy = create_mode(session, "sparen", "Sparen")
    point = _point(session, zone.id, 1, 390, comfort.id)
    client = client_als([("zone.read", zone.id), ("schedule.manage", zone.id)])

    response = client.post(
        f"/zones/{zone.id}/schedule/points/mode",
        data={"point_id": str(point.id), "mode_id": str(economy.id)},
    )

    assert response.status_code == 403
    session.refresh(point)
    assert point.setpoint_mode_id == comfort.id


def test_invalid_mode_change_fields_are_rejected_understandably(
    client_als, session: Session
) -> None:
    zone = create_zone(session, "ungueltiger-moduswechsel")
    comfort = create_mode(session, "komfort", "Komfort")
    point = _point(session, zone.id, 1, 390, comfort.id)
    client = client_als([("zone.read", zone.id), ("schedule.manage", zone.id)])

    invalid_point = client.post(
        f"/zones/{zone.id}/schedule/points/mode",
        data={"point_id": "keine Zahl", "mode_id": str(comfort.id)},
        headers=_csrf(client),
    )
    missing_mode = client.post(
        f"/zones/{zone.id}/schedule/points/mode",
        data={"point_id": str(point.id), "mode_id": ""},
        headers=_csrf(client),
    )
    unknown_mode = client.post(
        f"/zones/{zone.id}/schedule/points/mode",
        data={"point_id": str(point.id), "mode_id": "999999"},
        headers=_csrf(client),
    )

    assert invalid_point.status_code == 404
    assert "Bitte einen Modus auswählen." in missing_mode.text
    assert "Dieser Modus ist nicht bekannt." in unknown_mode.text
    session.refresh(point)
    assert point.setpoint_mode_id == comfort.id


def test_the_rendered_mode_form_changes_the_point_without_javascript(
    client_als, session: Session
) -> None:
    source(session, "web")
    zone = create_zone(session, "gerendertes-modusformular")
    comfort = create_mode(session, "komfort", "Komfort")
    economy = create_mode(session, "sparen", "Sparen")
    point = _point(session, zone.id, 1, 390, comfort.id)
    client = client_als([("zone.read", zone.id), ("schedule.manage", zone.id)])

    page = client.get(f"/zones/{zone.id}/schedule")
    match = re.search(
        rf'<form[^>]*action="/zones/{zone.id}/schedule/points/mode"[^>]*>(.*?)</form>',
        page.text,
        re.S,
    )
    assert match is not None
    body = match.group(1)
    fields = dict(re.findall(r'name="([^"]+)" value="([^"]*)"', body))
    fields["mode_id"] = str(economy.id)

    response = client.post(
        f"/zones/{zone.id}/schedule/points/mode",
        data=fields,
        follow_redirects=False,
    )

    assert response.status_code == 303
    session.refresh(point)
    assert point.setpoint_mode_id == economy.id
    assert 'type="submit">Ändern</button>' in body
