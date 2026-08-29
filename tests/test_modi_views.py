from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.hilfen import einstellungen_anlegen, modus_anlegen, quelle, zone_anlegen
from thermoctl.auth.csrf import csrf_token
from thermoctl.auth.sessions import COOKIE_NAME
from thermoctl.config import get_settings
from thermoctl.db.models.operations import AuditEvent
from thermoctl.db.models.zone import SetpointMode, ZoneSetpoint


def _csrf(client: TestClient) -> dict[str, str]:
    geheimnis = client.cookies[COOKIE_NAME]
    token = csrf_token(geheimnis, get_settings().secret_key.get_secret_value())
    return {"X-CSRF-Token": token}


def test_modusliste_braucht_mode_manage(client_als) -> None:
    assert client_als([("zone.read", None)]).get("/modi").status_code == 403
    assert client_als([("mode.manage", None)]).get("/modi").status_code == 200


def test_modus_neu_formular_wird_angezeigt(client_als) -> None:
    antwort = client_als([("mode.manage", None)]).get("/modi/neu")
    assert antwort.status_code == 200
    assert "Technischer Code" in antwort.text


def test_modus_wird_angelegt_und_auditiert(client_als, session: Session) -> None:
    quelle(session)
    client = client_als([("mode.manage", None)])
    antwort = client.post(
        "/modi",
        data={"code": "urlaub", "name": "Urlaub", "sort_order": "30"},
        headers=_csrf(client),
        follow_redirects=False,
    )

    modus = session.scalar(select(SetpointMode).where(SetpointMode.code == "urlaub"))
    assert antwort.status_code == 303
    assert modus is not None and modus.name == "Urlaub" and modus.sort_order == 30
    assert session.scalar(
        select(AuditEvent).where(
            AuditEvent.object_type == "setpoint_mode", AuditEvent.action == "create"
        )
    ) is not None


def test_doppelter_code_kommt_mit_wert_ins_formular_zurueck(
    client_als, session: Session
) -> None:
    modus_anlegen(session, "tag", "Tag")
    client = client_als([("mode.manage", None)])
    antwort = client.post(
        "/modi",
        data={"code": "tag", "name": "Mein Tag", "sort_order": "0"},
        headers=_csrf(client),
    )

    assert antwort.status_code == 200
    assert "bereits vergeben" in antwort.text
    assert 'value="Mein Tag"' in antwort.text


def test_modus_bearbeiten_formular_zeigt_werte(client_als, session: Session) -> None:
    modus = modus_anlegen(session, "nacht", "Nacht")
    antwort = client_als([("mode.manage", None)]).get(f"/modi/{modus.id}")
    assert antwort.status_code == 200
    assert 'value="nacht"' in antwort.text


def test_modus_wird_geaendert(client_als, session: Session) -> None:
    quelle(session)
    modus = modus_anlegen(session, "nacht", "Nacht")
    client = client_als([("mode.manage", None)])
    antwort = client.post(
        f"/modi/{modus.id}",
        data={"code": "abend", "name": "Abend", "sort_order": "20"},
        headers=_csrf(client),
        follow_redirects=False,
    )

    assert antwort.status_code == 303
    assert (modus.code, modus.name, modus.sort_order) == ("abend", "Abend", 20)
    assert session.scalar(
        select(AuditEvent).where(
            AuditEvent.object_id == str(modus.id), AuditEvent.action == "update"
        )
    ) is not None


def test_loeschformular_fuer_freien_modus(client_als, session: Session) -> None:
    modus = modus_anlegen(session, "urlaub", "Urlaub")
    antwort = client_als([("mode.manage", None)]).get(f"/modi/{modus.id}/loeschen")
    assert antwort.status_code == 200
    assert "wirklich gelöscht" in antwort.text


def test_freier_modus_wird_geloescht(client_als, session: Session) -> None:
    quelle(session)
    modus = modus_anlegen(session, "urlaub", "Urlaub")
    modus_id = modus.id
    client = client_als([("mode.manage", None)])
    antwort = client.post(
        f"/modi/{modus_id}/loeschen", headers=_csrf(client), follow_redirects=False
    )

    assert antwort.status_code == 303
    assert session.get(SetpointMode, modus_id) is None
    assert session.scalar(
        select(AuditEvent).where(
            AuditEvent.object_id == str(modus_id), AuditEvent.action == "delete"
        )
    ) is not None


def test_eingebauter_modus_ist_mit_begruendung_nicht_loeschbar(
    client_als, session: Session
) -> None:
    modus = modus_anlegen(session, "tag", "Tag")
    modus.is_builtin = True
    client = client_als([("mode.manage", None)])

    formular = client.get(f"/modi/{modus.id}/loeschen")
    antwort = client.post(f"/modi/{modus.id}/loeschen", headers=_csrf(client))

    assert "weil die Anwendung sie benötigt" in formular.text
    assert "weil die Anwendung sie benötigt" in antwort.text
    assert session.get(SetpointMode, modus.id) is modus


