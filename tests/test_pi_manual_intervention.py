"""I-Anteil zurücksetzen und setzen (Karte A391AABC, Teil 2): Eingriff in die Regelkette.

Der Eingriff schreibt nur eine **Anforderung** (`zone_state.pi_manual_*`) und einen Audit-Eintrag.
Übernommen wird sie vom nächsten Regelzyklus (`shadow_run._pi_outcome`), der Wert und Grund
(`manuell_reset` / `manuell_gesetzt`) ins Schaltprotokoll schreibt. Tore und sicherer Anlauf
gelten dabei unverändert.

Geprüft wird über dieselben Wege wie im Betrieb: Weboberfläche (`/ui-api`), REST, MCP.
"""

from collections.abc import Callable
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from tests.helpers import create_settings, source, user_with_permissions
from tests.test_shadow_run_pi import COLD_C, NOW, _assign_actuator, _pi_zone, _row_for
from thermoctl.auth.csrf import CSRF_HEADER, csrf_token
from thermoctl.auth.sessions import COOKIE_NAME
from thermoctl.auth.tokens import issue_token
from thermoctl.config import get_settings
from thermoctl.db.models.device import ZoneDevice
from thermoctl.db.models.operations import AuditEvent
from thermoctl.db.models.state import ShadowDecision, ZoneState
from thermoctl.db.models.zone import Zone
from thermoctl.domain.authz import Forbidden
from thermoctl.domain.pi_control import (
    INTEGRATOR_RESET,
    RESET_REASON_INVALID_STATE,
    RESET_REASON_WINDOW_OPEN,
)
from thermoctl.domain.ui_profile import WebUiProfile
from thermoctl.mcp import server as mcp_server
from thermoctl.services import shadow_run

Als = Callable[[list[tuple[str, int | None]]], TestClient]

MANAGE: list[tuple[str, int | None]] = [("zone.read", None), ("zone.manage", None)]
READ_ONLY: list[tuple[str, int | None]] = [("zone.read", None)]
STORED = Decimal("0.5")


def _zone(session: Session, name: str = "bad", **kwargs: Any) -> Zone:
    zone = _pi_zone(session, name, **kwargs)
    state = session.get(ZoneState, zone.id)
    assert state is not None
    state.pi_integral = STORED
    session.flush()
    return zone


def _state(session: Session, zone: Zone) -> ZoneState:
    session.expire_all()
    state = session.get(ZoneState, zone.id)
    assert state is not None
    return state


def _audit(session: Session) -> list[AuditEvent]:
    return list(session.scalars(select(AuditEvent).where(AuditEvent.action.like("pi.%"))))


def _csrf(client: TestClient) -> dict[str, str]:
    http_session = client.cookies.get(COOKIE_NAME)
    assert http_session is not None
    return {CSRF_HEADER: csrf_token(http_session, get_settings().secret_key.get_secret_value())}


def _ui_reset(client: TestClient, zone: Zone) -> Any:
    return client.post(f"/ui-api/rooms/{zone.id}/pi/reset", headers=_csrf(client))


def _ui_set(client: TestClient, zone: Zone, value: object) -> Any:
    return client.put(
        f"/ui-api/rooms/{zone.id}/pi/integral", json={"value": value}, headers=_csrf(client)
    )


def _bearer(
    session: Session, permissions: list[tuple[str, int | None]], name: str = "pi"
) -> dict[str, str]:
    source(session, "api")
    owner = user_with_permissions(session, f"{name}-rest", permissions)
    _token, plaintext = issue_token(session, owner, name, permissions, None)
    return {"Authorization": f"Bearer {plaintext}"}


def _mcp_plaintext(
    session: Session,
    permissions: list[tuple[str, int | None]],
    profile: WebUiProfile = WebUiProfile.ADMIN,
    name: str = "pi-mcp",
) -> str:
    source(session, "mcp")
    owner = user_with_permissions(session, name, permissions, ui_profile=profile)
    _token, plaintext = issue_token(session, owner, name, permissions, None)
    return plaintext


# -- Rechte: nur Verwalter ----------------------------------------------------------------


def test_web_rejects_everyone_without_zone_manage(client_als: Als, session: Session) -> None:
    create_settings(session)
    zone = _zone(session)
    client = client_als(READ_ONLY)

    assert _ui_reset(client, zone).status_code == 403
    assert _ui_set(client, zone, 0.3).status_code == 403

    assert _state(session, zone).pi_manual_reason is None
    assert _state(session, zone).pi_integral == STORED
    assert _audit(session) == []


