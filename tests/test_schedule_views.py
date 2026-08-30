from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.helpers import create_mode, create_zone, source
from thermoctl.auth.csrf import csrf_token
from thermoctl.auth.sessions import COOKIE_NAME
from thermoctl.config import get_settings
from thermoctl.db.models.operations import AuditEvent
from thermoctl.db.models.schedule import SchedulePoint


def _csrf(client: TestClient) -> dict[str, str]:
    geheimnis = client.cookies[COOKIE_NAME]
    return {
        "X-CSRF-Token": csrf_token(
            geheimnis, get_settings().secret_key.get_secret_value()
        )
    }


def _point(session: Session, zone_id: int, day: int, minute: int, mode_id: int) -> SchedulePoint:
    point = SchedulePoint(
        zone_id=zone_id,
        weekday=day,
        minute_of_day=minute,
        setpoint_mode_id=mode_id,
    )
    session.add(point)
    session.flush()
    return point


def test_wochenansicht_zeigt_ringuebergang_von_sonntag_auf_montag(
    client_als, session: Session
) -> None:
    zone = create_zone(session, "bad")
    day = create_mode(session, "tag", "Tag")
    night = create_mode(session, "nacht", "Nacht")
    _point(session, zone.id, 1, 360, day.id)
    _point(session, zone.id, 7, 1320, night.id)

    response = client_als([("zone.read", zone.id)]).get(f"/zones/{zone.id}/schedule")

    assert response.status_code == 200
    assert "Montag" in response.text and "Sonntag" in response.text
    assert 'title="Nacht ab 00:00"' in response.text
    assert 'title="Tag ab 06:00"' in response.text
    assert 'title="Nacht ab 22:00"' in response.text
    assert "Schaltpunkt anlegen" not in response.text


def test_punkt_anlegen_und_doppelbelegung_verstaendlich_melden(
    client_als, session: Session
) -> None:
    source(session)
    zone = create_zone(session, "bad")
    mode = create_mode(session, "tag", "Tag")
    client = client_als([("schedule.manage", zone.id), ("zone.read", zone.id)])
    daten = {"weekday": "2", "time_of_day": "03:15", "modus": str(mode.id)}

    angelegt = client.post(
        f"/zones/{zone.id}/schedule/points",
        data=daten,
        headers=_csrf(client),
        follow_redirects=False,
    )
    doppelt = client.post(
        f"/zones/{zone.id}/schedule/points", data=daten, headers=_csrf(client)
    )

    point = session.scalar(select(SchedulePoint).where(SchedulePoint.zone_id == zone.id))
    assert angelegt.status_code == 303
    assert point is not None and (point.weekday, point.minute_of_day) == (2, 195)
    assert doppelt.status_code == 200
    assert "Zu diesem Zeitpunkt gibt es bereits einen Punkt." in doppelt.text
    assert 'value="03:15"' in doppelt.text
    assert session.scalar(
        select(AuditEvent).where(AuditEvent.object_type == "schedule_point")
    ) is not None


def test_ungueltige_punkte_bleiben_mit_eingaben_im_formular(
    client_als, session: Session
) -> None:
    zone = create_zone(session, "bad")
    client = client_als([("schedule.manage", zone.id)])
    response = client.post(
        f"/zones/{zone.id}/schedule/points",
        data={"weekday": "2", "time_of_day": "25:61", "modus": "unbekannt"},
        headers=_csrf(client),
    )
    assert response.status_code == 200
    assert "gültige Uhrzeit" in response.text
    assert 'value="25:61"' in response.text


def test_punkt_loeschen_bestaetigen_und_ausfuehren(client_als, session: Session) -> None:
    source(session)
    zone = create_zone(session, "bad")
    mode = create_mode(session, "tag", "Tag")
    point = _point(session, zone.id, 2, 180, mode.id)
    point_id = point.id
    client = client_als([("schedule.manage", zone.id)])

    form = client.get(
        f"/zones/{zone.id}/schedule/points/{point_id}/delete"
    )
    response = client.post(
        f"/zones/{zone.id}/schedule/points/{point_id}/delete",
        headers=_csrf(client),
        follow_redirects=False,
    )

    assert form.status_code == 200 and "Dienstag" in form.text
    assert response.status_code == 303
    assert session.get(SchedulePoint, point_id) is None


