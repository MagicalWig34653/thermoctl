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
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.zone import SetpointMode, Zone, ZoneSetpoint

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




# -- Das Profil ---------------------------------------------------------------






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




# -- Sollwert, Übersteuerung, Sprung ------------------------------------------


def _stored(session: Session, zone: Zone, mode_code: str) -> Decimal:
    mode = session.scalars(select(SetpointMode).where(SetpointMode.code == mode_code)).one()
    return session.scalars(
        select(ZoneSetpoint.temperature_c).where(
            ZoneSetpoint.zone_id == zone.id, ZoneSetpoint.setpoint_mode_id == mode.id
        )
    ).one()


















# -- Zeitplan ------------------------------------------------------------------




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












# -- Heizzeit ------------------------------------------------------------------








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












# -- Die verbleibenden Zweige: Fensterhinweis, Fehlerfälle, Tausch --------------












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




# -- Abwesenheit ---------------------------------------------------------------












def test_a_tenant_without_a_controllable_room_gets_no_absence_form(
    tenant_client: Client, session: Session
) -> None:
    """Nur lesen zu dürfen heißt nicht, absenken zu dürfen."""
    mine, _theirs = _wohnung(session)
    page = _tenant(tenant_client, mine).get("/").text
    assert "Abwesenheit einstellen" not in page








# -- Die Zeitplan-Übernahme prüft das Recht am Ziel, nicht an der Quelle ---------


def test_the_adoption_offers_only_rooms_the_tenant_may_actually_write(
    tenant_client: Client, session: Session
) -> None:
    """Ein angebotenes Ziel, das mit 404 endet, ist schlimmer als kein Angebot.

    Der Endpunkt verlangt `schedule.manage` am **Ziel**; die Quelle braucht nur
    `zone.read`. Die Auswahl bot vorher alle lesbaren Räume an und hing zugleich am
    Schreibrecht des gerade angezeigten -- bei zonenbezogenen Rechten also gleich
    doppelt falsch.
    """
    mine, _theirs = _wohnung(session)
    nur_lesbar = create_zone(session, "flur")
    nur_lesbar.display_name = "Flur"
    session.flush()
    client = tenant_client(
        [("zone.read", mine.id), ("zone.read", nur_lesbar.id),
         ("schedule.manage", mine.id)]
    )
    page = client.get(f"/schedule?zone={mine.id}").text
    # Der einzige beschreibbare Raum ist der angezeigte selbst -- es bleibt kein Ziel
    # übrig, also darf gar keine Auswahl erscheinen.
    assert "/schedule/adopt" not in page




def test_a_room_without_write_permission_is_never_offered_as_a_target(
    tenant_client: Client, session: Session
) -> None:
    mine, _theirs = _wohnung(session)
    nur_lesbar = create_zone(session, "flur")
    nur_lesbar.display_name = "Flur"
    session.flush()
    client = tenant_client(
        [("zone.read", mine.id), ("zone.read", nur_lesbar.id),
         ("schedule.manage", mine.id)]
    )
    page = client.get(f"/schedule?zone={nur_lesbar.id}").text
    assert f'<option value="{nur_lesbar.id}"' not in page


# -- Ein noch leerer Tag lässt sich einrichten ----------------------------------


def _leerer_raum(session: Session) -> Zone:
    """Ein frisch angelegter Raum: zwei Sollwerte, aber kein einziger Schaltpunkt.

    Genau der Zustand nach `create_zone` -- die Funktion legt keine Schaltpunkte an.
    """
    create_settings(session)
    zone = create_zone(session, "arbeitszimmer")
    zone.display_name = "Arbeitszimmer"
    comfort = create_mode(session, "tag", "Komfort")
    night = create_mode(session, "nacht", "Nacht")
    session.add(
        ZoneSetpoint(zone_id=zone.id, setpoint_mode_id=comfort.id,
                     temperature_c=Decimal("21.0"))
    )
    session.add(
        ZoneSetpoint(zone_id=zone.id, setpoint_mode_id=night.id,
                     temperature_c=Decimal("18.0"))
    )
    session.flush()
    return zone