def test_web_rejects_zone_manage_for_a_different_zone(
    client_als: Als, session: Session
) -> None:
    create_settings(session)
    zone = _zone(session, "bad")
    other = _zone(session, "flur")
    client = client_als([("zone.read", None), ("zone.manage", other.id)])

    assert _ui_reset(client, zone).status_code == 403
    assert _ui_set(client, zone, 0.3).status_code == 403
    assert _state(session, zone).pi_manual_reason is None


def test_web_rejects_the_tenant_profile_even_with_zone_manage(
    tenant_client: Als, session: Session
) -> None:
    create_settings(session)
    zone = _zone(session)
    client = tenant_client(MANAGE)

    assert _ui_reset(client, zone).status_code == 403
    assert _ui_set(client, zone, 0.3).status_code == 403
    assert _state(session, zone).pi_manual_reason is None
    assert _audit(session) == []


def test_web_an_unreadable_room_stays_unknown(client_als: Als, session: Session) -> None:
    create_settings(session)
    zone = _zone(session)
    client = client_als([("zone.manage", None)])  # kein zone.read: der Raum ist unsichtbar

    assert _ui_reset(client, zone).status_code == 404
    assert _ui_set(client, zone, 0.3).status_code == 404


def test_rest_rejects_everyone_without_zone_manage(client: TestClient, session: Session) -> None:
    create_settings(session)
    zone = _zone(session)
    headers = _bearer(session, READ_ONLY)

    reset = client.post(f"/api/v1/zones/{zone.id}/pi-state/reset", headers=headers)
    assert reset.status_code == 403
    response = client.put(
        f"/api/v1/zones/{zone.id}/pi-state/integral", json={"value": "0.3"}, headers=headers
    )
    assert response.status_code == 403
    assert _state(session, zone).pi_manual_reason is None
    assert _audit(session) == []


def test_mcp_rejects_everyone_without_zone_manage(session: Session) -> None:
    create_settings(session)
    zone = _zone(session)
    plaintext = _mcp_plaintext(session, READ_ONLY)

    with pytest.raises(Forbidden):
        mcp_server.reset_pi_integral(session, plaintext, zone.id)
    with pytest.raises(Forbidden):
        mcp_server.set_pi_integral(session, plaintext, zone.id, Decimal("0.3"))
    assert _state(session, zone).pi_manual_reason is None
    assert _audit(session) == []


def test_mcp_rejects_the_tenant_profile_even_with_zone_manage(session: Session) -> None:
    create_settings(session)
    zone = _zone(session)
    plaintext = _mcp_plaintext(session, MANAGE, WebUiProfile.TENANT)

    with pytest.raises(Forbidden):
        mcp_server.reset_pi_integral(session, plaintext, zone.id)
    with pytest.raises(Forbidden):
        mcp_server.read_pi_state(session, plaintext, zone.id)


def test_reading_needs_zone_read_and_the_plant_view(
    client: TestClient, session: Session
) -> None:
    create_settings(session)
    zone = _zone(session)

    body = client.get(
        f"/api/v1/zones/{zone.id}/pi-state", headers=_bearer(session, READ_ONLY, "leser")
    )
    assert body.status_code == 200
    assert Decimal(body.json()["integral"]) == STORED
    assert body.json()["pending"] is None

    nothing = client.get(
        f"/api/v1/zones/{zone.id}/pi-state", headers=_bearer(session, [], "ohne")
    )
    assert nothing.status_code == 404  # ohne zone.read ist der Raum unsichtbar
    with pytest.raises(Forbidden):
        mcp_server.read_pi_state(session, _mcp_plaintext(session, READ_ONLY, WebUiProfile.TENANT,
                                                         "mieter"), zone.id)


# -- Wertebereich 0..1, serverseitig --------------------------------------------------------


@pytest.mark.parametrize(
    "value", [-0.01, 1.000001, 1.5, 7, "abc", None, "NaN", "Infinity", True]
)
def test_web_rejects_values_outside_zero_to_one(
    client_als: Als, session: Session, value: object
) -> None:
    create_settings(session)
    zone = _zone(session)

    response = _ui_set(client_als(MANAGE), zone, value)

    assert response.status_code == 422, response.text
    assert _state(session, zone).pi_manual_reason is None
    assert _audit(session) == []


