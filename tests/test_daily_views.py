from datetime import datetime
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.helpers import create_settings, create_zone, source
from thermoctl.auth.csrf import CSRF_HEADER, csrf_token
from thermoctl.auth.sessions import COOKIE_NAME
from thermoctl.config import get_settings
from thermoctl.db.models.override import ZoneOverride
from thermoctl.db.models.state import ShadowDecision


def _grundlage(session: Session):
    source(session, "web")
    source(session, "api")
    create_settings(session)
    return create_zone(session, "wohnzimmer")


def _csrf(client: TestClient) -> dict[str, str]:
    http_session = client.cookies.get(COOKIE_NAME)
    assert http_session is not None
    return {CSRF_HEADER: csrf_token(http_session, get_settings().secret_key.get_secret_value())}


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


def test_parameters_of_a_foreign_zone_yield_404(session: Session, client_als) -> None:
    eigene = _grundlage(session)
    fremde = create_zone(session, "fremd")
    client = client_als([("zone.manage", eigene.id)])
    assert client.get(f"/zones/{fremde.id}/parameters").status_code == 404
    assert (
        client.post(f"/zones/{fremde.id}/parameters", data={}, headers=_csrf(client)).status_code
        == 404
    )


def test_an_override_from_the_interface_uses_the_same_data_model_as_rest(
    session: Session, client_als
) -> None:
    zone = _grundlage(session)
    client = client_als([("zone.read", zone.id), ("override.create", zone.id)])

    response = client.post(
        f"/zones/{zone.id}/override",
        data={"temperature_c": "21.5", "end": "dauerhaft"},
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
        data={"temperature_c": "22", "end": "dauerhaft"},
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
    assert "Uebersteuerung (feste Temperatur)" in page.text

    response = client.post(
        f"/zones/{zone.id}/override/cancel",
        headers=_csrf(client),
        follow_redirects=False,
    )
    assert response.status_code == 303
    entry = session.scalar(select(ZoneOverride).where(ZoneOverride.zone_id == zone.id))
    assert entry is not None and entry.cancelled_at is not None


def test_an_override_on_a_foreign_zone_yields_404(session: Session, client_als) -> None:
    eigene = _grundlage(session)
    fremde = create_zone(session, "fremd")
    client = client_als(
        [("zone.read", eigene.id), ("override.create", eigene.id), ("override.cancel", eigene.id)]
    )
    assert (
        client.post(
            f"/zones/{fremde.id}/override",
            data={"temperature_c": "20", "end": "dauerhaft"},
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


def test_an_override_until_the_next_switch(session: Session, client_als) -> None:
    """The end is computed and stored when creating it, not remembered as a rule —
    a later schedule change does not shift an override already in progress."""
    from tests.helpers import create_mode
    from thermoctl.db.models.schedule import SchedulePoint

    zone = _grundlage(session)
    mode = create_mode(session, "tag-uebersteuerung", "Tag")
    session.add(
        SchedulePoint(zone_id=zone.id, weekday=1, minute_of_day=360, setpoint_mode_id=mode.id)
    )
    session.flush()
    client = client_als([("override.create", None), ("zone.read", None)])
    response = client.post(
        f"/zones/{zone.id}/override",
        data={"temperature_c": "21,5", "end": "naechste_schaltung"},
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
        data={"temperature_c": "20.0", "end": "dauer", "duration_minutes": "120"},
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
        data={"temperature_c": "20.0", "end": "naechste_schaltung"},
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
        {"temperature_c": "warm", "end": "dauerhaft"},
        # The lower bound is -20 degrees: a setpoint in the negative range means
        # "no heating here". Below that there is no longer a real intent, only a
        # typo.
        {"temperature_c": "-30", "end": "dauerhaft"},
        {"temperature_c": "50", "end": "dauerhaft"},
        {"temperature_c": "20", "end": "dauer", "duration_minutes": "0"},
        {"temperature_c": "20", "end": "dauer", "duration_minutes": "keine Zahl"},
        {"temperature_c": "20", "end": "irgendwas"},
    ):
        response = client.post(
            f"/zones/{zone.id}/override", data=data,
            headers=_csrf(client), follow_redirects=False,
        )
        assert response.status_code == 303, data
        assert "uebersteuerungsfehler" in (response.headers.get("location") or ""), data
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
        data={"temperature_c": "21,55", "end": "dauerhaft"},
        headers=_csrf(client), follow_redirects=False,
    )
    assert response.status_code == 303
    assert "uebersteuerungsfehler" in (response.headers.get("location") or "")
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
        data={"mode_id": str(mode.id), "direction": "hoch"},
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
            data={"mode_id": str(mode.id), "direction": "runter"},
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
        data={"mode_id": str(mode.id), "direction": "hoch"},
        headers=_csrf(client),
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "thermostatfehler" in response.headers["location"]
    row = session.scalars(
        select(ZoneSetpoint).where(ZoneSetpoint.setpoint_mode_id == mode.id)
    ).one()
    assert row.temperature_c == Decimal("35.0")


def test_thermostat_braucht_setpoint_write(session: Session, client_als) -> None:
    zone, mode = _zone_with_mode(session)
    client = client_als([("zone.read", zone.id)])
    response = client.post(
        f"/zones/{zone.id}/thermostat",
        data={"mode_id": str(mode.id), "direction": "hoch"},
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
    angezeigt = resolved_setpoint(session, zone, utcnow())
    assert angezeigt.mode_id is not None

    client = client_als([("zone.read", zone.id), ("setpoint.write", zone.id)])
    response = client.post(
        f"/zones/{zone.id}/thermostat",
        data={"mode_id": str(angezeigt.mode_id), "direction": "hoch"},
        headers=_csrf(client),
        follow_redirects=False,
    )
    assert response.status_code == 303

    row = session.scalars(
        select(ZoneSetpoint).where(
            ZoneSetpoint.zone_id == zone.id,
            ZoneSetpoint.setpoint_mode_id == angezeigt.mode_id,
        )
    ).one()
    assert row.temperature_c == angezeigt.temperature_c + Decimal("0.5")


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
        data={"mode_id": str(fremder.id), "direction": "hoch"},
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
        data={"temperature_c": "-5", "end": "dauerhaft"},
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

    # The starting value is set directly, not via `sollwerte_aendern`: that would
    # write an audit entry, and its foreign key to the user is actually enforced
    # under MariaDB -- under SQLite a made-up id would not have been noticed.
    zone, mode = _zone_with_mode(session, "0.0")
    client = client_als([("zone.read", zone.id), ("setpoint.write", zone.id)])
    client.post(
        f"/zones/{zone.id}/thermostat",
        data={"mode_id": str(mode.id), "direction": "runter"},
        headers=_csrf(client),
    )
    row = session.scalars(
        select(ZoneSetpoint).where(ZoneSetpoint.setpoint_mode_id == mode.id)
    ).one()
    assert row.temperature_c == Decimal("-0.5")
