"""Regelung, Betrieb und Einstellungen unter `/ui-api`: Rechte, Fehlerform, Wirkung, Protokoll.

Die Endpunkte sind dünne Adapter über denselben Domänenfunktionen wie die frühere HTML-Oberfläche;
hier wird geprüft, dass (a) dieselben Rechte gelten, (b) die Domäne die Eingabe prüft und ihr
Fehler als `{detail, code, field}` ankommt, (c) die Datenbank und das Protokoll danach
stimmen und (d) die drei Sperren getrennt bleiben und Geheimnisse nie ausgegeben werden.
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
    capability,
    create_device,
    create_settings,
    create_shadow_decision,
    create_zone,
    create_zone_state,
    role,
    source,
)
from thermoctl.auth.csrf import CSRF_HEADER, csrf_token
from thermoctl.auth.sessions import COOKIE_NAME
from thermoctl.config import Settings, get_settings
from thermoctl.db.base import utcnow
from thermoctl.db.models.device import DeviceCapabilityLink, ZoneDevice
from thermoctl.db.models.operations import AuditEvent, ClusterClaim, Setting
from thermoctl.db.models.zone import Zone
from thermoctl.integrations import notification
from thermoctl.services import cluster
from thermoctl.web import ui_api_operations

Als = Callable[[list[tuple[str, int | None]]], TestClient]

LESEN: list[tuple[str, int | None]] = [("zone.read", None)]
ALLES: list[tuple[str, int | None]] = [
    ("zone.read", None),
    ("zone.manage", None),
    ("device.manage", None),
    ("setting.manage", None),
    ("control.arm", None),
]


def _csrf(client: TestClient) -> dict[str, str]:
    http_session = client.cookies.get(COOKIE_NAME)
    assert http_session is not None
    return {CSRF_HEADER: csrf_token(http_session, get_settings().secret_key.get_secret_value())}


def _call(client: TestClient, method: str, url: str, body: dict[str, Any] | None = None) -> Any:
    return client.request(method, url, json=body, headers=_csrf(client))


def _fehler(response: Any, status: int, code: str, field: str | None = None) -> dict[str, Any]:
    assert response.status_code == status, response.text
    body = response.json()
    assert body["code"] == code
    assert isinstance(body["detail"], str) and body["detail"]
    if field is not None:
        assert body["field"] == field
    return body  # type: ignore[no-any-return]


def _env(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "_env_file": None,
        "database_url": "sqlite://",
        "secret_key": "s" * 32,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _anlage(session: Session) -> tuple[Setting, Zone]:
    source(session, "web")
    row = create_settings(session)
    zone = create_zone(session, "bad")
    return row, zone


def _ereignisse(session: Session, object_type: str) -> list[AuditEvent]:
    return list(session.scalars(select(AuditEvent).where(AuditEvent.object_type == object_type)))


def _thermostat(session: Session, zone: Zone, name: str, offset: str | None) -> int:
    device = create_device(session, name)
    session.add(
        DeviceCapabilityLink(
            device_id=device.id, capability_id=capability(session, "thermostat").id
        )
    )
    row = ZoneDevice(
        zone_id=zone.id,
        device_id=device.id,
        device_role_id=role(session, "actuator").id,
        thermostat_mode="self_regulating",
        temperature_backup_offset_k=Decimal(offset) if offset is not None else None,
    )
    session.add(row)
    session.flush()
    return row.id


def _schalter(session: Session, zone: Zone) -> None:
    """Ein gewöhnlicher Schaltaktor: macht die Zone für PI zulässig."""
    device = create_device(session, "schalter")
    session.add(
        DeviceCapabilityLink(device_id=device.id, capability_id=capability(session, "switch").id)
    )
    session.add(
        ZoneDevice(
            zone_id=zone.id,
            device_id=device.id,
            device_role_id=role(session, "actuator").id,
            thermostat_mode="controlled",
        )
    )
    session.flush()


# -- Raumtechnik: Lesen -------------------------------------------------------------------


def test_parameters_without_session_are_401(client: TestClient, session: Session) -> None:
    _, zone = _anlage(session)
    assert client.get(f"/ui-api/rooms/{zone.id}/parameters").status_code == 401
    assert client.put(f"/ui-api/rooms/{zone.id}/parameters", json={}).status_code == 401


def test_parameters_show_own_and_effective_values_separately(
    client_als: Als, session: Session
) -> None:
    row, zone = _anlage(session)
    zone.hysteresis_k = Decimal("0.70")
    zone.temperature_offset_k = None
    session.flush()
    body = client_als(ALLES).get(f"/ui-api/rooms/{zone.id}/parameters").json()
    hysteresis = body["control"]["hysteresis_k"]
    assert hysteresis["own"] == "0.70"
    assert hysteresis["effective"] == "0.70"
    assert hysteresis["inherited"] is False
    assert hysteresis["source"] == "zone"
    assert hysteresis["limit"]["minimum"] == "0.1"
    inherited = body["control"]["min_on_seconds"]
    assert inherited["own"] is None
    assert inherited["inherited"] is True
    assert inherited["effective"] == str(row.default_min_on_seconds)
    assert inherited["source"] == "plant"
    offset = body["control"]["temperature_offset_k"]
    assert offset["source"] == "default" and offset["effective"] == "0.00"
    assert body["valve_protection"] == {
        "enabled": False,
        "interval_days": 30,
        "duration_minutes": 10,
    }
    assert body["pi"]["enabled"] is False
    assert body["pi"]["eligible"] is False
    assert "Schaltaktor" in body["pi"]["eligibility_reason"]
    assert body["pi"]["limits"]["pi_gain_per_k"]["maximum"] == "0.50"
    assert body["sensor_failure"]["profile_source"] == "Vorgabe"
    assert body["sensor_failure"]["effective_profile_name"] == "Notbetrieb Vorgabe"
    assert body["sensor_failure"]["profiles"][0]["name"] == "Notbetrieb Vorgabe"
    assert body["window_temp_drop_detection_enabled"] is False
    assert body["assumed_relay_lifetime_operations"] == row.assumed_relay_lifetime_operations


def test_parameters_carry_the_pi_switching_table_and_the_ratio_from_the_server(
    client_als: Als, session: Session
) -> None:
    """Die Warnung an der PI-Regelung zeigt Zahlen des Servers; der Browser rechnet nicht."""
    row, zone = _anlage(session)
    body = client_als(ALLES).get(f"/ui-api/rooms/{zone.id}/parameters").json()
    model = body["pi_switching_model"]
    assert [(r["id"], r["per_hour"], r["per_day"], r["per_year"]) for r in model["rows"]] == [
        ("pi_worst_case", 30, 720, 262_800),
        ("pi_30_percent_duty", 18, 432, 157_680),
        ("hysteresis_max", 6, 144, 52_560),
    ]
    assert model["worst_case_per_year"] == 262_800
    assert model["assumed_lifetime_operations"] == row.assumed_relay_lifetime_operations == 500_000
    assert model["worst_case_ratio"] == "0.53"
    # Die Annahme ist eine Einstellung: ändert sie sich, ändert sich das Verhältnis mit.
    row.assumed_relay_lifetime_operations = 100_000
    session.flush()
    body = client_als(ALLES).get(f"/ui-api/rooms/{zone.id}/parameters").json()
    changed = body["pi_switching_model"]
    assert changed["worst_case_ratio"] == "2.63"
    assert changed["assumed_lifetime_operations"] == 100_000
    assert changed["worst_case_per_year"] == 262_800


def test_parameters_pi_eligibility_follows_the_actuators(
    client_als: Als, session: Session
) -> None:
    _, zone = _anlage(session)
    _schalter(session, zone)
    body = client_als(ALLES).get(f"/ui-api/rooms/{zone.id}/parameters").json()
    assert body["pi"]["eligible"] is True


def test_backup_offsets_need_device_manage(client_als: Als, session: Session) -> None:
    _, zone = _anlage(session)
    assignment = _thermostat(session, zone, "thermostat", "-1.50")
    ohne = [(code, None) for code in ("zone.read", "zone.manage")]
    page = client_als(ohne).get(f"/ui-api/rooms/{zone.id}/parameters").json()
    assert page["backup_offsets"] is None
    body = client_als(ALLES).get(f"/ui-api/rooms/{zone.id}/parameters").json()
    assert body["backup_offsets"] == [
        {"assignment_id": assignment, "device_name": "thermostat", "offset_k": "-1.50"}
    ]


def test_parameters_hidden_zone_is_the_same_404_as_unknown(
    client_als: Als, session: Session
) -> None:
    _, zone = _anlage(session)
    other = create_zone(session, "fremd")
    client = client_als([("zone.read", other.id), ("zone.manage", other.id)])
    hidden = client.get(f"/ui-api/rooms/{zone.id}/parameters")
    unknown = client.get("/ui-api/rooms/999999/parameters")
    assert hidden.status_code == unknown.status_code == 404
    assert hidden.json() == unknown.json()
    assert hidden.json()["code"] == "not_found"
    _fehler(_call(client, "PUT", f"/ui-api/rooms/{zone.id}/parameters", {}), 404, "not_found")


def test_parameters_without_manage_are_403(client_als: Als, session: Session) -> None:
    _, zone = _anlage(session)
    client = client_als(LESEN)
    _fehler(client.get(f"/ui-api/rooms/{zone.id}/parameters"), 403, "forbidden")
    _fehler(
        _call(client, "PUT", f"/ui-api/rooms/{zone.id}/parameters", {"hysteresis_k": "0.5"}),
        403,
        "forbidden",
    )
    session.refresh(zone)
    assert zone.hysteresis_k is None
    assert _ereignisse(session, "zone_settings") == []


def test_parameters_tenant_profile_does_not_block_the_rights(
    tenant_client: Als, session: Session
) -> None:
    _, zone = _anlage(session)
    client = tenant_client(ALLES)
    assert client.get(f"/ui-api/rooms/{zone.id}/parameters").status_code == 200
    response = _call(client, "PUT", f"/ui-api/rooms/{zone.id}/parameters", {"hysteresis_k": "0.5"})
    assert response.status_code == 200, response.text
    session.refresh(zone)
    assert zone.hysteresis_k is not None


def test_parameters_tenant_profile_without_rights_is_forbidden(
    tenant_client: Als, session: Session
) -> None:
    _, zone = _anlage(session)
    client = tenant_client([("zone.read", None)])
    _fehler(client.get(f"/ui-api/rooms/{zone.id}/parameters"), 403, "forbidden")
    _fehler(
        _call(client, "PUT", f"/ui-api/rooms/{zone.id}/parameters", {"hysteresis_k": "0.5"}),
        403,
        "forbidden",
    )
    session.refresh(zone)
    assert zone.hysteresis_k is None


def test_parameters_put_without_csrf_is_403(client_als: Als, session: Session) -> None:
    _, zone = _anlage(session)
    response = client_als(ALLES).put(
        f"/ui-api/rooms/{zone.id}/parameters",
        json={"hysteresis_k": "0.5"},
        headers={CSRF_HEADER: "falsch"},
    )
    assert response.status_code == 403
    assert response.json()["stale"] is True
    session.refresh(zone)
    assert zone.hysteresis_k is None


# -- Raumtechnik: Schreiben ---------------------------------------------------------------


def test_save_parameters_writes_the_zone_and_the_audit_log(
    client_als: Als, session: Session
) -> None:
    _, zone = _anlage(session)
    client = client_als(ALLES)
    response = _call(
        client,
        "PUT",
        f"/ui-api/rooms/{zone.id}/parameters",
        {
            "hysteresis_k": "0,6",
            "min_on_seconds": 420,
            "solar_gain_factor": "0.5",
            "valve_protection_enabled": True,
            "valve_protection_interval_days": 14,
            "valve_protection_duration_minutes": 5,
            "window_temp_drop_detection_enabled": True,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert Decimal(body["control"]["hysteresis_k"]["own"]) == Decimal("0.6")
    assert body["control"]["min_on_seconds"]["own"] == "420"
    session.refresh(zone)
    assert zone.hysteresis_k == Decimal("0.60")
    assert zone.min_on_seconds == 420
    assert zone.min_off_seconds is None
    assert zone.solar_gain_factor == Decimal("0.50")
    assert zone.valve_protection_enabled is True
    assert (zone.valve_protection_interval_days, zone.valve_protection_duration_minutes) == (14, 5)
    assert zone.window_temp_drop_detection_enabled is True
    events = _ereignisse(session, "zone_settings")
    assert len(events) == 2
    assert {event.action for event in events} == {"update"}
    assert all(event.object_id == str(zone.id) for event in events)


def test_null_returns_to_inheritance_and_absent_fields_stay_untouched(
    client_als: Als, session: Session
) -> None:
    _, zone = _anlage(session)
    zone.hysteresis_k = Decimal("0.80")
    zone.min_on_seconds = 600
    zone.valve_protection_enabled = True
    zone.pi_gain_per_k = Decimal("0.30")
    session.flush()
    client = client_als(ALLES)
    body = _call(
        client, "PUT", f"/ui-api/rooms/{zone.id}/parameters", {"hysteresis_k": None}
    ).json()
    assert body["control"]["hysteresis_k"]["inherited"] is True
    session.refresh(zone)
    assert zone.hysteresis_k is None
    assert zone.min_on_seconds == 600
    assert zone.valve_protection_enabled is True
    assert zone.pi_gain_per_k == Decimal("0.30")


def test_null_for_non_inheriting_fields_means_unchanged(
    client_als: Als, session: Session
) -> None:
    _, zone = _anlage(session)
    zone.valve_protection_interval_days = 21
    zone.pi_gain_per_k = Decimal("0.30")
    session.flush()
    response = _call(
        client_als(ALLES),
        "PUT",
        f"/ui-api/rooms/{zone.id}/parameters",
        {"valve_protection_interval_days": None, "pi_gain_per_k": None, "pi_min_on_seconds": None},
    )
    assert response.status_code == 200, response.text
    session.refresh(zone)
    assert zone.valve_protection_interval_days == 21
    assert zone.pi_gain_per_k == Decimal("0.30")


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"hysteresis_k": "viel"}, "hysteresis_k"),
        ({"min_on_seconds": "1.5"}, "min_on_seconds"),
        ({"min_off_seconds": -5}, "min_off_seconds"),
        ({"solar_gain_factor": "2"}, "solar_gain_factor"),
        ({"solar_gain_factor": "wolkig"}, "solar_gain_factor"),
        ({"valve_protection_interval_days": 0}, "valve_protection_interval_days"),
        ({"valve_protection_duration_minutes": 0}, "valve_protection_duration_minutes"),
        (
            {"valve_protection_interval_days": 1, "valve_protection_duration_minutes": 2000},
            "valve_protection_duration_minutes",
        ),
        ({"pi_gain_per_k": "0.9"}, "pi_gain_per_k"),
        ({"pi_integral_time_minutes": 61}, "pi_integral_time_minutes"),
        ({"pi_min_on_seconds": 10}, "pi_min_on_seconds"),
        ({"pi_min_off_seconds": 400}, "pi_min_off_seconds"),
        ({"pi_enabled": True, "pi_confirm": True}, "pi_enabled"),
        ({"pi_enabled": True}, "pi_confirm"),
        ({"sensor_failure_emergency_setpoint_c": "warm"}, "sensor_failure_emergency_setpoint_c"),
        ({"sensor_failure_emergency_setpoint_c": "99"}, "sensor_failure_emergency_setpoint_c"),
        ({"sensor_failure_profile_id": 99999}, "sensor_failure_profile_id"),
    ],
)
def test_rejected_input_names_the_field_and_writes_nothing(
    client_als: Als, session: Session, body: dict[str, Any], field: str
) -> None:
    _, zone = _anlage(session)
    client = client_als(ALLES)
    # Eine gültige Änderung im selben Aufruf darf bei einem Fehler nicht stehen bleiben.
    payload = {"hysteresis_k": "0.9", **body}
    _fehler(_call(client, "PUT", f"/ui-api/rooms/{zone.id}/parameters", payload), 422,
            "validation", field)
    session.refresh(zone)
    assert zone.hysteresis_k is None
    assert zone.sensor_failure_profile_id is None
    assert not zone.pi_enabled
    assert _ereignisse(session, "zone_settings") == []


def test_pi_is_switched_on_with_confirmation_on_an_eligible_zone(
    client_als: Als, session: Session
) -> None:
    _, zone = _anlage(session)
    _schalter(session, zone)
    client = client_als(ALLES)
    url = f"/ui-api/rooms/{zone.id}/parameters"
    _fehler(_call(client, "PUT", url, {"pi_enabled": True}), 422, "validation", "pi_confirm")
    body = _call(client, "PUT", url, {"pi_enabled": True, "pi_confirm": True}).json()
    assert body["pi"]["enabled"] is True
    session.refresh(zone)
    assert zone.pi_enabled is True
    # Später ohne erneute Bestätigung speichern; Abschalten braucht sie nie.
    assert _call(client, "PUT", url, {"hysteresis_k": "0.5"}).status_code == 200
    assert _call(client, "PUT", url, {"pi_enabled": False}).json()["pi"]["enabled"] is False


def test_notbetrieb_failure_rolls_back_the_control_parameters_too(
    client_als: Als, session: Session
) -> None:
    _, zone = _anlage(session)
    client = client_als(ALLES)
    _fehler(
        _call(
            client,
            "PUT",
            f"/ui-api/rooms/{zone.id}/parameters",
            {"hysteresis_k": "0.9", "sensor_failure_profile_id": 99999},
        ),
        422,
        "validation",
        "sensor_failure_profile_id",
    )
    session.refresh(zone)
    assert zone.hysteresis_k is None


def test_new_minimum_times_that_break_the_notbetrieb_cycle_are_422_not_500(
    client_als: Als, session: Session
) -> None:
    _, zone = _anlage(session)
    zone.sensor_failure_enabled = True
    session.flush()
    body = _fehler(
        _call(
            client_als(ALLES),
            "PUT",
            f"/ui-api/rooms/{zone.id}/parameters",
            {"min_on_seconds": 7000},
        ),
        422,
        "validation",
    )
    assert "field" not in body
    session.refresh(zone)
    assert zone.min_on_seconds is None


def test_zone_notbetrieb_is_saved_with_profile_and_setpoint(
    client_als: Als, session: Session
) -> None:
    _, zone = _anlage(session)
    client = client_als(ALLES)
    profile_id = _call(
        client, "PUT", f"/ui-api/rooms/{zone.id}/parameters", {}
    ).json()["sensor_failure"]["profiles"][0]["id"]
    body = _call(
        client,
        "PUT",
        f"/ui-api/rooms/{zone.id}/parameters",
        {
            "sensor_failure_enabled": True,
            "sensor_failure_profile_id": profile_id,
            "sensor_failure_emergency_setpoint_c": "17,5",
        },
    ).json()["sensor_failure"]
    assert body["own_enabled"] is True
    assert body["own_profile_id"] == profile_id
    assert Decimal(body["own_emergency_setpoint_c"]) == Decimal("17.5")
    assert body["profile_source"] == "Zone" and body["setpoint_source"] == "Zone"
    session.refresh(zone)
    assert zone.sensor_failure_enabled
    assert zone.sensor_failure_emergency_setpoint_c == Decimal("17.5")
    # `null` für Profil und Notsollwert heißt erben, wie ein leeres Feld im Formular.
    erben = _call(
        client,
        "PUT",
        f"/ui-api/rooms/{zone.id}/parameters",
        {"sensor_failure_profile_id": None, "sensor_failure_emergency_setpoint_c": None},
    ).json()["sensor_failure"]
    assert erben["own_profile_id"] is None and erben["own_emergency_setpoint_c"] is None


def test_backup_offsets_are_saved_with_device_manage(client_als: Als, session: Session) -> None:
    _, zone = _anlage(session)
    assignment = _thermostat(session, zone, "thermostat", "-1.50")
    client = client_als(ALLES)
    url = f"/ui-api/rooms/{zone.id}/parameters"
    body = _call(
        client, "PUT", url, {"backup_offsets": [{"assignment_id": assignment, "offset_k": "0,75"}]}
    ).json()
    assert body["backup_offsets"][0]["offset_k"] == "0.75"
    row = session.get(ZoneDevice, assignment)
    assert row is not None
    session.refresh(row)
    assert row.temperature_backup_offset_k == Decimal("0.75")
    # Ein fehlender Schlüssel lässt den Wert in Ruhe; `null` löscht ihn.
    _call(client, "PUT", url, {"hysteresis_k": "0.5"})
    session.refresh(row)
    assert row.temperature_backup_offset_k == Decimal("0.75")
    _call(client, "PUT", url, {"backup_offsets": [{"assignment_id": assignment}]})
    session.refresh(row)
    assert row.temperature_backup_offset_k is None


def test_backup_offsets_without_device_manage_are_refused_not_ignored(
    client_als: Als, session: Session
) -> None:
    _, zone = _anlage(session)
    assignment = _thermostat(session, zone, "thermostat", "-1.50")
    client = client_als([("zone.read", None), ("zone.manage", None)])
    _fehler(
        _call(
            client,
            "PUT",
            f"/ui-api/rooms/{zone.id}/parameters",
            {
                "hysteresis_k": "0.9",
                "backup_offsets": [{"assignment_id": assignment, "offset_k": 1}],
            },
        ),
        403,
        "forbidden",
    )
    row = session.get(ZoneDevice, assignment)
    assert row is not None
    session.refresh(row)
    assert row.temperature_backup_offset_k == Decimal("-1.50")
    session.refresh(zone)
    assert zone.hysteresis_k is None
    # Ohne den Teil bleibt zone.manage allein genug.
    assert _call(client, "PUT", f"/ui-api/rooms/{zone.id}/parameters", {"hysteresis_k": "0.9"}
                 ).status_code == 200


def test_backup_offsets_reject_unknown_assignments_and_bad_numbers(
    client_als: Als, session: Session
) -> None:
    _, zone = _anlage(session)
    other = create_zone(session, "anders")
    foreign = _thermostat(session, other, "fremdes-thermostat", None)
    assignment = _thermostat(session, zone, "thermostat", None)
    client = client_als(ALLES)
    url = f"/ui-api/rooms/{zone.id}/parameters"
    _fehler(
        _call(client, "PUT", url, {"backup_offsets": [{"assignment_id": foreign, "offset_k": 1}]}),
        422,
        "validation",
        f"backup_offset_{foreign}",
    )
    _fehler(
        _call(client, "PUT", url,
              {"backup_offsets": [{"assignment_id": assignment, "offset_k": "heiss"}]}),
        422,
        "validation",
        f"backup_offset_{assignment}",
    )
    # Außerhalb des Bereichs lehnt die Domäne ab; der Fehler kommt als 422.
    response = _call(
        client, "PUT", url,
        {"backup_offsets": [{"assignment_id": assignment, "offset_k": "500"}]},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "validation"


# -- Betrieb ------------------------------------------------------------------------------


def test_operation_without_session_is_401(client: TestClient) -> None:
    assert client.get("/ui-api/operation").status_code == 401
    assert client.put("/ui-api/operation/armed", json={"armed": False}).status_code == 401


def test_operation_needs_plant_wide_zone_read(client_als: Als, session: Session) -> None:
    _, zone = _anlage(session)
    _fehler(client_als([("zone.read", zone.id)]).get("/ui-api/operation"), 403, "forbidden")
    _fehler(client_als([("setting.manage", None)]).get("/ui-api/operation"), 403, "forbidden")


def test_operation_tenant_profile_follows_the_rights(tenant_client: Als, session: Session) -> None:
    _anlage(session)
    assert tenant_client(ALLES).get("/ui-api/operation").status_code == 200
    _fehler(tenant_client([]).get("/ui-api/operation"), 403, "forbidden")


def test_operation_without_setup_is_503(client_als: Als) -> None:
    _fehler(client_als(LESEN).get("/ui-api/operation"), 503, "setup_incomplete")


def test_operation_shows_decisions_with_reasons_and_emergency_per_zone(
    client_als: Als, session: Session
) -> None:
    _, zone = _anlage(session)
    state = create_zone_state(session, zone)
    state.temperature_c = Decimal("19.50")
    decision = create_shadow_decision(session, zone)
    decision.would_heat = True
    decision.reason = "Ist 19.5 liegt unter Soll 21.0 abzüglich Hysterese."
    decision.requested_controller = "pi"
    decision.effective_controller = "hysteresis"
    decision.controller_fallback_reason = "fenster_offen"
    decision.scheduled_setpoint_c = Decimal("21.0")
    other = create_zone(session, "keller")
    other_decision = create_shadow_decision(session, other)
    other_decision.requested_controller = "pi"
    other_decision.controller_fallback_reason = "unbekannt-xyz"
    session.flush()
    hidden = create_zone(session, "versteckt")
    client = client_als([("zone.read", zone.id), ("zone.read", other.id)])
    # Zonen-bezogenes zone.read allein genügt für die Seite nicht (wie früher).
    _fehler(client.get("/ui-api/operation"), 403, "forbidden")
    body = client_als(LESEN).get("/ui-api/operation").json()
    assert {room["id"] for room in body["rooms"]} == {zone.id, other.id, hidden.id}
    room = next(r for r in body["rooms"] if r["id"] == zone.id)
    assert room["temperature_c"] == "19.50"
    assert room["decision"]["would_heat"] is True
    assert room["decision"]["reason"].startswith("Ist 19.5")
    assert room["decision"]["requested_controller"] == "pi"
    assert room["decision"]["effective_controller"] == "hysteresis"
    assert room["decision"]["controller_fallback_label"] == "Ein Fenster ist offen."
    assert room["decision"]["planned_setpoint_c"] == "21.0"
    assert room["emergency"]["stage"] == "normal"
    assert room["emergency"]["actuators"] == [] and room["emergency"]["comparisons"] == []
    unknown = next(r for r in body["rooms"] if r["id"] == other.id)
    assert unknown["decision"]["controller_fallback_label"] == "unbekannt-xyz"
    silent = next(r for r in body["rooms"] if r["id"] == hidden.id)
    assert silent["decision"] is None and silent["temperature_c"] is None
    assert body["may_arm"] is False


def test_operation_only_lists_zones_the_principal_may_see(
    client_als: Als, session: Session
) -> None:
    _, zone = _anlage(session)
    create_zone(session, "fremd")
    body = client_als(LESEN).get("/ui-api/operation").json()
    assert len(body["rooms"]) == 2
    # Ein zonenweises Recht reicht nicht; ein anlagenweites mit leerer Anlage zeigt nichts.
    assert zone.id in [room["id"] for room in body["rooms"]]


def test_operation_shows_emergency_actuators_and_comparisons(
    client_als: Als, session: Session
) -> None:
    from thermoctl.domain.zones import latest_decisions_by_zone  # noqa: F401
    from thermoctl.services.emergency_state import (
        ActuatorEmergencyView,
        SourceComparisonView,
        ZoneEmergencyView,
    )

    _, zone = _anlage(session)
    view = ZoneEmergencyView(
        zone_id=zone.id,
        zone_name=zone.display_name,
        stage="rueckkehrpruefung",
        stage_label="Rückkehrprüfung",
        episode_id=1,
        failure_started_at=datetime(2026, 8, 29, 7, 0),
        active_source_device_id=5,
        active_source_device_name="Ersatz",
        source_measured_at=None,
        sensor_timeout_seconds=900,
        recovery_started_at=None,
        recovery_sample_count=1,
        recovery_samples=2,
        emergency_setpoint_c=Decimal("17.0"),
        outdoor=None,
        outdoor_status_text="kein Außenwert",
        actuators=(
            ActuatorEmergencyView(
                device_id=7,
                device_name="Ventil",
                kind="thermostat",
                phase="ein",
                phase_deadline_at=datetime(2026, 8, 29, 8, 30),
                on_seconds=600,
                off_seconds=1200,
                cycle_source="festtakt",
                handover_attempted=True,
                handover_result="ok",
                handover_status_text="übergeben",
                restore_attempted=False,
                restore_result=None,
                restore_status_text="offen",
            ),
        ),
        comparisons=(
            SourceComparisonView(
                device_id=7,
                device_name="Ventil",
                sample_count=12,
                mean_deviation_k=Decimal("0.4"),
                suggested_offset_text="-0,5 K",
            ),
        ),
        banner=None,
    )
    from unittest.mock import patch

    with patch.object(ui_api_operations, "zone_emergency_view", return_value=view):
        room = client_als(LESEN).get("/ui-api/operation").json()["rooms"][0]
    emergency = room["emergency"]
    assert emergency["stage"] == "rueckkehrpruefung"
    assert emergency["active_source_device_name"] == "Ersatz"
    assert emergency["recovery_sample_count"] == 1 and emergency["recovery_samples"] == 2
    assert emergency["failure_started_at"] == "2026-08-29T07:00:00+00:00"
    assert emergency["outdoor_temperature_c"] is None
    assert emergency["actuators"][0]["phase"] == "ein"
    assert emergency["actuators"][0]["phase_deadline_at"] == "2026-08-29T08:30:00+00:00"
    assert emergency["actuators"][0]["handover_status_text"] == "übergeben"
    assert emergency["comparisons"][0]["mean_deviation_k"] == "0.4"
    assert emergency["comparisons"][0]["suggested_offset_text"] == "-0,5 K"


def test_operation_shows_the_outdoor_reading_in_the_emergency_view(
    client_als: Als, session: Session
) -> None:
    from thermoctl.domain.outdoor import OutdoorReading
    from thermoctl.services.emergency_state import ZoneEmergencyView

    _, zone = _anlage(session)
    view = ZoneEmergencyView(
        zone_id=zone.id, zone_name="Bad", stage="normal", stage_label="Normal",
        episode_id=None, failure_started_at=None, active_source_device_id=None,
        active_source_device_name=None, source_measured_at=None, sensor_timeout_seconds=None,
        recovery_started_at=None, recovery_sample_count=0, recovery_samples=None,
        emergency_setpoint_c=None, outdoor=OutdoorReading("ok", Decimal("3.5"), None),
        outdoor_status_text="ok", actuators=(), comparisons=(), banner=None,
    )
    from unittest.mock import patch

    with patch.object(ui_api_operations, "zone_emergency_view", return_value=view):
        room = client_als(LESEN).get("/ui-api/operation").json()["rooms"][0]
    assert room["emergency"]["outdoor_temperature_c"] == "3.5"


def test_the_three_latches_are_reported_separately(
    client_als: Als, session: Session, client: TestClient
) -> None:
    row, _ = _anlage(session)
    http = client_als(LESEN)

    def actuation() -> dict[str, Any]:
        return http.get("/ui-api/operation").json()["actuation"]  # type: ignore[no-any-return]

    base = actuation()
    latches = (base["armed"], base["sending_allowed"], base["cluster_standby"])
    assert latches == (False, False, False)
    assert (base["live"], base["dry_run"], base["restart_required"]) == (False, True, False)
    assert base["state"] == "dry_run"
    row.control_armed = True
    restart = actuation()
    assert (restart["armed"], restart["sending_allowed"]) == (True, False)
    assert (restart["live"], restart["dry_run"], restart["restart_required"]) == (False, True, True)
    assert restart["state"] == "armed_restart_missing"
    http.app.state.sending_allowed = True  # type: ignore[attr-defined]
    try:
        live = actuation()
        row.control_armed = False
        only_latch = actuation()
    finally:
        http.app.state.sending_allowed = False  # type: ignore[attr-defined]
    assert (live["live"], live["dry_run"], live["restart_required"]) == (True, False, False)
    # Offener Startriegel allein ist kein "scharf": die gespeicherte Freigabe fehlt.
    assert (only_latch["armed"], only_latch["sending_allowed"]) == (False, True)
    assert (only_latch["live"], only_latch["dry_run"]) == (False, True)
    assert only_latch["state"] == "dry_run"


def test_the_cluster_role_is_a_third_separate_latch(client_als: Als, session: Session) -> None:
    row, _ = _anlage(session)
    http = client_als(LESEN)
    body = http.get("/ui-api/operation").json()
    assert body["cluster"] == {
        "instance_id": cluster.instance_id(),
        "standby": False,
        "takeover_timeout_seconds": cluster.takeover_timeout_seconds(row),
    }
    session.add(
        ClusterClaim(id=1, holder_id="andere-instanz", expires_at=utcnow() + timedelta(days=1))
    )
    session.flush()
    row.control_armed = True
    body = http.get("/ui-api/operation").json()
    assert body["cluster"]["standby"] is True
    assert body["actuation"]["cluster_standby"] is True
    assert body["actuation"]["armed"] is True


def _foreign_claim(session: Session) -> None:
    session.add(
        ClusterClaim(id=1, holder_id="andere-instanz", expires_at=utcnow() + timedelta(days=1))
    )
    session.flush()


def test_cluster_state_is_leading_when_this_instance_holds_the_claim(
    client_als: Als, session: Session
) -> None:
    _anlage(session)
    http = client_als(LESEN)
    http.app.state.process_started_at = utcnow()  # type: ignore[attr-defined]
    for url in ("/ui-api/operation", "/ui-api/rooms"):
        body = http.get(url).json()["actuation"]
        assert body["cluster_standby"] is False
        assert body["cluster_state"] == "leading"
        assert body["determining_until"] is None


def test_cluster_state_is_determining_shortly_after_start_and_standby_later(
    client_als: Als, session: Session
) -> None:
    row, _ = _anlage(session)
    _foreign_claim(session)
    timeout = cluster.takeover_timeout_seconds(row)
    http = client_als(LESEN)
    started = utcnow()
    http.app.state.process_started_at = started  # type: ignore[attr-defined]
    for url in ("/ui-api/operation", "/ui-api/rooms"):
        body = http.get(url).json()["actuation"]
        assert body["cluster_standby"] is True
        assert body["cluster_state"] == "determining"
        until = datetime.fromisoformat(body["determining_until"])
        assert until.replace(tzinfo=None) == started + timedelta(seconds=timeout)
    # Knapp innerhalb der Wartezeit: noch Erkennung; knapp darauf: echter Standby.
    http.app.state.process_started_at = utcnow() - timedelta(seconds=timeout - 5)  # type: ignore[attr-defined]
    assert http.get("/ui-api/operation").json()["actuation"]["cluster_state"] == "determining"
    http.app.state.process_started_at = utcnow() - timedelta(seconds=timeout + 1)  # type: ignore[attr-defined]
    for url in ("/ui-api/operation", "/ui-api/rooms"):
        body = http.get(url).json()["actuation"]
        assert body["cluster_standby"] is True
        assert body["cluster_state"] == "standby"
        assert body["determining_until"] is None


def test_without_a_process_start_value_a_non_leader_is_plain_standby(
    client_als: Als, session: Session
) -> None:
    _anlage(session)
    _foreign_claim(session)
    http = client_als(LESEN)
    if hasattr(http.app.state, "process_started_at"):
        del http.app.state.process_started_at
    body = http.get("/ui-api/operation").json()["actuation"]
    assert (body["cluster_standby"], body["cluster_state"]) == (True, "standby")
    assert body["determining_until"] is None


def test_create_app_records_the_process_start_in_utc(client: TestClient) -> None:
    started = client.app.state.process_started_at  # type: ignore[attr-defined]
    assert started.tzinfo is None  # naive UTC, like every database timestamp
    assert timedelta(0) <= utcnow() - started < timedelta(minutes=5)


# -- Freigabe -----------------------------------------------------------------------------


def test_arming_needs_control_arm(client_als: Als, session: Session) -> None:
    row, _ = _anlage(session)
    client = client_als([p for p in ALLES if p[0] != "control.arm"])
    _fehler(
        _call(client, "PUT", "/ui-api/operation/armed", {"armed": True, "reason": "geprüft"}),
        403,
        "forbidden",
    )
    session.refresh(row)
    assert row.control_armed is False
    assert _ereignisse(session, "setting") == []


def test_arming_tenant_profile_follows_the_rights(tenant_client: Als, session: Session) -> None:
    row, _ = _anlage(session)
    _fehler(
        _call(tenant_client([("zone.read", None)]), "PUT", "/ui-api/operation/armed",
              {"armed": True, "reason": "x"}),
        403,
        "forbidden",
    )
    session.refresh(row)
    assert row.control_armed is False
    response = _call(
        tenant_client(ALLES), "PUT", "/ui-api/operation/armed", {"armed": True, "reason": "x"}
    )
    assert response.status_code != 403, response.text


def test_arming_without_csrf_is_403(client_als: Als, session: Session) -> None:
    row, _ = _anlage(session)
    response = client_als(ALLES).put(
        "/ui-api/operation/armed",
        json={"armed": True, "reason": "x"},
        headers={CSRF_HEADER: "falsch"},
    )
    assert response.status_code == 403
    session.refresh(row)
    assert row.control_armed is False


def test_arming_without_setup_is_503(client_als: Als) -> None:
    _fehler(
        _call(client_als(ALLES), "PUT", "/ui-api/operation/armed", {"armed": False}),
        503,
        "setup_incomplete",
    )


def test_arming_requires_a_reason(client_als: Als, session: Session) -> None:
    row, _ = _anlage(session)
    client = client_als(ALLES)
    _fehler(
        _call(client, "PUT", "/ui-api/operation/armed", {"armed": True, "reason": "   "}),
        422,
        "validation",
        "reason",
    )
    session.refresh(row)
    assert row.control_armed is False
    assert _ereignisse(session, "setting") == []
    _fehler(_call(client, "PUT", "/ui-api/operation/armed", {"armed": "vielleicht"}), 422,
            "validation")


def test_arming_sets_only_the_stored_release_and_never_opens_the_mqtt_latch(
    client_als: Als, session: Session
) -> None:
    row, _ = _anlage(session)
    client = client_als(ALLES)
    assert getattr(client.app.state, "sending_allowed", False) is False
    response = _call(
        client, "PUT", "/ui-api/operation/armed", {"armed": True, "reason": "Probelauf bestanden"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["changed"] is True
    assert body["actuation"]["armed"] is True
    assert body["actuation"]["sending_allowed"] is False
    assert body["actuation"]["restart_required"] is True
    assert body["actuation"]["state"] == "armed_restart_missing"
    assert getattr(client.app.state, "sending_allowed", False) is False
    session.refresh(row)
    assert row.control_armed is True
    (event,) = _ereignisse(session, "setting")
    assert event.action == "arm" and event.detail == "Probelauf bestanden"
    assert event.actor_user_id is not None


def test_disarming_needs_no_reason_and_leaves_the_mqtt_latch_alone(
    client_als: Als, session: Session
) -> None:
    row, _ = _anlage(session)
    row.control_armed = True
    session.flush()
    client = client_als(ALLES)
    client.app.state.sending_allowed = True  # type: ignore[attr-defined]
    try:
        body = _call(client, "PUT", "/ui-api/operation/armed", {"armed": False}).json()
        still_open = client.app.state.sending_allowed  # type: ignore[attr-defined]
    finally:
        client.app.state.sending_allowed = False  # type: ignore[attr-defined]
    assert body["changed"] is True
    assert body["actuation"]["armed"] is False
    assert body["actuation"]["sending_allowed"] is True
    assert body["actuation"]["state"] == "dry_run"
    assert still_open is True
    session.refresh(row)
    assert row.control_armed is False
    (event,) = _ereignisse(session, "setting")
    assert event.action == "disarm"


def test_arming_twice_reports_no_change_and_logs_nothing_more(
    client_als: Als, session: Session
) -> None:
    _, _ = _anlage(session)
    client = client_als(ALLES)
    response = _call(client, "PUT", "/ui-api/operation/armed", {"armed": False})
    assert response.json()["changed"] is False
    assert _ereignisse(session, "setting") == []


# -- Einstellungen: Lesen -----------------------------------------------------------------


def test_settings_without_session_are_401(client: TestClient) -> None:
    for method, url in [
        ("GET", "/ui-api/settings"),
        ("PUT", "/ui-api/settings/defaults"),
        ("PUT", "/ui-api/settings/solar-location"),
        ("PUT", "/ui-api/settings/window-alarm"),
        ("PUT", "/ui-api/settings/window-temp-drop"),
        ("PUT", "/ui-api/settings/sensor-failure"),
        ("PUT", "/ui-api/settings/outdoor-source"),
        ("PUT", "/ui-api/settings/notifications"),
        ("POST", "/ui-api/settings/notifications/test"),
        ("GET", "/ui-api/interfaces"),
    ]:
        assert client.request(method, url).status_code == 401, (method, url)


def test_settings_show_every_group_with_limits(client_als: Als, session: Session) -> None:
    row, _ = _anlage(session)
    row.timezone = "Europe/Berlin"
    row.solar_forecast_latitude = Decimal("48.5")
    session.flush()
    outdoor = create_device(session, "aussen")
    body = client_als(LESEN).get("/ui-api/settings").json()
    assert body["may_edit"] is False
    hysteresis = next(f for f in body["defaults"] if f["name"] == "default_hysteresis_k")
    assert hysteresis["minimum"] == "0.1" and hysteresis["maximum"] == "5.0"
    assert hysteresis["integer"] is False and hysteresis["label"] == "Hysterese (K)"
    assert next(f for f in body["defaults"] if f["name"] == "shadow_interval_seconds")["integer"]
    assert body["timezone"] == "Europe/Berlin"
    assert body["solar"] == {"enabled": False, "latitude": "48.5", "longitude": None}
    assert [f["name"] for f in body["window_alarm"]] == [
        "window_alarm_open_minutes",
        "window_alarm_outdoor_threshold_c",
    ]
    assert len(body["window_temp_drop"]) == 6
    failure = body["sensor_failure"]
    assert failure["profile_name"] == "Notbetrieb Vorgabe"
    assert (failure["fixed_on_seconds"], failure["fixed_off_seconds"]) == (600, 1200)
    assert failure["curve_points"] == []
    assert body["outdoor"]["source_device_id"] is None
    assert body["outdoor"]["reading"]["status"] == "no_source" or body["outdoor"]["reading"]
    assert {"id": outdoor.id, "name": "aussen"} in body["outdoor"]["devices"]
    assert body["notifications"]["webhook_configured"] is False
    assert body["notifications"]["last_attempt_at"] is None
    assert client_als(ALLES).get("/ui-api/settings").json()["may_edit"] is True


def test_settings_need_plant_wide_zone_read_not_a_profile(
    client_als: Als, tenant_client: Als, session: Session
) -> None:
    _, zone = _anlage(session)
    _fehler(client_als([("zone.read", zone.id)]).get("/ui-api/settings"), 403, "forbidden")
    assert tenant_client(ALLES).get("/ui-api/settings").status_code == 200


def test_settings_without_setup_are_503(client_als: Als) -> None:
    _fehler(client_als(ALLES).get("/ui-api/settings"), 503, "setup_incomplete")


# -- Einstellungen: Schreiben, gemeinsame Randfälle ---------------------------------------


SCHREIBEND: list[tuple[str, str, dict[str, Any]]] = [
    ("PUT", "/ui-api/settings/defaults", {"values": {}, "timezone": "UTC"}),
    ("PUT", "/ui-api/settings/solar-location", {"enabled": True}),
    ("PUT", "/ui-api/settings/window-alarm", {"values": {}}),
    ("PUT", "/ui-api/settings/window-temp-drop", {"values": {}}),
    (
        "PUT",
        "/ui-api/settings/sensor-failure",
        {
            "fixed_on_seconds": 600,
            "fixed_off_seconds": 1200,
            "recovery_seconds": 60,
            "recovery_samples": 2,
            "warm_restart_hysteresis_k": "1",
            "emergency_setpoint_c": "17",
        },
    ),
    ("PUT", "/ui-api/settings/outdoor-source", {"device_id": None}),
    (
        "PUT",
        "/ui-api/settings/notifications",
        {
            "sensor_faults": False,
            "bridge_faults": False,
            "command_failures": False,
            "stuck_sensor": False,
            "window_alarm": False,
            "tenant_reports": False,
        },
    ),
    ("POST", "/ui-api/settings/notifications/test", {}),
]


@pytest.mark.parametrize("index", range(len(SCHREIBEND)))
def test_settings_writes_need_setting_manage_and_change_nothing(
    client_als: Als, session: Session, index: int
) -> None:
    row, _ = _anlage(session)
    method, url, body = SCHREIBEND[index]
    before = (row.timezone, row.solar_forecast_enabled, row.notify_sensor_faults)
    granted = [p for p in ALLES if p[0] != "setting.manage"]
    _fehler(_call(client_als(granted), method, url, body), 403, "forbidden")
    session.refresh(row)
    assert (row.timezone, row.solar_forecast_enabled, row.notify_sensor_faults) == before
    assert _ereignisse(session, "setting") == []


@pytest.mark.parametrize("index", range(len(SCHREIBEND)))
def test_settings_writes_tenant_profile_follows_the_rights(
    tenant_client: Als, session: Session, index: int
) -> None:
    _anlage(session)
    method, url, body = SCHREIBEND[index]
    granted = [p for p in ALLES if p[0] != "setting.manage"]
    _fehler(_call(tenant_client(granted), method, url, body), 403, "forbidden")
    assert _ereignisse(session, "setting") == []
    response = _call(tenant_client(ALLES), method, url, body)
    assert response.status_code != 403, response.text


@pytest.mark.parametrize("index", range(len(SCHREIBEND)))
def test_settings_writes_without_csrf_are_403(
    client_als: Als, session: Session, index: int
) -> None:
    _anlage(session)
    method, url, body = SCHREIBEND[index]
    response = client_als(ALLES).request(method, url, json=body, headers={CSRF_HEADER: "falsch"})
    assert response.status_code == 403
    assert response.json()["stale"] is True
    assert _ereignisse(session, "setting") == []


@pytest.mark.parametrize("index", range(len(SCHREIBEND)))
def test_settings_writes_without_setup_are_503(client_als: Als, index: int) -> None:
    method, url, body = SCHREIBEND[index]
    _fehler(_call(client_als(ALLES), method, url, body), 503, "setup_incomplete")


# -- Einstellungen: Regelvorgaben, Standort -----------------------------------------------


def _alle_vorgaben(client: TestClient) -> dict[str, str]:
    fields = client.get("/ui-api/settings").json()["defaults"]
    return {field["name"]: field["value"] for field in fields}


def test_defaults_are_validated_by_the_domain_and_written_with_audit(
    client_als: Als, session: Session
) -> None:
    row, _ = _anlage(session)
    client = client_als(ALLES)
    values = _alle_vorgaben(client)
    values["default_hysteresis_k"] = "0,5"
    values["default_min_on_seconds"] = "450"
    response = _call(
        client, "PUT", "/ui-api/settings/defaults", {"values": values, "timezone": "Europe/Vienna"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["timezone"] == "Europe/Vienna"
    session.refresh(row)
    assert row.default_hysteresis_k == Decimal("0.5")
    assert row.default_min_on_seconds == 450
    assert row.timezone == "Europe/Vienna"
    (event,) = _ereignisse(session, "setting")
    assert event.summary == "Globale Regelvorgaben geändert"


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"default_hysteresis_k": "0"}, "default_hysteresis_k"),
        ({"default_min_on_seconds": "5"}, "default_min_on_seconds"),
        ({"default_min_off_seconds": "30.5"}, "default_min_off_seconds"),
        ({"shadow_interval_seconds": "abc"}, "shadow_interval_seconds"),
        ({"stuck_reading_hours": ""}, "stuck_reading_hours"),
    ],
)
def test_defaults_out_of_range_are_422_with_the_field_and_write_nothing(
    client_als: Als, session: Session, change: dict[str, str], field: str
) -> None:
    row, _ = _anlage(session)
    client = client_als(ALLES)
    values = {**_alle_vorgaben(client), **change}
    values["default_min_on_seconds"] = values["default_min_on_seconds"]
    before = row.default_hysteresis_k
    _fehler(
        _call(client, "PUT", "/ui-api/settings/defaults",
              {"values": {**values, "default_hysteresis_k": values["default_hysteresis_k"]},
               "timezone": "UTC"}),
        422,
        "validation",
        field,
    )
    session.refresh(row)
    assert row.default_hysteresis_k == before
    assert row.timezone != "UTC" or before == row.default_hysteresis_k
    assert _ereignisse(session, "setting") == []


def test_defaults_reject_a_missing_value_an_unknown_key_and_an_empty_timezone(
    client_als: Als, session: Session
) -> None:
    _anlage(session)
    client = client_als(ALLES)
    values = _alle_vorgaben(client)
    missing = dict(values)
    del missing["stuck_reading_hours"]
    _fehler(
        _call(client, "PUT", "/ui-api/settings/defaults", {"values": missing, "timezone": "UTC"}),
        422, "validation", "stuck_reading_hours",
    )
    _fehler(
        _call(client, "PUT", "/ui-api/settings/defaults",
              {"values": {**values, "frei_erfunden": "1"}, "timezone": "UTC"}),
        422, "validation", "frei_erfunden",
    )
    _fehler(
        _call(client, "PUT", "/ui-api/settings/defaults", {"values": values, "timezone": " "}),
        422, "validation", "timezone",
    )
    assert _ereignisse(session, "setting") == []


def test_defaults_that_would_break_an_active_notbetrieb_profile_are_refused(
    client_als: Als, session: Session
) -> None:
    row, zone = _anlage(session)
    zone.sensor_failure_enabled = True
    session.flush()
    client = client_als(ALLES)
    values = {**_alle_vorgaben(client), "default_min_on_seconds": "7000"}
    response = _call(
        client, "PUT", "/ui-api/settings/defaults", {"values": values, "timezone": "UTC"}
    )
    assert response.status_code == 422 and response.json()["code"] == "validation"
    session.refresh(row)
    assert row.default_min_on_seconds == 300


def test_solar_location_is_saved_and_cleared(client_als: Als, session: Session) -> None:
    row, _ = _anlage(session)
    client = client_als(ALLES)
    body = _call(
        client,
        "PUT",
        "/ui-api/settings/solar-location",
        {"enabled": True, "latitude": "48,2", "longitude": "16.37"},
    ).json()
    assert body["solar"] == {"enabled": True, "latitude": "48.2", "longitude": "16.37"}
    session.refresh(row)
    assert row.solar_forecast_enabled and row.solar_forecast_latitude == Decimal("48.2")
    (event,) = _ereignisse(session, "setting")
    assert event.summary == "Standort und Schalter für die Sonnenprognose geändert"
    body = _call(client, "PUT", "/ui-api/settings/solar-location", {"enabled": False}).json()
    assert body["solar"] == {"enabled": False, "latitude": None, "longitude": None}


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"enabled": True, "latitude": "91", "longitude": "10"}, "solar_forecast_latitude"),
        ({"enabled": True, "latitude": "abc"}, "solar_forecast_latitude"),
        ({"enabled": True, "latitude": "10", "longitude": "-181"}, "solar_forecast_longitude"),
    ],
)
def test_invalid_coordinates_are_rejected_without_flipping_the_switch(
    client_als: Als, session: Session, body: dict[str, Any], field: str
) -> None:
    row, _ = _anlage(session)
    _fehler(_call(client_als(ALLES), "PUT", "/ui-api/settings/solar-location", body), 422,
            "validation", field)
    session.refresh(row)
    assert row.solar_forecast_enabled is False
    assert _ereignisse(session, "setting") == []


# -- Einstellungen: Fenster ---------------------------------------------------------------


def test_window_alarm_and_temp_drop_thresholds_are_saved(
    client_als: Als, session: Session
) -> None:
    row, _ = _anlage(session)
    client = client_als(ALLES)
    body = _call(
        client,
        "PUT",
        "/ui-api/settings/window-alarm",
        {"values": {"window_alarm_open_minutes": 45, "window_alarm_outdoor_threshold_c": "5,5"}},
    ).json()
    assert {f["name"]: f["value"] for f in body["window_alarm"]} == {
        "window_alarm_open_minutes": "45",
        "window_alarm_outdoor_threshold_c": "5.5",
    }
    session.refresh(row)
    assert row.window_alarm_open_minutes == 45
    (event,) = _ereignisse(session, "setting")
    assert event.summary == "Schwellen des Fenster-Alarms geändert"

    current = {f["name"]: f["value"] for f in body["window_temp_drop"]}
    current["window_temp_drop_window_minutes"] = "20"
    body = _call(client, "PUT", "/ui-api/settings/window-temp-drop", {"values": current}).json()
    assert next(f for f in body["window_temp_drop"]
                if f["name"] == "window_temp_drop_window_minutes")["value"] == "20"
    session.refresh(row)
    assert row.window_temp_drop_window_minutes == 20
    assert len(_ereignisse(session, "setting")) == 2


@pytest.mark.parametrize(
    ("url", "values", "field"),
    [
        ("window-alarm",
         {"window_alarm_open_minutes": 1, "window_alarm_outdoor_threshold_c": "5"},
         "window_alarm_open_minutes"),
        ("window-alarm", {"window_alarm_open_minutes": 30}, "window_alarm_outdoor_threshold_c"),
        ("window-alarm", {"window_alarm_open_minutes": 30, "window_alarm_outdoor_threshold_c": "5",
                          "hysteresis": 1}, "hysteresis"),
        ("window-temp-drop", {}, "window_temp_drop_window_minutes"),
        ("window-temp-drop", {"default_hysteresis_k": 1}, "default_hysteresis_k"),
    ],
)
def test_window_thresholds_are_validated_by_the_domain(
    client_als: Als, session: Session, url: str, values: dict[str, Any], field: str
) -> None:
    row, _ = _anlage(session)
    before = row.window_alarm_open_minutes
    _fehler(_call(client_als(ALLES), "PUT", f"/ui-api/settings/{url}", {"values": values}),
            422, "validation", field)
    session.refresh(row)
    assert row.window_alarm_open_minutes == before
    assert _ereignisse(session, "setting") == []


# -- Einstellungen: Notbetrieb ------------------------------------------------------------


SENSORAUSFALL: dict[str, Any] = {
    "fixed_on_seconds": 900,
    "fixed_off_seconds": "1500",
    "recovery_seconds": 120,
    "recovery_samples": 3,
    "warm_restart_hysteresis_k": "1,5",
    "emergency_setpoint_c": "18",
}


def test_sensor_failure_defaults_are_saved_with_curve(client_als: Als, session: Session) -> None:
    row, _ = _anlage(session)
    client = client_als(ALLES)
    response = _call(
        client,
        "PUT",
        "/ui-api/settings/sensor-failure",
        {
            **SENSORAUSFALL,
            "curve_points": [
                {"outdoor_c": "-5", "on_seconds": 900, "off_seconds": 900},
                {"outdoor_c": "5,5", "on_seconds": 0, "off_seconds": 1800},
            ],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()["sensor_failure"]
    assert body["fixed_on_seconds"] == 900 and body["recovery_samples"] == 3
    assert body["warm_restart_hysteresis_k"] == "1.50"
    assert Decimal(body["emergency_setpoint_c"]) == Decimal("18")
    assert [p["outdoor_c"] for p in body["curve_points"]] == ["-5.00", "5.50"]
    session.refresh(row)
    assert row.sensor_failure_default_emergency_setpoint_c == Decimal("18")
    assert row.sensor_failure_default_profile_id == body["profile_id"]
    assert len(_ereignisse(session, "setting")) >= 1


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"fixed_on_seconds": "oft"}, "fixed_on_seconds"),
        ({"recovery_samples": ""}, "recovery_samples"),
        ({"warm_restart_hysteresis_k": "heiss"}, "warm_restart_hysteresis_k"),
        ({"emergency_setpoint_c": None}, "emergency_setpoint_c"),
        ({"emergency_setpoint_c": "40"}, "emergency_setpoint_c"),
        ({"fixed_off_seconds": 0}, "fixed_off_seconds"),
        ({"curve_points": [{"outdoor_c": "kalt", "on_seconds": 1, "off_seconds": 1}]},
         "curve_points[0].outdoor_c"),
    ],
)
def test_sensor_failure_defaults_reject_bad_input_without_writing(
    client_als: Als, session: Session, change: dict[str, Any], field: str
) -> None:
    row, _ = _anlage(session)
    before = row.sensor_failure_default_emergency_setpoint_c
    _fehler(
        _call(client_als(ALLES), "PUT", "/ui-api/settings/sensor-failure",
              {**SENSORAUSFALL, **change}),
        422,
        "validation",
        field,
    )
    session.refresh(row)
    assert row.sensor_failure_default_emergency_setpoint_c == before
    assert _ereignisse(session, "setting") == []


def test_sensor_failure_profile_that_breaks_an_active_zone_is_refused_as_a_unit(
    client_als: Als, session: Session
) -> None:
    row, zone = _anlage(session)
    zone.sensor_failure_enabled = True
    zone.min_on_seconds = 800
    session.flush()
    response = _call(
        client_als(ALLES), "PUT", "/ui-api/settings/sensor-failure",
        {**SENSORAUSFALL, "fixed_on_seconds": 100},
    )
    assert response.status_code == 422 and response.json()["code"] == "validation"
    session.refresh(row)
    assert row.sensor_failure_default_emergency_setpoint_c != Decimal("18")


# -- Einstellungen: Außentemperatur ------------------------------------------------------


def test_outdoor_source_is_set_and_cleared_through_the_domain(
    client_als: Als, session: Session
) -> None:
    row, _ = _anlage(session)
    sensor = create_device(session, "aussenfuehler")
    session.add(
        DeviceCapabilityLink(
            device_id=sensor.id, capability_id=capability(session, "temperature").id
        )
    )
    session.flush()
    client = client_als(ALLES)
    body = _call(client, "PUT", "/ui-api/settings/outdoor-source", {"device_id": sensor.id}).json()
    assert body["outdoor"]["source_device_id"] == sensor.id
    assert body["outdoor"]["source_device_name"] == "aussenfuehler"
    session.refresh(row)
    assert row.outdoor_temperature_source_device_id == sensor.id
    (event,) = _ereignisse(session, "outdoor_temperature_source")
    assert event.action == "assign"
    body = _call(client, "PUT", "/ui-api/settings/outdoor-source", {"device_id": None}).json()
    assert body["outdoor"]["source_device_id"] is None
    session.refresh(row)
    assert row.outdoor_temperature_source_device_id is None
    assert [e.action for e in _ereignisse(session, "outdoor_temperature_source")] == [
        "assign",
        "unassign",
    ]


def test_outdoor_source_rejects_unknown_devices_and_devices_without_temperature(
    client_als: Als, session: Session
) -> None:
    row, _ = _anlage(session)
    plain = create_device(session, "steckdose")
    session.add(
        DeviceCapabilityLink(device_id=plain.id, capability_id=capability(session, "switch").id)
    )
    session.flush()
    client = client_als(ALLES)
    _fehler(_call(client, "PUT", "/ui-api/settings/outdoor-source", {"device_id": 99999}), 422,
            "validation", "device_id")
    _fehler(_call(client, "PUT", "/ui-api/settings/outdoor-source", {"device_id": plain.id}), 422,
            "validation", "device_id")
    session.refresh(row)
    assert row.outdoor_temperature_source_device_id is None
    assert _ereignisse(session, "outdoor_temperature_source") == []


# -- Benachrichtigungen -------------------------------------------------------------------


def test_notification_channels_are_saved_with_audit(client_als: Als, session: Session) -> None:
    row, _ = _anlage(session)
    client = client_als(ALLES)
    channels = {
        "sensor_faults": True,
        "bridge_faults": False,
        "command_failures": True,
        "stuck_sensor": False,
        "window_alarm": True,
        "tenant_reports": False,
    }
    body = _call(client, "PUT", "/ui-api/settings/notifications", channels).json()
    for name, value in channels.items():
        assert body["notifications"][name] is value
    session.refresh(row)
    assert (row.notify_sensor_faults, row.notify_bridge_faults, row.notify_command_failures) == (
        True, False, True
    )
    assert (row.notify_stuck_sensor, row.notify_window_alarm, row.notify_tenant_reports) == (
        False, True, False
    )
    (event,) = _ereignisse(session, "setting")
    assert event.summary == "Einstellungen für Störungsmeldungen geändert"
    # Alle sechs sind Pflicht: ein Teil-Aufruf schaltet nie still eine Meldungsart ab.
    _fehler(_call(client, "PUT", "/ui-api/settings/notifications", {"sensor_faults": False}),
            422, "validation")


def test_test_notification_without_webhook_sends_nothing(
    client_als: Als, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    row, _ = _anlage(session)
    monkeypatch.setattr(ui_api_operations, "get_settings", lambda: _env())
    _fehler(_call(client_als(ALLES), "POST", "/ui-api/settings/notifications/test"), 409,
            "no_webhook")
    session.refresh(row)
    assert row.notify_last_attempt_at is None


def test_test_notification_reports_success_and_failure_and_honours_the_cooldown(
    client_als: Als, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    row, _ = _anlage(session)
    monkeypatch.setattr(
        ui_api_operations,
        "get_settings",
        lambda: _env(notify_webhook="https://hook.example.invalid/T0K3N/pfad?key=abc",
                     notify_webhook_token="geheimes-token"),
    )
    results = [
        notification.WebhookTestResult(ok=True, status_code=204, duration_seconds=0.2, error=None),
        notification.WebhookTestResult(
            ok=False, status_code=500, duration_seconds=0.1, error="HTTP 500"
        ),
    ]
    calls: list[object] = []

    async def fake_send_test(env: Settings) -> notification.WebhookTestResult:
        calls.append(env)
        return results[len(calls) - 1]

    monkeypatch.setattr(notification, "send_test", fake_send_test)
    client = client_als(ALLES)
    first = _call(client, "POST", "/ui-api/settings/notifications/test")
    assert first.status_code == 200, first.text
    assert first.json() == {"ok": True, "status_code": 204, "duration_seconds": 0.2, "error": None}
    assert row.notify_last_ok is True and row.notify_last_error is None
    assert row.notify_last_attempt_at is not None
    # Innerhalb der Sperrzeit: kein zweiter Versand.
    _fehler(_call(client, "POST", "/ui-api/settings/notifications/test"), 429, "cooldown")
    assert len(calls) == 1
    # Nach Ablauf: der fehlgeschlagene Versand ist eine Antwort, kein Serverfehler.
    row.notify_last_attempt_at = utcnow() - timedelta(minutes=1)
    session.flush()
    second = _call(client, "POST", "/ui-api/settings/notifications/test")
    assert second.status_code == 200
    assert second.json()["ok"] is False and second.json()["error"] == "HTTP 500"
    assert row.notify_last_ok is False and row.notify_last_error == "HTTP 500"
    events = session.scalars(
        select(AuditEvent).where(AuditEvent.action.like("notification.test_%")).order_by(AuditEvent.id)
    ).all()
    assert [e.action for e in events] == [
        "notification.test_sent", "notification.test_failed",
    ]
    assert all((e.object_type, e.object_id) == ("setting", "1") for e in events)
    assert all(e.actor_user_id is not None for e in events)
    assert events[1].detail == "HTTP 500"
    for e in events:
        text = f"{e.summary} {e.detail}"
        assert "T0K3N" not in text and "geheimes-token" not in text and "hook.example" not in text
    shown = client.get("/ui-api/settings")
    assert shown.json()["notifications"]["webhook_configured"] is True
    assert shown.json()["notifications"]["last_error"] == "HTTP 500"
    for text in (first.text, second.text, shown.text):
        assert "T0K3N" not in text and "geheimes-token" not in text and "hook.example" not in text


# -- Schnittstellen -----------------------------------------------------------------------


def test_interfaces_need_setting_manage_not_a_profile(
    client_als: Als, tenant_client: Als, session: Session
) -> None:
    _anlage(session)
    _fehler(client_als(LESEN).get("/ui-api/interfaces"), 403, "forbidden")
    assert tenant_client(ALLES).get("/ui-api/interfaces").status_code == 200


def test_interfaces_never_return_a_secret(
    client_als: Als, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, zone = _anlage(session)
    env = _env(
        mqtt_enabled=True,
        mqtt_host="broker.example.invalid",
        mqtt_username="mqtt-benutzer",
        mqtt_password="geheim-mqtt",
        mcp_token="geheim-mcp-token",
        meross_email="haus@example.invalid",
        meross_password="geheim-meross",
        notify_webhook="https://hook.example.invalid:8443/T0K3N/geheimpfad?key=abc",
        notify_webhook_token="geheim-hook",
    )
    monkeypatch.setattr(ui_api_operations, "get_settings", lambda: env)
    response = client_als(ALLES).get("/ui-api/interfaces")
    assert response.status_code == 200, response.text
    for secret in (
        "geheim-mqtt", "geheim-mcp-token", "geheim-meross", "geheim-hook", "T0K3N", "geheimpfad",
        "key=abc", "s" * 32,
    ):
        assert secret not in response.text, secret
    body = response.json()
    by_key = {item["key"]: item for item in body["interfaces"]}
    webhook = next(d for d in by_key["benachrichtigung"]["details"] if d["name"] == "Webhook")
    assert webhook["value"] == "https://hook.example.invalid:8443/…"
    assert webhook["variable"] == "THERMOCTL_NOTIFY_WEBHOOK"
    token = next(d for d in by_key["benachrichtigung"]["details"] if d["name"] == "Token")
    assert token["value"] == "hinterlegt"
    mcp = by_key["mcp"]["details"][0]
    assert mcp["value"] == "hinterlegt"
    assert by_key["mqtt"]["state"] == "configured"
    assert by_key["mqtt"]["more"] == {"label": "Geräte ansehen", "path": "/geraete/liste"}
    assert by_key["benachrichtigung"]["more"] is None
    assert by_key["mcp"]["hint"] is not None
    assert body["homebridge_broker_configured"] is True
    (homebridge,) = body["homebridge_zones"]
    assert homebridge["zone_id"] == zone.id and "geheim" not in homebridge["config_json"]


def test_interfaces_without_a_webhook_show_the_placeholder_unchanged(
    client_als: Als, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _anlage(session)
    monkeypatch.setattr(ui_api_operations, "get_settings", lambda: _env())
    body = client_als(ALLES).get("/ui-api/interfaces").json()
    notifications = next(i for i in body["interfaces"] if i["key"] == "benachrichtigung")
    assert next(d for d in notifications["details"] if d["name"] == "Webhook")["value"] == "—"
    assert body["homebridge_broker_configured"] is False


def test_an_unparseable_webhook_is_reported_as_set_without_its_text(
    client_als: Als, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _anlage(session)
    monkeypatch.setattr(
        ui_api_operations, "get_settings", lambda: _env(notify_webhook="nicht-parsbar-geheim")
    )
    response = client_als(ALLES).get("/ui-api/interfaces")
    assert "nicht-parsbar-geheim" not in response.text
    notifications = next(i for i in response.json()["interfaces"] if i["key"] == "benachrichtigung")
    webhook = next(d for d in notifications["details"] if d["name"] == "Webhook")
    assert webhook["value"] == "hinterlegt"


def test_interfaces_name_only_visible_zones_in_the_homebridge_part(
    client_als: Als, session: Session
) -> None:
    _, zone = _anlage(session)
    create_zone(session, "fremd")
    client = client_als([("setting.manage", None), ("zone.read", zone.id)])
    body = client.get("/ui-api/interfaces").json()
    assert [z["zone_id"] for z in body["homebridge_zones"]] == [zone.id]
    assert "Fremd" not in client.get("/ui-api/interfaces").text


def test_interfaces_pass_the_bridge_state_of_the_running_app(
    client_als: Als, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _anlage(session)
    monkeypatch.setattr(
        ui_api_operations, "get_settings", lambda: _env(mqtt_enabled=True, mqtt_host="broker")
    )
    client = client_als(ALLES)
    client.app.state.bridge_reachable = True  # type: ignore[attr-defined]
    try:
        body = client.get("/ui-api/interfaces").json()
    finally:
        del client.app.state.bridge_reachable  # type: ignore[attr-defined]
    assert next(i for i in body["interfaces"] if i["key"] == "mqtt")["state"] == "running"


# -- Verdrahtung --------------------------------------------------------------------------


def test_operations_router_is_csrf_protected_and_hidden_from_the_schema() -> None:
    from thermoctl.auth.dependencies import csrf_protection
    from thermoctl.web.ui_api_operations import router

    assert csrf_protection in [d.dependency for d in router.dependencies]
    assert not router.include_in_schema
    assert router.prefix == "/ui-api"


def test_secrets_are_never_part_of_any_output_model() -> None:
    """Strukturell: kein Ausgabemodell hat ein Feld, das nach Geheimnis klingt."""
    import inspect

    from pydantic import BaseModel

    names = {
        field
        for _, model in inspect.getmembers(ui_api_operations, inspect.isclass)
        if issubclass(model, BaseModel) and model.__module__ == ui_api_operations.__name__
        for field in model.model_fields
    }
    names.discard("webhook_configured")  # nur das Flag, nie der Wert
    for forbidden in ("password", "passwort", "secret", "token", "webhook", "api_key"):
        assert not any(forbidden in name for name in names), forbidden