def test_zeitplan_uebernehmen_kopiert_genau_und_laesst_quelle_unveraendert(
    client_als, session: Session
) -> None:
    source(session)
    source_zone = create_zone(session, "quelle")
    ziel = create_zone(session, "ziel")
    day = create_mode(session, "tag", "Tag")
    night = create_mode(session, "nacht", "Nacht")
    _point(session, source_zone.id, 1, 360, day.id)
    _point(session, source_zone.id, 7, 1320, night.id)
    client = client_als(
        [("schedule.manage", ziel.id), ("zone.read", source_zone.id)]
    )

    response = client.post(
        f"/zones/{ziel.id}/schedule/adopt",
        data={"source_id": str(source_zone.id)},
        headers=_csrf(client),
        follow_redirects=False,
    )

    source_points = session.scalars(
        select(SchedulePoint).where(SchedulePoint.zone_id == source_zone.id)
    ).all()
    target_points = session.scalars(
        select(SchedulePoint).where(SchedulePoint.zone_id == ziel.id)
    ).all()
    assert response.status_code == 303
    assert [(p.weekday, p.minute_of_day, p.setpoint_mode_id) for p in target_points] == [
        (p.weekday, p.minute_of_day, p.setpoint_mode_id) for p in source_points
    ]
    assert len(source_points) == 2
    assert session.scalar(
        select(AuditEvent).where(
            AuditEvent.object_type == "schedule", AuditEvent.object_id == str(ziel.id)
        )
    ) is not None


def test_uebernahme_mit_bestehendem_plan_fragt_vorher_nach(
    client_als, session: Session
) -> None:
    source(session)
    source_zone = create_zone(session, "quelle")
    ziel = create_zone(session, "ziel")
    mode = create_mode(session, "tag", "Tag")
    old_point = _point(session, ziel.id, 2, 180, mode.id)
    _point(session, source_zone.id, 1, 360, mode.id)
    client = client_als(
        [("schedule.manage", ziel.id), ("zone.read", source_zone.id)]
    )
    pfad = f"/zones/{ziel.id}/schedule/adopt"

    nachfrage = client.post(
        pfad,
        data={"source_id": str(source_zone.id)},
        headers=_csrf(client),
    )
    assert nachfrage.status_code == 200
    assert "ersetzt ihn vollständig" in nachfrage.text
    assert session.get(SchedulePoint, old_point.id) is old_point

    bestaetigt = client.post(
        pfad,
        data={"source_id": str(source_zone.id), "confirmed": "ja"},
        headers=_csrf(client),
        follow_redirects=False,
    )
    assert bestaetigt.status_code == 303
    assert session.get(SchedulePoint, old_point.id) is None


def test_rechte_und_fremde_zonen_geben_404(client_als, session: Session) -> None:
    eigene = create_zone(session, "eigene")
    fremde = create_zone(session, "fremde")
    mode = create_mode(session, "tag", "Tag")
    leser = client_als([("zone.read", eigene.id)])
    assert leser.get(f"/zones/{fremde.id}/schedule").status_code == 404
    assert (
        leser.post(
            f"/zones/{eigene.id}/schedule/points",
            data={"weekday": "1", "time_of_day": "06:00", "modus": str(mode.id)},
            headers=_csrf(leser),
        ).status_code
        == 404
    )

    administrator = client_als([("schedule.manage", eigene.id), ("zone.read", eigene.id)])
    assert (
        administrator.get(f"/zones/{fremde.id}/schedule/adopt").status_code == 404
    )


def test_uebernahmeformular_und_fehlerhafte_auswahl(client_als, session: Session) -> None:
    ziel = create_zone(session, "ziel")
    client = client_als([("schedule.manage", ziel.id)])
    pfad = f"/zones/{ziel.id}/schedule/adopt"
    assert client.get(pfad).status_code == 200
    response = client.post(pfad, data={}, headers=_csrf(client))
    assert response.status_code == 200
    assert "Bitte eine Quellzone auswählen." in response.text


def test_unsinnige_auswahl_beim_punkt_anlegen(client_als, session: Session) -> None:
    """Wochentag und Modus kommen aus Auswahlfeldern — eine Anfrage muss sich daran
    trotzdem nicht halten. Beide Wege werden hier bewusst umgangen."""
    source(session)
    zone = create_zone(session, "zone-unsinn")
    mode = create_mode(session, "unsinn-tag", "Tag")
    client = client_als([("schedule.manage", None), ("zone.read", None)])
    kopf = _csrf(client)

    for daten, expected in (
        ({"weekday": "Montag", "time_of_day": "06:00", "modus": str(mode.id)}, "Wochentag"),
        ({"weekday": "1", "time_of_day": "06:00", "modus": "kein Modus"}, "Modus"),
    ):
        response = client.post(
            f"/zones/{zone.id}/schedule/points", data=daten, headers=kopf
        )
        assert response.status_code == 200, daten
        assert expected in response.text, daten
    assert session.scalar(
        select(SchedulePoint).where(SchedulePoint.zone_id == zone.id)
    ) is None