def test_frostschutzmodus_ist_mit_begruendung_nicht_loeschbar(
    client_als, session: Session
) -> None:
    einstellungen = einstellungen_anlegen(session)
    client = client_als([("mode.manage", None)])
    antwort = client.get(f"/modi/{einstellungen.frost_protection_mode_id}/loeschen")

    assert antwort.status_code == 200
    assert (
        "Der Frostschutzmodus kann nicht gelöscht werden — er ist die Rückfallebene, "
        "wenn ein Sensor ausfällt."
    ) in antwort.text


def test_sollwertformular_zeigt_alle_modi_und_hilfetext(client_als, session: Session) -> None:
    zone = zone_anlegen(session, "bad")
    modus_anlegen(session, "tag", "Tag")
    modus_anlegen(session, "nacht", "Nacht")
    antwort = client_als([("setpoint.write", zone.id)]).get(
        f"/zonen/{zone.id}/sollwerte"
    )

    assert antwort.status_code == 200
    assert "Tag (°C)" in antwort.text and "Nacht (°C)" in antwort.text
    assert "Leer lassen löscht den Sollwert" in antwort.text


def test_sollwerte_werden_als_decimal_gespeichert_und_auditiert(
    client_als, session: Session
) -> None:
    quelle(session)
    zone = zone_anlegen(session, "bad")
    modus = modus_anlegen(session, "tag", "Tag")
    client = client_als([("setpoint.write", zone.id)])
    antwort = client.post(
        f"/zonen/{zone.id}/sollwerte",
        data={f"sollwert_{modus.id}": "21.5"},
        headers=_csrf(client),
        follow_redirects=False,
    )

    sollwert = session.get(ZoneSetpoint, (zone.id, modus.id))
    assert antwort.status_code == 303
    assert sollwert is not None and sollwert.temperature_c == Decimal("21.5")
    assert session.scalar(
        select(AuditEvent).where(
            AuditEvent.object_type == "zone_setpoint",
            AuditEvent.object_id == str(zone.id),
        )
    ) is not None


def test_sollwert_ausserhalb_der_grenzen_wird_am_feld_abgewiesen(
    client_als, session: Session
) -> None:
    zone = zone_anlegen(session, "bad")
    modus = modus_anlegen(session, "tag", "Tag")
    client = client_als([("setpoint.write", zone.id)])
    antwort = client.post(
        f"/zonen/{zone.id}/sollwerte",
        data={f"sollwert_{modus.id}": "36.0"},
        headers=_csrf(client),
    )

    assert antwort.status_code == 200
    assert "zwischen 5,0 und 35,0 °C" in antwort.text
    assert 'value="36.0"' in antwort.text
    assert session.get(ZoneSetpoint, (zone.id, modus.id)) is None


def test_sollwert_mit_zwei_nachkommastellen_wird_abgewiesen(
    client_als, session: Session
) -> None:
    zone = zone_anlegen(session, "bad")
    modus = modus_anlegen(session, "tag", "Tag")
    client = client_als([("setpoint.write", zone.id)])
    antwort = client.post(
        f"/zonen/{zone.id}/sollwerte",
        data={f"sollwert_{modus.id}": "21.25"},
        headers=_csrf(client),
    )

    assert antwort.status_code == 200
    assert "höchstens eine Nachkommastelle" in antwort.text
    assert session.get(ZoneSetpoint, (zone.id, modus.id)) is None


def test_leeres_sollwertfeld_loescht_die_zeile(client_als, session: Session) -> None:
    quelle(session)
    zone = zone_anlegen(session, "bad")
    modus = modus_anlegen(session, "tag", "Tag")
    session.add(
        ZoneSetpoint(zone_id=zone.id, setpoint_mode_id=modus.id, temperature_c=Decimal("20.0"))
    )
    session.flush()
    client = client_als([("setpoint.write", zone.id)])
    antwort = client.post(
        f"/zonen/{zone.id}/sollwerte",
        data={f"sollwert_{modus.id}": ""},
        headers=_csrf(client),
        follow_redirects=False,
    )

    assert antwort.status_code == 303
    assert session.get(ZoneSetpoint, (zone.id, modus.id)) is None


def test_fremde_zone_ergibt_404(client_als, session: Session) -> None:
    eigene = zone_anlegen(session, "bad")
    fremde = zone_anlegen(session, "kueche")
    client = client_als([("setpoint.write", eigene.id)])

    assert client.get(f"/zonen/{fremde.id}/sollwerte").status_code == 404
    assert (
        client.post(
            f"/zonen/{fremde.id}/sollwerte", data={}, headers=_csrf(client)
        ).status_code
        == 404
    )
