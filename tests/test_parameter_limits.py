"""Grenzfälle aller Regelparameter, quer durch alle Zugänge (Aufgabe L1).

Die Zahlen stehen hier **bewusst als Literale** und nicht aus `PARAMETERS`/`LIMITS`
gelesen: Der Test ist der unabhängige Beleg dafür, dass die Grenzen beim Umbau auf eine
einzige Beschreibung exakt dieselben geblieben sind. Geprüft wird je Parameter die
Untergrenze, knapp darunter, die Obergrenze, knapp darüber und (wo sie bindend ist) die
Schrittweite -- für die globalen Standardwerte (Domäne, REST, `/ui-api`) und für die
Zone (Domäne, REST, MCP, `/ui-api`).

Das Speichern aller Raumfelder (REST `PUT /zones/{id}/parameters`, `/ui-api`) erzwingt
dieselben Bereiche wie der Einzelwert und MCP (Abschnitt „Sammel-Speichern"); vorher
prüfte es nur „nicht negativ". Werte, die ein Speichern gar nicht verändert (Altbestand),
blockieren es nicht -- außer der Hysterese, die immer geprüft wird.
"""

from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.helpers import create_settings, create_zone, source, user_with_permissions
from tests.test_ui_api_operations import ALLES, Als, _anlage, _call, _fehler
from thermoctl.auth.tokens import issue_token
from thermoctl.db.models.zone import Zone
from thermoctl.domain import control
from thermoctl.domain.control import ControlError, check_number
from thermoctl.domain.zone_settings import (
    PARAMETERS,
    ControlParameters,
    ParameterOutOfRange,
    save_control_parameters,
    set_parameter,
)
from thermoctl.mcp import server as mcp_server

# -- Die Zahlen, wie sie heute gelten -----------------------------------------------------

# name, Untergrenze, Obergrenze, Schritt
ZONE_TABLE: dict[str, tuple[str, str, str, str]] = {
    "hysteresis_k": ("0.0001", "5.0", "0.0001", "K"),
    "min_on_seconds": ("30", "7200", "10", "s"),
    "min_off_seconds": ("30", "7200", "10", "s"),
    "sensor_timeout_seconds": ("60", "86400", "30", "s"),
    "temperature_offset_k": ("-10.0", "10.0", "0.1", "K"),
    "window_resume_delay_seconds": ("0", "3600", "10", "s"),
    "solar_setback_max_k": ("0.0", "10.0", "0.1", "K"),
    "valve_protection_enabled": ("0", "1", "1", ""),
    "valve_protection_interval_days": ("1", "3650", "1", "Tage"),
    "valve_protection_duration_minutes": ("1", "5256000", "1", "Minuten"),
    "pi_enabled": ("0", "1", "1", ""),
    "pi_gain_per_k": ("0.05", "0.50", "0.05", "1/K"),
    "pi_integral_time_minutes": ("60", "720", "30", "min"),
    "pi_min_on_seconds": ("60", "300", "30", "s"),
    "pi_min_off_seconds": ("60", "300", "30", "s"),
}

GLOBAL_LIMITS: dict[str, tuple[str, str]] = {
    "polling_interval_seconds": ("5", "3600"),
    "shadow_interval_seconds": ("5", "3600"),
    "default_hysteresis_k": ("0.0001", "5.0"),
    "default_min_on_seconds": ("30", "7200"),
    "default_min_off_seconds": ("30", "7200"),
    "default_sensor_timeout_seconds": ("60", "86400"),
    "default_window_resume_delay_seconds": ("0", "3600"),
    "stuck_reading_hours": ("1", "168"),
    "measurement_retention_days": ("1", "3650"),
    "shadow_decision_retention_days": ("1", "3650"),
    "command_log_retention_days": ("1", "3650"),
    "command_log_max_mb": ("10", "1048576"),
    "session_lifetime_seconds": ("300", "31536000"),
    "default_solar_setback_max_k": ("0.0", "10.0"),
    "solar_setback_lookahead_hours": ("1", "12"),
    "assumed_relay_lifetime_operations": ("1000", "10000000"),
}

# Die sechs globalen Felder, die ein Zonen-Gegenstück haben (Zone ohne Präfix).
SHARED = [
    "hysteresis_k",
    "min_on_seconds",
    "min_off_seconds",
    "sensor_timeout_seconds",
    "window_resume_delay_seconds",
    "solar_setback_max_k",
]

# Knapp darunter / knapp darüber: eine Einheit der kleinsten gültigen Stelle.
BELOW_ABOVE: dict[str, tuple[str, str]] = {
    "hysteresis_k": ("0.0000", "5.0001"),
    "min_on_seconds": ("29", "7201"),
    "min_off_seconds": ("29", "7201"),
    "sensor_timeout_seconds": ("59", "86401"),
    "temperature_offset_k": ("-10.1", "10.1"),
    "window_resume_delay_seconds": ("-1", "3601"),
    "solar_setback_max_k": ("-0.1", "10.1"),
    "valve_protection_interval_days": ("0", "3651"),
    "valve_protection_duration_minutes": ("0", "5256001"),
    "pi_gain_per_k": ("0.04", "0.51"),
    "pi_integral_time_minutes": ("59", "721"),
    "pi_min_on_seconds": ("59", "301"),
    "pi_min_off_seconds": ("59", "301"),
}

