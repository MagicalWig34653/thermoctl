"""Die Wohnungssicht -- und vor allem: was sie **nicht** zeigt.

Der Kern dieser Datei ist die Zonenisolation. Ein Mieter mit Zugriff auf einen Raum
darf einen fremden Raum nirgends sehen: nicht als Name, nicht als Id, nicht in einer
Auswahlliste, nicht in einem versteckten Feld, nicht in einer Fehlermeldung. Die
Tests prüfen deshalb durchgehend auf **beides** -- Anzeigename und Id -- im gesamten
gerenderten HTML, statt auf einzelne Stellen, an denen man es vermuten würde.

Die zweite Zusicherung: was die Oberfläche rechnet, rechnet in Wahrheit die Domäne.
Sollwertschritt, Vorschau, nächste Schaltzeit und Heizzeit werden deshalb gegen die
Domänenfunktionen verglichen, nicht gegen erwartete Zeichenketten.
"""

from collections.abc import Callable
from datetime import timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.helpers import (
    create_mode,
    create_settings,
    create_zone,
    create_zone_state,
    source,
)
from thermoctl.auth.csrf import csrf_token
from thermoctl.auth.sessions import COOKIE_NAME
from thermoctl.config import get_settings
from thermoctl.db.base import utcnow
from thermoctl.db.models.override import ZoneOverride
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.zone import SetpointMode, Zone, ZoneSetpoint
from thermoctl.domain.schedule import next_switch, schedule_forecast

Client = Callable[[list[tuple[str, int | None]]], TestClient]

MORNING = 6 * 60 + 30
NIGHT = 23 * 60


@pytest.fixture(autouse=True)
def _audit_source(session: Session) -> None:
    source(session, "web")


def _csrf(client: TestClient) -> dict[str, str]:
    secret = client.cookies[COOKIE_NAME]
    return {"X-CSRF-Token": csrf_token(secret, get_settings().secret_key.get_secret_value())}


def _wohnung(session: Session) -> tuple[Zone, Zone]:
    """Ein eigener Raum und ein fremder -- der fremde trägt einen Namen, der in
    keinem gerenderten HTML auftauchen darf."""
    create_settings(session)
    mine = create_zone(session, "wohnzimmer")
    mine.display_name = "Wohnzimmer"
    theirs = create_zone(session, "fremdes-schlafzimmer")
    theirs.display_name = "Nachbars Schlafzimmer"
    comfort = create_mode(session, "tag", "Komfort")
    night = create_mode(session, "nacht", "Nacht")
    for zone in (mine, theirs):
        session.add(
            ZoneSetpoint(zone_id=zone.id, setpoint_mode_id=comfort.id,
                         temperature_c=Decimal("21.0"))
        )
        session.add(
            ZoneSetpoint(zone_id=zone.id, setpoint_mode_id=night.id,
                         temperature_c=Decimal("18.0"))
        )
        for weekday in range(1, 8):
            session.add(SchedulePoint(zone_id=zone.id, weekday=weekday,
                                      minute_of_day=MORNING, setpoint_mode_id=comfort.id))
            session.add(SchedulePoint(zone_id=zone.id, weekday=weekday,
                                      minute_of_day=NIGHT, setpoint_mode_id=night.id))
        create_zone_state(session, zone)
    session.flush()
    return mine, theirs


def _tenant(tenant_client: Client, zone: Zone, extra: list[str] | None = None) -> TestClient:
    rights: list[tuple[str, int | None]] = [("zone.read", zone.id)]
    for code in extra or []:
        rights.append((code, zone.id))
    return tenant_client(rights)


# -- Zonenisolation -----------------------------------------------------------


@pytest.mark.parametrize("path", ["/", "/schedule", "/heating-time"])
def test_no_page_ever_mentions_a_foreign_room(
    tenant_client: Client, session: Session, path: str
) -> None:
    mine, theirs = _wohnung(session)
    client = _tenant(tenant_client, mine, ["setpoint.write", "override.create",
                                           "override.cancel", "schedule.manage"])
    page = client.get(path)
    assert page.status_code == 200
    assert mine.display_name in page.text
    assert theirs.display_name not in page.text
    # Auch die Id nicht -- sie stünde sonst in einem versteckten Feld, einer
    # Auswahloption oder einer Adresse, ohne dass der Name irgendwo sichtbar wäre.
    # Geprüft werden die Stellen, an denen in diesen Vorlagen überhaupt eine
    # Zonen-Id steht; ein nacktes `value="3"` wäre kein brauchbares Muster, weil
    # dieselbe Zahl auch eine Modus-Id sein kann.
    for pattern in (
        f'name="zone_id" value="{theirs.id}"',
        f'name="source_id" value="{theirs.id}"',
        f"zone={theirs.id}",
        f"/zones/{theirs.id}/",
        f'<option value="{theirs.id}"',
    ):
        assert pattern not in page.text, pattern


def test_the_schedule_of_a_foreign_room_cannot_be_opened(
    tenant_client: Client, session: Session
) -> None:
    mine, theirs = _wohnung(session)
    client = _tenant(tenant_client, mine, ["schedule.manage"])
    assert client.get(f"/schedule?zone={theirs.id}").status_code == 404


def test_a_forged_zone_id_in_a_form_changes_nothing(
    tenant_client: Client, session: Session
) -> None:
    """Der eigentliche Angriff: die sichtbare Auswahl umgehen und die fremde Id von
    Hand einsetzen. Der Server prüft jede Id erneut gegen `visible_zones`."""
    mine, theirs = _wohnung(session)
    before = [
        (point.weekday, point.minute_of_day)
        for point in session.scalars(
            select(SchedulePoint).where(SchedulePoint.zone_id == theirs.id)
        )
    ]
    client = _tenant(tenant_client, mine, ["schedule.manage"])
    response = client.post(
        "/schedule/copy-day",
        data={"zone_id": str(theirs.id), "weekday": "1", "scope": "workdays"},
        headers=_csrf(client),
    )
    assert response.status_code == 404
    after = [
        (point.weekday, point.minute_of_day)
        for point in session.scalars(
            select(SchedulePoint).where(SchedulePoint.zone_id == theirs.id)
        )
    ]
    assert after == before