@pytest.mark.parametrize("value", ["-0.01", "1.000001", "2", "x", "NaN", "Infinity"])
def test_rest_rejects_values_outside_zero_to_one(
    client: TestClient, session: Session, value: str
) -> None:
    create_settings(session)
    zone = _zone(session)

    response = client.put(
        f"/api/v1/zones/{zone.id}/pi-state/integral",
        json={"value": value},
        headers=_bearer(session, MANAGE),
    )

    assert response.status_code == 422, response.text
    assert _state(session, zone).pi_manual_reason is None
    assert _audit(session) == []


@pytest.mark.parametrize("value", ["-0.01", "1.000001", "NaN", "Infinity"])
def test_mcp_rejects_values_outside_zero_to_one(session: Session, value: str) -> None:
    create_settings(session)
    zone = _zone(session)
    plaintext = _mcp_plaintext(session, MANAGE)

    with pytest.raises(mcp_server.PiInterventionError):
        mcp_server.set_pi_integral(session, plaintext, zone.id, Decimal(value))
    assert _state(session, zone).pi_manual_reason is None
    assert _audit(session) == []


@pytest.mark.parametrize("raw", ["-0", "9" * 5000])
def test_negative_zero_is_zero_and_absurd_numbers_are_refused_cleanly(raw: str) -> None:
    from thermoctl.domain import pi_intervention

    if raw == "-0":
        assert str(pi_intervention.check_value(raw)) == "0.000000"
    else:
        with pytest.raises(pi_intervention.PiInterventionError):
            pi_intervention.check_value(raw)


@pytest.mark.parametrize("value", ["0", "1", "0.5", "0.123456"])
def test_the_boundaries_themselves_are_accepted(
    client_als: Als, session: Session, value: str
) -> None:
    create_settings(session)
    zone = _zone(session)

    response = _ui_set(client_als(MANAGE), zone, value)

    assert response.status_code == 200, response.text
    state = _state(session, zone)
    assert state.pi_manual_integral == Decimal(value)
    assert state.pi_manual_reason == "manuell_gesetzt"


def test_requests_for_a_room_without_pi_or_state_are_refused(
    client_als: Als, session: Session
) -> None:
    create_settings(session)
    off = _zone(session, "hysterese", pi_enabled=False)
    no_state = _pi_zone(session, "ohne-zustand", with_state=False)
    client = client_als(MANAGE)

    for zone in (off, no_state):
        for response in (_ui_reset(client, zone), _ui_set(client, zone, 0.3)):
            assert response.status_code == 409, response.text
            assert response.json()["code"] == "pi_not_active"
    assert _audit(session) == []


# -- Audit und Anzeige der Anforderung -------------------------------------------------------


def test_web_reset_is_recorded_and_not_applied_yet(client_als: Als, session: Session) -> None:
    create_settings(session)
    source(session, "web")
    zone = _zone(session)
    decisions = session.scalar(select(func.count()).select_from(ShadowDecision))

    response = _ui_reset(client_als(MANAGE), zone)

    assert response.status_code == 200, response.text
    pi = response.json()["pi"]
    assert Decimal(pi["integral"]) == STORED  # Wirkung erst im nächsten Zyklus
    assert pi["pending"]["reason"] == "manuell_reset"
    assert Decimal(pi["pending"]["integral"]) == 0
    state = _state(session, zone)
    assert state.pi_integral == STORED
    assert state.pi_manual_reason == "manuell_reset"
    assert state.pi_manual_integral == 0
    [event] = _audit(session)
    assert event.action == "pi.integral_reset"
    assert event.object_type == "zone"
    assert event.object_id == str(zone.id)
    assert event.actor_user_id is not None
    assert "0.5" in (event.detail or "")  # der bisherige Wert steht im Eintrag
    assert session.scalar(select(func.count()).select_from(ShadowDecision)) == decisions


def test_web_set_is_recorded_with_old_and_new_value(
    client_als: Als, session: Session
) -> None:
    create_settings(session)
    source(session, "web")
    zone = _zone(session)

    assert _ui_set(client_als(MANAGE), zone, "0.3").status_code == 200

    [event] = _audit(session)
    assert event.action == "pi.integral_set"
    assert "0.5" in (event.detail or "") and "0.3" in (event.detail or "")
    assert _state(session, zone).pi_integral == STORED


