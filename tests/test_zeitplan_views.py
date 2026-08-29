from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.hilfen import modus_anlegen, quelle, zone_anlegen
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


def _punkt(session: Session, zone_id: int, tag: int, minute: int, modus_id: int) -> SchedulePoint:
    punkt = SchedulePoint(
        zone_id=zone_id,
        weekday=tag,
        minute_of_day=minute,
        setpoint_mode_id=modus_id,
    )
    session.add(punkt)
    session.flush()
    return punkt


def test_wochenansicht_zeigt_ringuebergang_von_sonntag_auf_montag(
    client_als, session: Session
) -> None:
    zone = zone_anlegen(session, "bad")
    tag = modus_anlegen(session, "tag", "Tag")
    nacht = modus_anlegen(session, "nacht", "Nacht")
    _punkt(session, zone.id, 1, 360, tag.id)
    _punkt(session, zone.id, 7, 1320, nacht.id)

    antwort = client_als([("zone.read", zone.id)]).get(f"/zonen/{zone.id}/zeitplan")

    assert antwort.status_code == 200
    assert "Montag" in antwort.text and "Sonntag" in antwort.text
    assert 'title="Nacht ab 00:00"' in antwort.text
    assert 'title="Tag ab 06:00"' in antwort.text
    assert 'title="Nacht ab 22:00"' in antwort.text
    assert "Schaltpunkt anlegen" not in antwort.text


def test_punkt_anlegen_und_doppelbelegung_verstaendlich_melden(
    client_als, session: Session
) -> None:
    quelle(session)
    zone = zone_anlegen(session, "bad")
    modus = modus_anlegen(session, "tag", "Tag")
    client = client_als([("schedule.manage", zone.id), ("zone.read", zone.id)])
    daten = {"wochentag": "2", "uhrzeit": "03:15", "modus": str(modus.id)}

    angelegt = client.post(
        f"/zonen/{zone.id}/zeitplan/punkte",
        data=daten,
        headers=_csrf(client),
        follow_redirects=False,
    )
    doppelt = client.post(
        f"/zonen/{zone.id}/zeitplan/punkte", data=daten, headers=_csrf(client)
    )

    punkt = session.scalar(select(SchedulePoint).where(SchedulePoint.zone_id == zone.id))
    assert angelegt.status_code == 303
    assert punkt is not None and (punkt.weekday, punkt.minute_of_day) == (2, 195)
    assert doppelt.status_code == 200
    assert "Zu diesem Zeitpunkt gibt es bereits einen Punkt." in doppelt.text
    assert 'value="03:15"' in doppelt.text
    assert session.scalar(
        select(AuditEvent).where(AuditEvent.object_type == "schedule_point")
    ) is not None


def test_ungueltige_punkte_bleiben_mit_eingaben_im_formular(
    client_als, session: Session
) -> None:
    zone = zone_anlegen(session, "bad")
    client = client_als([("schedule.manage", zone.id)])
    antwort = client.post(
        f"/zonen/{zone.id}/zeitplan/punkte",
        data={"wochentag": "2", "uhrzeit": "25:61", "modus": "unbekannt"},
        headers=_csrf(client),
    )
    assert antwort.status_code == 200
    assert "gültige Uhrzeit" in antwort.text
    assert 'value="25:61"' in antwort.text


def test_punkt_loeschen_bestaetigen_und_ausfuehren(client_als, session: Session) -> None:
    quelle(session)
    zone = zone_anlegen(session, "bad")
    modus = modus_anlegen(session, "tag", "Tag")
    punkt = _punkt(session, zone.id, 2, 180, modus.id)
    punkt_id = punkt.id
    client = client_als([("schedule.manage", zone.id)])

    formular = client.get(
        f"/zonen/{zone.id}/zeitplan/punkte/{punkt_id}/loeschen"
    )
    antwort = client.post(
        f"/zonen/{zone.id}/zeitplan/punkte/{punkt_id}/loeschen",
        headers=_csrf(client),
        follow_redirects=False,
    )

    assert formular.status_code == 200 and "Dienstag" in formular.text
    assert antwort.status_code == 303
    assert session.get(SchedulePoint, punkt_id) is None