def test_a_foreign_room_cannot_be_the_source_of_an_adoption(
    tenant_client: Client, session: Session
) -> None:
    """Auch die *Quelle* wird geprüft -- sonst ließe sich der Zeitplan eines fremden
    Raums in den eigenen kopieren und damit auslesen."""
    mine, theirs = _wohnung(session)
    second = create_zone(session, "bad")
    second.display_name = "Bad"
    session.flush()
    client = tenant_client(
        [("zone.read", mine.id), ("zone.read", second.id),
         ("schedule.manage", mine.id), ("schedule.manage", second.id)]
    )
    response = client.post(
        "/schedule/adopt",
        data={"zone_id": str(mine.id), "source_id": str(theirs.id)},
        headers=_csrf(client),
    )
    assert response.status_code == 404


def test_an_unparseable_zone_id_is_not_found_either(
    tenant_client: Client, session: Session
) -> None:
    mine, _theirs = _wohnung(session)
    client = _tenant(tenant_client, mine, ["schedule.manage"])
    assert client.get("/schedule?zone=keine-zahl").status_code == 404
    response = client.post(
        "/schedule/day", data={"zone_id": "keine-zahl"}, headers=_csrf(client)
    )
    assert response.status_code == 404


# -- Das Profil ---------------------------------------------------------------


def test_an_administrator_does_not_reach_the_tenant_pages(
    client_als: Client, session: Session
) -> None:
    """Die Wohnungsseiten sind für das Mieterprofil gebaut; ein Administrator hat
    für dieselben Fragen die Anlagenseiten."""
    mine, _theirs = _wohnung(session)
    client = client_als([("zone.read", None), ("schedule.manage", None)])
    assert client.get("/schedule").status_code == 403
    assert client.get("/heating-time").status_code == 403


def test_the_administrator_start_page_stays_the_plant_view(
    client_als: Client, session: Session
) -> None:
    _mine, _theirs = _wohnung(session)
    page = client_als([("zone.read", None)]).get("/")
    assert page.status_code == 200
    assert "tc-sidenav" in page.text
    assert "Angenehm zuhause" not in page.text


def test_the_tenant_start_page_shows_no_plant_internals(
    tenant_client: Client, session: Session
) -> None:
    """Der Punkt der ganzen Trennung: ein Mieter bekommt keine Anlagenbegriffe zu
    sehen, auch nicht beiläufig in einem Banner."""
    mine, _theirs = _wohnung(session)
    page = _tenant(tenant_client, mine).get("/").text
    for word in ("MQTT", "Broker", "Aktorfreigabe", "Trockenlauf", "Hysterese",
                 "Relais", "Bereitschaft", "Schattenentscheidung"):
        assert word not in page, word


def test_a_tenant_without_any_room_gets_a_readable_page_not_an_error(
    tenant_client: Client, session: Session
) -> None:
    create_settings(session)
    client = tenant_client([])
    assert client.get("/").status_code == 200
    assert "noch kein Raum freigegeben" in client.get("/schedule").text
    assert client.get("/heating-time").status_code == 200


# -- Sollwert, Übersteuerung, Sprung ------------------------------------------


def _stored(session: Session, zone: Zone, mode_code: str) -> Decimal:
    mode = session.scalars(select(SetpointMode).where(SetpointMode.code == mode_code)).one()
    return session.scalars(
        select(ZoneSetpoint.temperature_c).where(
            ZoneSetpoint.zone_id == zone.id, ZoneSetpoint.setpoint_mode_id == mode.id
        )
    ).one()


def test_two_quick_steps_are_two_steps(
    tenant_client: Client, session: Session
) -> None:
    """Der Schritt wird serverseitig gegen den **aktuellen** Wert gerechnet. Würde
    der Browser den neuen Wert mitschicken, ergäben zwei Klicks ohne
    zwischenzeitliches Neuladen nur einen Schritt."""
    mine, _theirs = _wohnung(session)
    client = _tenant(tenant_client, mine, ["setpoint.write"])
    mode = session.scalars(select(SetpointMode).where(SetpointMode.code == "tag")).one()
    before = _stored(session, mine, "tag")
    for _ in range(2):
        client.post(
            f"/zones/{mine.id}/thermostat",
            data={"mode_id": str(mode.id), "direction": "up"},
            headers=_csrf(client), follow_redirects=False,
        )
    assert _stored(session, mine, "tag") == before + Decimal("1.0")


def test_the_step_stops_at_the_domain_limit_and_says_so(
    tenant_client: Client, session: Session
) -> None:
    from thermoctl.domain.modes import MAXIMUM_TEMPERATURE_C

    mine, _theirs = _wohnung(session)
    mode = session.scalars(select(SetpointMode).where(SetpointMode.code == "tag")).one()
    session.execute(
        ZoneSetpoint.__table__.update()
        .where(ZoneSetpoint.zone_id == mine.id, ZoneSetpoint.setpoint_mode_id == mode.id)
        .values(temperature_c=MAXIMUM_TEMPERATURE_C)
    )
    session.flush()
    client = _tenant(tenant_client, mine, ["setpoint.write"])
    response = client.post(
        f"/zones/{mine.id}/thermostat",
        data={"mode_id": str(mode.id), "direction": "up"},
        headers=_csrf(client), follow_redirects=False,
    )
    assert response.status_code == 303
    assert "thermostat_errors" in response.headers["location"]
    assert _stored(session, mine, "tag") == MAXIMUM_TEMPERATURE_C


@pytest.mark.parametrize(
    ("data", "expect_end"),
    [
        ({"end": "duration", "duration_minutes": "30"}, True),
        ({"end": "duration", "duration_minutes": "60"}, True),
        ({"end": "duration", "duration_minutes": "120"}, True),
        ({"end": "next_switch"}, True),
    ],
)
def test_every_offered_duration_creates_a_running_override(
    tenant_client: Client, session: Session, data: dict[str, str], expect_end: bool
) -> None:
    mine, _theirs = _wohnung(session)
    client = _tenant(tenant_client, mine, ["override.create"])
    client.post(
        f"/zones/{mine.id}/override",
        data={"temperature_c": "22.5", **data},
        headers=_csrf(client), follow_redirects=False,
    )
    entry = session.scalars(
        select(ZoneOverride).where(ZoneOverride.zone_id == mine.id)
    ).one()
    assert entry.temperature_c == Decimal("22.5")
    assert (entry.ends_at is not None) is expect_end


