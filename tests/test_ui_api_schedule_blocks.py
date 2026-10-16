"""Atomare Block-Operationen und mehrstufige Rücknahme unter `/ui-api/schedule`.

Rechte, CSRF, 404 und Profil laufen über die Endpunkttabelle in
`test_ui_api_schedule.py`; hier steht die Wirkung: Erfolg, Kollision, Version,
Mitternacht, Sommerzeit, Undo-Kette und dass ein Fehler nichts verändert."""

from datetime import datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.helpers import source, zone_with_schedule
from tests.test_ui_api_schedule import (
    PLAN,
    Als,
    _audit,
    _call,
    _fehler,
    _jetzt,
    _mode,
    _points,
    _segmente,
    _version,
)
from thermoctl.db.models.operations import AuditEvent, Setting
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.zone import Zone


def _duo(session: Session) -> Zone:
    """Mo 06:00 Tag (120 Minuten), danach Nacht bis zum nächsten Mo 06:00."""
    source(session, "web")
    return zone_with_schedule(
        session,
        "duo",
        [(1, 360, "tag", Decimal("21.0")), (1, 480, "nacht", Decimal("17.0"))],
    )


def _trio(session: Session) -> Zone:
    """Mo 06:00 Tag, 08:00 Nacht, 10:00 Komfort (bis zum nächsten Mo 06:00)."""
    source(session, "web")
    return zone_with_schedule(
        session,
        "trio",
        [
            (1, 360, "tag", Decimal("21.0")),
            (1, 480, "nacht", Decimal("17.0")),
            (1, 600, "komfort", Decimal("23.0")),
        ],
    )


def _block(client: TestClient, zone: Zone, action: str, body: dict[str, Any]) -> Any:
    full = {"version": _version(client, zone), **body}
    return _call(client, "POST", f"/ui-api/schedule/{zone.id}/block/{action}", full)


TAG = {"weekday": 1, "start_minute": 360, "end_minute": 480}


def _state(session: Session, zone: Zone) -> tuple[list[tuple[int, int, int]], int]:
    return _points(session, zone), session.query(AuditEvent).count()


# -- Verschieben --------------------------------------------------------------------------


def test_move_block_to_free_place_audits_and_old_range_falls_back(
    client_als: Als, session: Session
) -> None:
    zone = _duo(session)
    client = client_als(PLAN)
    tag, nacht = _mode(session, "tag").id, _mode(session, "nacht").id
    response = _block(client, zone, "move", {**TAG, "to_weekday": 3, "to_start_minute": 600})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["changed"] is True and body["undo_token"] and body["version"] != "0"
    # Der alte Bereich erbt die Nacht; Tag steht jetzt Mi 10:00-12:00.
    assert _points(session, zone) == [(3, 600, tag), (3, 720, nacht)]
    assert _audit(session, "schedule") == 1
    wednesday = [s for s in body["segments"] if s["weekday"] == 3 and s["mode_id"] == tag]
    assert [(s["start_minute"], s["end_minute"]) for s in wednesday] == [(600, 720)]


def test_move_block_across_midnight_and_the_week_seam(client_als: Als, session: Session) -> None:
    zone = _duo(session)
    client = client_als(PLAN)
    tag, nacht = _mode(session, "tag").id, _mode(session, "nacht").id
    response = _block(client, zone, "move", {**TAG, "to_weekday": 7, "to_start_minute": 1380})
    assert response.status_code == 200, response.text
    assert _points(session, zone) == [(1, 60, nacht), (7, 1380, tag)]
    monday = [s for s in response.json()["segments"] if s["weekday"] == 1][0]
    assert (monday["start_minute"], monday["end_minute"], monday["mode_id"]) == (0, 60, tag)
    # Der Block reicht von So 23:00 bis Mo 01:00 und ist mit Ende über 1440 adressierbar.
    again = _block(
        client,
        zone,
        "move",
        {
            "weekday": 7,
            "start_minute": 1380,
            "end_minute": 1500,
            "to_weekday": 2,
            "to_start_minute": 600,
        },
    )
    assert again.status_code == 200, again.text
    assert _points(session, zone) == [(2, 600, tag), (2, 720, nacht)]


