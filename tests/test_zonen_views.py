from datetime import datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.hilfen import (
    betriebsart,
    geraet_anlegen,
    quelle,
    rolle,
    zone_anlegen,
)
from thermoctl.auth.csrf import CSRF_HEADER, csrf_token
from thermoctl.auth.sessions import COOKIE_NAME
from thermoctl.config import get_settings
from thermoctl.db.models.device import ZoneDevice
from thermoctl.db.models.operations import AuditEvent
from thermoctl.db.models.override import ZoneOverride
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.zone import SetpointMode, Zone, ZoneSetpoint
from thermoctl.db.models.zustand import ShadowDecision


def _csrf(client: TestClient) -> dict[str, str]:
    sitzung = client.cookies.get(COOKIE_NAME)
    assert sitzung is not None
    geheimnis = get_settings().secret_key.get_secret_value()
    return {CSRF_HEADER: csrf_token(sitzung, geheimnis)}


def _daten(session: Session, name: str = "wohnzimmer") -> dict[str, str]:
    return {
        "name": name,
        "display_name": "Wohnzimmer",
        "operating_mode": str(betriebsart(session).id),
        "sort_order": "4",
        "temperature_source_device_id": "",
    }


def test_zonenliste_zeigt_nur_sichtbare_zonen(client_als, session: Session) -> None:
    sichtbar = zone_anlegen(session, "sichtbar")
    zone_anlegen(session, "verborgen")

    antwort = client_als([("zone.read", sichtbar.id)]).get("/zonen")

    assert antwort.status_code == 200
    assert sichtbar.display_name in antwort.text
    assert "Verborgen" not in antwort.text


def test_leeres_zonenformular_braucht_anlagenweites_recht(client_als, session: Session) -> None:
    zone = zone_anlegen(session, "bestehend")
    assert client_als([("zone.manage", zone.id)]).get("/zonen/neu").status_code == 403
    assert client_als([("zone.manage", None)]).get("/zonen/neu").status_code == 200


def test_zone_anlegen_schreibt_audit(client_als, session: Session) -> None:
    quelle(session)
    client = client_als([("zone.manage", None)])

    antwort = client.post(
        "/zonen", data=_daten(session), headers=_csrf(client), follow_redirects=False
    )

    assert antwort.status_code == 303
    zone = session.scalar(select(Zone).where(Zone.name == "wohnzimmer"))
    assert zone is not None
    audit = session.scalar(select(AuditEvent).where(AuditEvent.object_id == str(zone.id)))
    assert audit is not None
    assert audit.action == "create"
    assert audit.actor_user_id is not None


def test_doppelter_name_bleibt_mit_feldmeldung_im_formular(
    client_als, session: Session
) -> None:
    zone_anlegen(session, "wohnzimmer")
    client = client_als([("zone.manage", None)])

    antwort = client.post("/zonen", data=_daten(session), headers=_csrf(client))

    assert antwort.status_code == 200
    assert "Dieser Name ist bereits vergeben." in antwort.text
    assert 'name="display_name" value="Wohnzimmer"' in antwort.text


def test_zone_bearbeiten_zeigt_werte_und_speichert_aenderung(
    client_als, session: Session
) -> None:
    quelle(session)
    zone = zone_anlegen(session, "alt")
    client = client_als([("zone.manage", zone.id)])
    formular = client.get(f"/zonen/{zone.id}")
    assert formular.status_code == 200
    assert 'value="alt"' in formular.text

    antwort = client.post(
        f"/zonen/{zone.id}",
        data=_daten(session, "neu"),
        headers=_csrf(client),
        follow_redirects=False,
    )
    assert antwort.status_code == 303
    assert zone.name == "neu"
    assert zone.sort_order == 4
    assert session.scalar(
        select(AuditEvent).where(
            AuditEvent.object_id == str(zone.id), AuditEvent.action == "update"
        )
    ) is not None


def test_zonenrecht_fuer_andere_zone_liefert_404(client_als, session: Session) -> None:
    eigene = zone_anlegen(session, "eigene")
    fremde = zone_anlegen(session, "fremde")
    client = client_als([("zone.manage", eigene.id)])

    assert client.get(f"/zonen/{fremde.id}").status_code == 404
    assert client.post(
        f"/zonen/{fremde.id}", data=_daten(session), headers=_csrf(client)
    ).status_code == 404