def test_rest_and_mcp_record_their_own_source(client: TestClient, session: Session) -> None:
    create_settings(session)
    zone = _zone(session)
    headers = _bearer(session, MANAGE)

    reset = client.post(f"/api/v1/zones/{zone.id}/pi-state/reset", headers=headers)
    assert reset.status_code == 200, reset.text
    assert reset.json()["pending"]["reason"] == "manuell_reset"
    put = client.put(
        f"/api/v1/zones/{zone.id}/pi-state/integral", json={"value": "0.25"}, headers=headers
    )
    assert put.status_code == 200, put.text
    assert Decimal(put.json()["pending"]["integral"]) == Decimal("0.25")

    plaintext = _mcp_plaintext(session, MANAGE)
    mcp_reset = mcp_server.reset_pi_integral(session, plaintext, zone.id)
    mcp_set = mcp_server.set_pi_integral(session, plaintext, zone.id, Decimal("0.7"))
    assert mcp_reset["pending"]["reason"] == "manuell_reset"
    assert Decimal(str(mcp_set["pending"]["integral"])) == Decimal("0.7")
    read = mcp_server.read_pi_state(session, plaintext, zone.id)
    assert read["pending"]["reason"] == "manuell_gesetzt"
    assert Decimal(str(read["integral"])) == STORED

    events = _audit(session)
    assert len(events) == 4
    assert len({e.source_id for e in events}) == 2  # api und mcp
    assert {e.actor_token_id is not None for e in events} == {True}


def test_a_later_request_replaces_an_earlier_one(client_als: Als, session: Session) -> None:
    create_settings(session)
    source(session, "web")
    zone = _zone(session)
    client = client_als(MANAGE)

    _ui_set(client, zone, "0.9")
    _ui_reset(client, zone)

    state = _state(session, zone)
    assert state.pi_manual_reason == "manuell_reset"
    assert state.pi_manual_integral == 0
    assert len(_audit(session)) == 2


def test_pending_request_shows_in_the_room(client_als: Als, session: Session) -> None:
    create_settings(session)
    source(session, "web")
    zone = _zone(session)
    client = client_als(MANAGE)
    _ui_set(client, zone, "0.3")

    pi = client.get(f"/ui-api/rooms/{zone.id}").json()["pi"]

    assert pi["pending"]["reason"] == "manuell_gesetzt"
    assert Decimal(pi["pending"]["integral"]) == Decimal("0.3")
    assert Decimal(pi["integral"]) == STORED
    assert client.get(f"/ui-api/rooms/{zone.id}").json()["actions"]["set_pi_integral"] is True
    reader = client_als(READ_ONLY)
    assert reader.get(f"/ui-api/rooms/{zone.id}").json()["actions"]["set_pi_integral"] is False


# -- Wirkung erst im nächsten Zyklus; Schaltprotokoll ---------------------------------------


def test_reset_takes_effect_in_the_next_cycle_and_is_logged(session: Session) -> None:
    zone = _zone(session)
    plaintext = _mcp_plaintext(session, MANAGE)
    mcp_server.reset_pi_integral(session, plaintext, zone.id)
    assert _state(session, zone).pi_integral == STORED  # noch nichts passiert

    row = _row_for(shadow_run.cycle(session, NOW), zone)

    assert row.pi_reset_reason == "manuell_reset"
    assert row.pi_integrator_action == INTEGRATOR_RESET
    assert row.pi_integral_before == STORED  # der Wert, der verworfen wurde
    assert row.pi_integral_after is not None and row.pi_integral_after < Decimal("0.01")
    state = _state(session, zone)
    assert state.pi_integral < Decimal("0.01")
    assert state.pi_manual_reason is None and state.pi_manual_integral is None

    nxt = _row_for(shadow_run.cycle(session, NOW + timedelta(seconds=60)), zone)
    assert nxt.pi_reset_reason != "manuell_reset"  # nur dieser eine Zyklus
    assert nxt.pi_integrator_action != INTEGRATOR_RESET