def test_setting_up_one_day_leaves_the_other_days_empty(
    tenant_client: Client, session: Session
) -> None:
    """Eingerichtet wird der Tag, auf dessen Knopf jemand drückt -- nicht die Woche."""
    zone = _leerer_raum(session)
    client = tenant_client([("zone.read", zone.id), ("schedule.manage", zone.id)])
    client.post(
        "/schedule/day",
        data={"zone_id": str(zone.id), "weekday": "1",
              "time_1": "06:30", "time_2": "22:00"},
        headers=_csrf(client), follow_redirects=False,
    )
    uebrige = session.scalars(
        select(SchedulePoint).where(
            SchedulePoint.zone_id == zone.id, SchedulePoint.weekday != 1
        )
    ).all()
    assert uebrige == []


def test_the_modes_come_from_the_server_not_from_the_form(
    tenant_client: Client, session: Session
) -> None:
    """Eine Modus-Id aus dem Browser wäre eine weitere Angabe, der man nicht glauben
    darf. Der Server bestimmt sie selbst und ignoriert, was mitgeschickt wird."""
    zone = _leerer_raum(session)
    fremd = create_mode(session, "sauna", "Sauna")
    session.flush()
    client = tenant_client([("zone.read", zone.id), ("schedule.manage", zone.id)])
    client.post(
        "/schedule/day",
        data={"zone_id": str(zone.id), "weekday": "1",
              "time_1": "06:30", "time_2": "22:00",
              "mode_id_1": str(fremd.id), "mode_id_2": str(fremd.id)},
        headers=_csrf(client), follow_redirects=False,
    )
    modi = {
        point.setpoint_mode_id
        for point in session.scalars(
            select(SchedulePoint).where(SchedulePoint.zone_id == zone.id)
        )
    }
    assert fremd.id not in modi


def test_the_frost_protection_mode_is_never_used_for_a_new_day(
    tenant_client: Client, session: Session
) -> None:
    """Der Frostschutz ist die untere Schranke der Regelung, kein Abschnitt eines
    Tagesablaufs."""
    from thermoctl.db.models.operations import Setting

    zone = _leerer_raum(session)
    row = session.get(Setting, 1)
    assert row is not None
    frost_id = row.frost_protection_mode_id
    session.add(
        ZoneSetpoint(zone_id=zone.id, setpoint_mode_id=frost_id,
                     temperature_c=Decimal("30.0"))  # absichtlich der wärmste Wert
    )
    session.flush()
    client = tenant_client([("zone.read", zone.id), ("schedule.manage", zone.id)])
    client.post(
        "/schedule/day",
        data={"zone_id": str(zone.id), "weekday": "1",
              "time_1": "06:30", "time_2": "22:00"},
        headers=_csrf(client), follow_redirects=False,
    )
    modi = {
        point.setpoint_mode_id
        for point in session.scalars(
            select(SchedulePoint).where(SchedulePoint.zone_id == zone.id)
        )
    }
    assert frost_id not in modi










def test_without_the_permission_no_setup_button_and_the_endpoint_refuses(
    tenant_client: Client, session: Session
) -> None:
    zone = _leerer_raum(session)
    client = tenant_client([("zone.read", zone.id)])
    assert "Einrichten</summary>" not in client.get(f"/schedule?zone={zone.id}").text
    response = client.post(
        "/schedule/day",
        data={"zone_id": str(zone.id), "weekday": "1",
              "time_1": "06:30", "time_2": "22:00"},
        headers=_csrf(client),
    )
    assert response.status_code == 404
    assert not list(
        session.scalars(select(SchedulePoint).where(SchedulePoint.zone_id == zone.id))
    )




# -- Der Zeitplan muss als änderbar erkennbar sein -------------------------------