NUMERIC_ZONE = list(BELOW_ABOVE)


def test_zone_table_is_exactly_the_documented_one() -> None:
    actual = {
        p.name: (str(p.minimum), str(p.maximum), str(p.step), p.unit or "") for p in PARAMETERS
    }
    assert {name: tuple(Decimal(x) for x in row[:3]) for name, row in actual.items()} == {
        name: tuple(Decimal(x) for x in row[:3]) for name, row in ZONE_TABLE.items()
    }
    assert {n: row[3] for n, row in actual.items()} == {n: r[3] for n, r in ZONE_TABLE.items()}


def test_global_limits_are_exactly_the_documented_ones() -> None:
    assert {
        name: (Decimal(lo), Decimal(hi)) for name, (lo, hi) in GLOBAL_LIMITS.items()
    } == control.LIMITS
    # Die Reihenfolge bestimmt, welcher Fehler zuerst gemeldet wird.
    assert list(control.LIMITS) == list(GLOBAL_LIMITS)
    assert control.GANZZAHLIG == frozenset(GLOBAL_LIMITS) - {
        "default_hysteresis_k",
        "default_solar_setback_max_k",
    }


# -- Globale Standardwerte: Domäne --------------------------------------------------------


@pytest.mark.parametrize("field", list(GLOBAL_LIMITS))
def test_global_value_at_both_limits_is_accepted(field: str) -> None:
    lo, hi = GLOBAL_LIMITS[field]
    assert check_number(field, lo) == Decimal(lo)
    assert check_number(field, hi) == Decimal(hi)


@pytest.mark.parametrize("field", list(GLOBAL_LIMITS))
def test_global_value_just_outside_the_limits_is_refused(field: str) -> None:
    lo, hi = GLOBAL_LIMITS[field]
    below = Decimal(lo) - (Decimal("0.0001") if field == "default_hysteresis_k" else
                           Decimal("0.1") if field == "default_solar_setback_max_k" else 1)
    above = Decimal(hi) + (Decimal("0.0001") if field == "default_hysteresis_k" else
                           Decimal("0.1") if field == "default_solar_setback_max_k" else 1)
    for bad in (below, above):
        with pytest.raises(ControlError) as raised:
            check_number(field, str(bad))
        assert raised.value.field == field
        assert raised.value.notice


@pytest.mark.parametrize("field", [f for f in GLOBAL_LIMITS if f not in (
    "default_hysteresis_k", "default_solar_setback_max_k")])
def test_global_integer_fields_refuse_fractions(field: str) -> None:
    lo, _ = GLOBAL_LIMITS[field]
    with pytest.raises(ControlError) as raised:
        check_number(field, f"{int(lo) + 1}.5")
    assert raised.value.field == field


@pytest.mark.parametrize("text", ["", "  ", "abc", "1e", "--1"])
def test_global_unparsable_input_is_refused(text: str) -> None:
    with pytest.raises(ControlError):
        check_number("default_min_on_seconds", text)


def test_global_decimal_comma_and_hysteresis_step() -> None:
    assert check_number("default_hysteresis_k", "0,5") == Decimal("0.5")
    assert check_number("default_hysteresis_k", "0.0002") == Decimal("0.0002")
    assert check_number("default_hysteresis_k", "0.50000") == Decimal("0.5")
    for bad in ("0.00015", "0.12345", "0.00009"):
        with pytest.raises(ControlError):
            check_number("default_hysteresis_k", bad)


def test_global_step_of_other_fields_is_not_enforced() -> None:
    """Heutiger Stand: nur Hysterese hat eine bindende Schrittweite (vier Nachkommastellen)."""
    assert check_number("default_min_on_seconds", "35") == 35
    assert check_number("default_sensor_timeout_seconds", "61") == 61
    assert check_number("default_solar_setback_max_k", "0.05") == Decimal("0.05")


# -- Globale Standardwerte: REST und /ui-api ---------------------------------------------


def _defaults_body(**changes: object) -> dict[str, object]:
    values: dict[str, object] = {
        "timezone": "Europe/Berlin",
        "polling_interval_seconds": 30,
        "shadow_interval_seconds": 60,
        "default_hysteresis_k": "0.3",
        "default_min_on_seconds": 300,
        "default_min_off_seconds": 300,
        "default_sensor_timeout_seconds": 1800,
        "default_window_resume_delay_seconds": 120,
        "stuck_reading_hours": 12,
        "measurement_retention_days": 30,
        "shadow_decision_retention_days": 365,
        "command_log_retention_days": 1095,
        "command_log_max_mb": 2048,
        "session_lifetime_seconds": 1209600,
        "default_solar_setback_max_k": "2.0",
        "solar_setback_lookahead_hours": 3,
        "assumed_relay_lifetime_operations": 500_000,
    }
    values.update(changes)
    return values


