"""The control page -- operating state, arming, global defaults.

Arming is the only operation in the project that immediately moves a valve.
The tests here therefore check not only that it works, but also that it
**does not** work without the dedicated permission -- and that the way back
into dry run fails on nothing.
"""

import re
from collections.abc import Callable
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.helpers import create_settings, create_zone, source
from thermoctl.auth.csrf import CSRF_HEADER, csrf_token
from thermoctl.auth.sessions import COOKIE_NAME
from thermoctl.config import get_settings
from thermoctl.db.models.operations import AuditEvent, Setting
from thermoctl.db.models.zone import Zone
from thermoctl.domain.control import (
    LIMITS,
    ControlError,
    arm,
    check_number,
    save_settings,
    save_solar_location,
)
from thermoctl.domain.sensor_failure_policy import (
    CurvePoint,
    ProfileValues,
    migration_default_profile_id,
    save_profile,
)

ClientBuilder = Callable[[list[tuple[str, int | None]]], TestClient]

ALL_PERMISSIONS: list[tuple[str, int | None]] = [
    ("zone.read", None),
    ("setting.manage", None),
    ("control.arm", None),
]


def _csrf(client: TestClient) -> dict[str, str]:
    http_session = client.cookies.get(COOKIE_NAME)
    assert http_session is not None
    return {CSRF_HEADER: csrf_token(http_session, get_settings().secret_key.get_secret_value())}


def _defaults(**overrides: str) -> dict[str, str]:
    values = {field: str(LIMITS[field][0]) for field in LIMITS}
    values["timezone"] = "Europe/Berlin"
    values.update(overrides)
    return values


# --- Domain ---------------------------------------------------------------


def test_checking_a_number_accepts_the_comma() -> None:
    """On a German keyboard you type 0,5 -- that is not a mistake."""
    assert check_number("default_hysteresis_k", "0,5") == Decimal("0.5")


@pytest.mark.parametrize(
    ("field", "input_value"),
    [
        ("default_min_on_seconds", "0"),
        ("default_hysteresis_k", "0"),
        ("shadow_interval_seconds", "0"),
        ("shadow_decision_retention_days", "0"),
        ("shadow_decision_retention_days", "-1"),
        ("shadow_decision_retention_days", "3651"),
        ("default_min_on_seconds", "99999"),
        ("default_hysteresis_k", "keine Zahl"),
        ("default_min_on_seconds", "60,5"),
        ("polling_interval_seconds", ""),
        ("assumed_relay_lifetime_operations", "0"),
        ("assumed_relay_lifetime_operations", "-1"),
        ("assumed_relay_lifetime_operations", "100"),
        ("assumed_relay_lifetime_operations", "1000000000"),
    ],
)
def test_unusable_defaults_are_rejected(field: str, input_value: str) -> None:
    """Zero seconds minimum on-time and zero Kelvin hysteresis are exactly the
    legacy system's defect: cycling at the setpoint on every tick."""
    with pytest.raises(ControlError) as errors:
        check_number(field, input_value)
    assert errors.value.field == field


def test_arming_requires_a_justification(session: Session) -> None:
    create_settings(session)
    source(session, "web")
    with pytest.raises(ControlError):
        arm(session, True, reason="   ", user_id=None)
    assert session.get(Setting, 1).control_armed is False


def test_going_back_to_dry_run_requires_no_justification(session: Session) -> None:
    """The way back is the one someone takes in a hurry. It must not fail on
    any formality."""
    create_settings(session)
    source(session, "web")
    arm(session, True, reason="Schattenlauf geprüft", user_id=None)
    assert arm(session, False, reason="", user_id=None) is True
    assert session.get(Setting, 1).control_armed is False


def test_the_same_thing_twice_does_not_write_a_second_entry(session: Session) -> None:
    """Otherwise the audit log would show an arming that never actually happened."""
    create_settings(session)
    source(session, "web")
    assert arm(session, True, reason="erste", user_id=None) is True
    assert arm(session, True, reason="zweite", user_id=None) is False
    entries = list(
        session.scalars(select(AuditEvent).where(AuditEvent.action == "arm"))
    )
    assert len(entries) == 1
    assert entries[0].detail == "erste"


# --- Interface -----------------------------------------------------------


def test_the_page_shows_the_dry_run(client_als: ClientBuilder, session: Session) -> None:
    create_settings(session)
    create_zone(session, "bad")
    response = client_als(ALL_PERMISSIONS).get("/control")
    assert response.status_code == 200
    assert "Trockenlauf" in response.text


def test_operating_pages_describe_dry_run_truthfully(
    client_als: ClientBuilder, session: Session
) -> None:
    create_settings(session)
    client = client_als(ALL_PERMISSIONS)

    for path in ("/", "/control"):
        page = client.get(path)
        assert page.status_code == 200
        assert "Weder Sollwerte noch Ein/Aus-Befehle gehen an Aktoren." in page.text
        assert "Aktorausgabe freigegeben" not in page.text
        assert "Tatsächlich <em>geschaltet</em> wird" not in page.text


