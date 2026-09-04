from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.helpers import create_mode, create_settings, create_zone, source
from thermoctl.auth.csrf import csrf_token
from thermoctl.auth.sessions import COOKIE_NAME
from thermoctl.config import get_settings
from thermoctl.db.models.operations import AuditEvent
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.zone import SetpointMode, ZoneSetpoint


def _csrf(client: TestClient) -> dict[str, str]:
    secret = client.cookies[COOKIE_NAME]
    token = csrf_token(secret, get_settings().secret_key.get_secret_value())
    return {"X-CSRF-Token": token}


def test_modusliste_braucht_mode_manage(client_als) -> None:
    assert client_als([("zone.read", None)]).get("/modes").status_code == 403
    assert client_als([("mode.manage", None)]).get("/modes").status_code == 200


def test_the_new_mode_form_is_shown(client_als) -> None:
    response = client_als([("mode.manage", None)]).get("/modes/new")
    assert response.status_code == 200
    assert "Technischer Code" in response.text


def test_a_mode_is_created_and_audited(client_als, session: Session) -> None:
    source(session)
    client = client_als([("mode.manage", None)])
    response = client.post(
        "/modes",
        data={"code": "urlaub", "name": "Urlaub", "sort_order": "30"},
        headers=_csrf(client),
        follow_redirects=False,
    )

    mode = session.scalar(select(SetpointMode).where(SetpointMode.code == "urlaub"))
    assert response.status_code == 303
    assert mode is not None and mode.name == "Urlaub" and mode.sort_order == 30
    assert session.scalar(
        select(AuditEvent).where(
            AuditEvent.object_type == "setpoint_mode", AuditEvent.action == "create"
        )
    ) is not None


def test_a_duplicate_code_returns_to_the_form_with_its_value(
    client_als, session: Session
) -> None:
    create_mode(session, "tag", "Tag")
    client = client_als([("mode.manage", None)])
    response = client.post(
        "/modes",
        data={"code": "tag", "name": "Mein Tag", "sort_order": "0"},
        headers=_csrf(client),
    )

    assert response.status_code == 200
    assert "bereits vergeben" in response.text
    assert 'value="Mein Tag"' in response.text


def test_the_edit_mode_form_shows_the_values(client_als, session: Session) -> None:
    mode = create_mode(session, "nacht", "Nacht")
    response = client_als([("mode.manage", None)]).get(f"/modes/{mode.id}")
    assert response.status_code == 200
    assert 'value="nacht"' in response.text