def _api_head(session: Session) -> dict[str, str]:
    source(session, "web")
    perms: list[tuple[str, int | None]] = [
        ("zone.read", None),
        ("zone.manage", None),
        ("setting.manage", None),
    ]
    user_record = user_with_permissions(session, "grenzen-api", perms)
    _token, plaintext = issue_token(session, user_record, "grenzen", perms, None)
    return {"Authorization": f"Bearer {plaintext}"}


GLOBAL_EDGES = [
    ("default_hysteresis_k", "0.0001", "5.0", "0.0000", "5.0001"),
    ("default_min_on_seconds", 30, 7200, 29, 7201),
    ("default_min_off_seconds", 30, 7200, 29, 7201),
    ("default_sensor_timeout_seconds", 60, 86400, 59, 86401),
    ("default_window_resume_delay_seconds", 0, 3600, -1, 3601),
    ("default_solar_setback_max_k", "0.0", "10.0", "-0.1", "10.1"),
]


@pytest.mark.parametrize(("field", "lo", "hi", "below", "above"), GLOBAL_EDGES)
def test_rest_defaults_accept_both_limits_and_refuse_just_outside(
    client: TestClient, session: Session, field: str, lo: Any, hi: Any, below: Any, above: Any
) -> None:
    row = create_settings(session)
    head = _api_head(session)
    for good in (lo, hi):
        response = client.put(
            "/api/v1/control/defaults", json=_defaults_body(**{field: good}), headers=head
        )
        assert response.status_code == 200, (field, good, response.text)
    session.refresh(row)
    stored = getattr(row, field)
    for bad in (below, above):
        response = client.put(
            "/api/v1/control/defaults", json=_defaults_body(**{field: bad}), headers=head
        )
        assert response.status_code == 422, (field, bad, response.text)
        assert field in response.json()["detail"]
    session.refresh(row)
    assert getattr(row, field) == stored, "ein abgelehnter Wert darf nichts verändern"


@pytest.mark.parametrize(("field", "lo", "hi", "below", "above"), GLOBAL_EDGES)
def test_ui_api_defaults_accept_both_limits_and_refuse_just_outside(
    client_als: Als, session: Session, field: str, lo: Any, hi: Any, below: Any, above: Any
) -> None:
    row, _ = _anlage(session)
    client = client_als(ALLES)
    fields = client.get("/ui-api/settings").json()["defaults"]
    base = {f["name"]: f["value"] for f in fields}
    url = "/ui-api/settings/defaults"
    for good in (lo, hi):
        values = {**base, field: str(good)}
        response = _call(client, "PUT", url, {"values": values, "timezone": "UTC"})
        assert response.status_code == 200, (field, good, response.text)
    session.refresh(row)
    stored = getattr(row, field)
    for bad in (below, above):
        values = {**base, field: str(bad)}
        _fehler(
            _call(client, "PUT", url, {"values": values, "timezone": "UTC"}),
            422,
            "validation",
            field,
        )
    session.refresh(row)
    assert getattr(row, field) == stored


def test_ui_api_defaults_show_the_limits_to_the_client(
    client_als: Als, session: Session
) -> None:
    _anlage(session)
    body = client_als(ALLES).get("/ui-api/settings").json()
    shown = {f["name"]: (Decimal(f["minimum"]), Decimal(f["maximum"])) for f in body["defaults"]}
    assert shown == {
        name: (Decimal(lo), Decimal(hi)) for name, (lo, hi) in GLOBAL_LIMITS.items()
    }


# -- Zone: Domäne (einzelner Parameter) --------------------------------------------------


def _zone(session: Session) -> Zone:
    create_settings(session)
    source(session, "system")
    zone = create_zone(session, "grenzzone")
    # Dauer-Obergrenze nur zusammen mit dem größten Abstand zulässig
    # (Dauer darf den Abstand nicht überschreiten).
    zone.valve_protection_interval_days = 3650
    session.flush()
    return zone


def _set(session: Session, zone: Zone, name: str, value: str) -> Decimal:
    return set_parameter(session, zone, name, Decimal(value), user_id=None, source="system")


@pytest.mark.parametrize("name", NUMERIC_ZONE)
def test_zone_set_parameter_accepts_both_limits(session: Session, name: str) -> None:
    zone = _zone(session)
    lo, hi, _step, _unit = ZONE_TABLE[name]
    assert _set(session, zone, name, lo) == Decimal(lo)
    assert _set(session, zone, name, hi) == Decimal(hi)
    assert Decimal(getattr(zone, name)) == Decimal(hi)


@pytest.mark.parametrize("name", NUMERIC_ZONE)
def test_zone_set_parameter_refuses_just_outside(session: Session, name: str) -> None:
    zone = _zone(session)
    for bad in BELOW_ABOVE[name]:
        before = getattr(zone, name)
        with pytest.raises(ParameterOutOfRange) as raised:
            _set(session, zone, name, bad)
        assert str(raised.value)
        assert getattr(zone, name) == before