def test_operating_pages_describe_armed_before_restart_truthfully(
    client_als: ClientBuilder, session: Session
) -> None:
    row = create_settings(session)
    row.control_armed = True
    session.flush()
    client = client_als(ALL_PERMISSIONS)

    for path in ("/", "/control"):
        page = client.get(path)
        assert page.status_code == 200
        assert "Scharf, Neustart fehlt" in page.text
        assert "Der beim Start gebaute MQTT-Riegel ist noch zu." in page.text
        assert "Aktorausgabe freigegeben" not in page.text


def test_operating_pages_describe_armed_after_restart_truthfully(
    client_als: ClientBuilder, session: Session
) -> None:
    row = create_settings(session)
    row.control_armed = True
    session.flush()
    client = client_als(ALL_PERMISSIONS)
    client.app.state.sending_allowed = True

    for path in ("/", "/control"):
        page = client.get(path)
        assert page.status_code == 200
        assert "Scharf und neu gestartet" in page.text
        assert "Jede Entscheidung unten geht an die Ventile." not in page.text

    # "/control" keeps the long, explicit wording -- it is a dedicated operations
    # page, not the at-a-glance dashboard, so the same message does not need to
    # be shortened there.
    control_page = client.get("/control")
    assert "Sollwerte gehen an selbstregelnde Thermostatventile" in control_page.text
    assert "Ein/Aus-Befehle gehen an Zigbee2MQTT-Schalter" in control_page.text
    assert "Zigbee2MQTT-Thermostatventile ohne eigene Regelung" in control_page.text
    assert "Meross-Steckdosen" in control_page.text

    # "/" (the start page) shortens the message so it reads truthfully at a glance
    # -- the earlier wording listed the two command *kinds* first and let
    # self-regulating valves trail behind, which one operator of a plant built
    # entirely from such valves read as "these are not actually driven". The
    # replacement leads with the fact that every assigned actuator is driven, and
    # puts which command kind goes where behind an explicit disclosure, checked
    # for its content rather than its exact wording so a future rephrase does not
    # break this test for its own sake.
    start_page = client.get("/")
    assert "alle zugeordneten Aktoren werden angesteuert" in start_page.text
    assert "Sollwerte gehen an selbstregelnde Thermostatventile" not in start_page.text
    disclosure_start = start_page.text.index('class="tc-info-disclosure"')
    disclosure_text = start_page.text[disclosure_start : disclosure_start + 400]
    assert "selbst" in disclosure_text  # self-regulating valves are not excluded
    assert "Thermostatventile" in disclosure_text
    assert "Sollwert" in disclosure_text
    assert "Ein/Aus" in disclosure_text