def test_move_block_onto_another_block_is_409_and_changes_nothing(
    client_als: Als, session: Session
) -> None:
    zone = _duo(session)
    client = client_als(PLAN)
    before = _state(session, zone)
    error = _fehler(
        _block(client, zone, "move", {**TAG, "to_weekday": 1, "to_start_minute": 400}),
        409,
        "overlap",
    )
    assert error["field"] == "time_range"
    assert _state(session, zone) == before


def test_move_block_in_place_or_within_its_own_range_is_a_noop(
    client_als: Als, session: Session
) -> None:
    zone = _duo(session)
    client = client_als(PLAN)
    before = _state(session, zone)
    body = _block(client, zone, "move", {**TAG, "to_weekday": 1, "to_start_minute": 360}).json()
    assert body["changed"] is False and body["undo_token"] is None
    assert _state(session, zone) == before


# -- Duplizieren --------------------------------------------------------------------------


def test_duplicate_block_keeps_the_source(client_als: Als, session: Session) -> None:
    zone = _duo(session)
    client = client_als(PLAN)
    tag, nacht = _mode(session, "tag").id, _mode(session, "nacht").id
    response = _block(client, zone, "duplicate", {**TAG, "to_weekday": 3, "to_start_minute": 600})
    assert response.status_code == 200, response.text
    assert _points(session, zone) == [
        (1, 360, tag),
        (1, 480, nacht),
        (3, 600, tag),
        (3, 720, nacht),
    ]
    assert _audit(session, "schedule") == 1


def test_duplicate_block_collision_is_409_without_partial_effect(
    client_als: Als, session: Session
) -> None:
    zone = _duo(session)
    client = client_als(PLAN)
    before = _state(session, zone)
    for to_day, to_minute in ((1, 300), (1, 360), (1, 470)):
        _fehler(
            _block(
                client,
                zone,
                "duplicate",
                {**TAG, "to_weekday": to_day, "to_start_minute": to_minute},
            ),
            409,
            "overlap",
        )
    assert _state(session, zone) == before


# -- Größe ändern -------------------------------------------------------------------------


def _resize(client: TestClient, zone: Zone, block: tuple[int, int, int], **new: Any) -> Any:
    weekday, start, end = block
    return _block(
        client,
        zone,
        "resize",
        {"weekday": weekday, "start_minute": start, "end_minute": end, **new},
    )


@pytest.mark.parametrize(
    ("block", "new", "expected"),
    [
        # Ende verkürzen: der Rest erbt den Modus davor (Komfort) ...
        ((1, 360, 480), {"new_start_minute": 360, "new_end_minute": 420}, "shrink_end"),
        # ... oder, mit adjacent, die Nacht rückt nach.
        (
            (1, 360, 480),
            {"new_start_minute": 360, "new_end_minute": 420, "adjacent": True},
            "shrink_end_adjacent",
        ),
        (
            (1, 360, 480),
            {"new_start_minute": 360, "new_end_minute": 540, "adjacent": True},
            "extend_end",
        ),
        ((1, 360, 480), {"new_start_minute": 400, "new_end_minute": 480}, "shrink_start"),
        (
            (1, 480, 600),
            {"new_start_minute": 440, "new_end_minute": 600, "adjacent": True},
            "extend_start",
        ),
        # Der Komfort-Block läuft über die Wochennaht (Ende 10440 = Mo 06:00 der Folgewoche).
        (
            (1, 600, 10440),
            {"new_start_minute": 600, "new_end_minute": 10380},
            "seam_shrink",
        ),
        (
            (1, 600, 10440),
            {"new_start_minute": 600, "new_end_minute": 10380, "adjacent": True},
            "seam_shrink_adjacent",
        ),
    ],
)
def test_resize_block_edges(
    client_als: Als,
    session: Session,
    block: tuple[int, int, int],
    new: dict[str, Any],
    expected: str,
) -> None:
    zone = _trio(session)
    client = client_als(PLAN)
    tag, nacht, komfort = (_mode(session, c).id for c in ("tag", "nacht", "komfort"))
    response = _resize(client, zone, block, **new)
    assert response.status_code == 200, response.text
    assert response.json()["changed"] is True and response.json()["undo_token"]
    assert (
        _points(session, zone)
        == {
            "shrink_end": [(1, 360, tag), (1, 420, komfort), (1, 480, nacht), (1, 600, komfort)],
            "shrink_end_adjacent": [(1, 360, tag), (1, 420, nacht), (1, 600, komfort)],
            "extend_end": [(1, 360, tag), (1, 540, nacht), (1, 600, komfort)],
            "shrink_start": [(1, 400, tag), (1, 480, nacht), (1, 600, komfort)],
            "extend_start": [(1, 360, tag), (1, 440, nacht), (1, 600, komfort)],
            "seam_shrink": [(1, 300, nacht), (1, 360, tag), (1, 480, nacht), (1, 600, komfort)],
            "seam_shrink_adjacent": [(1, 300, tag), (1, 480, nacht), (1, 600, komfort)],
        }[expected]
    )
    assert _audit(session, "schedule") == 1