@pytest.mark.parametrize(
    ("name", "off_step"),
    [
        ("hysteresis_k", "0.00015"),
        ("pi_gain_per_k", "0.07"),
        ("pi_integral_time_minutes", "75"),
        ("pi_min_on_seconds", "75"),
        ("pi_min_off_seconds", "75"),
    ],
)
def test_zone_binding_steps_are_refused(session: Session, name: str, off_step: str) -> None:
    zone = _zone(session)
    with pytest.raises(ParameterOutOfRange):
        _set(session, zone, name, off_step)


@pytest.mark.parametrize(
    ("name", "on_step"),
    [
        ("hysteresis_k", "0.0001"),
        ("hysteresis_k", "0.3"),
        ("pi_gain_per_k", "0.25"),
        ("pi_integral_time_minutes", "90"),
        ("pi_min_on_seconds", "90"),
        ("pi_min_off_seconds", "270"),
    ],
)
def test_zone_values_on_the_step_grid_are_accepted(
    session: Session, name: str, on_step: str
) -> None:
    zone = _zone(session)
    assert _set(session, zone, name, on_step) == Decimal(on_step)


def test_zone_step_of_the_other_fields_is_advisory_only(session: Session) -> None:
    """Heutiger Stand: Schritt 10/30/0,1 steht in der Tabelle, bindet aber nur Hysterese und PI."""
    zone = _zone(session)
    assert _set(session, zone, "min_on_seconds", "35") == 35
    assert _set(session, zone, "sensor_timeout_seconds", "61") == 61
    assert _set(session, zone, "temperature_offset_k", "0.05") == Decimal("0.05")
    assert _set(session, zone, "window_resume_delay_seconds", "5") == 5


# -- Zone: Domäne, alle Felder auf einmal -------------------------------------------------


def _all(zone: Zone, **changes: Any) -> dict[str, Decimal | int | bool | None]:
    values: dict[str, Decimal | int | bool | None] = {
        name: getattr(zone, name) for name in ControlParameters.__dataclass_fields__
    }
    values.update(changes)
    return values


def _save(session: Session, zone: Zone, **changes: Any) -> None:
    save_control_parameters(session, zone, _all(zone, **changes), user_id=None, source="system")


def test_zone_bulk_hysteresis_lower_bound_and_step(session: Session) -> None:
    zone = _zone(session)
    _save(session, zone, hysteresis_k=Decimal("0.0001"))
    assert zone.hysteresis_k == Decimal("0.0001")
    for bad in (Decimal("0.0000"), Decimal("0"), Decimal("-1"), Decimal("0.00015"),
                Decimal("NaN"), Decimal("Infinity")):
        with pytest.raises(ParameterOutOfRange):
            _save(session, zone, hysteresis_k=bad)
    assert zone.hysteresis_k == Decimal("0.0001")
    _save(session, zone, hysteresis_k=None)  # None = geerbt
    assert zone.hysteresis_k is None


def test_zone_bulk_pi_fields_are_checked_for_range_and_step(session: Session) -> None:
    zone = _zone(session)
    for name in ("pi_gain_per_k", "pi_integral_time_minutes", "pi_min_on_seconds",
                 "pi_min_off_seconds"):
        lo, hi, _, _ = ZONE_TABLE[name]
        number = Decimal if name == "pi_gain_per_k" else int
        _save(session, zone, **{name: number(lo)})
        _save(session, zone, **{name: number(hi)})
        for bad in BELOW_ABOVE[name]:
            with pytest.raises(ParameterOutOfRange):
                _save(session, zone, **{name: number(bad)})


def test_zone_bulk_valve_protection_bounds(session: Session) -> None:
    zone = _zone(session)
    _save(session, zone, valve_protection_interval_days=1, valve_protection_duration_minutes=1)
    _save(session, zone, valve_protection_interval_days=3650,
          valve_protection_duration_minutes=5_256_000)
    for field, bad in (("valve_protection_interval_days", 0),
                       ("valve_protection_interval_days", 3651),
                       ("valve_protection_duration_minutes", 0),
                       ("valve_protection_duration_minutes", 5_256_001)):
        with pytest.raises(ParameterOutOfRange):
            _save(session, zone, **{field: bad})


# -- Sammel-Speichern: alle erbbaren Felder, gleiche Bereiche wie der Einzelwert ----------