def test_set_takes_effect_in_the_next_cycle_and_is_logged(session: Session) -> None:
    zone = _zone(session)
    plaintext = _mcp_plaintext(session, MANAGE)
    mcp_server.set_pi_integral(session, plaintext, zone.id, Decimal("0.3"))
    assert _state(session, zone).pi_integral == STORED

    row = _row_for(shadow_run.cycle(session, NOW), zone)

    assert row.pi_reset_reason == "manuell_gesetzt"
    assert row.pi_integrator_action == "gesetzt"
    assert row.pi_integral_before == STORED
    assert row.pi_integral_after is not None
    assert Decimal("0.3") <= row.pi_integral_after < Decimal("0.31")
    assert _state(session, zone).pi_manual_reason is None
    # Der Regler rechnet ab jetzt mit dem gesetzten Wert, nicht mit dem alten.
    nxt = _row_for(shadow_run.cycle(session, NOW + timedelta(seconds=60)), zone)
    assert nxt.pi_integral_before is not None
    assert nxt.pi_integral_before < Decimal("0.4")


def test_a_manual_value_is_not_dropped_by_the_24h_rule_in_its_own_cycle(
    session: Session,
) -> None:
    """Der Wert wird im Zyklus seiner Übernahme nicht sofort von der 24-Stunden-Regel
    verworfen (sonst wäre „Setzen“ in einer lange kalten Anlage wirkungslos)."""
    zone = _zone(session, measured_c=Decimal("21.5"))  # warm: keine Heizanforderung
    state = _state(session, zone)
    state.pi_integral = Decimal("0")
    session.flush()
    plaintext = _mcp_plaintext(session, MANAGE)
    mcp_server.set_pi_integral(session, plaintext, zone.id, Decimal("0.4"))

    row = _row_for(shadow_run.cycle(session, NOW + timedelta(days=3)), zone)

    assert row.pi_reset_reason == "manuell_gesetzt"
    assert _state(session, zone).pi_integral > Decimal("0.3")


def test_safe_start_stays_unchanged_with_a_pending_request(session: Session) -> None:
    zone = _zone(session, "erstlauf", already_running=False)
    plaintext = _mcp_plaintext(session, MANAGE)
    mcp_server.set_pi_integral(session, plaintext, zone.id, Decimal("0.4"))

    row = _row_for(shadow_run.cycle(session, NOW), zone)

    # Der sichere Anlauf läuft wie ohne Eingriff: Hysterese entscheidet, PI wartet auf die
    # nächste Fenstergrenze.
    assert row.controller_fallback_reason == RESET_REASON_INVALID_STATE
    assert row.effective_controller == "hysteresis"
    assert row.would_heat is True
    state = _state(session, zone)
    assert state.pi_awaiting_boundary_until is not None
    # Der Wert ist übernommen (gehalten), das Protokoll nennt den Eingriff.
    assert state.pi_integral == Decimal("0.4")
    assert row.pi_integral_before == STORED
    assert row.pi_integral_after == Decimal("0.4")
    assert row.pi_reset_reason == "manuell_gesetzt"
    assert state.pi_manual_reason is None


def test_gates_stay_unchanged_with_a_pending_request(session: Session) -> None:
    zone = _zone(session, "fenster", measured_c=COLD_C)
    # Das Fenstertor gilt nur mit selbstregelndem Ventil (reine EIN/AUS-Räume sind ausgenommen).
    _assign_actuator(
        session, zone, thermostat_mode="self_regulating", capability_code="thermostat",
        suffix="-heizkörper",
    )
    state = _state(session, zone)
    state.window_open = True
    session.flush()
    plaintext = _mcp_plaintext(session, MANAGE)
    mcp_server.reset_pi_integral(session, plaintext, zone.id)

    row = _row_for(shadow_run.cycle(session, NOW), zone)

    assert row.effective_controller == "hysteresis"
    assert row.would_heat is False  # Fenster offen: das Tor entscheidet wie ohne Eingriff
    assert row.pi_reset_reason == "manuell_reset"
    assert row.controller_fallback_reason in (None, RESET_REASON_WINDOW_OPEN)
    state = _state(session, zone)
    assert state.pi_integral == 0
    assert state.pi_last_switch_at is None  # Modulator wie bei jedem Tor neutral


def test_a_request_for_a_zone_that_lost_pi_is_dropped_without_effect(
    session: Session,
) -> None:
    zone = _zone(session)
    plaintext = _mcp_plaintext(session, MANAGE)
    mcp_server.set_pi_integral(session, plaintext, zone.id, Decimal("0.8"))
    zone.pi_enabled = False
    session.flush()

    row = _row_for(shadow_run.cycle(session, NOW), zone)

    assert row.pi_reset_reason is None
    state = _state(session, zone)
    assert state.pi_integral == 0
    assert state.pi_manual_reason is None and state.pi_manual_integral is None