def test_loeschbestaetigung_nennt_alle_abhaengigkeiten(
    client_als, session: Session
) -> None:
    zone = zone_anlegen(session, "voll")
    geraet = geraet_anlegen(session, "sensor")
    session.add(
        ZoneDevice(
            zone_id=zone.id,
            device_id=geraet.id,
            device_role_id=rolle(session, "controller").id,
        )
    )
    modus = SetpointMode(code="tag", name="Tag")
    session.add(modus)
    session.flush()
    session.add_all(
        [
            SchedulePoint(
                zone_id=zone.id, weekday=1, minute_of_day=0, setpoint_mode_id=modus.id
            ),
            ZoneSetpoint(
                zone_id=zone.id, setpoint_mode_id=modus.id, temperature_c=Decimal("20.0")
            ),
            ZoneOverride(
                zone_id=zone.id,
                temperature_c=Decimal("21.0"),
                starts_at=datetime(2026, 8, 29),
                source_id=quelle(session).id,
            ),
            ShadowDecision(
                decided_at=datetime(2026, 8, 29),
                zone_id=zone.id,
                temperature_c=Decimal("19.0"),
                setpoint_c=Decimal("20.0"),
                setpoint_reason="Zeitplan",
                would_heat=True,
                outcome_code="ein",
                reason="Unter Sollwert",
            ),
        ]
    )
    session.flush()

    antwort = client_als([("zone.manage", zone.id)]).get(
        f"/zonen/{zone.id}/loeschen"
    )

    assert antwort.status_code == 200
    for erwartet in (
        "1 Schaltpunkte",
        "1 zugeordnete Geräte",
        "1 Sollwerte",
        "1 Übersteuerungen",
        "1 Schattenentscheidungen",
    ):
        assert erwartet in antwort.text


def test_zone_loeschen_entfernt_kaskaden_und_schreibt_audit(
    client_als, session: Session
) -> None:
    quelle(session)
    zone = zone_anlegen(session, "weg")
    modus = SetpointMode(code="nacht", name="Nacht")
    session.add(modus)
    session.flush()
    punkt = SchedulePoint(
        zone_id=zone.id, weekday=1, minute_of_day=0, setpoint_mode_id=modus.id
    )
    session.add(punkt)
    session.flush()
    zone_id = zone.id
    punkt_id = punkt.id
    client = client_als([("zone.manage", zone.id)])

    antwort = client.post(
        f"/zonen/{zone.id}/loeschen", headers=_csrf(client), follow_redirects=False
    )

    assert antwort.status_code == 303
    session.flush()
    session.expire_all()
    assert session.get(Zone, zone_id) is None
    assert session.get(SchedulePoint, punkt_id) is None
    assert session.scalar(
        select(AuditEvent).where(
            AuditEvent.object_id == str(zone_id), AuditEvent.action == "delete"
        )
    ) is not None


def test_ungueltige_eingaben_fuehren_zurueck_ins_formular(
    client_als, session: Session
) -> None:
    """Jede Verzweigung der Eingabepruefung einzeln — beim Anlegen und beim Aendern.

    Der Bericht des Umsetzenden zaehlt nicht, solange niemand es nachvollzogen hat: Die
    ganze Pruefung war bis hierher unbelegt, und genau diese Art Luecke hat in diesem
    Projekt schon zweimal grundlegende Fehler durchgelassen.
    """
    quelle(session, "web")
    art = betriebsart(session, "auto")
    zone = zone_anlegen(session, "bestehende-zone")
    client = client_als([("zone.manage", None), ("zone.read", None)])

    gueltig = {
        "name": "neu", "display_name": "Neu", "operating_mode": str(art.id),
        "sort_order": "0", "temperature_source_device_id": "",
    }
    faelle = [
        ({"name": ""}, "technischen Namen"),
        ({"display_name": ""}, "Anzeigenamen"),
        ({"operating_mode": ""}, "Betriebsart auswählen"),
        ({"operating_mode": "999999"}, "nicht bekannt"),
        ({"sort_order": "oben"}, "ganze Zahl"),
        ({"temperature_source_device_id": "kein Gerät"}, "bekanntes Gerät"),
        ({"temperature_source_device_id": "999999"}, "nicht bekannt"),
    ]
    for abweichung, erwartet in faelle:
        daten = {**gueltig, **abweichung}
        anlegen = client.post("/zonen", data=daten, headers=_csrf(client))
        assert anlegen.status_code == 200, abweichung
        assert erwartet in anlegen.text, abweichung

        aendern = client.post(f"/zonen/{zone.id}", data=daten, headers=_csrf(client))
        assert aendern.status_code == 200, abweichung
        assert erwartet in aendern.text, abweichung

    assert session.scalar(select(Zone).where(Zone.name == "neu")) is None
    assert zone.name == "bestehende-zone"