def test_a_running_override_is_shown_and_can_be_ended(
    tenant_client: Client, session: Session
) -> None:
    mine, _theirs = _wohnung(session)
    client = _tenant(tenant_client, mine, ["override.create", "override.cancel"])
    client.post(
        f"/zones/{mine.id}/override",
        data={"temperature_c": "23.0", "end": "duration", "duration_minutes": "60"},
        headers=_csrf(client), follow_redirects=False,
    )
    page = client.get("/").text
    assert "23,0 °C" in page
    assert "Danach gilt wieder der Zeitplan" in page

    client.post(
        f"/zones/{mine.id}/override/cancel", headers=_csrf(client), follow_redirects=False
    )
    entry = session.scalars(
        select(ZoneOverride).where(ZoneOverride.zone_id == mine.id)
    ).one()
    assert entry.cancelled_at is not None


def test_jump_next_takes_its_target_from_the_domain(
    tenant_client: Client, session: Session
) -> None:
    mine, _theirs = _wohnung(session)
    now = utcnow()
    expected = next_switch(session, mine, now)
    assert expected is not None
    points_before = sorted(
        (point.weekday, point.minute_of_day, point.setpoint_mode_id)
        for point in session.scalars(
            select(SchedulePoint).where(SchedulePoint.zone_id == mine.id)
        )
    )

    client = _tenant(tenant_client, mine, ["override.create"])
    response = client.post(
        f"/zones/{mine.id}/jump-next", headers=_csrf(client), follow_redirects=False
    )
    assert response.status_code == 303

    entry = session.scalars(
        select(ZoneOverride).where(ZoneOverride.zone_id == mine.id)
    ).one()
    assert entry.temperature_c == expected.setpoint.temperature_c
    assert entry.ends_at == expected.at
    # Der Wochenplan bleibt unangetastet -- das ist die halbe Funktion.
    points_after = sorted(
        (point.weekday, point.minute_of_day, point.setpoint_mode_id)
        for point in session.scalars(
            select(SchedulePoint).where(SchedulePoint.zone_id == mine.id)
        )
    )
    assert points_after == points_before


def test_jump_next_does_not_silently_stack_a_second_override(
    tenant_client: Client, session: Session
) -> None:
    mine, _theirs = _wohnung(session)
    client = _tenant(tenant_client, mine, ["override.create"])
    client.post(
        f"/zones/{mine.id}/override",
        data={"temperature_c": "24.0", "end": "duration", "duration_minutes": "60"},
        headers=_csrf(client), follow_redirects=False,
    )
    response = client.post(
        f"/zones/{mine.id}/jump-next", headers=_csrf(client), follow_redirects=False
    )
    assert response.status_code == 303
    assert "jump_next_errors" in response.headers["location"]
    running = list(
        session.scalars(
            select(ZoneOverride).where(
                ZoneOverride.zone_id == mine.id, ZoneOverride.cancelled_at.is_(None)
            )
        )
    )
    assert len(running) == 1
    assert running[0].temperature_c == Decimal("24.0")


def test_jump_next_replaces_the_running_override_when_asked_explicitly(
    tenant_client: Client, session: Session
) -> None:
    mine, _theirs = _wohnung(session)
    client = _tenant(tenant_client, mine, ["override.create"])
    client.post(
        f"/zones/{mine.id}/override",
        data={"temperature_c": "24.0", "end": "duration", "duration_minutes": "60"},
        headers=_csrf(client), follow_redirects=False,
    )
    client.post(
        f"/zones/{mine.id}/jump-next", data={"replace": "1"},
        headers=_csrf(client), follow_redirects=False,
    )
    running = list(
        session.scalars(
            select(ZoneOverride).where(
                ZoneOverride.zone_id == mine.id, ZoneOverride.cancelled_at.is_(None)
            )
        )
    )
    assert len(running) == 1
    assert running[0].temperature_c != Decimal("24.0")


def test_jump_next_without_a_schedule_says_so_and_creates_nothing(
    tenant_client: Client, session: Session
) -> None:
    create_settings(session)
    bare = create_zone(session, "kammer")
    create_zone_state(session, bare)
    session.flush()
    client = tenant_client([("zone.read", bare.id), ("override.create", bare.id)])
    response = client.post(
        f"/zones/{bare.id}/jump-next", headers=_csrf(client), follow_redirects=False
    )
    assert response.status_code == 303
    assert "jump_next_errors" in response.headers["location"]
    assert not list(
        session.scalars(select(ZoneOverride).where(ZoneOverride.zone_id == bare.id))
    )


# -- Zeitplan ------------------------------------------------------------------


def test_the_forecast_matches_the_domain(
    tenant_client: Client, session: Session
) -> None:
    """Verglichen wird gegen `schedule_forecast`, nicht gegen erwartete Texte --
    sonst prüfte der Test nur, dass die Vorlage sich selbst treu bleibt."""
    mine, _theirs = _wohnung(session)
    client = _tenant(tenant_client, mine)
    page = client.get(f"/schedule?zone={mine.id}").text
    segments = schedule_forecast(session, mine, utcnow())
    assert page.count('class="tc-forecast-part') == len(segments)


def test_without_the_schedule_permission_no_edit_control_is_offered(
    tenant_client: Client, session: Session
) -> None:
    mine, _theirs = _wohnung(session)
    page = _tenant(tenant_client, mine).get(f"/schedule?zone={mine.id}").text
    assert "/schedule/day" not in page
    assert "/schedule/copy-day" not in page
    assert "/schedule/adopt" not in page


def test_without_the_schedule_permission_every_write_endpoint_refuses(
    tenant_client: Client, session: Session
) -> None:
    """Die fehlende Schaltfläche ist keine Absicherung -- der Endpunkt ist es."""
    mine, _theirs = _wohnung(session)
    client = _tenant(tenant_client, mine)
    for path, data in (
        ("/schedule/day", {"zone_id": str(mine.id), "weekday": "1"}),
        ("/schedule/copy-day", {"zone_id": str(mine.id), "weekday": "1", "scope": "all"}),
        ("/schedule/adopt", {"zone_id": str(mine.id), "source_id": str(mine.id)}),
        ("/schedule/undo", {"zone_id": str(mine.id), "undo_token": "x"}),
    ):
        assert client.post(path, data=data, headers=_csrf(client)).status_code == 404, path