def test_arming_through_the_interface(
    client_als: ClientBuilder, session: Session
) -> None:
    create_settings(session)
    source(session, "web")
    client = client_als(ALL_PERMISSIONS)
    response = client.post(
        "/control/arm",
        data={"armed": "yes", "reason": "Vier Tage Schattenlauf verglichen"},
        headers=_csrf(client),
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert session.get(Setting, 1).control_armed is True
    entry = session.scalars(select(AuditEvent).where(AuditEvent.action == "arm")).one()
    assert entry.detail == "Vier Tage Schattenlauf verglichen"
    assert entry.summary == (
        "Regelung scharf geschaltet — Sollwertausgabe erst nach Neustart freigegeben"
    )


def test_without_control_arm_the_installation_stays_in_dry_run(
    client_als: ClientBuilder, session: Session
) -> None:
    """`setting.manage` alone is not enough. Someone allowed to maintain
    timezone and retention period should not be able to arm the heating on
    the side."""
    create_settings(session)
    client = client_als([("zone.read", None), ("setting.manage", None)])
    response = client.post(
        "/control/arm",
        data={"armed": "yes", "reason": "trotzdem"},
        headers=_csrf(client),
        follow_redirects=False,
    )
    assert response.status_code == 403
    assert session.get(Setting, 1).control_armed is False


def test_a_missing_justification_returns_to_the_form(
    client_als: ClientBuilder, session: Session
) -> None:
    create_settings(session)
    client = client_als(ALL_PERMISSIONS)
    response = client.post(
        "/control/arm", data={"armed": "yes", "reason": ""},
        headers=_csrf(client),
    )
    assert response.status_code == 200
    assert "Bitte kurz festhalten" in response.text
    assert session.get(Setting, 1).control_armed is False


def test_saving_defaults(client_als: ClientBuilder, session: Session) -> None:
    create_settings(session)
    source(session, "web")
    client = client_als(ALL_PERMISSIONS)
    response = client.post(
        "/settings",
        data=_defaults(
            default_hysteresis_k="0,4",
            shadow_interval_seconds="90",
            shadow_decision_retention_days="730",
        ),
        headers=_csrf(client),
        follow_redirects=False,
    )
    assert response.status_code == 303
    row = session.get(Setting, 1)
    assert row.default_hysteresis_k == Decimal("0.4")
    assert row.shadow_interval_seconds == 90
    assert row.shadow_decision_retention_days == 730


def test_the_assumed_relay_lifetime_is_saved_through_the_same_form(
    client_als: ClientBuilder, session: Session
) -> None:
    """It is a plant-wide default like the others, not a special case -- same form,
    same domain check, same LIMITS bound."""
    create_settings(session)
    source(session, "web")
    client = client_als(ALL_PERMISSIONS)
    response = client.post(
        "/settings",
        data=_defaults(assumed_relay_lifetime_operations="250000"),
        headers=_csrf(client),
        follow_redirects=False,
    )
    assert response.status_code == 303
    row = session.get(Setting, 1)
    assert row.assumed_relay_lifetime_operations == 250_000


def test_a_rejected_default_leaves_nothing_half_written(
    client_als: ClientBuilder, session: Session
) -> None:
    """The bug that once left setup half-created: writing before everything is checked."""
    create_settings(session)
    before = session.get(Setting, 1).shadow_interval_seconds
    client = client_als(ALL_PERMISSIONS)
    response = client.post(
        "/settings",
        data=_defaults(shadow_interval_seconds="90", default_min_on_seconds="0"),
        headers=_csrf(client),
    )
    assert response.status_code == 200
    session.expire_all()
    assert session.get(Setting, 1).shadow_interval_seconds == before


def test_without_setting_manage_read_only(client_als: ClientBuilder, session: Session) -> None:
    create_settings(session)
    read_only = client_als([("zone.read", None)])
    assert read_only.get("/control").status_code == 200
    assert read_only.get("/settings").status_code == 200
    assert (
        read_only.post(
            "/settings", data=_defaults(), headers=_csrf(read_only)
        ).status_code
        == 403
    )


def test_the_operations_page_names_the_second_bolt(
    client_als: ClientBuilder, session: Session
) -> None:
    """Armed but nothing sent: anyone unaware of this state searches for
    hours in the wrong place."""
    create_settings(session)
    source(session, "web")
    arm(session, True, reason="Test", user_id=None)

    page = client_als(ALL_PERMISSIONS).get("/control")
    assert page.status_code == 200
    assert "Der beim Start gebaute MQTT-Riegel ist noch zu." in page.text


def test_the_hint_does_not_appear_in_dry_run(
    client_als: ClientBuilder, session: Session
) -> None:
    """Counter-check: without it, the test above would also be satisfied by
    a version that always shows the hint -- and then every page would carry
    a warning that nobody reads any more."""
    create_settings(session)
    page = client_als(ALL_PERMISSIONS).get("/control")
    assert "Der beim Start gebaute MQTT-Riegel ist noch zu." not in page.text


def test_an_empty_timezone_is_refused_with_its_field(session: Session) -> None:
    """The timezone has no sensible default -- an empty one would silently shift every
    schedule, because schedules are stored in local time."""
    create_settings(session)
    with pytest.raises(ControlError) as fehler:
        save_settings(session, {}, "   ", user_id=None)
    assert fehler.value.field == "timezone"


@pytest.mark.parametrize("eingabe", ["keine Zahl", "12,5,7", ""])
def test_a_coordinate_that_is_no_number_names_its_field(
    session: Session, eingabe: str
) -> None:
    """The coordinate arrives as text from a form, so anything can be in it.

    An empty one is the exception and means "no location" -- checked separately in the
    REST tests. Everything else has to come back naming the field, so the page can mark
    it, instead of raising a bare `InvalidOperation`.
    """
    create_settings(session)
    if eingabe == "":
        save_solar_location(
            session, enabled=False, latitude_text="", longitude_text="", user_id=None
        )
        return
    with pytest.raises(ControlError) as fehler:
        save_solar_location(
            session, enabled=True, latitude_text=eingabe, longitude_text="0", user_id=None
        )
    assert fehler.value.field == "solar_forecast_latitude"


def test_the_solar_setback_can_be_switched_on_through_its_own_form(
    angemeldeter_client: TestClient, session: Session
) -> None:
    """Abgeschickt wird, was die Seite wirklich rendert — Name **und** Wert.

    Der Schalter trug einmal keinen `value`, dann schickte der Browser `"on"`, und die
    View verglich damit. Seit das Makro `value="yes"` setzt, traf der Vergleich nie
    mehr zu: Die Sonnenabsenkung liess sich nicht mehr einschalten, ohne dass irgendwo
    ein Fehler erschien. Aus dem Betrieb gemeldet.

    Ein Test, der den Wert selbst hinschreibt, hätte das nicht gesehen — er stimmt
    immer mit der Hälfte überein, gegen die er geschrieben wurde.
    """
    create_settings(session)
    source(session, "web")
    session.flush()

    page = angemeldeter_client.get("/settings")
    assert page.status_code == 200
    formular = re.search(
        r'<form[^>]*>(?:(?!</form>).)*name="solar_forecast_enabled".*?</form>',
        page.text,
        re.S,
    )
    assert formular is not None, "Kein Formular mit dem Sonnenschalter gefunden"
    rumpf = formular.group(0)

    daten = dict(re.findall(r'name="([^"]+)"[^>]*value="([^"]*)"', rumpf))
    schalter = re.search(
        r'name="solar_forecast_enabled"[^>]*value="([^"]*)"', rumpf
    ) or re.search(r'value="([^"]*)"[^>]*name="solar_forecast_enabled"', rumpf)
    assert schalter is not None, "Der Schalter rendert keinen Wert"
    daten["solar_forecast_enabled"] = schalter.group(1)
    daten["solar_forecast_latitude"] = "52.520"
    daten["solar_forecast_longitude"] = "13.405"

    antwort = angemeldeter_client.post(
        "/settings", data=daten, headers=_csrf(angemeldeter_client), follow_redirects=False
    )
    assert antwort.status_code in (200, 303), antwort.text[:400]

    row = session.get(Setting, 1)
    assert row is not None
    assert row.solar_forecast_enabled is True
    assert row.solar_forecast_latitude == Decimal("52.520")


# --- Außentemperatur und Fenster-Alarm --------------------------------------


def test_the_outdoor_source_can_be_set_and_cleared(
    client_als: ClientBuilder, session: Session
) -> None:
    from tests.helpers import capability, create_device
    from thermoctl.db.models.device import DeviceCapabilityLink

    create_settings(session)
    source(session, "web")
    device = create_device(session, "aussenfuehler-form")
    session.add(
        DeviceCapabilityLink(
            device_id=device.id, capability_id=capability(session, "temperature").id
        )
    )
    session.flush()
    client = client_als(ALL_PERMISSIONS)

    response = client.post(
        "/settings/outdoor-source",
        data={"device_id": str(device.id)},
        headers=_csrf(client),
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert session.get(Setting, 1).outdoor_temperature_source_device_id == device.id

    response = client.post(
        "/settings/outdoor-source",
        data={"device_id": ""},
        headers=_csrf(client),
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert session.get(Setting, 1).outdoor_temperature_source_device_id is None


def test_a_device_without_temperature_capability_is_rejected_as_outdoor_source(
    client_als: ClientBuilder, session: Session
) -> None:
    from tests.helpers import capability, create_device
    from thermoctl.db.models.device import DeviceCapabilityLink

    create_settings(session)
    source(session, "web")
    ventil = create_device(session, "ventil-als-aussenfuehler-form")
    session.add(
        DeviceCapabilityLink(
            device_id=ventil.id, capability_id=capability(session, "switch").id
        )
    )
    session.flush()
    client = client_als(ALL_PERMISSIONS)

    response = client.post(
        "/settings/outdoor-source",
        data={"device_id": str(ventil.id)},
        headers=_csrf(client),
    )
    assert response.status_code == 200
    assert "Temperatur" in response.text
    assert session.get(Setting, 1).outdoor_temperature_source_device_id is None


def test_a_non_numeric_outdoor_source_device_id_is_rejected(
    client_als: ClientBuilder, session: Session
) -> None:
    create_settings(session)
    source(session, "web")
    client = client_als(ALL_PERMISSIONS)

    response = client.post(
        "/settings/outdoor-source",
        data={"device_id": "kein-gerät"},
        headers=_csrf(client),
    )
    assert response.status_code == 200
    assert "bekanntes Gerät" in response.text
    assert session.get(Setting, 1).outdoor_temperature_source_device_id is None


def test_an_unknown_outdoor_source_device_id_is_rejected(
    client_als: ClientBuilder, session: Session
) -> None:
    create_settings(session)
    source(session, "web")
    client = client_als(ALL_PERMISSIONS)

    response = client.post(
        "/settings/outdoor-source",
        data={"device_id": "999999"},
        headers=_csrf(client),
    )
    assert response.status_code == 200
    assert "nicht bekannt" in response.text
    assert session.get(Setting, 1).outdoor_temperature_source_device_id is None


def test_the_settings_page_shows_the_outdoor_source_selection(
    client_als: ClientBuilder, session: Session
) -> None:
    create_settings(session)
    response = client_als(ALL_PERMISSIONS).get("/settings")
    assert response.status_code == 200
    assert 'name="device_id"' in response.text
    assert "Außentemperatur" in response.text


def test_saving_the_window_alarm_thresholds(
    client_als: ClientBuilder, session: Session
) -> None:
    create_settings(session)
    source(session, "web")
    client = client_als(ALL_PERMISSIONS)

    response = client.post(
        "/settings/window-alarm",
        data={
            "window_alarm_open_minutes": "45",
            "window_alarm_outdoor_threshold_c": "2,5",
        },
        headers=_csrf(client),
        follow_redirects=False,
    )
    assert response.status_code == 303
    row = session.get(Setting, 1)
    assert row.window_alarm_open_minutes == 45
    assert row.window_alarm_outdoor_threshold_c == Decimal("2.5")


def test_an_unusable_window_alarm_threshold_is_rejected_and_names_its_field(
    client_als: ClientBuilder, session: Session
) -> None:
    create_settings(session)
    source(session, "web")
    client = client_als(ALL_PERMISSIONS)

    response = client.post(
        "/settings/window-alarm",
        data={"window_alarm_open_minutes": "1", "window_alarm_outdoor_threshold_c": "0"},
        headers=_csrf(client),
    )
    assert response.status_code == 200
    assert "window_alarm_open_minutes" in response.text or "zwischen" in response.text
    # Nothing was half-written.
    row = session.get(Setting, 1)
    assert row.window_alarm_open_minutes == 30


def test_the_window_alarm_thresholds_never_reach_limits_or_the_rest_schema() -> None:
    """The project owner's explicit instruction: nothing new about the window
    alarm reaches REST or MCP. Both adapters build their field list from
    `LIMITS` (`api/schemas.py::ControlResponse`, `mcp/server.py`), so keeping the
    two thresholds out of `LIMITS` is what keeps them out of both -- this is the
    guard that would catch an accidental merge of `WINDOW_ALARM_LIMITS` into it.
    """
    from thermoctl.api.schemas import ControlResponse
    from thermoctl.domain.control import WINDOW_ALARM_LIMITS

    for field in WINDOW_ALARM_LIMITS:
        assert field not in LIMITS
        assert field not in ControlResponse.model_fields


def test_saving_the_window_temp_drop_thresholds(
    client_als: ClientBuilder, session: Session
) -> None:
    create_settings(session)
    source(session, "web")
    client = client_als(ALL_PERMISSIONS)

    response = client.post(
        "/settings/window-temp-drop",
        data={
            "window_temp_drop_window_minutes": "20",
            "window_temp_drop_threshold_k": "2,0",
            "window_temp_drop_hold_minutes": "45",
            "window_temp_drop_gap_tolerance_minutes": "12",
            "window_temp_drop_max_suspected_minutes": "120",
            "window_temp_drop_silence_minutes": "90",
        },
        headers=_csrf(client),
        follow_redirects=False,
    )
    assert response.status_code == 303
    row = session.get(Setting, 1)
    assert row.window_temp_drop_window_minutes == 20
    assert row.window_temp_drop_threshold_k == Decimal("2.0")
    assert row.window_temp_drop_hold_minutes == 45
    assert row.window_temp_drop_gap_tolerance_minutes == 12
    assert row.window_temp_drop_max_suspected_minutes == 120
    assert row.window_temp_drop_silence_minutes == 90


def test_an_unusable_window_temp_drop_threshold_is_rejected_and_names_its_field(
    client_als: ClientBuilder, session: Session
) -> None:
    create_settings(session)
    source(session, "web")
    client = client_als(ALL_PERMISSIONS)

    response = client.post(
        "/settings/window-temp-drop",
        data={
            "window_temp_drop_window_minutes": "1",
            "window_temp_drop_threshold_k": "0",
            "window_temp_drop_hold_minutes": "1",
            "window_temp_drop_gap_tolerance_minutes": "-1",
            "window_temp_drop_max_suspected_minutes": "1",
            "window_temp_drop_silence_minutes": "1",
        },
        headers=_csrf(client),
    )
    assert response.status_code == 200
    assert "window_temp_drop" in response.text or "zwischen" in response.text
    # Nothing was half-written.
    row = session.get(Setting, 1)
    assert row.window_temp_drop_window_minutes == 15


def test_the_window_temp_drop_thresholds_never_reach_limits_or_the_rest_schema() -> None:
    """Same guard as `test_the_window_alarm_thresholds_never_reach_limits_or_the_rest_schema`
    above, for the temperature-based window detection's own six thresholds."""
    from thermoctl.api.schemas import ControlResponse
    from thermoctl.domain.control import WINDOW_TEMP_DROP_LIMITS

    for field in WINDOW_TEMP_DROP_LIMITS:
        assert field not in LIMITS
        assert field not in ControlResponse.model_fields


def test_the_start_page_keeps_the_three_latches_apart(
    client_als: ClientBuilder, session: Session
) -> None:
    """Die drei Riegel sind drei Aussagen, nicht eine.

    Aktorfreigabe, MQTT-Ausgabe und Verbundrolle antworten auf drei verschiedene
    Fragen und können unabhängig voneinander schiefstehen: scharf, aber der beim
    Start gebaute Riegel ist zu; oder beides offen, aber diese Instanz ist die
    Bereitschaft und regelt gar nicht. Ein zusammengefasstes "System OK" wäre in
    jedem dieser Fälle wahr und trotzdem irreführend -- der Grund, aus dem die
    Bestandsaufnahme diese Trennung ausdrücklich als "darf nicht verlorengehen"
    führt.
    """
    create_settings(session)
    page = client_als(ALL_PERMISSIONS).get("/").text
    for label in ("Aktorfreigabe", "MQTT-Ausgabe", "Rolle im Verbund"):
        assert label in page, label
    # Drei getrennte Blöcke, nicht drei Wörter in einem Satz.
    assert page.count('class="tc-latch"') == 3


# --- Notbetrieb bei Sensorausfall (Auftrag 8a) ------------------------------


def _profile_values(**overrides: object) -> ProfileValues:
    base = dict(
        name="Notbetrieb Vorgabe",
        fixed_on_seconds=600,
        fixed_off_seconds=1200,
        recovery_seconds=60,
        recovery_samples=2,
        warm_restart_hysteresis_k=Decimal("1"),
        curve_points=(),
    )
    base.update(overrides)
    return ProfileValues(**base)  # type: ignore[arg-type]


def _set_profile(session: Session, **overrides: object) -> None:
    """Updates the one Vorgabeprofil `create_settings` already seeds (helpers.py
    mirrors the real migration) -- `save_profile` without a `profile_id` would
    create a *second*, unrelated row with the same name, and
    `migration_default_profile_id` always keeps using the oldest (the bare one
    from `create_settings`), silently ignoring whatever this test just set up."""
    profile_id = migration_default_profile_id(session)
    save_profile(session, _profile_values(**overrides), profile_id=profile_id)


def test_the_settings_page_shows_the_effective_sensor_failure_profile(
    client_als: ClientBuilder, session: Session
) -> None:
    create_settings(session)
    _set_profile(session)
    page = client_als(ALL_PERMISSIONS).get("/settings").text
    assert "Notbetrieb bei Sensorausfall" in page
    assert 'name="fixed_on_seconds"' in page
    assert 'value="600"' in page


def test_saving_the_sensor_failure_defaults_round_trips(
    client_als: ClientBuilder, session: Session
) -> None:
    create_settings(session)
    source(session, "web")
    _set_profile(session)
    client = client_als(ALL_PERMISSIONS)

    response = client.post(
        "/settings/sensor-failure",
        data={
            "fixed_on_seconds": "900",
            "fixed_off_seconds": "1200",
            "recovery_seconds": "60",
            "recovery_samples": "2",
            "warm_restart_hysteresis_k": "1",
            "emergency_setpoint_c": "17.5",
            "curve_outdoor_c": ["-10", "0", "15"],
            "curve_on_seconds": ["1200", "600", "0"],
            "curve_off_seconds": ["600", "1200", "1800"],
        },
        headers=_csrf(client),
        follow_redirects=False,
    )
    assert response.status_code == 303

    page = client.get("/settings").text
    assert 'value="900"' in page

    row = session.get(Setting, 1)
    profile_id = row.sensor_failure_default_profile_id or migration_default_profile_id(session)
    from thermoctl.domain.sensor_failure_policy import read_profile

    reloaded = read_profile(session, profile_id)
    assert reloaded.values.fixed_on_seconds == 900
    assert [p.outdoor_c for p in reloaded.values.curve_points] == [
        Decimal("-10.00"),
        Decimal("0.00"),
        Decimal("15.00"),
    ]


def test_emptying_the_curve_rows_falls_back_to_fixed_cycling(
    client_als: ClientBuilder, session: Session
) -> None:
    """Keine Kennlinienzeile heißt Festtakt -- derselbe Vertrag wie in der Domäne
    (`validate_profile`), hier über die Oberfläche belegt statt nur über `save_profile`
    direkt (`tests/test_sensor_failure_policy.py`)."""
    create_settings(session)
    source(session, "web")
    _set_profile(
        session,
        curve_points=(
            CurvePoint(Decimal("-10"), 1200, 600),
            CurvePoint(Decimal("15"), 0, 1800),
        ),
    )
    client = client_als(ALL_PERMISSIONS)

    response = client.post(
        "/settings/sensor-failure",
        data={
            "fixed_on_seconds": "600",
            "fixed_off_seconds": "1200",
            "recovery_seconds": "60",
            "recovery_samples": "2",
            "warm_restart_hysteresis_k": "1",
            "emergency_setpoint_c": "16",
            "curve_outdoor_c": [""],
            "curve_on_seconds": [""],
            "curve_off_seconds": [""],
        },
        headers=_csrf(client),
        follow_redirects=False,
    )
    assert response.status_code == 303
    row = session.get(Setting, 1)
    profile_id = row.sensor_failure_default_profile_id or migration_default_profile_id(session)
    from thermoctl.domain.sensor_failure_policy import read_profile

    assert read_profile(session, profile_id).values.curve_points == ()


def test_an_invalid_curve_point_rejects_the_whole_write_without_data_loss(
    client_als: ClientBuilder, session: Session
) -> None:
    create_settings(session)
    source(session, "web")
    _set_profile(session)
    client = client_als(ALL_PERMISSIONS)

    response = client.post(
        "/settings/sensor-failure",
        data={
            "fixed_on_seconds": "999",
            "fixed_off_seconds": "1200",
            "recovery_seconds": "60",
            "recovery_samples": "2",
            "warm_restart_hysteresis_k": "1",
            "emergency_setpoint_c": "16",
            # Kein Punkt mit on_seconds == 0: fehlender oberer Aus-Punkt, ungültig.
            "curve_outdoor_c": ["-10", "0"],
            "curve_on_seconds": ["1200", "600"],
            "curve_off_seconds": ["600", "1200"],
        },
        headers=_csrf(client),
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert "Kennlinie" in response.text

    page = client.get("/settings").text
    assert 'value="600"' in page  # unverändert, nicht auf 999 geschrieben


def _minimal_zone_parameter_form(**overrides: str) -> dict[str, str]:
    """A parameter-page POST where every regular field is left blank (= inherit) --
    only the Notbetrieb fields under test actually carry a value."""
    values: dict[str, str] = {}
    values.update(overrides)
    return values


def test_the_zone_parameter_page_shows_the_sensor_failure_section(
    client_als: ClientBuilder, session: Session
) -> None:
    create_settings(session)
    _set_profile(session)
    zone = create_zone(session, "notbetrieb-ui-zeigen-zone")
    page = client_als([("zone.read", None), ("zone.manage", None)]).get(
        f"/zones/{zone.id}/parameters"
    ).text
    assert "Notbetrieb bei Sensorausfall" in page
    assert 'name="sensor_failure_enabled"' in page
    assert 'name="sensor_failure_profile_id"' in page


def test_enabling_the_zone_and_setting_an_own_setpoint_through_the_parameter_page(
    client_als: ClientBuilder, session: Session
) -> None:
    create_settings(session)
    source(session, "web")
    _set_profile(session)
    zone = create_zone(session, "notbetrieb-ui-zone")
    client = client_als([("zone.read", None), ("zone.manage", None)])

    response = client.post(
        f"/zones/{zone.id}/parameters",
        data=_minimal_zone_parameter_form(
            sensor_failure_enabled="yes", sensor_failure_emergency_setpoint_c="19"
        ),
        headers=_csrf(client),
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert zone.sensor_failure_enabled is True
    assert zone.sensor_failure_emergency_setpoint_c == Decimal("19")

    # "zurück auf erben": derselbe Checkbox-Zustand, aber das Notsollwert-Feld
    # diesmal leer -- erbt wieder vom anlagenweiten Wert.
    response = client.post(
        f"/zones/{zone.id}/parameters",
        data=_minimal_zone_parameter_form(sensor_failure_enabled="yes"),
        headers=_csrf(client),
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert zone.sensor_failure_emergency_setpoint_c is None
    assert zone.sensor_failure_enabled is True


def test_an_unknown_sensor_failure_profile_is_rejected_without_data_loss(
    client_als: ClientBuilder, session: Session
) -> None:
    create_settings(session)
    source(session, "web")
    _set_profile(session)
    zone = create_zone(session, "notbetrieb-ui-ungueltiges-profil")
    client = client_als([("zone.read", None), ("zone.manage", None)])

    response = client.post(
        f"/zones/{zone.id}/parameters",
        data=_minimal_zone_parameter_form(
            sensor_failure_enabled="yes", sensor_failure_profile_id="999999"
        ),
        headers=_csrf(client),
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert "nicht gefunden" in response.text
    assert zone.sensor_failure_enabled is False


def test_backup_offsets_are_only_shown_and_saved_with_device_manage(
    client_als: ClientBuilder, session: Session
) -> None:
    from tests.helpers import capability, create_device, role
    from thermoctl.db.models.device import DeviceCapabilityLink, ZoneDevice

    create_settings(session)
    source(session, "web")
    _set_profile(session)
    zone = create_zone(session, "notbetrieb-ui-ausgleich-zone")
    thermostat_device = create_device(session, "notbetrieb-ui-thermostat")
    session.add(
        DeviceCapabilityLink(
            device_id=thermostat_device.id, capability_id=capability(session, "thermostat").id
        )
    )
    assignment = ZoneDevice(
        zone_id=zone.id,
        device_id=thermostat_device.id,
        device_role_id=role(session, "actuator").id,
        self_regulating=True,
    )
    session.add(assignment)
    session.flush()

    without_device_manage = client_als([("zone.read", None), ("zone.manage", None)])
    page = without_device_manage.get(f"/zones/{zone.id}/parameters").text
    assert f"backup_offset_{assignment.id}" not in page

    with_device_manage = client_als(
        [("zone.read", None), ("zone.manage", None), ("device.manage", None)]
    )
    page = with_device_manage.get(f"/zones/{zone.id}/parameters").text
    assert f"backup_offset_{assignment.id}" in page

    response = with_device_manage.post(
        f"/zones/{zone.id}/parameters",
        data=_minimal_zone_parameter_form(
            **{f"backup_offset_{assignment.id}": "1.5"}
        ),
        headers=_csrf(with_device_manage),
        follow_redirects=False,
    )
    assert response.status_code == 303
    from thermoctl.domain.sensor_failure_policy import read_backup_offset

    assert read_backup_offset(session, assignment.id) == Decimal("1.50")


def test_a_non_numeric_sensor_failure_profile_id_names_its_field(
    client_als: ClientBuilder, session: Session
) -> None:
    create_settings(session)
    zone = create_zone(session, "notbetrieb-ui-profil-keine-zahl")
    client = client_als([("zone.read", None), ("zone.manage", None)])

    response = client.post(
        f"/zones/{zone.id}/parameters",
        data=_minimal_zone_parameter_form(
            sensor_failure_enabled="yes", sensor_failure_profile_id="abc"
        ),
        headers=_csrf(client),
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert "bekanntes Profil" in response.text
    assert zone.sensor_failure_enabled is False


def test_a_non_numeric_sensor_failure_setpoint_names_its_field(
    client_als: ClientBuilder, session: Session
) -> None:
    create_settings(session)
    zone = create_zone(session, "notbetrieb-ui-sollwert-keine-zahl")
    client = client_als([("zone.read", None), ("zone.manage", None)])

    response = client.post(
        f"/zones/{zone.id}/parameters",
        data=_minimal_zone_parameter_form(
            sensor_failure_enabled="yes", sensor_failure_emergency_setpoint_c="abc"
        ),
        headers=_csrf(client),
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert "gültige Zahl" in response.text
    assert zone.sensor_failure_emergency_setpoint_c is None


def _ausgleich_assignment(session: Session, zone: Zone) -> int:
    from tests.helpers import capability, create_device, role
    from thermoctl.db.models.device import DeviceCapabilityLink, ZoneDevice

    device = create_device(session, f"{zone.name}-thermostat")
    thermostat_capability_id = capability(session, "thermostat").id
    session.add(
        DeviceCapabilityLink(device_id=device.id, capability_id=thermostat_capability_id)
    )
    assignment = ZoneDevice(
        zone_id=zone.id,
        device_id=device.id,
        device_role_id=role(session, "actuator").id,
        self_regulating=True,
    )
    session.add(assignment)
    session.flush()
    return assignment.id


def test_a_non_numeric_backup_offset_names_its_field(
    client_als: ClientBuilder, session: Session
) -> None:
    create_settings(session)
    zone = create_zone(session, "notbetrieb-ui-ausgleich-keine-zahl")
    assignment_id = _ausgleich_assignment(session, zone)
    client = client_als(
        [("zone.read", None), ("zone.manage", None), ("device.manage", None)]
    )

    response = client.post(
        f"/zones/{zone.id}/parameters",
        data=_minimal_zone_parameter_form(**{f"backup_offset_{assignment_id}": "abc"}),
        headers=_csrf(client),
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert "gültige Zahl" in response.text


def test_clearing_a_backup_offset_through_the_parameter_page_resets_it(
    client_als: ClientBuilder, session: Session
) -> None:
    from thermoctl.domain.control import save_sensor_failure_backup_offset
    from thermoctl.domain.sensor_failure_policy import read_backup_offset

    create_settings(session)
    source(session, "web")
    zone = create_zone(session, "notbetrieb-ui-ausgleich-loeschen")
    assignment_id = _ausgleich_assignment(session, zone)
    save_sensor_failure_backup_offset(session, assignment_id, Decimal("2"), user_id=None)
    assert read_backup_offset(session, assignment_id) == Decimal("2")

    client = client_als(
        [("zone.read", None), ("zone.manage", None), ("device.manage", None)]
    )
    response = client.post(
        f"/zones/{zone.id}/parameters",
        data=_minimal_zone_parameter_form(**{f"backup_offset_{assignment_id}": ""}),
        headers=_csrf(client),
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert read_backup_offset(session, assignment_id) == Decimal("0")


def test_an_invalid_curve_point_value_on_the_settings_page_names_its_field(
    client_als: ClientBuilder, session: Session
) -> None:
    create_settings(session)
    source(session, "web")
    client = client_als(ALL_PERMISSIONS)

    response = client.post(
        "/settings/sensor-failure",
        data={
            "fixed_on_seconds": "600",
            "fixed_off_seconds": "1200",
            "recovery_seconds": "60",
            "recovery_samples": "2",
            "warm_restart_hysteresis_k": "1",
            "emergency_setpoint_c": "16",
            "curve_outdoor_c": ["abc", "15"],
            "curve_on_seconds": ["1200", "0"],
            "curve_off_seconds": ["600", "1800"],
        },
        headers=_csrf(client),
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert "Kennlinie" in response.text


def test_a_non_numeric_top_level_sensor_failure_field_on_the_settings_page_is_rejected(
    client_als: ClientBuilder, session: Session
) -> None:
    create_settings(session)
    source(session, "web")
    client = client_als(ALL_PERMISSIONS)

    response = client.post(
        "/settings/sensor-failure",
        data={
            "fixed_on_seconds": "abc",
            "fixed_off_seconds": "1200",
            "recovery_seconds": "60",
            "recovery_samples": "2",
            "warm_restart_hysteresis_k": "1",
            "emergency_setpoint_c": "16",
            "curve_outdoor_c": [],
            "curve_on_seconds": [],
            "curve_off_seconds": [],
        },
        headers=_csrf(client),
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert "gültige Zahlen" in response.text
