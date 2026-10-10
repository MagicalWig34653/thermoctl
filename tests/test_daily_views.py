from datetime import datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from tests.helpers import (
    capability,
    create_mode,
    create_settings,
    create_zone,
    integration,
    role,
    source,
    zone_with_schedule,
)
from thermoctl.auth.csrf import CSRF_HEADER, csrf_token
from thermoctl.auth.sessions import COOKIE_NAME
from thermoctl.config import get_settings
from thermoctl.db.models.device import Device, DeviceCapabilityLink, ZoneDevice
from thermoctl.db.models.operations import AuditEvent, Setting
from thermoctl.db.models.override import ZoneOverride
from thermoctl.db.models.state import ShadowDecision
from thermoctl.db.models.zone import Zone


def _grundlage(session: Session):
    source(session, "web")
    source(session, "api")
    create_settings(session)
    return create_zone(session, "wohnzimmer")


def _assign_switch_actuator(session: Session, zone: Zone) -> None:
    """The minimal assignment `pi_eligible()` accepts."""
    device = Device(
        integration_id=integration(session).id,
        external_id=f"{zone.name}-relais",
        display_name=f"{zone.name}-relais",
    )
    session.add(device)
    session.flush()
    session.add(
        DeviceCapabilityLink(device_id=device.id, capability_id=capability(session, "switch").id)
    )
    session.add(
        ZoneDevice(
            zone_id=zone.id,
            device_id=device.id,
            device_role_id=role(session, "actuator").id,
            self_regulating=False,
        )
    )
    session.flush()


def _csrf(client: TestClient) -> dict[str, str]:
    http_session = client.cookies.get(COOKIE_NAME)
    assert http_session is not None
    return {CSRF_HEADER: csrf_token(http_session, get_settings().secret_key.get_secret_value())}


@pytest.mark.parametrize(
    "moment_utc",
    [datetime(2026, 1, 4, 23, 30), datetime(2026, 8, 30, 22, 30)],
)
def test_the_start_page_uses_local_day_and_time_for_its_schedule_track(
    session: Session,
    client_als,
    monkeypatch: pytest.MonkeyPatch,
    moment_utc: datetime,
) -> None:
    zone = zone_with_schedule(
        session,
        "wohnzimmer",
        [(7, 0, "sonntag", Decimal("18.0")), (1, 0, "montag", Decimal("21.0"))],
    )
    settings = session.get(Setting, 1)
    assert settings is not None
    settings.timezone = "Europe/Berlin"
    client = client_als([("zone.read", zone.id)])
    monkeypatch.setattr("thermoctl.web.start_views.utcnow", lambda: moment_utc)

    response = client.get("/")

    assert response.status_code == 200
    assert "ab 00:00 Montag" in response.text
    assert "ab 00:00 Sonntag" not in response.text
    assert 'class="tc-track-now" style="left: 2.0833%"' in response.text


