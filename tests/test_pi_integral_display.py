"""I-Anteil anzeigen (Karte 5481A15A, lesender Teil): Raumseite, Verlaufsreihe, Kacheln.

Alles ist nur lesend. Geprüft wird über HTTP: Werte, letzter Reset, Lücken bleiben Lücken,
und Wohnungskonten sehen nichts davon.
"""

from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.helpers import (
    create_settings,
    create_zone,
    sensor_status_of,
    user_with_permissions,
)
from thermoctl.auth.csrf import CSRF_HEADER, csrf_token
from thermoctl.auth.sessions import COOKIE_NAME, create_session
from thermoctl.auth.tokens import issue_token
from thermoctl.config import get_settings
from thermoctl.db.base import utcnow
from thermoctl.db.models.dashboard import DashboardTile
from thermoctl.db.models.identity import AccessGroup
from thermoctl.db.models.state import ShadowDecision, ZoneState
from thermoctl.db.models.zone import Zone
from thermoctl.domain import dashboard_templates
from thermoctl.domain.pi_control import (
    INTEGRATOR_CONTINUE,
    INTEGRATOR_HOLD,
    INTEGRATOR_RESET,
    RESET_REASON_NO_HEATING,
    RESET_REASON_WINDOW_OPEN,
)
from thermoctl.domain.ui_profile import WebUiProfile
from thermoctl.mcp import server as mcp_server

Als = Callable[[list[tuple[str, int | None]]], TestClient]

READ: list[tuple[str, int | None]] = [("zone.read", None)]
NO_HEATING_LABEL = "Seit mehr als 24 Stunden gab es keine Wärmeanforderung."


def _now() -> datetime:
    return utcnow().replace(second=0, microsecond=0)


def _pi_zone(
    session: Session, name: str, *, integral: str | None = "0.42", pi_enabled: bool = True
) -> Zone:
    zone = create_zone(session, name)
    zone.pi_enabled = pi_enabled
    if integral is not None:
        session.add(
            ZoneState(
                zone_id=zone.id,
                sensor_status_id=sensor_status_of(session).id,
                window_open=False,
                updated_at=_now(),
                pi_integral=Decimal(integral),
            )
        )
    session.flush()
    return zone


def _row(
    session: Session,
    zone: Zone,
    at: datetime,
    *,
    after: str | None = None,
    action: str | None = None,
    reason: str | None = None,
) -> None:
    session.add(
        ShadowDecision(
            decided_at=at,
            zone_id=zone.id,
            setpoint_reason="Zeitplan",
            would_heat=False,
            outcome_code="aus",
            reason="Test",
            temperature_c=Decimal("20.0"),
            setpoint_c=Decimal("21.0"),
            pi_integral_after=Decimal(after) if after is not None else None,
            pi_integrator_action=action,
            pi_reset_reason=reason,
        )
    )
    session.flush()