def test_editing_one_day_moves_exactly_that_days_points(
    tenant_client: Client, session: Session
) -> None:
    mine, _theirs = _wohnung(session)
    client = _tenant(tenant_client, mine, ["schedule.manage"])
    monday = sorted(
        session.scalars(
            select(SchedulePoint).where(
                SchedulePoint.zone_id == mine.id, SchedulePoint.weekday == 1
            )
        ),
        key=lambda point: point.minute_of_day,
    )
    response = client.post(
        "/schedule/day",
        data={
            "zone_id": str(mine.id), "weekday": "1",
            "point_id_1": str(monday[0].id), "time_1": "07:00",
            "point_id_2": str(monday[1].id), "time_2": "23:00",
        },
        headers=_csrf(client), follow_redirects=False,
    )
    assert response.status_code == 303
    session.expire_all()
    minutes = sorted(
        point.minute_of_day
        for point in session.scalars(
            select(SchedulePoint).where(
                SchedulePoint.zone_id == mine.id, SchedulePoint.weekday == 1
            )
        )
    )
    assert minutes == [7 * 60, NIGHT]
    # Dienstag bleibt unverändert.
    tuesday = sorted(
        point.minute_of_day
        for point in session.scalars(
            select(SchedulePoint).where(
                SchedulePoint.zone_id == mine.id, SchedulePoint.weekday == 2
            )
        )
    )
    assert tuesday == [MORNING, NIGHT]


def test_a_point_of_another_day_cannot_be_moved_through_this_form(
    tenant_client: Client, session: Session
) -> None:
    mine, _theirs = _wohnung(session)
    client = _tenant(tenant_client, mine, ["schedule.manage"])
    tuesday = sorted(
        session.scalars(
            select(SchedulePoint).where(
                SchedulePoint.zone_id == mine.id, SchedulePoint.weekday == 2
            )
        ),
        key=lambda point: point.minute_of_day,
    )
    response = client.post(
        "/schedule/day",
        data={
            "zone_id": str(mine.id), "weekday": "1",
            "point_id_1": str(tuesday[0].id), "time_1": "07:00",
            "point_id_2": str(tuesday[1].id), "time_2": "23:00",
        },
        headers=_csrf(client),
    )
    assert response.status_code == 404


def test_copying_a_day_to_the_workdays_and_undoing_it_again(
    tenant_client: Client, session: Session
) -> None:
    mine, _theirs = _wohnung(session)
    client = _tenant(tenant_client, mine, ["schedule.manage"])
    monday = sorted(
        session.scalars(
            select(SchedulePoint).where(
                SchedulePoint.zone_id == mine.id, SchedulePoint.weekday == 1
            )
        ),
        key=lambda point: point.minute_of_day,
    )
    client.post(
        "/schedule/day",
        data={
            "zone_id": str(mine.id), "weekday": "1",
            "point_id_1": str(monday[0].id), "time_1": "05:00",
            "point_id_2": str(monday[1].id), "time_2": "23:00",
        },
        headers=_csrf(client), follow_redirects=False,
    )
    page = client.post(
        "/schedule/copy-day",
        data={"zone_id": str(mine.id), "weekday": "1", "scope": "workdays"},
        headers=_csrf(client),
    )
    assert page.status_code == 200
    assert "Mo–Fr" in page.text
    session.expire_all()
    friday = sorted(
        point.minute_of_day
        for point in session.scalars(
            select(SchedulePoint).where(
                SchedulePoint.zone_id == mine.id, SchedulePoint.weekday == 5
            )
        )
    )
    assert friday == [5 * 60, NIGHT]
    saturday = sorted(
        point.minute_of_day
        for point in session.scalars(
            select(SchedulePoint).where(
                SchedulePoint.zone_id == mine.id, SchedulePoint.weekday == 6
            )
        )
    )
    assert saturday == [MORNING, NIGHT]

    token = page.text.split('name="undo_token" value="', 1)[1].split('"', 1)[0]
    client.post(
        "/schedule/undo",
        data={"zone_id": str(mine.id), "undo_token": token},
        headers=_csrf(client),
    )
    session.expire_all()
    friday_again = sorted(
        point.minute_of_day
        for point in session.scalars(
            select(SchedulePoint).where(
                SchedulePoint.zone_id == mine.id, SchedulePoint.weekday == 5
            )
        )
    )
    assert friday_again == [MORNING, NIGHT]


def test_an_undo_token_for_another_zone_is_refused(
    tenant_client: Client, session: Session
) -> None:
    mine, _theirs = _wohnung(session)
    second = create_zone(session, "bad")
    session.flush()
    client = tenant_client(
        [("zone.read", mine.id), ("zone.read", second.id),
         ("schedule.manage", mine.id), ("schedule.manage", second.id)]
    )
    # Erst etwas ändern: eine Kopie, die nichts verändert, hat auch nichts
    # rückgängig zu machen und liefert deshalb gar kein Undo-Token.
    monday = sorted(
        session.scalars(
            select(SchedulePoint).where(
                SchedulePoint.zone_id == mine.id, SchedulePoint.weekday == 1
            )
        ),
        key=lambda point: point.minute_of_day,
    )
    client.post(
        "/schedule/day",
        data={
            "zone_id": str(mine.id), "weekday": "1",
            "point_id_1": str(monday[0].id), "time_1": "05:30",
            "point_id_2": str(monday[1].id), "time_2": "23:00",
        },
        headers=_csrf(client), follow_redirects=False,
    )
    page = client.post(
        "/schedule/copy-day",
        data={"zone_id": str(mine.id), "weekday": "1", "scope": "all"},
        headers=_csrf(client),
    )
    token = page.text.split('name="undo_token" value="', 1)[1].split('"', 1)[0]
    response = client.post(
        "/schedule/undo",
        data={"zone_id": str(second.id), "undo_token": token},
        headers=_csrf(client),
    )
    assert response.status_code == 400


def test_adopting_a_schedule_between_two_own_rooms(
    tenant_client: Client, session: Session
) -> None:
    mine, _theirs = _wohnung(session)
    second = create_zone(session, "bad")
    second.display_name = "Bad"
    session.flush()
    client = tenant_client(
        [("zone.read", mine.id), ("zone.read", second.id),
         ("schedule.manage", mine.id), ("schedule.manage", second.id)]
    )
    response = client.post(
        "/schedule/adopt",
        data={"zone_id": str(second.id), "source_id": str(mine.id)},
        headers=_csrf(client), follow_redirects=False,
    )
    assert response.status_code == 303
    adopted = sorted(
        (point.weekday, point.minute_of_day)
        for point in session.scalars(
            select(SchedulePoint).where(SchedulePoint.zone_id == second.id)
        )
    )
    assert adopted == sorted((weekday, minute)
                             for weekday in range(1, 8) for minute in (MORNING, NIGHT))