def test_the_parameter_page_shows_inherited_values_and_empty_restores_inheritance(
    session: Session, client_als
) -> None:
    zone = _grundlage(session)
    zone.hysteresis_k = Decimal("0.70")
    client = client_als([("zone.manage", zone.id)])

    response = client.post(
        f"/zones/{zone.id}/parameters",
        data={
            "hysteresis_k": "",
            "min_on_seconds": "",
            "min_off_seconds": "",
            "sensor_timeout_seconds": "",
            "temperature_offset_k": "",
            "window_resume_delay_seconds": "",
        },
        headers=_csrf(client),
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert zone.hysteresis_k is None
    assert "Derzeit 0.30 K aus dem globalen Standard" in response.text


def test_a_negative_hysteresis_is_refused_but_the_offset_is_saved(
    session: Session, client_als
) -> None:
    zone = _grundlage(session)
    client = client_als([("zone.manage", zone.id)])
    pfad = f"/zones/{zone.id}/parameters"

    errors = client.post(pfad, data={"hysteresis_k": "-0.2"}, headers=_csrf(client))
    assert errors.status_code == 200
    assert "darf nicht negativ" in errors.text
    assert zone.hysteresis_k is None

    response = client.post(
        pfad,
        data={"temperature_offset_k": "-1.25"},
        headers=_csrf(client),
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert zone.temperature_offset_k == Decimal("-1.25")


def test_the_parameter_page_refuses_a_valve_run_longer_than_its_interval(
    session: Session, client_als,
) -> None:
    zone = _grundlage(session)
    client = client_als([("zone.manage", zone.id)])
    response = client.post(
        f"/zones/{zone.id}/parameters",
        data={"valve_protection_interval_days": "1",
              "valve_protection_duration_minutes": "1441"},
        headers=_csrf(client),
    )
    assert response.status_code == 200
    assert "darf nicht länger als der Abstand" in response.text
    assert zone.valve_protection_duration_minutes == 10


def test_the_rendered_parameter_form_carries_the_valve_protection_field_names(
    session: Session, client_als,
) -> None:
    import re

    zone = _grundlage(session)
    client = client_als([("zone.manage", zone.id)])
    path = f"/zones/{zone.id}/parameters"
    page = client.get(path)
    assert page.status_code == 200
    assert "Erzeugt regelmäßig eine Ventilschutz-Entscheidung" in page.text
    assert "Im Trockenlauf wird sie nur protokolliert" in page.text
    assert "scharf und neu gestartet wird der zugeordnete Aktor angesteuert" in page.text
    assert "Im Trockenlauf wird die Entscheidung nur protokolliert" in page.text
    assert "Bewegt das Heizungsventil regelmäßig" not in page.text
    assert "So lange bleibt das Ventil" not in page.text
    form = re.search(
        rf'<form\b[^>]*action="{re.escape(path)}"[^>]*>(.*?)</form>',
        page.text,
        re.DOTALL,
    )
    assert form is not None
    fields: dict[str, str] = {}
    rendered_names: set[str] = set()
    for field in re.findall(r"<input\b[^>]*>", form.group(1)):
        name = re.search(r'name="([^"]+)"', field)
        if name is None:
            continue
        rendered_names.add(name.group(1))
        if 'type="checkbox"' in field and " checked" not in field:
            continue
        rendered = re.search(r'value="([^"]*)"', field)
        fields[name.group(1)] = rendered.group(1) if rendered else ""

    assert {
        "valve_protection_enabled",
        "valve_protection_interval_days",
        "valve_protection_duration_minutes",
    }.issubset(rendered_names)
    fields["valve_protection_interval_days"] = "20"
    fields["valve_protection_duration_minutes"] = "15"
    response = client.post(path, data=fields, headers=_csrf(client), follow_redirects=False)

    assert response.status_code == 303
    assert zone.valve_protection_interval_days == 20
    assert zone.valve_protection_duration_minutes == 15


def test_the_parameter_page_shows_the_pi_warning_with_the_switching_table_and_relay_wear_link(
    session: Session, client_als
) -> None:
    """The wear warning has to be readable *at* the switch, not buried in a footnote
    -- and the numbers behind the info disclosure are the ones from the specification
    and from the project owner's explicit request, not invented ones."""
    zone = _grundlage(session)
    client = client_als([("zone.manage", zone.id)])

    page = client.get(f"/zones/{zone.id}/parameters")

    assert page.status_code == 200
    assert "PI-Regelung (Beta)" in page.text
    # Kein pauschaler Vergleich: PI "kann je nach Einstellung" mehr Schaltspiele erzeugen,
    # und die Tabelle ist ausdrücklich eine Modellrechnung, keine Messung.
    assert "kann je nach Einstellung mehr Schaltspiele erzeugen" in page.text
    assert "deutlich häufiger" not in page.text and "verkürzt" not in page.text
    assert "Modellrechnung" in page.text and "Lebensdauer" in page.text
    assert "262.800" in page.text  # worst case, per year
    assert "52.560" in page.text  # hysteresis ceiling, per year
    # The vorgabe, 500,000 -- and the ratio recomputed against it (262,800 / 500,000).
    assert "500.000" in page.text
    assert "rund 0,53" in page.text
    assert 'href="/relay-wear"' in page.text
    assert "Kessel oder Verdichter" in page.text


def test_the_pi_warning_ratio_follows_a_changed_assumption(
    session: Session, client_als
) -> None:
    """The sentence is computed against the *setting*, not a fixed 2.6 -- change the
    assumption and the stated ratio changes with it."""
    zone = _grundlage(session)
    row = session.get(Setting, 1)
    row.assumed_relay_lifetime_operations = 100_000
    session.flush()
    client = client_als([("zone.manage", zone.id)])

    page = client.get(f"/zones/{zone.id}/parameters")

    assert page.status_code == 200
    assert "100.000" in page.text
    assert "500.000" not in page.text
    assert "rund 2,63" in page.text


def test_an_ineligible_zone_shows_the_reason_and_refuses_to_switch_pi_on(
    session: Session, client_als
) -> None:
    zone = _grundlage(session)
    zone.solar_gain_factor = Decimal("0.40")
    client = client_als([("zone.manage", zone.id)])

    page = client.get(f"/zones/{zone.id}/parameters")
    assert page.status_code == 200
    assert "eignet sich derzeit nicht für PI" in page.text
    assert "Schaltaktor" in page.text

    response = client.post(
        f"/zones/{zone.id}/parameters",
        data={"pi_enabled": "yes", "pi_confirm": "yes", "solar_gain_factor": "0.90"},
        headers=_csrf(client),
    )

    assert response.status_code == 200
    assert "PI-Regelung (Beta) kann für diese Zone nicht eingeschaltet werden" in response.text
    assert zone.pi_enabled is False
    # The rejected PI value must not have silently carried an unrelated field's
    # change through -- `save_control_parameters` never ran.
    assert zone.solar_gain_factor == Decimal("0.40")


def test_switching_pi_on_needs_the_confirmation_checkbox_first(
    session: Session, client_als
) -> None:
    zone = _grundlage(session)
    _assign_switch_actuator(session, zone)
    client = client_als([("zone.manage", zone.id)])

    response = client.post(
        f"/zones/{zone.id}/parameters",
        data={"pi_enabled": "yes"},
        headers=_csrf(client),
    )

    assert response.status_code == 200
    assert "bestätigen" in response.text
    assert zone.pi_enabled is False


def test_switching_pi_on_and_off_through_the_interface_with_audit_entry(
    session: Session, client_als
) -> None:
    zone = _grundlage(session)
    _assign_switch_actuator(session, zone)
    client = client_als([("zone.manage", zone.id)])

    on = client.post(
        f"/zones/{zone.id}/parameters",
        data={
            "pi_enabled": "yes",
            "pi_confirm": "yes",
            "pi_gain_per_k": "0.25",
            "pi_integral_time_minutes": "180",
            "pi_min_on_seconds": "60",
            "pi_min_off_seconds": "60",
        },
        headers=_csrf(client),
        follow_redirects=False,
    )

    assert on.status_code == 303
    assert zone.pi_enabled is True
    entry = session.scalar(
        select(AuditEvent).where(AuditEvent.object_type == "zone_settings")
    )
    assert entry is not None
    assert zone.display_name in entry.summary

    # Turning it back off needs no re-confirmation, and the page says the zone
    # regulates ordinarily again once it is off.
    off = client.post(
        f"/zones/{zone.id}/parameters",
        data={},
        headers=_csrf(client),
        follow_redirects=True,
    )
    assert off.status_code == 200
    assert zone.pi_enabled is False


def test_valve_protection_checkbox_round_trip(session: Session, client_als) -> None:
    """Checked -> saved -> still checked, and unchecked -> saved -> still
    unchecked, through the one form on the page (task-mandated round trip)."""
    zone = _grundlage(session)
    client = client_als([("zone.manage", zone.id)])
    path = f"/zones/{zone.id}/parameters"

    on = client.post(
        path, data={"valve_protection_enabled": "yes"}, headers=_csrf(client),
        follow_redirects=False,
    )
    assert on.status_code == 303
    assert zone.valve_protection_enabled is True
    page = client.get(path)
    after_name = page.text.split('name="valve_protection_enabled"')[1]
    assert "checked" in after_name.split(">")[0]

    off = client.post(path, data={}, headers=_csrf(client), follow_redirects=False)
    assert off.status_code == 303
    assert zone.valve_protection_enabled is False
    page = client.get(path)
    after_name = page.text.split('name="valve_protection_enabled"')[1]
    assert "checked" not in after_name.split(">")[0]


def test_pi_enabled_stays_on_across_further_saves_without_reconfirming(
    session: Session, client_als
) -> None:
    """Once PI is on for a zone, `pi_confirm` must not be demanded again on every
    later save -- only the moment it is switched on (already covered by
    `test_switching_pi_on_needs_the_confirmation_checkbox_first` and
    `test_an_ineligible_zone_...`). This checks the GET side: a reload after PI is
    already on shows it checked and *not* disabled, so a browser submits it as
    checked on the next save without the user touching it again."""
    zone = _grundlage(session)
    _assign_switch_actuator(session, zone)
    client = client_als([("zone.manage", zone.id)])
    path = f"/zones/{zone.id}/parameters"
    client.post(
        path,
        data={
            "pi_enabled": "yes",
            "pi_confirm": "yes",
            "pi_gain_per_k": "0.25",
            "pi_integral_time_minutes": "180",
            "pi_min_on_seconds": "60",
            "pi_min_off_seconds": "60",
        },
        headers=_csrf(client),
    )
    assert zone.pi_enabled is True

    page = client.get(path)
    pi_field = page.text.split('id="pi_enabled"')[1].split(">")[0]
    assert "checked" in pi_field
    assert "disabled" not in pi_field

    # A further save that keeps everything as-is (like a browser resubmitting the
    # same checked checkbox) must not require `pi_confirm` again.
    again = client.post(
        path,
        data={
            "pi_enabled": "yes",
            "pi_gain_per_k": "0.25",
            "pi_integral_time_minutes": "180",
            "pi_min_on_seconds": "60",
            "pi_min_off_seconds": "60",
        },
        headers=_csrf(client),
        follow_redirects=False,
    )
    assert again.status_code == 303
    assert zone.pi_enabled is True


def test_an_enabled_but_now_ineligible_pi_switch_is_never_disabled_in_the_form(
    session: Session, client_als
) -> None:
    """The disabled attribute is only ever meant to block switching PI *on* for an
    ineligible zone (`not pi_eligibility.eligible and not values.pi_enabled`). If it
    were also applied while PI is already on, a real browser would silently drop
    the field from the submitted form and the merged save would read that as
    'off' -- switching a zone's live regulation off through a page reload the user
    never asked for. This zone becomes ineligible (`pi_eligible` requires a
    switch-capable actuator) after PI is already enabled, and the checkbox must
    stay enabled in the markup regardless."""
    zone = _grundlage(session)
    _assign_switch_actuator(session, zone)
    client = client_als([("zone.manage", zone.id)])
    path = f"/zones/{zone.id}/parameters"
    client.post(
        path,
        data={
            "pi_enabled": "yes",
            "pi_confirm": "yes",
            "pi_gain_per_k": "0.25",
            "pi_integral_time_minutes": "180",
            "pi_min_on_seconds": "60",
            "pi_min_off_seconds": "60",
        },
        headers=_csrf(client),
    )
    assert zone.pi_enabled is True

    # Removing the actuator assignment makes the zone ineligible without
    # touching `pi_enabled` itself -- exactly the situation `pi_eligibility`'s own
    # docstring describes ("a zone that was eligible ... can become ineligible
    # later by a device reassignment").
    session.execute(delete(ZoneDevice).where(ZoneDevice.zone_id == zone.id))
    session.flush()

    page = client.get(path)
    assert "eignet sich derzeit nicht für PI" in page.text
    pi_field = page.text.split('id="pi_enabled"')[1].split(">")[0]
    assert "checked" in pi_field
    assert "disabled" not in pi_field

    # Resubmitting `pi_enabled=yes` for a now-ineligible zone is refused by
    # `validate_pi_parameters` (unrelated pre-existing domain rule, untouched here)
    # -- but the point of the field staying enabled/submittable is that this
    # rejection is a visible, explicit error, not a silent, unexplained switch-off.
    # A *disabled* checkbox would instead have dropped the field from the request
    # entirely, which `save_control_parameters` reads as an explicit "off" and
    # would have turned PI off without the user asking for it or seeing why.
    resubmit = client.post(
        path,
        data={
            "pi_enabled": "yes",
            "pi_gain_per_k": "0.25",
            "pi_integral_time_minutes": "180",
            "pi_min_on_seconds": "60",
            "pi_min_off_seconds": "60",
        },
        headers=_csrf(client),
        follow_redirects=False,
    )
    assert resubmit.status_code == 200
    assert "kann für diese Zone nicht eingeschaltet werden" in resubmit.text
    assert zone.pi_enabled is True

    # The silent-switch-off this guards against, made explicit: omitting the field
    # (what a disabled checkbox would submit) really would turn PI off.
    omitted = client.post(
        path,
        data={
            "pi_gain_per_k": "0.25",
            "pi_integral_time_minutes": "180",
            "pi_min_on_seconds": "60",
            "pi_min_off_seconds": "60",
        },
        headers=_csrf(client),
        follow_redirects=False,
    )
    assert omitted.status_code == 303
    assert zone.pi_enabled is False


def test_parameters_of_a_foreign_zone_yield_404(session: Session, client_als) -> None:
    eigene = _grundlage(session)
    fremde = create_zone(session, "fremd")
    client = client_als([("zone.manage", eigene.id)])
    assert client.get(f"/zones/{fremde.id}/parameters").status_code == 404
    assert (
        client.post(f"/zones/{fremde.id}/parameters", data={}, headers=_csrf(client)).status_code
        == 404
    )


def test_the_parameter_page_shows_the_window_temp_drop_switch_off_by_default(
    session: Session, client_als
) -> None:
    zone = _grundlage(session)
    client = client_als([("zone.manage", zone.id)])

    response = client.get(f"/zones/{zone.id}/parameters")

    assert response.status_code == 200
    assert 'name="window_temp_drop_detection_enabled"' in response.text
    after_name = response.text.split('name="window_temp_drop_detection_enabled"')[1]
    assert "checked" not in after_name.split(">")[0]
    # There used to be a second `<form>` here with its own "Speichern" -- the
    # project owner reported it reads as one settings page with two save buttons,
    # and a user who flips this switch and presses the *other* one loses the
    # change. Only one form, one button, for the whole page now.
    assert response.text.count(">Speichern<") == 1


def test_the_window_temp_drop_switch_is_saved_through_the_single_parameter_form(
    session: Session, client_als
) -> None:
    """Regression for the reported bug: the switch used to live in its own
    `<form>`/`<button>` below the main one and was silently lost whenever a user
    pressed the main "Speichern" instead. It is now part of that one form/request,
    while the domain rule for setting it stays in
    `domain.zone_settings.set_window_temp_drop_detection` (Grundsatz 6) -- this
    only exercises the merged HTTP entry point."""
    zone = _grundlage(session)
    assert zone.window_temp_drop_detection_enabled is False
    client = client_als([("zone.manage", zone.id)])
    path = f"/zones/{zone.id}/parameters"

    on = client.post(
        path,
        data={"window_temp_drop_detection_enabled": "yes"},
        headers=_csrf(client),
        follow_redirects=False,
    )
    assert on.status_code == 303
    assert zone.window_temp_drop_detection_enabled is True
    entry = session.scalars(
        select(AuditEvent).where(AuditEvent.object_type == "zone_settings")
    ).all()[-1]
    assert "eingeschaltet" in entry.summary

    # A checkbox left unchecked is not sent at all by a real browser -- this is
    # the actual "off" request, not an explicit "off" value.
    off = client.post(path, data={}, headers=_csrf(client), follow_redirects=False)
    assert off.status_code == 303
    assert zone.window_temp_drop_detection_enabled is False
    entry = session.scalars(
        select(AuditEvent).where(AuditEvent.object_type == "zone_settings")
    ).all()[-1]
    assert "ausgeschaltet" in entry.summary


def test_saving_parameters_with_the_window_temp_drop_switch_unchanged_audits_once(
    session: Session, client_als
) -> None:
    """`save_control_parameters` always writes its own audit entry, regardless of
    whether anything actually changed (unrelated to this task). But
    `set_window_temp_drop_detection`'s own unchanged-value guard must still hold
    inside the merged request: an unchanged switch must not add a *second* entry
    on top of the one the main save always makes."""
    zone = _grundlage(session)
    client = client_als([("zone.manage", zone.id)])
    before = session.scalars(
        select(AuditEvent).where(AuditEvent.object_type == "zone_settings")
    ).all()

    client.post(
        f"/zones/{zone.id}/parameters",
        data={},
        headers=_csrf(client),
        follow_redirects=False,
    )

    after = session.scalars(
        select(AuditEvent).where(AuditEvent.object_type == "zone_settings")
    ).all()
    assert len(after) == len(before) + 1


def test_the_window_temp_drop_switch_survives_a_rejected_save_in_the_redisplayed_form(
    session: Session, client_als
) -> None:
    """A validation error must not silently drop the submitted checkbox state --
    the redisplayed form has to echo back what the user just typed/checked, not
    the zone's last-saved state."""
    zone = _grundlage(session)
    client = client_als([("zone.manage", zone.id)])

    response = client.post(
        f"/zones/{zone.id}/parameters",
        data={"hysteresis_k": "-0.2", "window_temp_drop_detection_enabled": "yes"},
        headers=_csrf(client),
    )

    assert response.status_code == 200
    assert "darf nicht negativ" in response.text
    assert zone.window_temp_drop_detection_enabled is False
    after_name = response.text.split('name="window_temp_drop_detection_enabled"')[1]
    assert "checked" in after_name.split(">")[0]


def test_the_window_temp_drop_switch_is_not_part_of_control_parameters(
    session: Session, client_als
) -> None:
    """The switch is now saved *through* `/zones/{id}/parameters` (see above), but
    it must still not become one of the `ControlParameters` fields REST exposes
    verbatim via `ControlParametersResponse` -- it is set via its own domain call
    (`set_window_temp_drop_detection`), not through `save_control_parameters`."""
    from thermoctl.domain.zone_settings import ControlParameters

    assert "window_temp_drop_detection_enabled" not in ControlParameters.__dataclass_fields__


def test_an_override_from_the_interface_uses_the_same_data_model_as_rest(
    session: Session, client_als
) -> None:
    zone = _grundlage(session)
    client = client_als([("zone.read", zone.id), ("override.create", zone.id)])

    response = client.post(
        f"/zones/{zone.id}/override",
        data={"temperature_c": "21.5", "end": "permanent"},
        headers=_csrf(client),
        follow_redirects=False,
    )

    assert response.status_code == 303
    entry = session.scalar(select(ZoneOverride).where(ZoneOverride.zone_id == zone.id))
    assert entry is not None
    # REST returns exactly this persisted ZoneOverride row; the web view calls
    # the same domain function and does not create a second UI data type.
    assert (entry.temperature_c, entry.ends_at, entry.cancelled_at) == (
        Decimal("21.5"),
        None,
        None,
    )


def test_showing_and_cancelling_an_override(session: Session, client_als) -> None:
    zone = _grundlage(session)
    client = client_als(
        [("zone.read", zone.id), ("override.create", zone.id), ("override.cancel", zone.id)]
    )
    client.post(
        f"/zones/{zone.id}/override",
        data={"temperature_c": "22", "end": "permanent"},
        headers=_csrf(client),
    )
    page = client.get("/")
    # Comma, not a period: the interface is German, and "22.0 °C" reads here
    # like a typo. Input fields keep the period -- an <input type="number">
    # silently discards a value with a comma.
    assert "Übersteuerung auf 22,0 °C" in page.text
    # This used to also carry the fixed phrase "manually chosen fixed temperature".
    # The reasoning now comes from the domain itself -- the same text that the
    # shadow log and the REST response also carry, instead of a second wording
    # just for this page.
    assert "Übersteuerung (feste Temperatur)" in page.text

    response = client.post(
        f"/zones/{zone.id}/override/cancel",
        headers=_csrf(client),
        follow_redirects=False,
    )
    assert response.status_code == 303
    entry = session.scalar(select(ZoneOverride).where(ZoneOverride.zone_id == zone.id))
    assert entry is not None and entry.cancelled_at is not None


def test_a_running_timed_override_describes_its_remaining_time_not_its_end_as_past(
    session: Session, client_als, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The chip names the override's **end**, and that end is still in the future while

    the override runs -- a duration override with 42 minutes left has an `ends_at`
    42 minutes from now, not 42 minutes ago. The counter-check that fails without the
    fix: `age` used to read every moment as elapsed and rendered this as "gerade
    eben", which claims the override just finished rather than that it still has
    42 minutes to go.

    Both the moment the override is created and the moment the page is rendered are
    pinned to the same instant -- otherwise the small, real delay between the two
    requests could round 42 minutes remaining down to 41 and make this assertion
    flaky through no fault of the code under test.
    """
    # `age_in_words` liegt seit v0.9.0 in `domain.time` statt in `web`: die
    # Problemmeldung (`domain.problem_report`) braucht dieselbe Formulierung, und
    # die Domäne darf keinen Adapter importieren.
    import thermoctl.domain.schedule as schedule_module
    import thermoctl.domain.time as time_module
    import thermoctl.web.daily_views as daily_views_module
    import thermoctl.web.start_views as start_views_module

    frozen = datetime(2026, 8, 29, 12, 0, 0)
    monkeypatch.setattr(schedule_module, "utcnow", lambda: frozen)
    monkeypatch.setattr(daily_views_module, "utcnow", lambda: frozen)
    monkeypatch.setattr(time_module, "utcnow", lambda: frozen)
    monkeypatch.setattr(start_views_module, "utcnow", lambda: frozen)

    zone = _grundlage(session)
    client = client_als([("zone.read", zone.id), ("override.create", zone.id)])
    client.post(
        f"/zones/{zone.id}/override",
        data={"temperature_c": "22", "end": "duration", "duration_minutes": "42"},
        headers=_csrf(client),
    )
    page = client.get("/")
    assert "endet noch 42 Minuten" in page.text
    assert "gerade eben" not in page.text


def test_an_override_on_a_foreign_zone_yields_404(session: Session, client_als) -> None:
    eigene = _grundlage(session)
    fremde = create_zone(session, "fremd")
    client = client_als(
        [("zone.read", eigene.id), ("override.create", eigene.id), ("override.cancel", eigene.id)]
    )
    assert (
        client.post(
            f"/zones/{fremde.id}/override",
            data={"temperature_c": "20", "end": "permanent"},
            headers=_csrf(client),
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/zones/{fremde.id}/override/cancel", headers=_csrf(client)
        ).status_code
        == 404
    )


def test_the_overview_explains_a_missing_reading_and_shows_the_decision(
    session: Session, client_als
) -> None:
    zone = _grundlage(session)
    session.add(
        ShadowDecision(
            decided_at=datetime(2026, 8, 29, 7),
            zone_id=zone.id,
            temperature_c=None,
            setpoint_c=Decimal("16.0"),
            setpoint_reason="Frostschutz",
            would_heat=False,
            previous_would_heat=None,
            outcome_code="sensor_missing",
            reason="Kein verwertbarer Messwert",
        )
    )
    session.flush()
    client = client_als([("zone.read", zone.id)])

    response = client.get("/")

    assert response.status_code == 200
    assert "kein Messwert" in response.text
    assert "None" not in response.text
    assert ">0 °C" not in response.text
    assert "Kein verwertbarer Messwert" in response.text
    # The operating mode is only shown when it deviates from the default: "Auto"
    # under every zone would be noise, "Off" on the other hand is the reason why
    # it stays cold there. See test_betriebsart_steht_nur_da_wenn_sie_abweicht.
    assert "Betriebsart" not in response.text


def test_the_overview_shows_the_latest_of_several_decisions(
    session: Session, client_als
) -> None:
    """`zone_status_context` no longer reads a zone's whole decision history to
    find the newest row (see `latest_decisions_by_zone` in `domain/zones.py`) --
    it now finds it with a `GROUP BY`. This proves that shortcut still lands on
    the *right* row: an older decision, a newer one that wins, and a third with
    the exact same `decided_at` as the newest (MariaDB's `DATETIME` only has
    second precision) that must lose to it by `id` alone, exactly as
    `_running_override`'s tiebreak already does for overrides.
    """
    zone = _grundlage(session)
    newest_moment = datetime(2026, 8, 29, 9)
    session.add(
        ShadowDecision(
            decided_at=datetime(2026, 8, 29, 7),
            zone_id=zone.id,
            temperature_c=Decimal("18.0"),
            setpoint_c=Decimal("21.0"),
            setpoint_reason="Zeitplan",
            would_heat=True,
            outcome_code="would_heat",
            reason="Älteste Entscheidung",
        )
    )
    session.add(
        ShadowDecision(
            decided_at=newest_moment,
            zone_id=zone.id,
            temperature_c=Decimal("20.5"),
            setpoint_c=Decimal("21.0"),
            setpoint_reason="Zeitplan",
            would_heat=True,
            outcome_code="would_heat",
            reason="Vorletzte Entscheidung (gleiche Sekunde, kleinere id)",
        )
    )
    session.add(
        ShadowDecision(
            decided_at=newest_moment,
            zone_id=zone.id,
            temperature_c=Decimal("20.9"),
            setpoint_c=Decimal("21.0"),
            setpoint_reason="Zeitplan",
            would_heat=False,
            outcome_code="setpoint_reached",
            reason="Tatsächlich jüngste Entscheidung",
        )
    )
    session.flush()

    response = client_als([("zone.read", zone.id)]).get("/")

    assert response.status_code == 200
    assert "Tatsächlich jüngste Entscheidung" in response.text
    assert "Älteste Entscheidung" not in response.text
    assert "Vorletzte Entscheidung" not in response.text


def test_the_overview_labels_heating_as_a_decision(session: Session, client_als) -> None:
    zone = _grundlage(session)
    session.add(
        ShadowDecision(
            decided_at=datetime(2026, 8, 29, 7),
            zone_id=zone.id,
            temperature_c=Decimal("19.0"),
            setpoint_c=Decimal("21.0"),
            setpoint_reason="Zeitplan",
            would_heat=True,
            previous_would_heat=False,
            outcome_code="would_heat",
            reason="Unter Sollwert",
        )
    )
    session.flush()

    response = client_als([("zone.read", zone.id)]).get("/")

    assert "Heizentscheidung" in response.text
    assert ">Heizt<" not in response.text


def test_an_override_until_the_next_switch(session: Session, client_als) -> None:
    """The end is computed and stored when creating it, not remembered as a rule —
    a later schedule change does not shift an override already in progress."""
    from tests.helpers import create_mode
    from thermoctl.db.models.schedule import SchedulePoint

    zone = _grundlage(session)
    mode = create_mode(session, "tag-übersteuerung", "Tag")
    session.add(
        SchedulePoint(zone_id=zone.id, weekday=1, minute_of_day=360, setpoint_mode_id=mode.id)
    )
    session.flush()
    client = client_als([("override.create", None), ("zone.read", None)])
    response = client.post(
        f"/zones/{zone.id}/override",
        data={"temperature_c": "21,5", "end": "next_switch"},
        headers=_csrf(client), follow_redirects=False,
    )
    assert response.status_code == 303
    entry = session.scalar(select(ZoneOverride).where(ZoneOverride.zone_id == zone.id))
    assert entry is not None and entry.ends_at is not None


def test_an_override_for_a_duration(session: Session, client_als) -> None:
    zone = _grundlage(session)
    client = client_als([("override.create", None), ("zone.read", None)])
    response = client.post(
        f"/zones/{zone.id}/override",
        data={"temperature_c": "20.0", "end": "duration", "duration_minutes": "120"},
        headers=_csrf(client), follow_redirects=False,
    )
    assert response.status_code == 303
    entry = session.scalar(select(ZoneOverride).where(ZoneOverride.zone_id == zone.id))
    assert entry is not None and entry.ends_at is not None


def test_an_override_without_a_schedule_lasts_indefinitely(session: Session, client_als) -> None:
    """Without a schedule point there is no next switch — then it holds until
    someone cancels it. Silently doing nothing at all would be the worse answer."""
    zone = _grundlage(session)
    client = client_als([("override.create", None), ("zone.read", None)])
    client.post(
        f"/zones/{zone.id}/override",
        data={"temperature_c": "20.0", "end": "next_switch"},
        headers=_csrf(client),
    )
    entry = session.scalar(select(ZoneOverride).where(ZoneOverride.zone_id == zone.id))
    assert entry is not None and entry.ends_at is None


def test_nonsensical_overrides_are_refused(session: Session, client_als) -> None:
    """A heating system that bends a nonsensical input into shape is worse than one
    that refuses it."""
    zone = _grundlage(session)
    client = client_als([("override.create", None), ("zone.read", None)])
    for data in (
        {"temperature_c": "warm", "end": "permanent"},
        # The lower bound is -20 degrees: a setpoint in the negative range means
        # "no heating here". Below that there is no longer a real intent, only a
        # typo.
        {"temperature_c": "-30", "end": "permanent"},
        {"temperature_c": "50", "end": "permanent"},
        {"temperature_c": "20", "end": "duration", "duration_minutes": "0"},
        {"temperature_c": "20", "end": "duration", "duration_minutes": "keine Zahl"},
        {"temperature_c": "20", "end": "irgendwas"},
    ):
        response = client.post(
            f"/zones/{zone.id}/override", data=data,
            headers=_csrf(client), follow_redirects=False,
        )
        assert response.status_code == 303, data
        assert "override_errors" in (response.headers.get("location") or ""), data
    assert session.scalar(select(ZoneOverride).where(ZoneOverride.zone_id == zone.id)) is None


def test_nonsensical_control_parameters_stay_in_the_form(session: Session, client_als) -> None:
    zone = _grundlage(session)
    client = client_als([("zone.manage", None), ("zone.read", None)])
    for field, value in (("hysteresis_k", "keine Zahl"), ("min_on_seconds", "-5")):
        response = client.post(
            f"/zones/{zone.id}/parameters", data={field: value}, headers=_csrf(client)
        )
        assert response.status_code == 200, field
    assert zone.hysteresis_k is None and zone.min_on_seconds is None


def test_an_invalid_emergency_setpoint_does_not_save_the_changed_hysteresis(
    session: Session, client_als
) -> None:
    """One form, one "Speichern" button, but two domain calls underneath (control
    parameters and the sensor-failure policy). They must be atomic: a rejected
    emergency setpoint must not leave an already-committed hysteresis change
    behind. Before the fix, `save_control_parameters` ran and was implicitly
    committed by the request's session dependency even though the request then
    returned the form with an error for the second part.
    """
    zone = _grundlage(session)
    assert zone.hysteresis_k is None
    assert zone.sensor_failure_enabled is False
    client = client_als([("zone.manage", zone.id)])

    response = client.post(
        f"/zones/{zone.id}/parameters",
        data={
            "hysteresis_k": "0.50",
            "sensor_failure_enabled": "on",
            "sensor_failure_emergency_setpoint_c": "100",
        },
        headers=_csrf(client),
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "Wert zwischen" in response.text
    assert zone.hysteresis_k is None
    assert zone.sensor_failure_enabled is False
    assert 'value="0.50"' in response.text


def test_an_override_with_two_decimal_places_is_refused(
    session: Session, client_als
) -> None:
    """The interface no longer validates on its own — it only catches what the
    domain says. It used to let two decimal places through, the REST interface
    did not."""
    zone = _grundlage(session)
    client = client_als([("override.create", None), ("zone.read", None)])
    response = client.post(
        f"/zones/{zone.id}/override",
        data={"temperature_c": "21,55", "end": "permanent"},
        headers=_csrf(client), follow_redirects=False,
    )
    assert response.status_code == 303
    assert "override_errors" in (response.headers.get("location") or "")
    assert session.scalar(select(ZoneOverride).where(ZoneOverride.zone_id == zone.id)) is None


def test_the_operating_mode_is_shown_only_when_it_deviates(session: Session, client_als) -> None:
    """Counter-check to the line above. A zone set to "Off" would otherwise look like
    every other zone -- and it is precisely the reason a room stays cold."""
    from thermoctl.db.models.lookup import OperatingMode

    zone = _grundlage(session)
    aus = OperatingMode(code="off", label="Aus")
    session.add(aus)
    session.flush()
    zone.operating_mode_id = aus.id
    session.flush()

    response = client_als([("zone.read", zone.id)]).get("/")
    assert "Betriebsart: Aus" in response.text


# --- Thermostat on the home page --------------------------------------------


def _zone_with_mode(session: Session, temperature: str = "21.0"):
    """A zone whose effective setpoint comes from a schedule mode."""
    from tests.helpers import create_mode
    from thermoctl.db.models.schedule import SchedulePoint
    from thermoctl.db.models.zone import ZoneSetpoint

    zone = _grundlage(session)
    mode = create_mode(session, "thermostat-tag", "Tag")
    session.add(
        ZoneSetpoint(
            zone_id=zone.id, setpoint_mode_id=mode.id, temperature_c=Decimal(temperature)
        )
    )
    session.add(
        SchedulePoint(
            zone_id=zone.id, weekday=1, minute_of_day=0, setpoint_mode_id=mode.id
        )
    )
    session.flush()
    return zone, mode


def test_the_thermostat_raises_the_setpoint_of_the_current_mode(
    session: Session, client_als
) -> None:
    """Not an override: the click permanently changes the stored setpoint of the
    mode. That is why the page also shows, right next to it, which mode is meant."""
    from thermoctl.db.models.zone import ZoneSetpoint

    zone, mode = _zone_with_mode(session)
    client = client_als([("zone.read", zone.id), ("setpoint.write", zone.id)])

    response = client.post(
        f"/zones/{zone.id}/thermostat",
        data={"mode_id": str(mode.id), "direction": "up"},
        headers=_csrf(client),
        follow_redirects=False,
    )
    assert response.status_code == 303
    row = session.scalars(
        select(ZoneSetpoint).where(
            ZoneSetpoint.zone_id == zone.id, ZoneSetpoint.setpoint_mode_id == mode.id
        )
    ).one()
    assert row.temperature_c == Decimal("21.5")


def test_two_clicks_are_two_steps(session: Session, client_als) -> None:
    """The step is computed against the current value, not against the one the
    page knew at render time -- otherwise the second click would have no effect."""
    from thermoctl.db.models.zone import ZoneSetpoint

    zone, mode = _zone_with_mode(session)
    client = client_als([("zone.read", zone.id), ("setpoint.write", zone.id)])
    for _ in range(2):
        client.post(
            f"/zones/{zone.id}/thermostat",
            data={"mode_id": str(mode.id), "direction": "down"},
            headers=_csrf(client),
        )
    row = session.scalars(
        select(ZoneSetpoint).where(ZoneSetpoint.setpoint_mode_id == mode.id)
    ).one()
    assert row.temperature_c == Decimal("20.0")


def test_the_thermostat_stops_at_the_limit(session: Session, client_als) -> None:
    """35 degrees is the end of the road, not an error state -- the domain limit
    applies, and the page then shows the unchanged value with a hint."""
    from thermoctl.db.models.zone import ZoneSetpoint

    zone, mode = _zone_with_mode(session, "35.0")
    client = client_als([("zone.read", zone.id), ("setpoint.write", zone.id)])
    response = client.post(
        f"/zones/{zone.id}/thermostat",
        data={"mode_id": str(mode.id), "direction": "up"},
        headers=_csrf(client),
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "thermostat_errors" in response.headers["location"]
    row = session.scalars(
        select(ZoneSetpoint).where(ZoneSetpoint.setpoint_mode_id == mode.id)
    ).one()
    assert row.temperature_c == Decimal("35.0")


def test_thermostat_braucht_setpoint_write(session: Session, client_als) -> None:
    zone, mode = _zone_with_mode(session)
    client = client_als([("zone.read", zone.id)])
    response = client.post(
        f"/zones/{zone.id}/thermostat",
        data={"mode_id": str(mode.id), "direction": "up"},
        headers=_csrf(client),
    )
    assert response.status_code == 404


def test_without_setpoint_write_no_thermostat_is_on_the_page(
    session: Session, client_als
) -> None:
    """Counter-check to the display: whoever may not adjust it sees the setpoint,
    but no step buttons."""
    zone, _mode = _zone_with_mode(session)
    read_only = client_als([("zone.read", zone.id)]).get("/")
    assert "tc-stage" not in read_only.text

    darf = client_als([("zone.read", zone.id), ("setpoint.write", zone.id)]).get("/")
    assert "tc-stage" in darf.text


def test_the_thermostat_works_even_without_a_stored_setpoint(
    session: Session, client_als
) -> None:
    """The state of a freshly set-up plant: no setpoints maintained, no schedule.
    The page then shows the frost-protection fallback of 16 degrees -- and the
    thermostat used to look for a row that does not exist, and answered with a
    404. On the page it looked as if nothing happened when pressing it.
    """
    from sqlalchemy import select

    from thermoctl.db.base import utcnow
    from thermoctl.db.models.zone import ZoneSetpoint
    from thermoctl.domain.schedule import resolved_setpoint

    zone = _grundlage(session)
    session.query(ZoneSetpoint).filter_by(zone_id=zone.id).delete()
    session.flush()
    shown = resolved_setpoint(session, zone, utcnow())
    assert shown.mode_id is not None

    client = client_als([("zone.read", zone.id), ("setpoint.write", zone.id)])
    response = client.post(
        f"/zones/{zone.id}/thermostat",
        data={"mode_id": str(shown.mode_id), "direction": "up"},
        headers=_csrf(client),
        follow_redirects=False,
    )
    assert response.status_code == 303

    row = session.scalars(
        select(ZoneSetpoint).where(
            ZoneSetpoint.zone_id == zone.id,
            ZoneSetpoint.setpoint_mode_id == shown.mode_id,
        )
    ).one()
    assert row.temperature_c == shown.temperature_c + Decimal("0.5")


def test_the_thermostat_for_a_foreign_mode_stays_a_404(
    session: Session, client_als
) -> None:
    """Counter-check: the fallback only applies to the mode the page is currently
    showing. Any other one still gets a clear refusal instead of a setpoint
    conjured out of nowhere."""
    from tests.helpers import create_mode

    zone = _grundlage(session)
    fremder = create_mode(session, "nie-benutzt")
    client = client_als([("zone.read", zone.id), ("setpoint.write", zone.id)])
    response = client.post(
        f"/zones/{zone.id}/thermostat",
        data={"mode_id": str(fremder.id), "direction": "up"},
        headers=_csrf(client),
    )
    assert response.status_code == 404


def test_overriding_into_the_negative_range(session: Session, client_als) -> None:
    """"Under-steering" means: a setpoint below zero.

    At 1 degree the system still heats as soon as it gets colder. Anyone who
    only wants to monitor a garage or shed and not temper it needs a value that
    the room temperature never falls below.
    """
    from sqlalchemy import select

    from thermoctl.db.models.override import ZoneOverride

    zone = _grundlage(session)
    client = client_als([("override.create", None), ("zone.read", None)])
    response = client.post(
        f"/zones/{zone.id}/override",
        data={"temperature_c": "-5", "end": "permanent"},
        headers=_csrf(client),
        follow_redirects=False,
    )
    assert response.status_code == 303
    entry = session.scalars(
        select(ZoneOverride).where(ZoneOverride.zone_id == zone.id)
    ).one()
    assert entry.temperature_c == Decimal("-5.0")


def test_the_thermostat_goes_below_zero(session: Session, client_als) -> None:
    """Counter-check from the other side: the step buttons are also allowed to go
    below zero."""
    from sqlalchemy import select

    from thermoctl.db.models.zone import ZoneSetpoint

    # The starting value is set directly, not via `change_setpoints`: that would
    # write an audit entry, and its foreign key to the user is actually enforced
    # under MariaDB -- under SQLite a made-up id would not have been noticed.
    zone, mode = _zone_with_mode(session, "0.0")
    client = client_als([("zone.read", zone.id), ("setpoint.write", zone.id)])
    client.post(
        f"/zones/{zone.id}/thermostat",
        data={"mode_id": str(mode.id), "direction": "down"},
        headers=_csrf(client),
    )
    row = session.scalars(
        select(ZoneSetpoint).where(ZoneSetpoint.setpoint_mode_id == mode.id)
    ).one()
    assert row.temperature_c == Decimal("-0.5")


def test_a_thermostat_request_with_a_nonsensical_mode_is_a_bad_request(
    angemeldeter_client: TestClient, session: Session
) -> None:
    """Both fields come from a form, and a form can be replayed by hand.

    Neither may end in a stack trace: an unparsable mode and an unknown direction are
    caller errors, and the answer has to say so rather than turning into a 500.
    """
    zone = create_zone(session, "krummzone")
    create_settings(session)
    session.flush()
    head = _csrf(angemeldeter_client)

    kein_modus = angemeldeter_client.post(
        f"/zones/{zone.id}/thermostat",
        data={"mode_id": "kein Modus", "direction": "up"},
        headers=head,
        follow_redirects=False,
    )
    assert kein_modus.status_code == 400

    mode = create_mode(session, "tag")
    session.flush()
    falsche_richtung = angemeldeter_client.post(
        f"/zones/{zone.id}/thermostat",
        data={"mode_id": str(mode.id), "direction": "seitwärts"},
        headers=_csrf(angemeldeter_client),
        follow_redirects=False,
    )
    assert falsche_richtung.status_code == 400


# --- Außentemperatur und Fenster-Alarm auf der Startseite -------------------


def test_the_start_page_shows_the_outdoor_temperature(
    angemeldeter_client: TestClient, session: Session
) -> None:
    from thermoctl.db.models.measurement import Measurement

    row = create_settings(session)
    device = Device(
        integration_id=integration(session).id,
        external_id="draussen",
        display_name="draussen",
    )
    session.add(device)
    session.flush()
    session.add(
        DeviceCapabilityLink(
            device_id=device.id, capability_id=capability(session, "temperature").id
        )
    )
    row.outdoor_temperature_source_device_id = device.id
    session.add(
        Measurement(
            device_id=device.id,
            capability_id=capability(session, "temperature").id,
            value_numeric=Decimal("3.2"),
            measured_at=datetime.now(),
            received_at=datetime.now(),
        )
    )
    session.flush()

    response = angemeldeter_client.get("/")
    assert response.status_code == 200
    assert "3,2" in response.text
    assert "Außentemperatur" in response.text


def test_the_start_page_names_no_source_and_stale_distinctly_from_a_value(
    angemeldeter_client: TestClient, session: Session
) -> None:
    """"keine Quelle gewählt" and "Wert veraltet" must read as prose, not as a
    number someone could mistake for a real temperature."""
    create_settings(session)
    response = angemeldeter_client.get("/")
    assert response.status_code == 200
    assert "keine Quelle gewählt" in response.text


def test_the_start_page_shows_a_forgotten_window_chip(
    angemeldeter_client: TestClient, session: Session
) -> None:
    from tests.helpers import sensor_status_of
    from thermoctl.db.models.state import ZoneState

    zone = _grundlage(session)
    session.add(
        ZoneState(
            zone_id=zone.id,
            sensor_status_id=sensor_status_of(session, "keine_quelle").id,
            window_alarm=True,
            updated_at=datetime.now(),
        )
    )
    session.flush()

    response = angemeldeter_client.get("/")
    assert response.status_code == 200
    assert "Fenster vergessen" in response.text