def test_fremder_zeitplanpunkt_ergibt_404(client_als, session: Session) -> None:
    """Ein Punkt einer anderen Zone laesst sich nicht ueber die eigene loeschen."""
    source(session)
    eigene = create_zone(session, "eigene-zeitplan")
    fremde = create_zone(session, "fremde-zeitplan")
    mode = create_mode(session, "fremd-tag", "Tag")
    fremder = _point(session, fremde.id, 1, 360, mode.id)
    client = client_als([("schedule.manage", None), ("zone.read", None)])

    assert client.get(
        f"/zones/{eigene.id}/schedule/points/{fremder.id}/delete"
    ).status_code == 404
    assert client.post(
        f"/zones/{eigene.id}/schedule/points/{fremder.id}/delete", headers=_csrf(client)
    ).status_code == 404
    assert session.get(SchedulePoint, fremder.id) is not None


def test_uebernahme_von_sich_selbst_ergibt_404(client_als, session: Session) -> None:
    """Eine Zone kann ihren Zeitplan nicht von sich selbst uebernehmen — das waere ein
    Vorgang ohne Wirkung, der aussaehe, als haette er gewirkt."""
    source(session)
    zone = create_zone(session, "zone-selbstuebernahme")
    client = client_als([("schedule.manage", None), ("zone.read", None)])
    response = client.post(
        f"/zones/{zone.id}/schedule/adopt",
        data={"source_id": str(zone.id)},
        headers=_csrf(client),
    )
    assert response.status_code == 404


def test_unbekannter_zeitplanpunkt_ergibt_404(client_als, session: Session) -> None:
    source(session)
    zone = create_zone(session, "zone-unbekannter-punkt")
    client = client_als([("schedule.manage", None), ("zone.read", None)])
    assert client.get(f"/zones/{zone.id}/schedule/points/999999/delete").status_code == 404


# --- Verschieben (Ziel des Ziehens in der Wochenansicht) --------------------


def test_punkt_verschieben(client_als, session: Session) -> None:
    zone = create_zone(session, "bad")
    source(session, "web")
    day = create_mode(session, "tag", "Tag")
    point = _point(session, zone.id, 1, 360, day.id)
    mandant = client_als([("zone.read", None), ("schedule.manage", None)])

    response = mandant.post(
        f"/zones/{zone.id}/schedule/points/move",
        data={"point_id": str(point.id), "weekday": "3", "time_of_day": "07:15"},
        headers=_csrf(mandant),
        follow_redirects=False,
    )
    assert response.status_code == 303
    session.refresh(point)
    assert (point.weekday, point.minute_of_day) == (3, 435)


def test_verschieben_behaelt_die_kennung_und_protokolliert_woher_wohin(
    client_als, session: Session
) -> None:
    """Loeschen und neu Anlegen waere fachlich dasselbe, haette aber zwei
    unzusammenhaengende Audit-Eintraege und zwischendurch eine Luecke im Plan."""
    zone = create_zone(session, "bad")
    source(session, "web")
    day = create_mode(session, "tag", "Tag")
    point = _point(session, zone.id, 1, 360, day.id)
    previous_identifier = point.id
    mandant = client_als([("zone.read", None), ("schedule.manage", None)])

    mandant.post(
        f"/zones/{zone.id}/schedule/points/move",
        data={"point_id": str(point.id), "weekday": "2", "time_of_day": "22:30"},
        headers=_csrf(mandant),
    )
    session.refresh(point)
    assert point.id == previous_identifier
    entry = session.scalars(
        select(AuditEvent).where(AuditEvent.object_type == "schedule_point")
    ).one()
    assert entry.detail == "Mo 06:00 → Di 22:30"


def test_verschieben_auf_einen_belegten_zeitpunkt_wird_abgewiesen(
    client_als, session: Session
) -> None:
    zone = create_zone(session, "bad")
    source(session, "web")
    day = create_mode(session, "tag", "Tag")
    night = create_mode(session, "nacht", "Nacht")
    beweglich = _point(session, zone.id, 1, 360, day.id)
    _point(session, zone.id, 1, 1320, night.id)
    mandant = client_als([("zone.read", None), ("schedule.manage", None)])

    response = mandant.post(
        f"/zones/{zone.id}/schedule/points/move",
        data={"point_id": str(beweglich.id), "weekday": "1", "time_of_day": "22:00"},
        headers=_csrf(mandant),
    )
    assert response.status_code == 200
    # Die Meldung gehoert an die Wochenansicht, nicht an das Uhrzeitfeld des
    # Anlege-Formulars: Beide Wege melden denselben Satz, und in der ersten Fassung
    # landete er an einem Formular, das der Benutzer gar nicht angefasst hatte.
    assert "data-verschiebefehler" in response.text
    assert "wurde nicht verschoben" in response.text
    session.refresh(beweglich)
    assert beweglich.minute_of_day == 360