INHERITED = [
    "hysteresis_k",
    "min_on_seconds",
    "min_off_seconds",
    "sensor_timeout_seconds",
    "temperature_offset_k",
    "window_resume_delay_seconds",
    "solar_setback_max_k",
]
LABELS = {
    "hysteresis_k": "Hysterese",
    "min_on_seconds": "Mindest-Einschaltdauer",
    "min_off_seconds": "Mindest-Ausschaltdauer",
    "sensor_timeout_seconds": "Sensorausfall nach",
    "temperature_offset_k": "Sensorabgleich",
    "window_resume_delay_seconds": "Nachlauf nach Fensterschluss",
    "solar_setback_max_k": "Obergrenze Sonnenabsenkung",
}
DECIMAL_FIELDS = {"hysteresis_k", "temperature_offset_k", "solar_setback_max_k"}
# Klar außerhalb, auch die Werte aus der früheren Lücke (0, zehn Millionen, 99).
WILD: dict[str, tuple[str, str]] = {
    "hysteresis_k": ("0", "99"),
    "min_on_seconds": ("0", "10000000"),
    "min_off_seconds": ("0", "10000000"),
    "sensor_timeout_seconds": ("0", "10000000"),
    "temperature_offset_k": ("-99", "99"),
    "window_resume_delay_seconds": ("-1", "10000000"),
    "solar_setback_max_k": ("-99", "99"),
}


def _number(name: str, text: str) -> Decimal | int:
    return Decimal(text) if name in DECIMAL_FIELDS else int(text)


def _range_message(name: str) -> str:
    lo, hi, _step, _unit = ZONE_TABLE[name]
    return f"{LABELS[name]} muss zwischen {lo} und {hi} liegen."


def _outside(name: str) -> list[str]:
    return [*BELOW_ABOVE[name], *WILD[name]]


@pytest.mark.parametrize("name", INHERITED)
def test_zone_bulk_accepts_both_limits_of_every_inherited_field(
    session: Session, name: str
) -> None:
    zone = _zone(session)
    lo, hi, _, _ = ZONE_TABLE[name]
    for good in (lo, hi):
        _save(session, zone, **{name: _number(name, good)})
        assert getattr(zone, name) == _number(name, good)


@pytest.mark.parametrize("name", INHERITED)
def test_zone_bulk_refuses_everything_outside_with_the_range_message(
    session: Session, name: str
) -> None:
    zone = _zone(session)
    for bad in _outside(name):
        before = getattr(zone, name)
        with pytest.raises(ParameterOutOfRange) as raised:
            _save(session, zone, **{name: _number(name, bad)})
        assert str(raised.value) == _range_message(name), bad
        assert getattr(zone, name) == before


@pytest.mark.parametrize("name", INHERITED)
def test_zone_bulk_none_stays_inherited(session: Session, name: str) -> None:
    zone = _zone(session)
    _save(session, zone, **{name: _number(name, ZONE_TABLE[name][0])})
    _save(session, zone, **{name: None})
    assert getattr(zone, name) is None


@pytest.mark.parametrize("text", ["NaN", "Infinity", "-Infinity"])
def test_zone_bulk_non_finite_numbers_give_a_message(session: Session, text: str) -> None:
    zone = _zone(session)
    for name in ("hysteresis_k", "temperature_offset_k", "solar_setback_max_k"):
        with pytest.raises(ParameterOutOfRange):
            _save(session, zone, **{name: Decimal(text)})


def test_zone_bulk_hysteresis_upper_bound_is_enforced_like_the_single_value(
    session: Session,
) -> None:
    zone = _zone(session)
    with pytest.raises(ParameterOutOfRange, match=r"^Hysterese muss zwischen 0\.0001 und 5\.0"):
        _save(session, zone, hysteresis_k=Decimal("5.0001"))
    _save(session, zone, hysteresis_k=Decimal("5.0"))


# -- Altbestand außerhalb des Bereichs ----------------------------------------------------
# Früher konnte der Sammel-Weg Werte wie 0 s Mindestdauer speichern. Solche Werte im
# Bestand sollen ein Speichern *anderer* Felder nicht blockieren; ändert man das Feld
# selbst, gilt der Bereich. Die Hysterese wird als Schutz vor Schalten am Sollwert immer
# geprüft.


@pytest.mark.parametrize("via", ["bulk", "single"])
def test_legacy_out_of_range_value_does_not_block_saving_another_field(
    session: Session, via: str
) -> None:
    zone = _zone(session)
    zone.min_on_seconds = 0
    zone.sensor_timeout_seconds = 10_000_000
    zone.temperature_offset_k = Decimal("99")
    session.flush()
    if via == "bulk":
        _save(session, zone, hysteresis_k=Decimal("0.5"))
    else:
        _set(session, zone, "hysteresis_k", "0.5")
    assert zone.hysteresis_k == Decimal("0.5")
    assert (zone.min_on_seconds, zone.sensor_timeout_seconds) == (0, 10_000_000)
    assert zone.temperature_offset_k == Decimal("99")


def test_legacy_value_must_be_valid_when_it_is_the_one_being_changed(session: Session) -> None:
    zone = _zone(session)
    zone.min_on_seconds = 0
    session.flush()
    with pytest.raises(ParameterOutOfRange):
        _save(session, zone, min_on_seconds=5)
    _save(session, zone, min_on_seconds=30)
    assert zone.min_on_seconds == 30


def test_legacy_hysteresis_outside_the_range_blocks_until_it_is_corrected(
    session: Session,
) -> None:
    zone = _zone(session)
    zone.hysteresis_k = Decimal("7.0")
    session.flush()
    with pytest.raises(ParameterOutOfRange, match="^Hysterese muss zwischen"):
        _save(session, zone, min_on_seconds=300)
    _save(session, zone, hysteresis_k=Decimal("0.5"), min_on_seconds=300)
    assert zone.min_on_seconds == 300