def test_a_request_replaced_while_the_cycle_runs_is_not_lost(session: Session) -> None:
    """Der Zyklus hat Anforderung A gelesen, ein Verwalter ersetzt sie durch B (anderer Prozess,
    schon committet). Der Zyklus darf B nicht löschen und nicht mit A rechnen: B bleibt für den
    nächsten Zyklus vorgemerkt."""
    zone = _zone(session)
    plaintext = _mcp_plaintext(session, MANAGE)
    mcp_server.set_pi_integral(session, plaintext, zone.id, Decimal("0.3"))
    state = session.get(ZoneState, zone.id)
    assert state is not None and state.pi_manual_reason == "manuell_gesetzt"  # A im Speicher
    session.execute(  # B, an der geladenen Zeile vorbei -- wie ein zweiter Prozess
        update(ZoneState)
        .where(ZoneState.zone_id == zone.id)
        .values(pi_manual_reason="manuell_reset", pi_manual_integral=Decimal(0))
        .execution_options(synchronize_session=False)
    )

    row = _row_for(shadow_run.cycle(session, NOW), zone)

    assert row.pi_reset_reason != "manuell_gesetzt"  # A wurde nicht übernommen
    session.expire_all()
    state = session.get(ZoneState, zone.id)
    assert state is not None
    assert state.pi_manual_reason == "manuell_reset" and state.pi_manual_integral == 0
    row = _row_for(shadow_run.cycle(session, NOW + timedelta(seconds=60)), zone)
    assert row.pi_reset_reason == "manuell_reset"  # B wirkt im Zyklus danach


@pytest.mark.parametrize("how", ["pi_off", "ineligible"])
def test_a_dropped_request_is_audited_and_logged(session: Session, how: str) -> None:
    source(session, "system")
    zone = _zone(session)
    plaintext = _mcp_plaintext(session, MANAGE)
    mcp_server.set_pi_integral(session, plaintext, zone.id, Decimal("0.8"))
    if how == "pi_off":
        zone.pi_enabled = False
    else:
        zone.pi_enabled = True
        session.query(ZoneDevice).filter_by(zone_id=zone.id).delete()  # kein EIN/AUS-Aktor mehr
    session.flush()

    shadow_run.cycle(session, NOW)

    dropped = [e for e in session.scalars(select(AuditEvent)) if e.action == "pi.integral_dropped"]
    assert len(dropped) == 1
    assert dropped[0].object_id == str(zone.id)
    assert "manuell_gesetzt" in (dropped[0].detail or "") and "0.8" in (dropped[0].detail or "")
    assert _state(session, zone).pi_manual_reason is None


def test_the_cycle_alone_never_invents_a_manual_reason(session: Session) -> None:
    zone = _zone(session)

    row = _row_for(shadow_run.cycle(session, NOW), zone)

    assert row.pi_reset_reason not in ("manuell_reset", "manuell_gesetzt")


def test_the_registered_mcp_tools_reach_the_domain(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from contextlib import nullcontext

    create_settings(session)
    zone = _zone(session)
    plaintext = _mcp_plaintext(session, MANAGE)
    tools: dict[str, Any] = {}

    class RecordingServer:
        def tool(self, name: str | None = None) -> Callable[[Any], Any]:
            def register(function: Any) -> Any:
                assert name is not None
                tools[name] = function
                return function

            return register

        def run(self, transport: str = "stdio", **kwargs: object) -> None:
            raise AssertionError("Der Test startet keinen Transport")

    monkeypatch.setattr(mcp_server, "session_scope", lambda _factory: nullcontext(session))
    mcp_server._register_tools(RecordingServer(), object(), plaintext)  # type: ignore[arg-type]

    assert tools["read_pi_state"](zone.id)["pending"] is None
    chosen = tools["set_pi_integral"](zone.id, Decimal("0.2"))
    assert chosen["pending"]["reason"] == "manuell_gesetzt"
    assert tools["reset_pi_integral"](zone.id)["pending"]["reason"] == "manuell_reset"
    assert tools["read_pi_state"](zone.id)["pending"]["reason"] == "manuell_reset"