def test_zeitplan_uebernehmen_kopiert_genau_und_laesst_quelle_unveraendert(
    client_als, session: Session
) -> None:
    quelle(session)
    quellzone = zone_anlegen(session, "quelle")
    ziel = zone_anlegen(session, "ziel")
    tag = modus_anlegen(session, "tag", "Tag")
    nacht = modus_anlegen(session, "nacht", "Nacht")
    _punkt(session, quellzone.id, 1, 360, tag.id)
    _punkt(session, quellzone.id, 7, 1320, nacht.id)
    client = client_als(
        [("schedule.manage", ziel.id), ("zone.read", quellzone.id)]
    )

    antwort = client.post(
        f"/zonen/{ziel.id}/zeitplan/uebernehmen",
        data={"quelle_id": str(quellzone.id)},
        headers=_csrf(client),
        follow_redirects=False,
    )

    quellpunkte = session.scalars(
        select(SchedulePoint).where(SchedulePoint.zone_id == quellzone.id)
    ).all()
    zielpunkte = session.scalars(
        select(SchedulePoint).where(SchedulePoint.zone_id == ziel.id)
    ).all()
    assert antwort.status_code == 303
    assert [(p.weekday, p.minute_of_day, p.setpoint_mode_id) for p in zielpunkte] == [
        (p.weekday, p.minute_of_day, p.setpoint_mode_id) for p in quellpunkte
    ]
    assert len(quellpunkte) == 2
    assert session.scalar(
        select(AuditEvent).where(
            AuditEvent.object_type == "schedule", AuditEvent.object_id == str(ziel.id)
        )
    ) is not None


def test_uebernahme_mit_bestehendem_plan_fragt_vorher_nach(
    client_als, session: Session
) -> None:
    quelle(session)
    quellzone = zone_anlegen(session, "quelle")
    ziel = zone_anlegen(session, "ziel")
    modus = modus_anlegen(session, "tag", "Tag")
    alter_punkt = _punkt(session, ziel.id, 2, 180, modus.id)
    _punkt(session, quellzone.id, 1, 360, modus.id)
    client = client_als(
        [("schedule.manage", ziel.id), ("zone.read", quellzone.id)]
    )
    pfad = f"/zonen/{ziel.id}/zeitplan/uebernehmen"

    nachfrage = client.post(
        pfad,
        data={"quelle_id": str(quellzone.id)},
        headers=_csrf(client),
    )
    assert nachfrage.status_code == 200
    assert "ersetzt ihn vollständig" in nachfrage.text
    assert session.get(SchedulePoint, alter_punkt.id) is alter_punkt

    bestaetigt = client.post(
        pfad,
        data={"quelle_id": str(quellzone.id), "bestaetigt": "ja"},
        headers=_csrf(client),
        follow_redirects=False,
    )
    assert bestaetigt.status_code == 303
    assert session.get(SchedulePoint, alter_punkt.id) is None


def test_rechte_und_fremde_zonen_geben_404(client_als, session: Session) -> None:
    eigene = zone_anlegen(session, "eigene")
    fremde = zone_anlegen(session, "fremde")
    modus = modus_anlegen(session, "tag", "Tag")
    leser = client_als([("zone.read", eigene.id)])
    assert leser.get(f"/zonen/{fremde.id}/zeitplan").status_code == 404
    assert (
        leser.post(
            f"/zonen/{eigene.id}/zeitplan/punkte",
            data={"wochentag": "1", "uhrzeit": "06:00", "modus": str(modus.id)},
            headers=_csrf(leser),
        ).status_code
        == 404
    )

    verwalter = client_als([("schedule.manage", eigene.id), ("zone.read", eigene.id)])
    assert (
        verwalter.get(f"/zonen/{fremde.id}/zeitplan/uebernehmen").status_code == 404
    )


def test_uebernahmeformular_und_fehlerhafte_auswahl(client_als, session: Session) -> None:
    ziel = zone_anlegen(session, "ziel")
    client = client_als([("schedule.manage", ziel.id)])
    pfad = f"/zonen/{ziel.id}/zeitplan/uebernehmen"
    assert client.get(pfad).status_code == 200
    antwort = client.post(pfad, data={}, headers=_csrf(client))
    assert antwort.status_code == 200
    assert "Bitte eine Quellzone auswählen." in antwort.text