# -- Zone: REST --------------------------------------------------------------------------


def _rest_zone(session: Session) -> tuple[Zone, dict[str, str]]:
    create_settings(session)
    zone = create_zone(session, "restzone")
    zone.valve_protection_interval_days = 3650
    session.flush()
    return zone, _api_head(session)


@pytest.mark.parametrize("name", NUMERIC_ZONE)
def test_rest_single_parameter_limits(
    client: TestClient, session: Session, name: str
) -> None:
    zone, head = _rest_zone(session)
    lo, hi, _, _ = ZONE_TABLE[name]
    url = f"/api/v1/zones/{zone.id}/parameters/{name}"
    for good in (lo, hi):
        response = client.put(url, headers=head, json={"value": good})
        assert response.status_code == 200, (name, good, response.text)
    for bad in BELOW_ABOVE[name]:
        response = client.put(url, headers=head, json={"value": bad})
        assert response.status_code == 422, (name, bad, response.text)


def _rest_bulk(
    client: TestClient, zone: Zone, head: dict[str, str], **changes: Any
) -> Any:
    body: dict[str, Any] = {
        "valve_protection_interval_days": 3650,
        "valve_protection_duration_minutes": 10,
    }
    body.update(changes)
    return client.put(f"/api/v1/zones/{zone.id}/parameters", headers=head, json=body)


def test_rest_bulk_hysteresis_lower_bound_and_step(client: TestClient, session: Session) -> None:
    zone, head = _rest_zone(session)
    assert _rest_bulk(client, zone, head, hysteresis_k="0.0001").status_code == 200
    for bad in ("0", "0.0000", "0.00015"):
        response = _rest_bulk(client, zone, head, hysteresis_k=bad)
        assert response.status_code == 422, bad
    assert _rest_bulk(client, zone, head, hysteresis_k="-1").status_code == 422  # pydantic ge=0
    assert _rest_bulk(client, zone, head, hysteresis_k="5.0").status_code == 200
    assert _rest_bulk(client, zone, head, hysteresis_k="5.0001").status_code == 422


@pytest.mark.parametrize(
    ("name", "lo", "hi", "below", "above"),
    [
        ("pi_gain_per_k", "0.05", "0.50", "0.04", "0.51"),
        ("pi_integral_time_minutes", 60, 720, 59, 721),
        ("pi_min_on_seconds", 60, 300, 59, 301),
        ("pi_min_off_seconds", 60, 300, 59, 301),
    ],
)
def test_rest_bulk_pi_limits(
    client: TestClient, session: Session, name: str, lo: Any, hi: Any, below: Any, above: Any
) -> None:
    zone, head = _rest_zone(session)
    for good in (lo, hi):
        assert _rest_bulk(client, zone, head, **{name: good}).status_code == 200
    for bad in (below, above):
        assert _rest_bulk(client, zone, head, **{name: bad}).status_code == 422, bad


@pytest.mark.parametrize("name", INHERITED)
def test_rest_bulk_inherited_fields_enforce_the_same_range_as_the_single_value(
    client: TestClient, session: Session, name: str
) -> None:
    zone, head = _rest_zone(session)
    lo, hi, _, _ = ZONE_TABLE[name]
    for good in (lo, hi):
        response = _rest_bulk(client, zone, head, **{name: good})
        assert response.status_code == 200, (name, good, response.text)
    session.refresh(zone)
    stored = getattr(zone, name)
    for bad in _outside(name):
        response = _rest_bulk(client, zone, head, **{name: bad})
        assert response.status_code == 422, (name, bad, response.text)
        if Decimal(bad) >= 0 or name == "temperature_offset_k":
            # negativ lehnt für die meisten Felder schon das Schema (ge=0) ab
            assert response.json()["detail"] == f"control_parameters: {_range_message(name)}"
    session.refresh(zone)
    assert getattr(zone, name) == stored


def test_rest_single_value_is_not_blocked_by_a_legacy_value_elsewhere(
    client: TestClient, session: Session
) -> None:
    zone, head = _rest_zone(session)
    zone.min_on_seconds = 0
    session.flush()
    response = client.put(
        f"/api/v1/zones/{zone.id}/parameters/hysteresis_k", headers=head, json={"value": "0.5"}
    )
    assert response.status_code == 200, response.text


def test_rest_schema_is_what_openapi_publishes_today(client: TestClient) -> None:
    properties = client.get("/openapi.json").json()["components"]["schemas"][
        "WriteControlParameters"
    ]["properties"]
    assert properties["valve_protection_interval_days"]["maximum"] == 3650
    assert properties["valve_protection_duration_minutes"]["maximum"] == 5_256_000
    assert properties["valve_protection_interval_days"]["exclusiveMinimum"] == 0