def test_adopting_a_schedule_onto_itself_is_refused(
    tenant_client: Client, session: Session
) -> None:
    mine, _theirs = _wohnung(session)
    client = _tenant(tenant_client, mine, ["schedule.manage"])
    response = client.post(
        "/schedule/adopt",
        data={"zone_id": str(mine.id), "source_id": str(mine.id)},
        headers=_csrf(client),
    )
    assert response.status_code == 400


def test_an_unknown_copy_scope_is_refused(
    tenant_client: Client, session: Session
) -> None:
    mine, _theirs = _wohnung(session)
    client = _tenant(tenant_client, mine, ["schedule.manage"])
    response = client.post(
        "/schedule/copy-day",
        data={"zone_id": str(mine.id), "weekday": "1", "scope": "alles"},
        headers=_csrf(client),
    )
    assert response.status_code == 400


# -- Heizzeit ------------------------------------------------------------------


@pytest.mark.parametrize("period", ["7", "30", "90"])
def test_every_period_renders_only_visible_rooms(
    tenant_client: Client, session: Session, period: str
) -> None:
    mine, theirs = _wohnung(session)
    page = _tenant(tenant_client, mine).get(f"/heating-time?period={period}").text
    assert mine.display_name in page
    assert theirs.display_name not in page


def test_an_unknown_period_falls_back_instead_of_failing(
    tenant_client: Client, session: Session
) -> None:
    mine, _theirs = _wohnung(session)
    page = _tenant(tenant_client, mine).get("/heating-time?period=alles")
    assert page.status_code == 200
    assert "7 Tage" in page.text


def test_the_dry_run_is_spelled_out_as_text(
    tenant_client: Client, session: Session
) -> None:
    """Nicht über Farbe, nicht über eine Hinweisblase: eine Zahl, die jemand für die
    tatsächliche Geschichte der Anlage hält, wäre schlimmer als gar keine."""
    mine, _theirs = _wohnung(session)
    page = _tenant(tenant_client, mine).get("/heating-time").text
    assert "geheizt <em>hätte</em>" in page
    # Die Werte dürfen nicht als Energie, Verbrauch oder Kosten **beschriftet**
    # sein. Der Satz, dass sich daraus *kein* genauer Verbrauch ableiten lässt,
    # ist ausdrücklich erwünscht -- er sagt dasselbe von der anderen Seite.
    # Die Werte selbst dürfen nicht als Energie oder Kosten *beschriftet* sein. Die
    # beiden Sätze, die ausdrücklich sagen, dass es **kein** Energie- oder
    # Kostenmesser ist und sich daraus kein Verbrauch ableiten lässt, sind erwünscht
    # -- sie sagen dasselbe von der anderen Seite. Geprüft werden deshalb die
    # Einheiten, die eine solche Beschriftung mit sich brächte.
    for unit in ("kWh", "€", "Euro", "kW/h"):
        assert unit not in page, unit
    assert "Heizzeit" in page
    assert "Heizdauer" in page


def test_an_armed_plant_shows_no_dry_run_warning(
    tenant_client: Client, session: Session
) -> None:
    mine, _theirs = _wohnung(session)
    from thermoctl.db.models.operations import Setting

    row = session.get(Setting, 1)
    assert row is not None
    row.control_armed = True
    session.flush()
    page = _tenant(tenant_client, mine).get("/heating-time").text
    assert "hätte" not in page


# -- HTMX, CSRF, Präfix --------------------------------------------------------


def test_the_live_poll_is_quiet_and_uses_the_server_interval(
    tenant_client: Client, session: Session
) -> None:
    mine, _theirs = _wohnung(session)
    from thermoctl.db.models.operations import Setting

    row = session.get(Setting, 1)
    assert row is not None
    row.shadow_interval_seconds = 42
    session.flush()
    page = _tenant(tenant_client, mine).get("/").text
    assert 'data-tc-quiet-poll="true"' in page
    assert "every 42s [!document.hidden]" in page


def test_a_mutation_without_the_csrf_token_is_refused(
    tenant_client: Client, session: Session
) -> None:
    mine, _theirs = _wohnung(session)
    client = _tenant(tenant_client, mine, ["schedule.manage"])
    response = client.post(
        "/schedule/copy-day",
        data={"zone_id": str(mine.id), "weekday": "1", "scope": "all"},
    )
    assert response.status_code == 403


def test_every_link_carries_the_ingress_prefix(
    client_with_prefix: TestClient, session: Session
) -> None:
    """Ohne das Präfix führt jede Verknüpfung aus dem Ingress-Pfad heraus."""
    from tests.helpers import user_with_permissions
    from thermoctl.auth.sessions import create_session
    from thermoctl.domain.ui_profile import WebUiProfile

    mine, _theirs = _wohnung(session)
    user_record = user_with_permissions(
        session, "praefix-mieterin",
        [("zone.read", mine.id), ("schedule.manage", mine.id)],
        ui_profile=WebUiProfile.TENANT,
    )
    _http, secret = create_session(session, user_record, 3600)
    client_with_prefix.cookies.set(COOKIE_NAME, secret)
    page = client_with_prefix.get("/schedule").text
    assert 'href="/api/hassio_ingress/A1b2C3d4e5/heating-time' in page
    assert 'action="/api/hassio_ingress/A1b2C3d4e5/schedule/' in page


def test_the_next_switch_line_comes_from_the_domain(
    tenant_client: Client, session: Session
) -> None:
    mine, _theirs = _wohnung(session)
    expected = next_switch(session, mine, utcnow())
    assert expected is not None
    page = _tenant(tenant_client, mine).get("/").text
    assert "Als Nächstes" in page
    shown = f"{expected.setpoint.temperature_c:.1f}".replace(".", ",")
    assert shown in page