@pytest.mark.parametrize(
    ("block", "new", "code"),
    [
        # Verlängern ohne adjacent überdeckt den Nachbarn.
        ((1, 360, 480), {"new_start_minute": 360, "new_end_minute": 540}, "overlap"),
        ((1, 480, 600), {"new_start_minute": 440, "new_end_minute": 600}, "overlap"),
        # Mit adjacent darf der Nachbar nicht ganz verschwinden (Ende auf/hinter seinem Ende).
        (
            (1, 360, 480),
            {"new_start_minute": 360, "new_end_minute": 600, "adjacent": True},
            "overlap",
        ),
        (
            (1, 360, 480),
            {"new_start_minute": 360, "new_end_minute": 660, "adjacent": True},
            "overlap",
        ),
        (
            (1, 480, 600),
            {"new_start_minute": 300, "new_end_minute": 600, "adjacent": True},
            "overlap",
        ),
        # Beide Kanten auf einmal, rückwärts, zu groß.
        ((1, 360, 480), {"new_start_minute": 350, "new_end_minute": 490}, "validation"),
        ((1, 360, 480), {"new_start_minute": 360, "new_end_minute": 360}, "validation"),
        ((1, 360, 480), {"new_start_minute": 360, "new_end_minute": 99999}, "validation"),
        # Block gibt es nicht.
        ((1, 360, 470), {"new_start_minute": 360, "new_end_minute": 400}, "block_not_found"),
        ((1, 361, 480), {"new_start_minute": 361, "new_end_minute": 400}, "block_not_found"),
        ((1, 360, 0), {"new_start_minute": 360, "new_end_minute": 400}, "validation"),
        ((8, 360, 480), {"new_start_minute": 360, "new_end_minute": 400}, "validation"),
        ((1, 1440, 1500), {"new_start_minute": 1440, "new_end_minute": 1400}, "validation"),
    ],
)
def test_resize_block_rejections_change_nothing(
    client_als: Als, session: Session, block: tuple[int, int, int], new: dict[str, Any], code: str
) -> None:
    zone = _trio(session)
    client = client_als(PLAN)
    before = _state(session, zone)
    response = _resize(client, zone, block, **new)
    _fehler(response, {"overlap": 409, "block_not_found": 404}.get(code, 422), code)
    assert _state(session, zone) == before


def test_resize_without_change_is_a_noop(client_als: Als, session: Session) -> None:
    zone = _trio(session)
    client = client_als(PLAN)
    before = _state(session, zone)
    body = _resize(client, zone, (1, 360, 480), new_start_minute=360, new_end_minute=480).json()
    assert body["changed"] is False and body["undo_token"] is None
    assert _state(session, zone) == before


def test_resize_single_block_plan(client_als: Als, session: Session) -> None:
    """Ein einzelner Punkt besitzt die ganze Woche: kein Nachbar. Was frei wird, geht an
    den Frostschutzmodus (Hintergrund des leeren Plans)."""
    source(session, "web")
    zone = zone_with_schedule(session, "solo", [(2, 0, "tag", Decimal("21.0"))])
    client = client_als(PLAN)
    setting = session.get(Setting, 1)
    assert setting is not None
    frost, tag = setting.frost_protection_mode_id, _mode(session, "tag").id
    # Verlängern geht nicht: die Woche ist schon voll.
    _fehler(
        _resize(client, zone, (2, 0, 10080), new_start_minute=0, new_end_minute=10090),
        422,
        "validation",
    )
    # adjacent ohne Nachbarn verhält sich wie ohne.
    done = _resize(
        client, zone, (2, 0, 10080), new_start_minute=0, new_end_minute=10000, adjacent=True
    )
    assert done.status_code == 200, done.text
    assert _points(session, zone) == [(1, 1360, frost), (2, 0, tag)]
    start = _resize(client, zone, (2, 0, 10000), new_start_minute=10, new_end_minute=10000)
    assert start.status_code == 200, start.text
    assert _points(session, zone) == [(1, 1360, frost), (2, 10, tag)]