@pytest.mark.parametrize("name", [n for n in ZONE_TABLE if not n.endswith("_enabled")
                                  and not n.startswith("valve_protection")])
def test_rest_schema_documents_the_real_range_of_every_parameter(
    client: TestClient, name: str
) -> None:
    """Die OpenAPI-Beschreibung nennt Grenzen und Schritt der Tabelle (B4), nicht `ge=0`."""
    properties = client.get("/openapi.json").json()["components"]["schemas"][
        "WriteControlParameters"
    ]["properties"]
    lo, hi, step, unit = ZONE_TABLE[name]
    description = properties[name]["description"]
    assert f"{lo} bis {hi}" in description
    assert f"Schritt {step}" in description
    if unit:
        assert unit in description


# -- Zone: MCP ---------------------------------------------------------------------------


def _mcp_token(session: Session) -> str:
    source(session, "mcp")
    perms: list[tuple[str, int | None]] = [("zone.read", None), ("zone.manage", None)]
    user_record = user_with_permissions(session, "grenzen-mcp", perms)
    _row, plaintext = issue_token(session, user_record, "grenzen-mcp", perms, None)
    return plaintext


@pytest.mark.parametrize("name", NUMERIC_ZONE)
def test_mcp_set_parameter_limits(session: Session, name: str) -> None:
    zone = _zone(session)
    plaintext = _mcp_token(session)
    lo, hi, _, _ = ZONE_TABLE[name]
    for good in (lo, hi):
        result = mcp_server.set_control_parameters(
            session, plaintext, zone.id, name, Decimal(good)
        )
        assert Decimal(str(result["value"])) == Decimal(good)
    for bad in BELOW_ABOVE[name]:
        with pytest.raises(ParameterOutOfRange):
            mcp_server.set_control_parameters(session, plaintext, zone.id, name, Decimal(bad))


def test_mcp_lists_the_zone_limits(session: Session) -> None:
    zone = _zone(session)
    plaintext = _mcp_token(session)
    listed = mcp_server.read_control_parameters(session, plaintext, zone.id)["parameter"]
    assert isinstance(listed, list)
    shown = {
        entry["name"]: (Decimal(entry["minimum"]), Decimal(entry["maximum"])) for entry in listed
    }
    assert shown == {
        name: (Decimal(row[0]), Decimal(row[1])) for name, row in ZONE_TABLE.items()
    }


# -- Zone: /ui-api ------------------------------------------------------------------------


@pytest.fixture
def raum(session: Session, client_als: Als) -> Callable[[], tuple[TestClient, Zone]]:
    def build() -> tuple[TestClient, Zone]:
        _, zone = _anlage(session)
        return client_als(ALLES), zone

    return build


def _put_parameters(client: TestClient, zone: Zone, body: dict[str, Any]) -> Any:
    return _call(client, "PUT", f"/ui-api/rooms/{zone.id}/parameters", body)


def test_ui_api_shows_the_zone_limits_to_the_client(
    raum: Callable[[], tuple[TestClient, Zone]],
) -> None:
    client, zone = raum()
    body = client.get(f"/ui-api/rooms/{zone.id}/parameters").json()
    shown: dict[str, tuple[Decimal, Decimal, Decimal]] = {}
    for name, entry in body["control"].items():
        limit = entry["limit"]
        shown[name] = (Decimal(limit["minimum"]), Decimal(limit["maximum"]), Decimal(limit["step"]))
    for name, limit in body["pi"]["limits"].items():
        shown[name] = (Decimal(limit["minimum"]), Decimal(limit["maximum"]), Decimal(limit["step"]))
    for name, (lo, hi, step) in shown.items():
        assert (lo, hi, step) == tuple(Decimal(x) for x in ZONE_TABLE[name][:3]), name
    assert set(shown) == {
        "hysteresis_k", "min_on_seconds", "min_off_seconds", "sensor_timeout_seconds",
        "temperature_offset_k", "window_resume_delay_seconds", "solar_setback_max_k",
        "pi_gain_per_k", "pi_integral_time_minutes", "pi_min_on_seconds", "pi_min_off_seconds",
    }


def test_ui_api_hysteresis_lower_bound_and_step(
    raum: Callable[[], tuple[TestClient, Zone]], session: Session
) -> None:
    client, zone = raum()
    assert _put_parameters(client, zone, {"hysteresis_k": "0.0001"}).status_code == 200
    assert _put_parameters(client, zone, {"hysteresis_k": "0,0001"}).status_code == 200
    for bad in ("0", "0.0000", "0.00015"):
        _fehler(_put_parameters(client, zone, {"hysteresis_k": bad}), 422, "validation")
    _fehler(_put_parameters(client, zone, {"hysteresis_k": "-1"}), 422, "validation",
            "hysteresis_k")  # Formularprüfung: nicht negativ
    session.refresh(zone)
    assert zone.hysteresis_k == Decimal("0.0001")