def test_a_stale_measurement_is_explained_as_an_effect_not_a_cause(
    tenant_client: Client, session: Session
) -> None:
    from thermoctl.db.models.lookup import SensorStatus
    from thermoctl.db.models.state import ZoneState

    mine, _theirs = _wohnung(session)
    stale = session.scalars(
        select(SensorStatus).where(SensorStatus.code != "ok")
    ).first()
    if stale is None:
        stale = SensorStatus(code="ausgefallen", label="Ausgefallen")
        session.add(stale)
        session.flush()
    state = session.scalars(
        select(ZoneState).where(ZoneState.zone_id == mine.id)
    ).one()
    state.sensor_status_id = stale.id
    state.measured_at = utcnow() - timedelta(minutes=24)
    session.flush()
    import re as _re

    page = _tenant(tenant_client, mine).get("/").text
    flat = _re.sub(r"\s+", " ", page)
    assert "Messwert möglicherweise nicht aktuell" in flat
    assert "Die Heizung regelt weiter, aber der angezeigte Wert kann veraltet sein." in flat
    # Der Effekt, nicht die Ursache: kein Gerätename, kein Sensorzustandscode, kein
    # Wort aus der Anlagentechnik im Hinweis selbst.
    notice = flat.split('class="tc-notice"', 1)[1][:400]
    for word in ("Sensor", "MQTT", "Zigbee", "ausgefallen"):
        assert word not in notice, word


# -- Die verbleibenden Zweige: Fensterhinweis, Fehlerfälle, Tausch --------------


def test_an_open_window_is_reported_as_the_effect_it_has(
    tenant_client: Client, session: Session
) -> None:
    """Ein offenes Fenster ist der einzige zweite Hinweis, den die Wohnungssicht
    kennt. Er erscheint nur, wenn kein Messwertproblem vorliegt -- zwei Hinweise
    übereinander beantworten keine Frage besser als einer."""
    from thermoctl.db.models.state import ZoneState

    mine, _theirs = _wohnung(session)
    state = session.scalars(select(ZoneState).where(ZoneState.zone_id == mine.id)).one()
    state.window_open = True
    session.flush()
    page = _tenant(tenant_client, mine).get("/").text
    assert "Fenster steht offen" in page
    assert "wird in diesem Raum nicht geheizt" in page


def test_a_room_without_a_state_row_is_skipped_by_the_notice(
    tenant_client: Client, session: Session
) -> None:
    """Eine frisch angelegte Zone hat noch keinen Zustand. Sie darf den Hinweis
    weder auslösen noch die Seite scheitern lassen."""
    create_settings(session)
    fresh = create_zone(session, "neu")
    fresh.display_name = "Neuer Raum"
    session.flush()
    page = _tenant(tenant_client, fresh).get("/")
    assert page.status_code == 200
    assert "Heizung läuft normal" in page.text


def test_swapping_the_two_switch_times_of_one_day(
    tenant_client: Client, session: Session
) -> None:
    """Der Grund, warum das Formular beide Punkte zusammen behandelt: werden beide
    Zeiten getauscht, wäre ein einzelnes Verschieben auf die noch belegte Zeit des
    jeweils anderen Punktes ein Konflikt. Nacheinander gelöscht und neu angelegt
    geht es auf."""
    mine, _theirs = _wohnung(session)
    client = _tenant(tenant_client, mine, ["schedule.manage"])
    monday = sorted(
        session.scalars(
            select(SchedulePoint).where(
                SchedulePoint.zone_id == mine.id, SchedulePoint.weekday == 1
            )
        ),
        key=lambda point: point.minute_of_day,
    )
    response = client.post(
        "/schedule/day",
        data={
            "zone_id": str(mine.id), "weekday": "1",
            "point_id_1": str(monday[0].id), "time_1": "23:00",
            "point_id_2": str(monday[1].id), "time_2": "06:30",
        },
        headers=_csrf(client), follow_redirects=False,
    )
    assert response.status_code == 303
    session.expire_all()
    pairs = sorted(
        (point.minute_of_day, point.setpoint_mode_id)
        for point in session.scalars(
            select(SchedulePoint).where(
                SchedulePoint.zone_id == mine.id, SchedulePoint.weekday == 1
            )
        )
    )
    # Dieselben zwei Zeiten, aber die Modi haben die Plätze getauscht.
    assert [minute for minute, _mode in pairs] == [MORNING, NIGHT]
    assert pairs[0][1] != monday[0].setpoint_mode_id


def test_an_impossible_time_is_shown_as_a_correctable_input(
    tenant_client: Client, session: Session
) -> None:
    mine, _theirs = _wohnung(session)
    client = _tenant(tenant_client, mine, ["schedule.manage"])
    monday = sorted(
        session.scalars(
            select(SchedulePoint).where(
                SchedulePoint.zone_id == mine.id, SchedulePoint.weekday == 1
            )
        ),
        key=lambda point: point.minute_of_day,
    )
    page = client.post(
        "/schedule/day",
        data={
            "zone_id": str(mine.id), "weekday": "1",
            "point_id_1": str(monday[0].id), "time_1": "25:99",
            "point_id_2": str(monday[1].id), "time_2": "23:00",
        },
        headers=_csrf(client),
    )
    assert page.status_code == 200
    assert "alert-warning" in page.text
    session.expire_all()
    unchanged = sorted(
        point.minute_of_day
        for point in session.scalars(
            select(SchedulePoint).where(
                SchedulePoint.zone_id == mine.id, SchedulePoint.weekday == 1
            )
        )
    )
    assert unchanged == [MORNING, NIGHT]


def test_moving_a_point_onto_an_occupied_time_is_reported(
    tenant_client: Client, session: Session
) -> None:
    """Nur ein Punkt ändert sich, und zwar auf die Zeit des anderen -- die Domäne
    lehnt das ab, und die Seite sagt es, statt still nichts zu tun."""
    mine, _theirs = _wohnung(session)
    client = _tenant(tenant_client, mine, ["schedule.manage"])
    monday = sorted(
        session.scalars(
            select(SchedulePoint).where(
                SchedulePoint.zone_id == mine.id, SchedulePoint.weekday == 1
            )
        ),
        key=lambda point: point.minute_of_day,
    )
    page = client.post(
        "/schedule/day",
        data={
            "zone_id": str(mine.id), "weekday": "1",
            "point_id_1": str(monday[0].id), "time_1": "23:00",
            "point_id_2": str(monday[1].id), "time_2": "23:00",
        },
        headers=_csrf(client),
    )
    assert page.status_code == 200
    assert "alert-warning" in page.text