# -- Löschen ------------------------------------------------------------------------------


def test_delete_block_lets_the_previous_block_continue(client_als: Als, session: Session) -> None:
    zone = _trio(session)
    client = client_als(PLAN)
    tag, komfort = _mode(session, "tag").id, _mode(session, "komfort").id
    response = _block(
        client, zone, "delete", {"weekday": 1, "start_minute": 480, "end_minute": 600}
    )
    assert response.status_code == 200, response.text
    assert _points(session, zone) == [(1, 360, tag), (1, 600, komfort)]
    assert response.json()["undo_token"]


def test_deleting_the_last_block_empties_the_plan(client_als: Als, session: Session) -> None:
    """Der letzte Block besitzt die ganze Woche; ohne ihn bleibt der Plan leer (Frostschutz
    als Hintergrund)."""
    zone = _duo(session)
    client = client_als(PLAN)
    tag = _mode(session, "tag").id
    _block(client, zone, "delete", {"weekday": 1, "start_minute": 480, "end_minute": 10440})
    assert _points(session, zone) == [(1, 360, tag)]
    last = _block(client, zone, "delete", {"weekday": 1, "start_minute": 360, "end_minute": 10440})
    assert last.status_code == 200, last.text
    assert _points(session, zone) == [] and last.json()["segments"] == []


def test_delete_unknown_block_is_404(client_als: Als, session: Session) -> None:
    zone = _trio(session)
    client = client_als(PLAN)
    before = _state(session, zone)
    _fehler(
        _block(client, zone, "delete", {"weekday": 1, "start_minute": 480, "end_minute": 590}),
        404,
        "block_not_found",
    )
    assert _state(session, zone) == before


# -- Version, Rechte ----------------------------------------------------------------------


def test_stale_version_is_409_for_every_block_operation(client_als: Als, session: Session) -> None:
    zone = _duo(session)
    client = client_als(PLAN)
    first = _block(client, zone, "duplicate", {**TAG, "to_weekday": 3, "to_start_minute": 600})
    assert first.status_code == 200
    before = _state(session, zone)
    base = f"/ui-api/schedule/{zone.id}/block"
    target = {**TAG, "to_weekday": 5, "to_start_minute": 600}
    for action, extra in (
        ("move", target),
        ("duplicate", target),
        ("resize", {**TAG, "new_start_minute": 360, "new_end_minute": 400}),
        ("delete", dict(TAG)),
    ):
        error = _fehler(
            _call(client, "POST", f"{base}/{action}", {"version": "0", **extra}),
            409,
            "version_conflict",
        )
        assert error["field"] == "version"
    assert _state(session, zone) == before


# -- Sommerzeit ---------------------------------------------------------------------------