def _room(client: TestClient, zone: Zone) -> dict[str, Any]:
    response = client.get(f"/ui-api/rooms/{zone.id}")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _history(client: TestClient, zone: Zone, start: datetime, end: datetime) -> dict[str, Any]:
    response = client.get(
        "/ui-api/history",
        params={
            "zone_id": zone.id,
            "from": start.isoformat(),
            "to": end.isoformat(),
            "resolution": 600,
        },
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


# -- Raumseite und Raumliste --------------------------------------------------------------


def test_room_shows_the_live_integral_and_the_newest_reset(
    client_als: Als, session: Session
) -> None:
    create_settings(session)
    zone = _pi_zone(session, "bad", integral="0.42")
    other = _pi_zone(session, "flur", integral="0.10")
    now = _now()
    _row(session, zone, now - timedelta(days=5), after="0", action=INTEGRATOR_RESET,
         reason=RESET_REASON_WINDOW_OPEN)
    _row(session, zone, now - timedelta(days=3), after="0.1", action=INTEGRATOR_CONTINUE)
    _row(session, zone, now - timedelta(days=2), after="0", action=INTEGRATOR_RESET,
         reason=RESET_REASON_NO_HEATING)
    # Jünger, aber kein Reset: angehalten zählt nicht, auch mit Grund.
    _row(session, zone, now - timedelta(hours=1), after="0.42", action=INTEGRATOR_HOLD,
         reason=RESET_REASON_WINDOW_OPEN)
    # Ein jüngerer Reset eines anderen Raums darf nicht hierher geraten.
    _row(session, other, now - timedelta(minutes=5), after="0", action=INTEGRATOR_RESET,
         reason=RESET_REASON_NO_HEATING)
    client = client_als(READ)

    pi = _room(client, zone)["pi"]

    assert Decimal(pi["integral"]) == Decimal("0.42")
    assert pi["last_reset"] == {
        "at": (now - timedelta(days=2)).isoformat() + "+00:00",
        "reason": RESET_REASON_NO_HEATING,
        "reason_label": NO_HEATING_LABEL,
    }
    listed = {room["id"]: room["pi"] for room in client.get("/ui-api/rooms").json()["rooms"]}
    assert listed[zone.id] == pi
    assert Decimal(listed[other.id]["integral"]) == Decimal("0.10")


def test_room_without_reset_state_or_pi(client_als: Als, session: Session) -> None:
    create_settings(session)
    fresh = _pi_zone(session, "frisch", integral="0")
    no_state = _pi_zone(session, "ohne-zustand", integral=None)
    off = _pi_zone(session, "hysterese", pi_enabled=False)
    client = client_als(READ)

    fresh_pi = _room(client, fresh)["pi"]
    assert Decimal(fresh_pi["integral"]) == 0
    assert fresh_pi["last_reset"] is None
    assert _room(client, no_state)["pi"] == {"integral": None, "last_reset": None}
    assert _room(client, off)["pi"] is None


def test_unknown_reset_reason_is_shown_as_stored(client_als: Als, session: Session) -> None:
    create_settings(session)
    zone = _pi_zone(session, "alt")
    _row(session, zone, _now() - timedelta(hours=2), action=INTEGRATOR_RESET, reason="frueher_mal")

    reset = _room(client_als(READ), zone)["pi"]["last_reset"]

    assert reset["reason"] == "frueher_mal"
    assert reset["reason_label"] is None


def test_tenant_profile_never_sees_the_integral(
    tenant_client: Als, session: Session
) -> None:
    create_settings(session)
    zone = _pi_zone(session, "wohnung")
    now = _now()
    _row(session, zone, now - timedelta(minutes=30), after="0.42", action=INTEGRATOR_RESET,
         reason=RESET_REASON_NO_HEATING)
    client = tenant_client(READ)

    assert _room(client, zone)["pi"] is None
    assert [r["pi"] for r in client.get("/ui-api/rooms").json()["rooms"]] == [None]
    history = _history(client, zone, now - timedelta(hours=1), now)
    assert history["pi_resets"] is None
    assert {bucket["pi_integral"] for bucket in history["buckets"]} == {None}


PI_NUMBERS = (
    "pi_integral_before",
    "pi_integral_after",
    "pi_raw_duty",
    "pi_frozen_duty",
    "pi_feedforward_duty",
)


def _decision_with_pi_numbers(session: Session, zone: Zone) -> None:
    _row(session, zone, _now() - timedelta(minutes=1), after="0.42", action=INTEGRATOR_HOLD)
    decision = session.scalars(select(ShadowDecision)).one()
    decision.pi_integral_before = Decimal("0.42")
    decision.pi_raw_duty = Decimal("0.56")
    decision.pi_frozen_duty = Decimal("0.55")
    decision.pi_feedforward_duty = Decimal("0.10")
    session.flush()


@pytest.mark.parametrize("profile", [WebUiProfile.ADMIN, WebUiProfile.TENANT])
def test_operation_overview_gives_pi_numbers_to_the_plant_view_only(
    client_als: Als, tenant_client: Als, session: Session, profile: WebUiProfile
) -> None:
    """Die Rohwerte verraten den I-Anteil: Alle PI-Zahlen sind Anlagentechnik."""
    create_settings(session)
    zone = _pi_zone(session, "betrieb")
    _decision_with_pi_numbers(session, zone)
    client = (client_als if profile is WebUiProfile.ADMIN else tenant_client)(READ)

    response = client.get("/ui-api/operation")

    assert response.status_code == 200, response.text
    decision = response.json()["rooms"][0]["decision"]
    assert decision["reason"] == "Test"
    visible = [decision[key] is not None for key in PI_NUMBERS]
    assert visible == [profile is WebUiProfile.ADMIN] * len(PI_NUMBERS)


@pytest.mark.parametrize("profile", [WebUiProfile.ADMIN, WebUiProfile.TENANT])
def test_mcp_decisions_give_pi_numbers_to_the_plant_view_only(
    session: Session, profile: WebUiProfile
) -> None:
    zone = _pi_zone(session, "mcp")
    _decision_with_pi_numbers(session, zone)
    owner = user_with_permissions(session, f"mcp-{profile.value}", [("zone.read", zone.id)])
    for group in session.scalars(select(AccessGroup)):
        group.ui_profile = profile.value
    _token, plaintext = issue_token(
        session, owner, "diagnose", [("zone.read", zone.id)], None
    )

    [entry] = mcp_server.shadow_decisions(session, plaintext, zone.id, 1)

    assert entry["reason"] == "Test"
    visible = [entry[key] is not None for key in PI_NUMBERS]
    assert visible == [profile is WebUiProfile.ADMIN] * len(PI_NUMBERS)


# -- Verlauf ------------------------------------------------------------------------------


def test_history_series_keeps_gaps_and_takes_the_last_value_of_a_bucket(
    client_als: Als, session: Session
) -> None:
    create_settings(session)
    zone = _pi_zone(session, "reihe")
    start = _now() - timedelta(hours=1)
    _row(session, zone, start + timedelta(minutes=1), after="0.20", action=INTEGRATOR_CONTINUE)
    _row(session, zone, start + timedelta(minutes=9), after="0.30", action=INTEGRATOR_CONTINUE)
    # Fach 2: Zeilen ohne I-Wert (Hysterese) -- Lücke, kein fortgeschriebener Wert.
    _row(session, zone, start + timedelta(minutes=12))
    # Fach 3: letzte Zeile ohne Wert, davor einer: der vorhandene zählt.
    _row(session, zone, start + timedelta(minutes=21), after="0.50", action=INTEGRATOR_HOLD)
    _row(session, zone, start + timedelta(minutes=29))
    # Fach 5: gar keine Zeile in Fach 4.
    _row(session, zone, start + timedelta(minutes=41), after="0", action=INTEGRATOR_RESET,
         reason=RESET_REASON_NO_HEATING)

    body = _history(client_als(READ), zone, start, start + timedelta(hours=1))

    values = [
        None if b["pi_integral"] is None else Decimal(b["pi_integral"]) for b in body["buckets"]
    ]
    assert values == [Decimal("0.3"), None, Decimal("0.5"), None, Decimal("0"), None]
    assert body["pi_resets"] == [
        {
            "at": (start + timedelta(minutes=41)).isoformat() + "+00:00",
            "reason": RESET_REASON_NO_HEATING,
            "reason_label": NO_HEATING_LABEL,
        }
    ]


def test_history_marks_a_run_of_reset_rows_once(client_als: Als, session: Session) -> None:
    """Ältere Fassungen schrieben bei offenem Fenster in jedem Zyklus „zurückgesetzt“."""
    create_settings(session)
    zone = _pi_zone(session, "altlauf")
    start = _now() - timedelta(hours=1)
    for minute, action in (
        (1, INTEGRATOR_RESET),
        (2, INTEGRATOR_RESET),
        (3, INTEGRATOR_CONTINUE),
        (4, INTEGRATOR_RESET),
    ):
        _row(session, zone, start + timedelta(minutes=minute), after="0", action=action,
             reason=RESET_REASON_WINDOW_OPEN)

    body = _history(client_als(READ), zone, start, start + timedelta(hours=1))

    assert [reset["at"] for reset in body["pi_resets"]] == [
        (start + timedelta(minutes=minute)).isoformat() + "+00:00" for minute in (1, 4)
    ]


def test_history_breaks_the_line_where_rows_without_a_value_lie_between(
    client_als: Als, session: Session
) -> None:
    """Zwei Fächer mit Wert, dazwischen Zyklen ohne: nichts darf sie verbinden."""
    create_settings(session)
    zone = _pi_zone(session, "bruch")
    start = _now() - timedelta(hours=1)
    _row(session, zone, start + timedelta(minutes=1), after="0.40", action=INTEGRATOR_CONTINUE)
    _row(session, zone, start + timedelta(minutes=2), after="0.42", action=INTEGRATOR_CONTINUE)
    for minute in range(3, 16):  # 13 Zyklen ohne I-Wert, über die Fachgrenze hinweg
        _row(session, zone, start + timedelta(minutes=minute))
    _row(session, zone, start + timedelta(minutes=16), after="0.61", action=INTEGRATOR_CONTINUE)
    _row(session, zone, start + timedelta(minutes=21), after="0.62", action=INTEGRATOR_CONTINUE)
    # Lange Pause ganz ohne Zeilen: auch das trennt.
    _row(session, zone, start + timedelta(minutes=39), after="0.70", action=INTEGRATOR_CONTINUE)

    body = _history(client_als(READ), zone, start, start + timedelta(hours=1))

    assert [b["pi_break"] for b in body["buckets"]] == [False, True, False, True, False, False]


def test_room_and_history_date_a_run_of_reset_rows_the_same(
    client_als: Als, session: Session
) -> None:
    create_settings(session)
    zone = _pi_zone(session, "gleich")
    start = _now() - timedelta(hours=1)
    for minute, action in (
        (1, INTEGRATOR_RESET),
        (2, INTEGRATOR_CONTINUE),
        (3, INTEGRATOR_RESET),
        (4, INTEGRATOR_RESET),
    ):
        _row(session, zone, start + timedelta(minutes=minute), after="0", action=action,
             reason=RESET_REASON_NO_HEATING)
    client = client_als(READ)
    began = (start + timedelta(minutes=3)).isoformat() + "+00:00"

    assert _room(client, zone)["pi"]["last_reset"]["at"] == began
    assert _history(client, zone, start, start + timedelta(hours=1))["pi_resets"][-1]["at"] == began
    # Beginnt der Ausschnitt mitten in der Folge, liegt ihr Anfang außerhalb: keine Marke.
    later = _history(client, zone, start + timedelta(minutes=4), start + timedelta(hours=1))
    assert later["pi_resets"] == []


def test_history_offers_the_series_only_where_it_means_something(
    client_als: Als, session: Session
) -> None:
    create_settings(session)
    never = create_zone(session, "nie-pi")
    was_pi = _pi_zone(session, "war-pi", pi_enabled=False)
    enabled_empty = _pi_zone(session, "neu-pi")
    start = _now() - timedelta(hours=1)
    _row(session, never, start + timedelta(minutes=5))
    _row(session, was_pi, start + timedelta(minutes=5), after="0.4", action=INTEGRATOR_CONTINUE)
    client = client_als(READ)
    end = start + timedelta(hours=1)

    assert _history(client, never, start, end)["pi_resets"] is None
    assert _history(client, was_pi, start, end)["pi_resets"] == []
    assert _history(client, enabled_empty, start, end)["pi_resets"] == []


# -- Kacheln ------------------------------------------------------------------------------

URL = "/ui-api/dashboard"


def _login(
    client: TestClient,
    session: Session,
    name: str,
    profile: WebUiProfile = WebUiProfile.ADMIN,
    permissions: list[tuple[str, int | None]] = READ,
) -> None:
    user = user_with_permissions(session, name, permissions, ui_profile=profile)
    _http, secret = create_session(session, user, 3600)
    client.cookies.set(COOKIE_NAME, secret)


def _put(client: TestClient, version: int, *tiles: dict[str, Any]) -> Any:
    secret = client.cookies.get(COOKIE_NAME)
    assert secret is not None
    headers = {CSRF_HEADER: csrf_token(secret, get_settings().secret_key.get_secret_value())}
    return client.put(
        URL,
        json={"version": version, "sections": [{"title": "S", "tiles": list(tiles)}]},
        headers=headers,
    )


def _tiles(body: dict[str, Any]) -> list[dict[str, Any]]:
    return [tile for section in body["sections"] for tile in section["tiles"]]


def test_chart_tile_remembers_show_pi_and_the_controller_tile_is_saved(
    client: TestClient, session: Session
) -> None:
    create_settings(session)
    zone = create_zone(session, "kachel")
    _login(client, session, "verwaltung")

    saved = _put(
        client,
        0,
        {"kind": "chart", "zone_id": zone.id, "show_pi": True},
        {"kind": "controller", "zone_id": zone.id, "show_pi": True},
        {"kind": "room", "zone_id": zone.id, "show_pi": True},
    )

    assert saved.status_code == 200, saved.text
    tiles = _tiles(client.get(URL).json())
    assert [(t["kind"], t["show_pi"], t["zone_id"]) for t in tiles] == [
        ("chart", True, zone.id),
        ("controller", False, None),
        ("room", False, zone.id),
    ]


def test_tenant_gets_neither_the_controller_tile_nor_show_pi(
    client: TestClient, session: Session
) -> None:
    create_settings(session)
    zone = create_zone(session, "mietraum")
    _login(client, session, "mieter", WebUiProfile.TENANT)

    refused = _put(client, 0, {"kind": "controller"})
    assert refused.status_code == 422
    assert refused.json()["field"] == "kind"

    saved = _put(client, 0, {"kind": "chart", "zone_id": zone.id, "show_pi": True})
    assert saved.status_code == 200, saved.text
    assert [t["show_pi"] for t in _tiles(saved.json())] == [False]


def test_stored_pi_content_disappears_when_the_account_becomes_a_tenant(
    client: TestClient, session: Session
) -> None:
    create_settings(session)
    zone = create_zone(session, "wechsel")
    _login(client, session, "wechsler")
    assert _put(
        client, 0, {"kind": "chart", "zone_id": zone.id, "show_pi": True}, {"kind": "controller"}
    ).status_code == 200

    for group in session.scalars(select(AccessGroup)):
        group.ui_profile = WebUiProfile.TENANT.value
    session.flush()

    tiles = _tiles(client.get(URL).json())
    assert [(t["kind"], t["show_pi"]) for t in tiles] == [("chart", False)]


def test_controller_tile_needs_a_visible_room(client: TestClient, session: Session) -> None:
    create_settings(session)
    _login(client, session, "ohne-raum", permissions=[("audit.read", None)])

    assert _put(client, 0, {"kind": "controller"}).status_code == 422


def test_template_carries_show_pi_to_a_new_layout(session: Session) -> None:
    create_settings(session)
    zone = create_zone(session, "vorlage")
    user = user_with_permissions(session, "vorlagen-nutzer", READ)
    tile = {
        "kind": "chart",
        "zone_id": zone.id,
        "title": "",
        "period_days": 1,
        "width": 8,
        "metric": None,
        "destination": None,
        "note": "",
        "show_pi": True,
    }
    template = dashboard_templates.create_template(session, "mit I", [("S", [tile])])

    dashboard_templates.apply_template(session, user.id, template.id)

    assert dashboard_templates.template_sections(session, template.id)[0][1][0]["show_pi"] is True
    assert [row.show_pi for row in session.scalars(select(DashboardTile))] == [True]