@pytest.mark.parametrize(
    ("path", "field"),
    [
        ("/schedule/copy-day", "weekday"),
        ("/schedule/adopt", "source_id"),
        ("/schedule/undo", "zone_id"),
    ],
)
def test_an_unparseable_number_in_any_schedule_form_is_not_found(
    tenant_client: Client, session: Session, path: str, field: str
) -> None:
    mine, _theirs = _wohnung(session)
    client = _tenant(tenant_client, mine, ["schedule.manage"])
    data = {"zone_id": str(mine.id), "weekday": "1", "scope": "all",
            "source_id": str(mine.id), "undo_token": "x"}
    data[field] = "keine-zahl"
    assert client.post(path, data=data, headers=_csrf(client)).status_code == 404


def test_copying_a_day_that_changes_nothing_says_so_without_an_undo(
    tenant_client: Client, session: Session
) -> None:
    """Alle Tage sehen schon gleich aus. Dann gibt es nichts rückgängig zu machen --
    und ein Undo-Knopf, der nichts täte, wäre eine Lüge."""
    mine, _theirs = _wohnung(session)
    client = _tenant(tenant_client, mine, ["schedule.manage"])
    page = client.post(
        "/schedule/copy-day",
        data={"zone_id": str(mine.id), "weekday": "1", "scope": "all"},
        headers=_csrf(client),
    )
    assert page.status_code == 200
    assert "nichts geändert" in page.text
    assert 'name="undo_token"' not in page.text


def test_a_forged_undo_token_is_refused(
    tenant_client: Client, session: Session
) -> None:
    mine, _theirs = _wohnung(session)
    client = _tenant(tenant_client, mine, ["schedule.manage"])
    response = client.post(
        "/schedule/undo",
        data={"zone_id": str(mine.id), "undo_token": "abc.def"},
        headers=_csrf(client),
    )
    assert response.status_code == 400


def test_undoing_twice_is_reported_instead_of_silently_reverting_something_else(
    tenant_client: Client, session: Session
) -> None:
    """Der zweite Klick auf denselben Rückgängig-Knopf trifft einen Zeitplan, der
    nicht mehr der von damals ist. Die Domäne erkennt das an der Revision."""
    mine, _theirs = _wohnung(session)
    client = _tenant(tenant_client, mine, ["schedule.manage"])
    monday = sorted(
        session.scalars(
            select(SchedulePoint).where(
                SchedulePoint.zone_id == mine.id, SchedulePoint.weekday == 1
            )
        ),
        key=lambda point: point.minute_of_day,
    )
    client.post(
        "/schedule/day",
        data={
            "zone_id": str(mine.id), "weekday": "1",
            "point_id_1": str(monday[0].id), "time_1": "05:30",
            "point_id_2": str(monday[1].id), "time_2": "23:00",
        },
        headers=_csrf(client), follow_redirects=False,
    )
    page = client.post(
        "/schedule/copy-day",
        data={"zone_id": str(mine.id), "weekday": "1", "scope": "all"},
        headers=_csrf(client),
    )
    token = page.text.split('name="undo_token" value="', 1)[1].split('"', 1)[0]
    first = client.post(
        "/schedule/undo",
        data={"zone_id": str(mine.id), "undo_token": token},
        headers=_csrf(client),
    )
    assert first.status_code == 200
    second = client.post(
        "/schedule/undo",
        data={"zone_id": str(mine.id), "undo_token": token},
        headers=_csrf(client),
    )
    assert second.status_code == 200
    assert "alert-warning" in second.text


def test_an_unparseable_weekday_when_editing_a_day_is_not_found(
    tenant_client: Client, session: Session
) -> None:
    """Eigener Fall neben der Zonen-Id: der Wochentag wird ebenso aus dem Formular
    gelesen und ebenso wenig geglaubt."""
    mine, _theirs = _wohnung(session)
    client = _tenant(tenant_client, mine, ["schedule.manage"])
    response = client.post(
        "/schedule/day",
        data={"zone_id": str(mine.id), "weekday": "Montag"},
        headers=_csrf(client),
    )
    assert response.status_code == 404


def test_a_weekday_outside_the_week_is_shown_as_a_correctable_input(
    tenant_client: Client, session: Session
) -> None:
    """Eine Zahl, die kein Wochentag ist, kommt an der Zahlprüfung vorbei und wird
    erst von der Domäne abgelehnt. Die Seite zeigt deren Text, statt mit einem
    Serverfehler zu antworten."""
    mine, _theirs = _wohnung(session)
    client = _tenant(tenant_client, mine, ["schedule.manage"])
    page = client.post(
        "/schedule/copy-day",
        data={"zone_id": str(mine.id), "weekday": "9", "scope": "all"},
        headers=_csrf(client),
    )
    assert page.status_code == 200
    assert "alert-warning" in page.text


# -- Abwesenheit ---------------------------------------------------------------


def test_absence_covers_exactly_the_rooms_the_server_allows(
    tenant_client: Client, session: Session
) -> None:
    """Der Kern der Funktion: welche Räume betroffen sind, entscheidet der Server.

    Der Mieter darf hier zwei Räume sehen, aber nur einen bedienen. Die Abwesenheit
    darf deshalb genau einen absenken -- und den fremden Raum ohnehin nicht.
    """
    mine, theirs = _wohnung(session)
    read_only = create_zone(session, "flur")
    read_only.display_name = "Flur"
    session.flush()
    client = tenant_client(
        [("zone.read", mine.id), ("zone.read", read_only.id),
         ("override.create", mine.id), ("override.cancel", mine.id)]
    )
    response = client.post(
        "/absence",
        data={"return_on": "2030-01-05", "temperature_c": "17"},
        headers=_csrf(client), follow_redirects=False,
    )
    assert response.status_code == 303

    affected = {
        entry.zone_id
        for entry in session.scalars(
            select(ZoneOverride).where(ZoneOverride.absence_id.is_not(None))
        )
    }
    assert affected == {mine.id}
    assert theirs.id not in affected
    assert read_only.id not in affected