def test_a_mode_is_updated(client_als, session: Session) -> None:
    source(session)
    mode = create_mode(session, "nacht", "Nacht")
    client = client_als([("mode.manage", None)])
    response = client.post(
        f"/modes/{mode.id}",
        data={"code": "abend", "name": "Abend", "sort_order": "20"},
        headers=_csrf(client),
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert (mode.code, mode.name, mode.sort_order) == ("abend", "Abend", 20)
    assert session.scalar(
        select(AuditEvent).where(
            AuditEvent.object_id == str(mode.id), AuditEvent.action == "update"
        )
    ) is not None


def test_the_delete_form_for_an_unused_mode(client_als, session: Session) -> None:
    mode = create_mode(session, "urlaub", "Urlaub")
    response = client_als([("mode.manage", None)]).get(f"/modes/{mode.id}/delete")
    assert response.status_code == 200
    assert "wirklich gelöscht" in response.text


def test_an_unused_mode_is_deleted(client_als, session: Session) -> None:
    source(session)
    mode = create_mode(session, "urlaub", "Urlaub")
    mode_id = mode.id
    client = client_als([("mode.manage", None)])
    response = client.post(
        f"/modes/{mode_id}/delete", headers=_csrf(client), follow_redirects=False
    )

    assert response.status_code == 303
    assert session.get(SetpointMode, mode_id) is None
    assert session.scalar(
        select(AuditEvent).where(
            AuditEvent.object_id == str(mode_id), AuditEvent.action == "delete"
        )
    ) is not None


def test_a_builtin_mode_cannot_be_deleted_and_says_why(
    client_als, session: Session
) -> None:
    mode = create_mode(session, "tag", "Tag")
    mode.is_builtin = True
    client = client_als([("mode.manage", None)])

    form = client.get(f"/modes/{mode.id}/delete")
    response = client.post(f"/modes/{mode.id}/delete", headers=_csrf(client))

    assert "weil die Anwendung sie benötigt" in form.text
    assert "weil die Anwendung sie benötigt" in response.text
    assert session.get(SetpointMode, mode.id) is mode


def test_the_frost_protection_mode_cannot_be_deleted_and_says_why(
    client_als, session: Session
) -> None:
    settings = create_settings(session)
    client = client_als([("mode.manage", None)])
    response = client.get(f"/modes/{settings.frost_protection_mode_id}/delete")

    assert response.status_code == 200
    assert (
        "Der Frostschutzmodus kann nicht gelöscht werden — er ist die Rückfallebene, "
        "wenn ein Sensor ausfällt."
    ) in response.text


def test_the_setpoint_form_shows_every_mode_and_the_help_text(client_als, session: Session) -> None:
    zone = create_zone(session, "bad")
    create_mode(session, "tag", "Tag")
    create_mode(session, "nacht", "Nacht")
    response = client_als([("setpoint.write", zone.id)]).get(
        f"/zones/{zone.id}/setpoints"
    )

    assert response.status_code == 200
    assert "Tag (°C)" in response.text and "Nacht (°C)" in response.text
    assert "Leer lassen löscht den Sollwert" in response.text


def test_setpoints_are_stored_as_decimal_and_audited(
    client_als, session: Session
) -> None:
    source(session)
    zone = create_zone(session, "bad")
    mode = create_mode(session, "tag", "Tag")
    client = client_als([("setpoint.write", zone.id)])
    response = client.post(
        f"/zones/{zone.id}/setpoints",
        data={f"setpoint_{mode.id}": "21.5"},
        headers=_csrf(client),
        follow_redirects=False,
    )

    setpoint = session.get(ZoneSetpoint, (zone.id, mode.id))
    assert response.status_code == 303
    assert setpoint is not None and setpoint.temperature_c == Decimal("21.5")
    assert session.scalar(
        select(AuditEvent).where(
            AuditEvent.object_type == "zone_setpoint",
            AuditEvent.object_id == str(zone.id),
        )
    ) is not None


def test_a_setpoint_outside_the_limits_is_refused_at_the_field(
    client_als, session: Session
) -> None:
    zone = create_zone(session, "bad")
    mode = create_mode(session, "tag", "Tag")
    client = client_als([("setpoint.write", zone.id)])
    response = client.post(
        f"/zones/{zone.id}/setpoints",
        data={f"setpoint_{mode.id}": "36.0"},
        headers=_csrf(client),
    )

    assert response.status_code == 200
    assert "zwischen -20,0 und 35,0 °C" in response.text
    assert 'value="36.0"' in response.text
    assert session.get(ZoneSetpoint, (zone.id, mode.id)) is None


def test_a_setpoint_with_two_decimal_places_is_refused(
    client_als, session: Session
) -> None:
    zone = create_zone(session, "bad")
    mode = create_mode(session, "tag", "Tag")
    client = client_als([("setpoint.write", zone.id)])
    response = client.post(
        f"/zones/{zone.id}/setpoints",
        data={f"setpoint_{mode.id}": "21.25"},
        headers=_csrf(client),
    )

    assert response.status_code == 200
    assert "höchstens eine Nachkommastelle" in response.text
    assert session.get(ZoneSetpoint, (zone.id, mode.id)) is None


def test_an_empty_setpoint_field_deletes_the_row(client_als, session: Session) -> None:
    source(session)
    zone = create_zone(session, "bad")
    mode = create_mode(session, "tag", "Tag")
    session.add(
        ZoneSetpoint(zone_id=zone.id, setpoint_mode_id=mode.id, temperature_c=Decimal("20.0"))
    )
    session.flush()
    client = client_als([("setpoint.write", zone.id)])
    response = client.post(
        f"/zones/{zone.id}/setpoints",
        data={f"setpoint_{mode.id}": ""},
        headers=_csrf(client),
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert session.get(ZoneSetpoint, (zone.id, mode.id)) is None


def test_fremde_zone_ergibt_404(client_als, session: Session) -> None:
    eigene = create_zone(session, "bad")
    fremde = create_zone(session, "küche")
    client = client_als([("setpoint.write", eigene.id)])

    assert client.get(f"/zones/{fremde.id}/setpoints").status_code == 404
    assert (
        client.post(
            f"/zones/{fremde.id}/setpoints", data={}, headers=_csrf(client)
        ).status_code
        == 404
    )


def test_a_mode_in_use_cannot_be_deleted(client_als, session: Session) -> None:
    """The third deletion guard: a mode that a schedule points to does not disappear.

    Without it, deleting the mode would tear apart the schedule of every zone that
    uses it -- silently, because the foreign key would only surface at the next
    control cycle.
    """
    create_settings(session)
    source(session, "web")
    urlaub = create_mode(session, "urlaub", "Urlaub")
    zone = create_zone(session, "zone-mit-plan")
    session.add(
        SchedulePoint(
            zone_id=zone.id, weekday=1, minute_of_day=360, setpoint_mode_id=urlaub.id
        )
    )
    session.flush()

    client = client_als([("mode.manage", None)])
    response = client.get(f"/modes/{urlaub.id}/delete")
    assert response.status_code == 200
    assert "Zeitpläne oder historische" in response.text

    response = client.post(f"/modes/{urlaub.id}/delete", headers=_csrf(client))
    assert response.status_code == 200
    assert session.get(SetpointMode, urlaub.id) is not None


def test_unbekannter_modus_ergibt_404(client_als) -> None:
    client = client_als([("mode.manage", None)])
    assert client.get("/modes/999999").status_code == 404
    assert client.get("/modes/999999/delete").status_code == 404


def test_empty_and_overlong_mode_values_stay_in_the_form(
    client_als, session: Session
) -> None:
    source(session, "web")
    client = client_als([("mode.manage", None)])
    fälle = [
        ({"code": "  ", "name": "Name", "sort_order": "0"}, "Code darf nicht leer"),
        ({"code": "c" * 33, "name": "Name", "sort_order": "0"}, "höchstens 32"),
        ({"code": "gut", "name": "  ", "sort_order": "0"}, "Name darf nicht leer"),
        ({"code": "gut", "name": "n" * 65, "sort_order": "0"}, "höchstens 64"),
        ({"code": "gut", "name": "Name", "sort_order": "keine Zahl"}, "ganze Zahl"),
    ]
    for data, expected in fälle:
        response = client.post("/modes", data=data, headers=_csrf(client))
        assert response.status_code == 200, data
        assert expected in response.text, data
    assert session.scalar(select(SetpointMode).where(SetpointMode.code == "gut")) is None


def test_an_existing_setpoint_is_updated(client_als, session: Session) -> None:
    """The third case besides create and delete -- untested until now."""
    create_settings(session)
    source(session, "web")
    day = create_mode(session, "tag-ändern", "Tag")
    zone = create_zone(session, "zone-sollwert-ändern")
    client = client_als([("setpoint.write", None), ("zone.read", None)])
    client.post(
        f"/zones/{zone.id}/setpoints", data={f"setpoint_{day.id}": "20.0"},
        headers=_csrf(client),
    )
    client.post(
        f"/zones/{zone.id}/setpoints", data={f"setpoint_{day.id}": "22.5"},
        headers=_csrf(client),
    )
    rows = session.scalars(
        select(ZoneSetpoint).where(
            ZoneSetpoint.zone_id == zone.id, ZoneSetpoint.setpoint_mode_id == day.id
        )
    ).all()
    assert len(rows) == 1
    assert rows[0].temperature_c == Decimal("22.5")


def test_a_non_numeric_setpoint_stays_in_the_form(client_als, session: Session) -> None:
    create_settings(session)
    source(session, "web")
    day = create_mode(session, "tag-keine-zahl", "Tag")
    zone = create_zone(session, "zone-keine-zahl")
    client = client_als([("setpoint.write", None), ("zone.read", None)])
    response = client.post(
        f"/zones/{zone.id}/setpoints", data={f"setpoint_{day.id}": "warm"},
        headers=_csrf(client),
    )
    assert response.status_code == 200
    assert "muss eine Zahl sein" in response.text
    assert session.scalar(select(ZoneSetpoint).where(ZoneSetpoint.zone_id == zone.id)) is None


def test_an_infinite_setpoint_is_refused(client_als, session: Session) -> None:
    """`Decimal("nan")` and `Decimal("Infinity")` are valid decimal numbers.

    Without the finiteness check, such a value would run all the way to the database
    and from there into the control decision -- every comparison with NaN is false,
    so the zone would never heat and never switch off.
    """
    create_settings(session)
    source(session, "web")
    day = create_mode(session, "tag-unendlich", "Tag")
    zone = create_zone(session, "zone-unendlich")
    client = client_als([("setpoint.write", None), ("zone.read", None)])
    for value in ("nan", "Infinity"):
        response = client.post(
            f"/zones/{zone.id}/setpoints", data={f"setpoint_{day.id}": value},
            headers=_csrf(client),
        )
        assert response.status_code == 200, value
        assert "endliche Zahl" in response.text, value
    assert session.scalar(select(ZoneSetpoint).where(ZoneSetpoint.zone_id == zone.id)) is None


def test_updating_a_mode_with_an_invalid_value_stays_in_the_form(
    client_als, session: Session
) -> None:
    source(session, "web")
    mode = create_mode(session, "änderbar", "Änderbar")
    client = client_als([("mode.manage", None)])
    response = client.post(
        f"/modes/{mode.id}",
        data={"code": "  ", "name": "Neuer Name", "sort_order": "0"},
        headers=_csrf(client),
    )
    assert response.status_code == 200
    assert "Code darf nicht leer" in response.text
    assert mode.code == "änderbar"


def test_the_setpoint_limit_exists_in_exactly_one_place() -> None:
    """The limit has already stood differently in three places at once.

    Back then the UI checked by hand with no decimal places, the REST interface
    checked via its schema, and the MCP server did not check at all. It has lived
    in the domain ever since -- but a copied-down number sneaks back in easily:
    when moving from 5 to 1 degree it still appeared once more in `alltag_views.py`,
    in the discovery payload, and in the form's markup.

    The test therefore looks for bare limit values outside the domain. It is
    coarse -- a 5 in a line about hysteresis means something else -- so it only
    looks for the pattern in which a temperature limit occurs.

    **Also in the templates.** Its first version only looked at Python files and
    missed `sollwerte.html`, where `min="5"` and `max="35"` appeared as strings.
    That surfaced only at the next move of the limit -- exactly the moment the
    guard should have caught it.
    """
    import re
    from pathlib import Path

    wurzel = Path(__file__).resolve().parent.parent / "thermoctl"
    match = []

    # Templates: a page that asks for temperatures must not contain a bare limit --
    # neither as `min="5"` nor as the argument `"35"` to `number_field`. Such a page is
    # recognized by the degree sign.
    # Two classes: the upper limit and the new lower limit are unambiguous as a
    # number -- a `35` or `-20` in quotes is never anything else here. The old lower
    # limits `5` and `1`, however, also stand for minutes or sort order; they only
    # count if the line itself talks about temperature. That is exactly where the
    # first version failed: it flagged the override's minute field.
    eindeutig = {"35", "35.0", "-20", "-20.0"}
    mehrdeutig = {"5", "5.0", "1", "1.0"}
    zahl_in_anfuehrung = re.compile(r"""["'](-?\d{1,2}(?:\.\d)?)["']""")
    for file in sorted(wurzel.parent.rglob("web/templates/*.html")):
        text = file.read_text(encoding="utf-8")
        if "°C" not in text:
            continue
        for nummer, row in enumerate(text.splitlines(), 1):
            if "temperatur" in row.lower():
                continue  # refers to the passed-through constants
            found = set(zahl_in_anfuehrung.findall(row))
            above_temperature = "°C" in row or "sollwert" in row.lower()
            if found & eindeutig or (above_temperature and found & mehrdeutig):
                match.append(f"{file.name}:{nummer}: {row.strip()}")

    # Python: a number at a spot where a temperature limit stands. The context is
    # often in the line before (`temperature_c: Decimal = Field(` wraps), hence a
    # small window.
    grenzstelle = re.compile(
        r"""(?:ge=|le=|min_temp["']?\s*:\s*|max_temp["']?\s*:\s*)"""
        r"""(?:Decimal\(["'])?-?\d+(?:\.\d+)?"""
    )
    for file in sorted(wurzel.rglob("*.py")):
        if file.name == "modes.py":
            continue  # that is where it belongs
        rows = file.read_text(encoding="utf-8").splitlines()
        for nummer, row in enumerate(rows, 1):
            if "MINIMUM_TEMPERATURE_C" in row or "MAXIMUM_TEMPERATURE_C" in row:
                continue  # refers to the constants
            if not grenzstelle.search(row):
                continue
            # Narrowed to the setpoint field: a bare "temp" in the surroundings also
            # matched `sensor_timeout_seconds` next to `temperature_offset_k` --
            # both temperature-adjacent with entirely different limits.
            umfeld = " ".join(rows[max(0, nummer - 3) : nummer + 1])
            if "temperature_c" in umfeld or "min_temp" in umfeld or "max_temp" in umfeld:
                match.append(f"{file.relative_to(wurzel)}:{nummer}: {row.strip()}")

    assert not match, "setpoint limit outside the domain:\n" + "\n".join(match)


def test_the_message_names_the_limit_that_applies() -> None:
    """It is built from the constants, not copied down -- otherwise, after the next
    move, it would name a number that no longer applies."""
    import pytest as _pytest

    from thermoctl.domain.modes import (
        MAXIMUM_TEMPERATURE_C,
        MINIMUM_TEMPERATURE_C,
        DomainError,
        check_temperature,
    )

    with _pytest.raises(DomainError) as errors:
        check_temperature(MINIMUM_TEMPERATURE_C - Decimal("0.1"))
    notice = errors.value.notice
    assert f"{MINIMUM_TEMPERATURE_C:.1f}".replace(".", ",") in notice
    assert f"{MAXIMUM_TEMPERATURE_C:.1f}".replace(".", ",") in notice