def test_duplicated_block_in_the_spring_gap_is_stored_as_given_and_previewed_consistently(
    client_als: Als, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """So 02:30 gibt es am 29.03.2026 nicht. Der Block bleibt in Ortszeit (So 02:30-04:30)
    im Plan; die Vorschau beginnt ihn zum ersten Zeitpunkt danach (03:30 MESZ = 01:30 UTC)."""
    zone = _duo(session)
    client = client_als(PLAN)
    tag, nacht = _mode(session, "tag").id, _mode(session, "nacht").id
    response = _block(client, zone, "duplicate", {**TAG, "to_weekday": 7, "to_start_minute": 150})
    assert response.status_code == 200, response.text
    assert (7, 150, tag) in _points(session, zone) and (7, 270, nacht) in _points(session, zone)
    _jetzt(monkeypatch, datetime(2026, 3, 28, 23, 0))
    segments = _segmente(client, zone)
    assert [(s["mode_name"], s["starts_at"]) for s in segments] == [
        ("Nacht", "2026-03-28T23:00:00+00:00"),
        ("Tag", "2026-03-29T01:30:00+00:00"),
        ("Nacht", "2026-03-29T02:30:00+00:00"),  # 04:30 MESZ
    ]


# -- Mehrstufige Rücknahme ----------------------------------------------------------------


def _undo(client: TestClient, zone: Zone, token: str) -> Any:
    return _call(client, "POST", f"/ui-api/schedule/{zone.id}/undo", {"undo_token": token})


def test_undo_chain_walks_back_through_block_and_point_operations(
    client_als: Als, session: Session
) -> None:
    zone = _duo(session)
    client = client_als(PLAN)
    tag, nacht = _mode(session, "tag").id, _mode(session, "nacht").id
    states = [_points(session, zone)]
    token: str | None = None
    steps: list[tuple[str, str, dict[str, Any]]] = [
        ("POST", "block/duplicate", {**TAG, "to_weekday": 3, "to_start_minute": 600}),
        ("POST", "block/move", {**TAG, "to_weekday": 5, "to_start_minute": 0}),
        (
            "POST",
            "paint",
            {"weekday": 2, "start_minute": 60, "end_minute": 120, "mode_id": tag},
        ),
        ("POST", "points", {"weekday": 6, "minute_of_day": 700, "mode_id": nacht}),
        ("POST", "block/delete", {"weekday": 3, "start_minute": 600, "end_minute": 720}),
    ]
    for method, path, body in steps:
        response = _call(
            client,
            method,
            f"/ui-api/schedule/{zone.id}/{path}",
            {"version": _version(client, zone), "previous_undo_token": token, **body},
        )
        assert response.status_code == 200, (path, response.text)
        token = response.json()["undo_token"]
        assert token
        states.append(_points(session, zone))
    assert len(set(map(tuple, states))) == len(states)
    # Fünf Schritte zurück, jeweils mit der Marke aus der vorigen Antwort.
    for expected in reversed(states[:-1]):
        assert token
        undone = _undo(client, zone, token)
        assert undone.status_code == 200, undone.text
        assert _points(session, zone) == expected
        token = undone.json()["undo_token"]
    assert token is None  # Anfang der Kette erreicht


def test_undo_chain_covers_every_point_operation(client_als: Als, session: Session) -> None:
    zone = _trio(session)
    client = client_als(PLAN)
    nacht = _mode(session, "nacht").id
    base = f"/ui-api/schedule/{zone.id}"
    original = _points(session, zone)
    point = session.query(SchedulePoint).filter_by(zone_id=zone.id, minute_of_day=480).one()
    calls: list[tuple[str, str, dict[str, Any] | None]] = [
        ("PUT", f"{base}/points/{point.id}", {"weekday": 1, "minute_of_day": 500}),
        ("PUT", f"{base}/points/{point.id}/mode", {"mode_id": _mode(session, "tag").id}),
        ("POST", f"{base}/points", {"weekday": 4, "minute_of_day": 60, "mode_id": nacht}),
        ("DELETE", f"{base}/points/{point.id}", None),
        ("POST", f"{base}/copy-day", {"weekday": 1, "scope": "all"}),
    ]
    token: str | None = None
    states = [original]
    for method, url, body in calls:
        version = _version(client, zone)
        if method == "DELETE":
            suffix = f"?version={version}" + (f"&previous_undo_token={token}" if token else "")
            response = _call(client, method, url + suffix)
        else:
            assert body is not None
            response = _call(
                client, method, url, {"version": version, "previous_undo_token": token, **body}
            )
        assert response.status_code == 200, (url, response.text)
        token = response.json()["undo_token"]
        assert token, url
        states.append(_points(session, zone))
    for expected in reversed(states[:-1]):
        assert token
        undone = _undo(client, zone, token)
        assert undone.status_code == 200, undone.text
        assert _points(session, zone) == expected
        token = undone.json()["undo_token"]
    assert token is None


def test_undo_chain_is_stale_after_a_foreign_change_and_breaks_on_mismatching_history(
    client_als: Als, session: Session
) -> None:
    zone = _duo(session)
    client = client_als(PLAN)
    first = _block(
        client, zone, "duplicate", {**TAG, "to_weekday": 3, "to_start_minute": 600}
    ).json()["undo_token"]
    # Eine Vorgängermarke, die nicht zum Stand passt, beginnt eine neue Kette.
    second = _block(
        client,
        zone,
        "duplicate",
        {**TAG, "to_weekday": 5, "to_start_minute": 600, "previous_undo_token": first + "x"},
    ).json()["undo_token"]
    assert second
    undone = _undo(client, zone, second)
    assert undone.status_code == 200 and undone.json()["undo_token"] is None
    # Die alte Marke gehört nicht mehr zum Stand (zwischendurch wurde zurückgenommen).
    after = _points(session, zone)
    _fehler(_undo(client, zone, first), 409, "undo_stale")
    assert _points(session, zone) == after


def test_previous_token_of_another_state_or_zone_is_ignored(
    client_als: Als, session: Session
) -> None:
    zone = _duo(session)
    client = client_als(PLAN)
    token = _block(
        client, zone, "duplicate", {**TAG, "to_weekday": 3, "to_start_minute": 600}
    ).json()["undo_token"]
    # `version` im Körper passt zum Stand, aber die Marke gehört zu einem früheren Plan:
    # erst eine fremde Änderung, dann die Marke als Vorgänger.
    _block(client, zone, "delete", {"weekday": 3, "start_minute": 600, "end_minute": 720})
    chained = _block(
        client,
        zone,
        "duplicate",
        {**TAG, "to_weekday": 4, "to_start_minute": 600, "previous_undo_token": token},
    ).json()["undo_token"]
    undone = _undo(client, zone, chained)
    assert undone.status_code == 200 and undone.json()["undo_token"] is None


def test_tampered_history_is_rejected(client_als: Als, session: Session) -> None:
    import base64
    import json

    zone = _duo(session)
    client = client_als(PLAN)
    first = _block(
        client, zone, "duplicate", {**TAG, "to_weekday": 3, "to_start_minute": 600}
    ).json()["undo_token"]
    second = _block(
        client,
        zone,
        "duplicate",
        {**TAG, "to_weekday": 5, "to_start_minute": 600, "previous_undo_token": first},
    ).json()["undo_token"]
    encoded, signature = second.split(".", 1)
    payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
    assert payload["history"]
    payload["history"] = [[[1, 0, 1]]]
    forged = (
        base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode())
        .decode()
        .rstrip("=")
        + "."
        + signature
    )
    error = _fehler(_undo(client, zone, forged), 422, "validation")
    assert error["field"] == "undo_token"