@pytest.mark.parametrize(
    ("name", "lo", "hi", "below", "above"),
    [
        ("pi_gain_per_k", "0.05", "0.50", "0.04", "0.51"),
        ("pi_integral_time_minutes", 60, 720, 59, 721),
        ("pi_min_on_seconds", 60, 300, 59, 301),
        ("pi_min_off_seconds", 60, 300, 59, 301),
    ],
)
def test_ui_api_pi_limits(
    raum: Callable[[], tuple[TestClient, Zone]], name: str, lo: Any, hi: Any, below: Any, above: Any
) -> None:
    client, zone = raum()
    for good in (lo, hi):
        assert _put_parameters(client, zone, {name: good}).status_code == 200, (name, good)
    for bad in (below, above):
        _fehler(_put_parameters(client, zone, {name: bad}), 422, "validation", name)


@pytest.mark.parametrize(
    ("name", "interval", "duration", "field"),
    [
        ("valve_protection_interval_days", 0, None, "valve_protection_interval_days"),
        ("valve_protection_interval_days", 3651, None, "valve_protection_interval_days"),
        ("valve_protection_duration_minutes", None, 0, "valve_protection_duration_minutes"),
        ("valve_protection_duration_minutes", 3650, 5_256_001,
         "valve_protection_duration_minutes"),
    ],
)
def test_ui_api_valve_protection_limits(
    raum: Callable[[], tuple[TestClient, Zone]],
    name: str, interval: int | None, duration: int | None, field: str,
) -> None:
    client, zone = raum()
    body: dict[str, Any] = {}
    if interval is not None:
        body["valve_protection_interval_days"] = interval
    if duration is not None:
        body["valve_protection_duration_minutes"] = duration
    _fehler(_put_parameters(client, zone, body), 422, "validation", field)
    ok = {"valve_protection_interval_days": 3650, "valve_protection_duration_minutes": 5_256_000}
    assert _put_parameters(client, zone, ok).status_code == 200


@pytest.mark.parametrize("name", INHERITED)
def test_ui_api_bulk_inherited_fields_enforce_the_same_range_as_the_single_value(
    raum: Callable[[], tuple[TestClient, Zone]], session: Session, name: str
) -> None:
    client, zone = raum()
    lo, hi, _, _ = ZONE_TABLE[name]
    for good in (lo, hi):
        response = _put_parameters(client, zone, {name: good})
        assert response.status_code == 200, (name, good, response.text)
    session.refresh(zone)
    stored = getattr(zone, name)
    for bad in _outside(name):
        response = _put_parameters(client, zone, {name: bad})
        body = _fehler(response, 422, "validation", name)
        if Decimal(bad) >= 0 or name == "temperature_offset_k":
            # negative Werte meldet für die meisten Felder schon die Formularprüfung
            assert body["detail"] == _range_message(name), (name, bad)
    session.refresh(zone)
    assert getattr(zone, name) == stored


# -- Einheitliche Meldungen (L1, B2) ------------------------------------------------------


def test_zone_and_global_use_the_same_texts_for_the_same_violation(session: Session) -> None:
    zone = _zone(session)
    with pytest.raises(ControlError) as raised:
        check_number("default_min_on_seconds", "29")
    assert raised.value.notice == "Mindest-Einschaltdauer muss zwischen 30 und 7200 liegen."
    with pytest.raises(ParameterOutOfRange) as zone_raised:
        _set(session, zone, "min_on_seconds", "29")
    assert str(zone_raised.value) == raised.value.notice

    with pytest.raises(ControlError) as global_step:
        check_number("default_hysteresis_k", "0.00015")
    assert global_step.value.notice == "Hysterese muss in Schritten von 0.0001 liegen."
    with pytest.raises(ParameterOutOfRange) as zone_step:
        _set(session, zone, "hysteresis_k", "0.00015")
    assert str(zone_step.value) == global_step.value.notice

    with pytest.raises(ControlError) as global_range:
        check_number("default_hysteresis_k", "5.0001")
    with pytest.raises(ParameterOutOfRange) as zone_range:
        _set(session, zone, "hysteresis_k", "5.0001")
    assert str(zone_range.value) == global_range.value.notice
    assert global_range.value.notice == "Hysterese muss zwischen 0.0001 und 5.0 liegen."


def test_the_same_hysteresis_text_for_bulk_single_and_global(session: Session) -> None:
    zone = _zone(session)
    with pytest.raises(ParameterOutOfRange) as bulk:
        _save(session, zone, hysteresis_k=Decimal("0"))
    with pytest.raises(ParameterOutOfRange) as single:
        _set(session, zone, "hysteresis_k", "0")
    with pytest.raises(ControlError) as default:
        check_number("default_hysteresis_k", "0")
    assert str(bulk.value) == str(single.value) == default.value.notice
    assert default.value.notice == "Hysterese muss zwischen 0.0001 und 5.0 liegen."


@pytest.mark.parametrize("text", ["NaN", "Infinity", "-Infinity"])
def test_global_non_finite_numbers_are_refused_not_a_crash(text: str) -> None:
    for field in ("default_hysteresis_k", "polling_interval_seconds"):
        with pytest.raises(ControlError):
            check_number(field, text)