def test_verschieben_auf_den_eigenen_platz_ist_kein_fehler(
    client_als, session: Session
) -> None:
    """Beim Ziehen landet ein Balken leicht wieder dort, wo er war. Das darf nicht als
    Kollision mit sich selbst durchfallen."""
    zone = create_zone(session, "bad")
    source(session, "web")
    day = create_mode(session, "tag", "Tag")
    point = _point(session, zone.id, 1, 360, day.id)
    mandant = client_als([("zone.read", None), ("schedule.manage", None)])

    response = mandant.post(
        f"/zones/{zone.id}/schedule/points/move",
        data={"point_id": str(point.id), "weekday": "1", "time_of_day": "06:00"},
        headers=_csrf(mandant),
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert not session.scalars(
        select(AuditEvent).where(AuditEvent.object_type == "schedule_point")
    ).all()


def test_verschieben_braucht_schedule_manage(client_als, session: Session) -> None:
    zone = create_zone(session, "bad")
    day = create_mode(session, "tag", "Tag")
    point = _point(session, zone.id, 1, 360, day.id)
    mandant = client_als([("zone.read", None)])
    response = mandant.post(
        f"/zones/{zone.id}/schedule/points/move",
        data={"point_id": str(point.id), "weekday": "2", "time_of_day": "07:00"},
        headers=_csrf(mandant),
    )
    assert response.status_code == 404
    session.refresh(point)
    assert point.weekday == 1


def test_unsinnige_zieldaten_werden_abgewiesen(client_als, session: Session) -> None:
    zone = create_zone(session, "bad")
    day = create_mode(session, "tag", "Tag")
    point = _point(session, zone.id, 1, 360, day.id)
    mandant = client_als([("zone.read", None), ("schedule.manage", None)])
    for daten in (
        {"weekday": "9", "time_of_day": "07:00"},
        {"weekday": "kein Tag", "time_of_day": "07:00"},
        {"weekday": "2", "time_of_day": "25:00"},
        {"weekday": "2", "time_of_day": ""},
    ):
        response = mandant.post(
            f"/zones/{zone.id}/schedule/points/move",
            data=daten | {"point_id": str(point.id)},
            headers=_csrf(mandant),
        )
        assert response.status_code == 200, daten
    session.refresh(point)
    assert (point.weekday, point.minute_of_day) == (1, 360)


def test_wochenansicht_liefert_die_punktkennung_zum_ziehen(
    client_als, session: Session
) -> None:
    """Ohne sie hat der Balken nichts, was er verschieben koennte -- und das Ziehen
    waere still wirkungslos statt sichtbar kaputt."""
    zone = create_zone(session, "bad")
    day = create_mode(session, "tag", "Tag")
    point = _point(session, zone.id, 1, 360, day.id)
    response = client_als([("zone.read", None), ("schedule.manage", None)]).get(
        f"/zones/{zone.id}/schedule"
    )
    assert f'data-punkt="{point.id}"' in response.text
    assert "zeitplan-ziehbar" in response.text


def test_ohne_schedule_manage_ist_kein_balken_ziehbar(
    client_als, session: Session
) -> None:
    """Gegenprobe: Sonst waere der obige Test auch von einer Fassung erfuellt, die
    jeden Balken fuer jeden ziehbar macht."""
    zone = create_zone(session, "bad")
    day = create_mode(session, "tag", "Tag")
    _point(session, zone.id, 1, 360, day.id)
    response = client_als([("zone.read", None)]).get(f"/zones/{zone.id}/schedule")
    assert response.status_code == 200
    assert "zeitplan-ziehbar" not in response.text


def test_der_anlegeweg_meldet_weiter_am_feld(client_als, session: Session) -> None:
    """Gegenprobe zum eigenen Kanal fuer Verschiebefehler: Der Weg ueber das Formular
    soll seine Meldung weiterhin dort bekommen, wo die Eingabe steht."""
    zone = create_zone(session, "bad")
    source(session, "web")
    day = create_mode(session, "tag", "Tag")
    _point(session, zone.id, 1, 360, day.id)
    mandant = client_als([("zone.read", None), ("schedule.manage", None)])

    response = mandant.post(
        f"/zones/{zone.id}/schedule/points",
        data={"weekday": "1", "time_of_day": "06:00", "modus": str(day.id)},
        headers=_csrf(mandant),
    )
    assert response.status_code == 200
    assert "bereits einen Punkt" in response.text
    assert "data-verschiebefehler" not in response.text