def test_a_running_absence_is_visible_and_can_be_ended_in_one_step(
    tenant_client: Client, session: Session
) -> None:
    mine, _theirs = _wohnung(session)
    second = create_zone(session, "bad")
    second.display_name = "Bad"
    session.flush()
    client = tenant_client(
        [("zone.read", mine.id), ("zone.read", second.id),
         ("override.create", mine.id), ("override.create", second.id)]
    )
    client.post(
        "/absence", data={"return_on": "2030-01-05", "temperature_c": "17"},
        headers=_csrf(client), follow_redirects=False,
    )
    page = client.get("/").text
    assert "Abwesenheit läuft" in page
    assert mine.display_name in page
    assert second.display_name in page

    client.post("/absence/end", headers=_csrf(client), follow_redirects=False)
    still_running = [
        entry
        for entry in session.scalars(
            select(ZoneOverride).where(ZoneOverride.absence_id.is_not(None))
        )
        if entry.cancelled_at is None
    ]
    assert still_running == []
    assert "Abwesenheit läuft" not in client.get("/").text


def test_an_absence_leaves_an_unrelated_override_alone(
    tenant_client: Client, session: Session
) -> None:
    """`end_absence` beendet nur die Übersteuerungen dieser Klammer -- nicht die
    jeweils jüngste einer Zone, die inzwischen aus einem anderen Grund entstanden
    sein kann."""
    mine, _theirs = _wohnung(session)
    second = create_zone(session, "bad")
    session.flush()
    client = tenant_client(
        [("zone.read", mine.id), ("zone.read", second.id),
         ("override.create", mine.id)]
    )
    client.post(
        "/absence", data={"return_on": "2030-01-05", "temperature_c": "17"},
        headers=_csrf(client), follow_redirects=False,
    )
    # Eine eigenständige Übersteuerung, die nichts mit der Abwesenheit zu tun hat.
    client.post(
        f"/zones/{mine.id}/override",
        data={"temperature_c": "23.0", "end": "duration", "duration_minutes": "60"},
        headers=_csrf(client), follow_redirects=False,
    )
    client.post("/absence/end", headers=_csrf(client), follow_redirects=False)
    loose = session.scalars(
        select(ZoneOverride).where(ZoneOverride.absence_id.is_(None))
    ).all()
    assert [entry.cancelled_at for entry in loose] == [None]


@pytest.mark.parametrize(
    "data",
    [
        {"return_on": "morgen", "temperature_c": "17"},
        {"return_on": "2030-01-05", "temperature_c": "warm"},
        {"return_on": "", "temperature_c": ""},
    ],
)
def test_an_unusable_absence_input_changes_nothing_and_says_so(
    tenant_client: Client, session: Session, data: dict[str, str]
) -> None:
    mine, _theirs = _wohnung(session)
    client = _tenant(tenant_client, mine, ["override.create"])
    response = client.post(
        "/absence", data=data, headers=_csrf(client), follow_redirects=False
    )
    assert response.status_code == 303
    assert "absence_errors" in response.headers["location"]
    assert not list(session.scalars(select(ZoneOverride)))


def test_a_return_date_in_the_past_is_refused_by_the_domain(
    tenant_client: Client, session: Session
) -> None:
    mine, _theirs = _wohnung(session)
    client = _tenant(tenant_client, mine, ["override.create"])
    response = client.post(
        "/absence", data={"return_on": "2020-01-05", "temperature_c": "17"},
        headers=_csrf(client), follow_redirects=False,
    )
    assert response.status_code == 303
    assert "absence_errors" in response.headers["location"]
    assert not list(session.scalars(select(ZoneOverride)))


def test_a_tenant_without_a_controllable_room_gets_no_absence_form(
    tenant_client: Client, session: Session
) -> None:
    """Nur lesen zu dürfen heißt nicht, absenken zu dürfen."""
    mine, _theirs = _wohnung(session)
    page = _tenant(tenant_client, mine).get("/").text
    assert "Abwesenheit einstellen" not in page


def test_ending_an_absence_that_is_not_running_is_reported(
    tenant_client: Client, session: Session
) -> None:
    mine, _theirs = _wohnung(session)
    client = _tenant(tenant_client, mine, ["override.create"])
    response = client.post(
        "/absence/end", headers=_csrf(client), follow_redirects=False
    )
    assert response.status_code == 303
    assert "absence_errors" in response.headers["location"]


def test_a_room_taken_away_afterwards_no_longer_appears_in_the_absence_banner(
    tenant_client: Client, session: Session
) -> None:
    """Rechte können zurückgenommen werden, während eine Abwesenheit läuft.

    Der Name eines Raums, den jemand heute nicht mehr sehen darf, hat auch dann
    nichts in einer Antwort zu suchen, wenn er ihn gestern selbst abgesenkt hat.
    """
    from thermoctl.db.models.identity import GroupPermission
    from thermoctl.db.models.lookup import Permission

    mine, _theirs = _wohnung(session)
    second = create_zone(session, "bad")
    second.display_name = "Bad"
    session.flush()
    client = tenant_client(
        [("zone.read", mine.id), ("zone.read", second.id),
         ("override.create", mine.id), ("override.create", second.id)]
    )
    client.post(
        "/absence", data={"return_on": "2030-01-05", "temperature_c": "17"},
        headers=_csrf(client), follow_redirects=False,
    )
    assert "Bad" in client.get("/").text

    # Das Leserecht für den zweiten Raum wird zurückgenommen.
    read = session.scalars(
        select(Permission).where(Permission.code == "zone.read")
    ).one()
    session.execute(
        GroupPermission.__table__.delete().where(
            GroupPermission.permission_id == read.id,
            GroupPermission.zone_id == second.id,
        )
    )
    session.flush()

    page = client.get("/").text
    assert "Abwesenheit läuft" in page
    assert "Bad" not in page


def test_the_tenant_shell_carries_the_stale_page_handling_and_the_loading_bar(
    tenant_client: Client, session: Session
) -> None:
    """Beide gehören zum gemeinsamen Kern (`base_core.html`) und dürfen der
    Wohnungssicht nicht abhandenkommen.

    Beides sind aus dem Betrieb gemeldete Fehler, keine Zierde: ohne den
    stale-page-Hinweis tut ein Bedienelement stumm nichts, ohne den Ladebalken
    wirkt eine langsame Antwort wie ein Ausfall.
    """
    mine, _theirs = _wohnung(session)
    page = _tenant(tenant_client, mine).get("/").text
    assert "HX-Stale-Page" in page
    assert 'id="tc-loading-bar"' in page