def test_umbenennen_auf_einen_vergebenen_namen_bleibt_im_formular(
    client_als, session: Session
) -> None:
    """Beim Anlegen war der Fall belegt, beim Aendern nicht — es ist derselbe Konflikt."""
    quelle(session, "web")
    art = betriebsart(session, "auto")
    zone_anlegen(session, "schon-da")
    andere = zone_anlegen(session, "wird-umbenannt")
    client = client_als([("zone.manage", None), ("zone.read", None)])
    antwort = client.post(
        f"/zonen/{andere.id}",
        data={
            "name": "schon-da", "display_name": "Andere", "operating_mode": str(art.id),
            "sort_order": "0", "temperature_source_device_id": "",
        },
        headers=_csrf(client),
    )
    assert antwort.status_code == 200
    assert "bereits vergeben" in antwort.text
    assert andere.name == "wird-umbenannt"


# --- Betriebsart aus der Ferne ---------------------------------------------


def test_betriebsart_setzen_schreibt_und_protokolliert(session: Session) -> None:
    """Eigene Funktion neben `zone_aendern`: Ein Befehl von aussen kennt nur die
    Betriebsart und wuerde mit `zone_aendern` alles andere mit dem ueberschreiben, was
    der Aufrufer gerade zufaellig zur Hand hat."""
    from sqlalchemy import select

    from tests.hilfen import quelle
    from thermoctl.db.models.lookup import OperatingMode
    from thermoctl.db.models.operations import AuditEvent
    from thermoctl.domain.zonen import betriebsart_setzen

    zone = zone_anlegen(session, "betriebsartzone")
    quelle(session, "system")
    aus = OperatingMode(code="off", label="Aus")
    session.add(aus)
    session.flush()

    assert betriebsart_setzen(session, zone, "off", akteur_id=None, quelle="system") is True
    assert zone.operating_mode_id == aus.id
    eintrag = session.scalars(
        select(AuditEvent).where(AuditEvent.object_type == "zone")
    ).one()
    assert "Aus" in (eintrag.detail or "")


def test_dieselbe_betriebsart_schreibt_keinen_eintrag(session: Session) -> None:
    """Home Assistant schickt seinen Zustand gern noch einmal. Ein Protokoll, das jede
    Wiederholung als Aenderung fuehrt, ist nach einer Woche unlesbar."""
    from sqlalchemy import select

    from tests.hilfen import quelle
    from thermoctl.db.models.operations import AuditEvent
    from thermoctl.domain.zonen import betriebsart_setzen

    zone = zone_anlegen(session, "wiederholungszone")
    quelle(session, "system")
    code = zone.operating_mode.code

    assert betriebsart_setzen(session, zone, code, akteur_id=None, quelle="system") is False
    assert not session.scalars(
        select(AuditEvent).where(AuditEvent.object_type == "zone")
    ).all()


def test_unbekannte_betriebsart_wird_abgewiesen(session: Session) -> None:
    from thermoctl.domain.zonen import Betriebsartunbekannt, betriebsart_setzen

    zone = zone_anlegen(session, "unbekanntbetrieb")
    with pytest.raises(Betriebsartunbekannt):
        betriebsart_setzen(session, zone, "gemuetlich", akteur_id=None, quelle="system")