def test_undo_with_malformed_history_in_a_signed_token_is_422(
    client_als: Als, session: Session
) -> None:
    from thermoctl.web.shared.schedule import undo_token

    zone = _duo(session)
    client = client_als(PLAN)
    token = undo_token(zone.id, ((), (), 0), history=[(("x",),)])  # type: ignore[arg-type]
    _fehler(_undo(client, zone, token), 422, "validation")


def test_move_and_duplicate_reject_an_invalid_target_without_change(
    client_als: Als, session: Session
) -> None:
    zone = _duo(session)
    client = client_als(PLAN)
    before = _state(session, zone)
    for action in ("move", "duplicate"):
        for to_day, to_minute, field in (
            (0, 0, "weekday"),
            (8, 0, "weekday"),
            (1, 1440, "time_range"),
            (1, -1, "time_range"),
        ):
            error = _fehler(
                _block(
                    client,
                    zone,
                    action,
                    {**TAG, "to_weekday": to_day, "to_start_minute": to_minute},
                ),
                422,
                "validation",
            )
            assert error["field"] == field
    assert _state(session, zone) == before


def test_previous_token_that_does_not_end_in_the_current_plan_is_ignored(
    client_als: Als, session: Session
) -> None:
    from thermoctl.web.shared.schedule import undo_token

    zone = _duo(session)
    client = client_als(PLAN)
    version = int(_version(client, zone))
    liar = undo_token(zone.id, (((1, 0, 1),), ((1, 0, 2),), version))
    # Eigentlich ein Stand, den es nie gab; die Kette beginnt neu, die Absicht gelingt.
    response = _block(
        client,
        zone,
        "duplicate",
        {**TAG, "to_weekday": 3, "to_start_minute": 600, "previous_undo_token": liar},
    )
    assert response.status_code == 200, response.text
    undone = _undo(client, zone, response.json()["undo_token"])
    assert undone.status_code == 200 and undone.json()["undo_token"] is None
