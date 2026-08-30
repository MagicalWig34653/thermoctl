from datetime import datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.hilfen import (
    geraet_anlegen,
    modus_anlegen,
    quelle,
    rolle,
    zone_anlegen,
)
from thermoctl.auth.csrf import CSRF_HEADER, csrf_token
from thermoctl.auth.sessions import COOKIE_NAME
from thermoctl.config import get_settings
from thermoctl.db.models.device import ZoneDevice
from thermoctl.db.models.lookup import DeviceCapability
from thermoctl.db.models.messwert import Measurement
from thermoctl.db.models.operations import AuditEvent
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.zone import ZoneSetpoint
from thermoctl.domain.geraetezuordnung import geraet_tauschen


def _csrf(client: TestClient) -> dict[str, str]:
    sitzung = client.cookies.get(COOKIE_NAME)
    assert sitzung is not None
    return {
        CSRF_HEADER: csrf_token(
            sitzung, get_settings().secret_key.get_secret_value()
        )
    }


def _zuordnen(session: Session, zone_id: int, geraet_id: int, rollencode: str) -> None:
    session.add(
        ZoneDevice(
            zone_id=zone_id,
            device_id=geraet_id,
            device_role_id=rolle(session, rollencode).id,
        )
    )
    session.flush()


def test_tausch_erhaelt_zonenkonfiguration_und_uebernimmt_alle_rollen(
    session: Session,
) -> None:
    quelle(session)
    zone = zone_anlegen(session, "wohnzimmer")
    zone.hysteresis_k = Decimal("0.45")
    zone.min_on_seconds = 420
    zone.sensor_timeout_seconds = 900
    modus = modus_anlegen(session, "tag")
    session.add_all(
        [
            ZoneSetpoint(
                zone_id=zone.id,
                setpoint_mode_id=modus.id,
                temperature_c=Decimal("21.5"),
            ),
            SchedulePoint(
                zone_id=zone.id,
                weekday=1,
                minute_of_day=360,
                setpoint_mode_id=modus.id,
            ),
        ]
    )
    altes = geraet_anlegen(session, "alt")
    neues = geraet_anlegen(session, "neu")
    _zuordnen(session, zone.id, altes.id, "actuator")
    _zuordnen(session, zone.id, altes.id, "controller")
    session.flush()
    vorher_sollwerte = list(
        session.execute(
            select(
                ZoneSetpoint.zone_id,
                ZoneSetpoint.setpoint_mode_id,
                ZoneSetpoint.temperature_c,
            ).where(ZoneSetpoint.zone_id == zone.id)
        )
    )
    vorher_zeitplan = list(
        session.execute(
            select(
                SchedulePoint.zone_id,
                SchedulePoint.weekday,
                SchedulePoint.minute_of_day,
                SchedulePoint.setpoint_mode_id,
            ).where(SchedulePoint.zone_id == zone.id)
        )
    )
    vorher_parameter = (
        zone.hysteresis_k,
        zone.min_on_seconds,
        zone.min_off_seconds,
        zone.sensor_timeout_seconds,
        zone.temperature_offset_k,
        zone.window_resume_delay_seconds,
    )

    geraet_tauschen(session, zone, altes, neues, akteur_id=None)

    assert list(
        session.execute(
            select(
                ZoneSetpoint.zone_id,
                ZoneSetpoint.setpoint_mode_id,
                ZoneSetpoint.temperature_c,
            ).where(ZoneSetpoint.zone_id == zone.id)
        )
    ) == vorher_sollwerte
    assert list(
        session.execute(
            select(
                SchedulePoint.zone_id,
                SchedulePoint.weekday,
                SchedulePoint.minute_of_day,
                SchedulePoint.setpoint_mode_id,
            ).where(SchedulePoint.zone_id == zone.id)
        )
    ) == vorher_zeitplan
    assert (
        zone.hysteresis_k,
        zone.min_on_seconds,
        zone.min_off_seconds,
        zone.sensor_timeout_seconds,
        zone.temperature_offset_k,
        zone.window_resume_delay_seconds,
    ) == vorher_parameter
    neue_rollen = set(
        session.scalars(
            select(ZoneDevice.device_role_id).where(
                ZoneDevice.zone_id == zone.id, ZoneDevice.device_id == neues.id
            )
        )
    )
    assert neue_rollen == {rolle(session, "actuator").id, rolle(session, "controller").id}
    assert session.scalar(
        select(ZoneDevice).where(
            ZoneDevice.zone_id == zone.id, ZoneDevice.device_id == altes.id
        )
    ) is None


def test_tausch_laesst_messhistorie_beim_alten_geraet(session: Session) -> None:
    quelle(session)
    zone = zone_anlegen(session, "bad")
    altes = geraet_anlegen(session, "sensor-alt")
    neues = geraet_anlegen(session, "sensor-neu")
    zone.temperature_source_device_id = altes.id
    faehigkeit = DeviceCapability(code="temperature", label="Temperatur")
    session.add(faehigkeit)
    session.flush()
    messwert = Measurement(
        device_id=altes.id,
        capability_id=faehigkeit.id,
        value_numeric=Decimal("19.750"),
        measured_at=datetime(2026, 8, 29, 10, 0),
        received_at=datetime(2026, 8, 29, 10, 0),
    )
    session.add(messwert)
    session.flush()

    geraet_tauschen(session, zone, altes, neues, akteur_id=None)

    assert zone.temperature_source_device_id == neues.id
    assert session.get(Measurement, messwert.id).device_id == altes.id
    assert session.scalar(
        select(Measurement).where(Measurement.device_id == neues.id)
    ) is None


def test_tausch_in_einer_zone_laesst_zweite_zone_unberuehrt(session: Session) -> None:
    quelle(session)
    zone_a = zone_anlegen(session, "a")
    zone_b = zone_anlegen(session, "b")
    altes = geraet_anlegen(session, "alt-gemeinsam")
    neues = geraet_anlegen(session, "neu-a")
    _zuordnen(session, zone_a.id, altes.id, "window_contact")
    _zuordnen(session, zone_b.id, altes.id, "window_contact")

    geraet_tauschen(session, zone_a, altes, neues, akteur_id=None)

    assert session.scalar(
        select(ZoneDevice).where(
            ZoneDevice.zone_id == zone_b.id, ZoneDevice.device_id == altes.id
        )
    ) is not None
    assert session.scalar(
        select(ZoneDevice).where(
            ZoneDevice.zone_id == zone_b.id, ZoneDevice.device_id == neues.id
        )
    ) is None


def test_tausch_schreibt_audit(session: Session) -> None:
    quelle(session)
    zone = zone_anlegen(session, "audit-zone")
    altes = geraet_anlegen(session, "audit-alt")
    neues = geraet_anlegen(session, "audit-neu")
    _zuordnen(session, zone.id, altes.id, "actuator")

    geraet_tauschen(session, zone, altes, neues, akteur_id=None)

    eintrag = session.scalar(
        select(AuditEvent).where(
            AuditEvent.object_type == "zone_device", AuditEvent.action == "replace"
        )
    )
    assert eintrag is not None
    assert "audit-alt" in eintrag.summary
    assert "audit-neu" in eintrag.summary


def test_seite_zeigt_zuordnungen_und_messquelle(client_als, session: Session) -> None:
    zone = zone_anlegen(session, "anzeige")
    geraet = geraet_anlegen(session, "thermostat")
    zone.temperature_source_device_id = geraet.id
    _zuordnen(session, zone.id, geraet.id, "controller")

    antwort = client_als([("device.read", zone.id)]).get(
        f"/zonen/{zone.id}/geraete"
    )

    assert antwort.status_code == 200
    assert "thermostat" in antwort.text
    assert "controller" in antwort.text
    assert "Gerät tauschen" not in antwort.text


def test_doppelte_rolle_zeigt_verstaendliche_meldung(client_als, session: Session) -> None:
    quelle(session)
    zone = zone_anlegen(session, "doppelt")
    geraet = geraet_anlegen(session, "kontakt")
    geraeterolle = rolle(session, "window_contact")
    _zuordnen(session, zone.id, geraet.id, "window_contact")
    client = client_als([("device.manage", zone.id)])

    antwort = client.post(
        f"/zonen/{zone.id}/geraete/zuordnen",
        data={"device_id": geraet.id, "role_id": geraeterolle.id},
        headers=_csrf(client),
    )

    assert antwort.status_code == 200
    assert "in dieser Rolle bereits zugeordnet" in antwort.text


def test_aendernde_wege_und_rechte(client_als, session: Session) -> None:
    quelle(session)
    eigene = zone_anlegen(session, "eigene")
    fremde = zone_anlegen(session, "fremde")
    altes = geraet_anlegen(session, "weg")
    neues = geraet_anlegen(session, "hin")
    client = client_als([("device.manage", eigene.id)])
    kopf = _csrf(client)

    zuordnen = client.post(
        f"/zonen/{eigene.id}/geraete/zuordnen",
        data={"device_id": altes.id, "role_id": rolle(session, "actuator").id},
        headers=kopf,
        follow_redirects=False,
    )
    assert zuordnen.status_code == 303
    zuordnung = session.scalar(
        select(ZoneDevice).where(ZoneDevice.zone_id == eigene.id)
    )
    assert zuordnung is not None

    messquelle = client.post(
        f"/zonen/{eigene.id}/geraete/messquelle",
        data={"device_id": altes.id},
        headers=kopf,
        follow_redirects=False,
    )
    assert messquelle.status_code == 303
    assert eigene.temperature_source_device_id == altes.id

    tausch = client.post(
        f"/zonen/{eigene.id}/geraete/tauschen",
        data={"old_device_id": altes.id, "new_device_id": neues.id},
        headers=kopf,
        follow_redirects=False,
    )
    assert tausch.status_code == 303
    neue_zuordnung = session.scalar(
        select(ZoneDevice).where(
            ZoneDevice.zone_id == eigene.id, ZoneDevice.device_id == neues.id
        )
    )
    assert neue_zuordnung is not None

    # Die Kennung steht im Rumpf, nicht im Pfad: hx-boost liest die `action` eines
    # Formulars einmal, deshalb benutzen Tabelle und Herausziehen denselben Endpunkt.
    loesen = client.post(
        f"/zonen/{eigene.id}/geraete/loesen",
        data={"zuordnung_id": str(neue_zuordnung.id)},
        headers=kopf,
        follow_redirects=False,
    )
    assert loesen.status_code == 303
    assert session.get(ZoneDevice, neue_zuordnung.id) is None

    assert client.get(f"/zonen/{fremde.id}/geraete").status_code == 404
    assert client.post(
        f"/zonen/{fremde.id}/geraete/messquelle",
        data={"device_id": neues.id},
        headers=kopf,
    ).status_code == 404


def test_ungueltige_eingaben_bei_der_zuordnung(client_als, session: Session) -> None:
    """Jeder Fehlerweg der Zuordnungsseite — bisher war nur der Erfolgsfall belegt."""
    quelle(session)
    zone = zone_anlegen(session, "zone-fehlerwege")
    geraet = geraet_anlegen(session, "vorhanden")
    client = client_als([("device.manage", None), ("device.read", None)])
    kopf = _csrf(client)

    # Unbekanntes Geraet, unbekannte Rolle, gar keine Angabe.
    for daten in (
        {"device_id": "999999", "role_id": str(rolle(session, "actuator").id)},
        {"device_id": str(geraet.id), "role_id": "999999"},
        {"device_id": "", "role_id": ""},
        {"device_id": "kein Geraet", "role_id": "1"},
    ):
        antwort = client.post(f"/zonen/{zone.id}/geraete/zuordnen", data=daten, headers=kopf)
        assert antwort.status_code == 200, daten
        assert session.scalar(
            select(ZoneDevice).where(ZoneDevice.zone_id == zone.id)
        ) is None, daten


def test_messquelle_laesst_sich_wieder_loesen(client_als, session: Session) -> None:
    """Leeres Feld heisst 'keine Messquelle' — die Zone gilt danach als ohne Quelle."""
    quelle(session)
    zone = zone_anlegen(session, "zone-messquelle-weg")
    geraet = geraet_anlegen(session, "quelle-weg")
    client = client_als([("device.manage", None), ("device.read", None)])
    kopf = _csrf(client)
    client.post(
        f"/zonen/{zone.id}/geraete/messquelle", data={"device_id": str(geraet.id)},
        headers=kopf,
    )
    assert zone.temperature_source_device_id == geraet.id
    antwort = client.post(
        f"/zonen/{zone.id}/geraete/messquelle", data={"device_id": ""},
        headers=kopf, follow_redirects=False,
    )
    assert antwort.status_code == 303
    assert zone.temperature_source_device_id is None


def test_unbekannte_messquelle_bleibt_ohne_wirkung(client_als, session: Session) -> None:
    quelle(session)
    zone = zone_anlegen(session, "zone-messquelle-unbekannt")
    client = client_als([("device.manage", None), ("device.read", None)])
    antwort = client.post(
        f"/zonen/{zone.id}/geraete/messquelle", data={"device_id": "999999"},
        headers=_csrf(client),
    )
    assert antwort.status_code == 200
    assert zone.temperature_source_device_id is None


def test_tausch_mit_unsinnigen_geraeten_meldet_verstaendlich(
    client_als, session: Session
) -> None:
    """Drei Faelle: gleiches Geraet, unbekanntes Geraet, ein Geraet ohne Zuordnung."""
    quelle(session)
    zone = zone_anlegen(session, "zone-tausch-unsinn")
    eines = geraet_anlegen(session, "eines")
    anderes = geraet_anlegen(session, "anderes")
    client = client_als([("device.manage", None), ("device.read", None)])
    kopf = _csrf(client)

    for daten in (
        {"old_device_id": str(eines.id), "new_device_id": str(eines.id)},
        {"old_device_id": str(eines.id), "new_device_id": "999999"},
        {"old_device_id": str(eines.id), "new_device_id": str(anderes.id)},
    ):
        antwort = client.post(f"/zonen/{zone.id}/geraete/tauschen", data=daten, headers=kopf)
        assert antwort.status_code == 200, daten
        assert session.scalar(
            select(ZoneDevice).where(ZoneDevice.zone_id == zone.id)
        ) is None, daten


def test_fremde_zuordnung_laesst_sich_nicht_loesen(client_als, session: Session) -> None:
    """Eine Zuordnung einer anderen Zone ergibt 404, nicht 403."""
    quelle(session)
    eigene = zone_anlegen(session, "eigene-loesen")
    fremde = zone_anlegen(session, "fremde-loesen")
    geraet = geraet_anlegen(session, "fremdgeraet")
    fremde_zuordnung = ZoneDevice(
        zone_id=fremde.id, device_id=geraet.id,
        device_role_id=rolle(session, "actuator").id,
    )
    session.add(fremde_zuordnung)
    session.flush()
    client = client_als([("device.manage", None), ("device.read", None)])
    antwort = client.post(
        f"/zonen/{eigene.id}/geraete/loesen",
        data={"zuordnung_id": str(fremde_zuordnung.id)},
        headers=_csrf(client),
    )
    assert antwort.status_code == 404
    assert session.get(ZoneDevice, fremde_zuordnung.id) is not None


def test_loesen_einer_fremden_zuordnung_wird_in_der_domaene_abgewiesen(
    session: Session,
) -> None:
    """Die Ansicht faengt den Fall schon mit 404 ab. Die Domaene prueft trotzdem selbst:

    Sie wird spaeter auch von REST und MCP aufgerufen, und eine Regel, die nur in einem
    Adapter steht, gilt nicht fuer die anderen.
    """
    import pytest

    from thermoctl.domain.geraetezuordnung import geraet_loesen

    quelle(session)
    eine = zone_anlegen(session, "zone-loesen-a")
    andere = zone_anlegen(session, "zone-loesen-b")
    geraet = geraet_anlegen(session, "geraet-loesen")
    zuordnung = ZoneDevice(
        zone_id=andere.id, device_id=geraet.id,
        device_role_id=rolle(session, "actuator").id,
    )
    session.add(zuordnung)
    session.flush()
    with pytest.raises(ValueError, match="gehört nicht zu dieser Zone"):
        geraet_loesen(session, eine, zuordnung, akteur_id=None)
    assert session.get(ZoneDevice, zuordnung.id) is not None


def test_ablegeziele_nur_mit_device_manage(client_als, session: Session) -> None:
    """Das Ziehen ist eine zweite Bedienart derselben Aenderung -- es muss an derselben
    Rechtepruefung haengen wie die Formulare. Ein Ablegeziel, das man sieht und nicht
    benutzen darf, ist eine Einladung zu einer 403."""
    zone = zone_anlegen(session, "ziehzone")
    # Ohne ein Geraet gibt es nichts zu ziehen -- die Karten entstehen aus der Liste.
    geraet_anlegen(session, "ziehbares-geraet")

    darf = client_als([("device.read", None), ("device.manage", zone.id), ("zone.read", None)])
    seite = darf.get(f"/zonen/{zone.id}/geraete")
    assert seite.status_code == 200
    assert 'data-ziel="messquelle"' in seite.text
    assert "tc-ziehbar" in seite.text

    nur_lesen = client_als([("device.read", None), ("zone.read", None)])
    seite = nur_lesen.get(f"/zonen/{zone.id}/geraete")
    assert seite.status_code == 200
    assert "data-ziel=" not in seite.text
    assert "tc-ziehbar" not in seite.text


def test_anlagenbild_bietet_keine_ablegeziele(client_als, session: Session) -> None:
    """Gegenprobe: Auf dem Anlagenbild waere ein Ablegeziel eine Zusage, die die Seite
    nicht einloest -- dort gibt es keine Formulare, die es abschicken koennte."""
    zone_anlegen(session, "bildzone")
    seite = client_als([("device.read", None), ("device.manage", None), ("zone.read", None)]).get(
        "/anlage"
    )
    assert seite.status_code == 200
    assert "data-ziel=" not in seite.text


# --- Faehigkeitspruefung ----------------------------------------------------


def _mit_faehigkeit(session: Session, name: str, *codes: str):
    """Ein Geraet, dessen Faehigkeiten bekannt sind."""
    from tests.hilfen import faehigkeit
    from thermoctl.db.models.device import DeviceCapabilityLink

    geraet = geraet_anlegen(session, name)
    for code in codes:
        session.add(
            DeviceCapabilityLink(
                device_id=geraet.id, capability_id=faehigkeit(session, code).id
            )
        )
    session.flush()
    return geraet


def test_sensor_laesst_sich_nicht_als_aktor_zuordnen(session: Session) -> None:
    """Vorher ging das. Die Zuordnung sah danach richtig aus, das Anlagenbild zeigte
    einen vollstaendigen Weg, und geschaltet haette trotzdem nie etwas -- ein Fehler, der
    erst im Winter auffaellt und dann nach einem Regelungsfehler aussieht."""
    from thermoctl.domain.geraetezuordnung import FaehigkeitFehlt, geraet_zuordnen

    zone = zone_anlegen(session, "faehigkeitszone")
    sensor = _mit_faehigkeit(session, "nur-thermometer", "temperature", "battery")
    with pytest.raises(FaehigkeitFehlt, match="Schaltausgang"):
        geraet_zuordnen(
            session, zone, sensor, rolle(session, "actuator"), akteur_id=None
        )


def test_ventil_laesst_sich_als_aktor_zuordnen(session: Session) -> None:
    """Gegenprobe. Ohne sie waere der Test oben auch von einer Fassung erfuellt, die
    jede Zuordnung ablehnt."""
    from thermoctl.domain.geraetezuordnung import geraet_zuordnen

    zone = zone_anlegen(session, "ventilzone")
    ventil = _mit_faehigkeit(session, "echtes-ventil", "switch")
    zuordnung = geraet_zuordnen(
        session, zone, ventil, rolle(session, "actuator"), akteur_id=None
    )
    assert zuordnung.device_id == ventil.id


def test_geraet_ohne_bekannte_faehigkeiten_wird_durchgelassen(session: Session) -> None:
    """Die Faehigkeiten stammen aus der Geraeteliste der Bruecke. Wer ein Geraet
    einbindet, das sich dort sparsam beschreibt, soll seine Anlage trotzdem einrichten
    koennen -- abgewiesen wird nur ein nachweislicher Widerspruch."""
    from thermoctl.domain.geraetezuordnung import geraet_zuordnen

    zone = zone_anlegen(session, "unbekanntzone")
    schweigsam = geraet_anlegen(session, "sagt-nichts-ueber-sich")
    geraet_zuordnen(session, zone, schweigsam, rolle(session, "actuator"), akteur_id=None)


def test_messquelle_muss_temperatur_messen(session: Session) -> None:
    from thermoctl.domain.geraetezuordnung import FaehigkeitFehlt, messquelle_setzen

    zone = zone_anlegen(session, "messquellenzone")
    ventil = _mit_faehigkeit(session, "ventil-als-messquelle", "switch")
    with pytest.raises(FaehigkeitFehlt, match="Temperatur"):
        messquelle_setzen(session, zone, ventil, akteur_id=None)


def test_fensterkontakt_muss_einen_kontakt_melden(session: Session) -> None:
    from thermoctl.domain.geraetezuordnung import FaehigkeitFehlt, geraet_zuordnen

    zone = zone_anlegen(session, "kontaktzone")
    ventil = _mit_faehigkeit(session, "ventil-als-kontakt", "switch")
    with pytest.raises(FaehigkeitFehlt, match="Kontakt"):
        geraet_zuordnen(
            session, zone, ventil, rolle(session, "window_contact"), akteur_id=None
        )


def test_tausch_prueft_jede_stelle_die_uebergeht(session: Session) -> None:
    """Der stillste Weg, ein unpassendes Geraet an eine Stelle zu setzen: Man waehlt zwei
    Namen aus und sieht gar nicht, welche Rollen dabei mitgehen."""
    from thermoctl.domain.geraetezuordnung import (
        FaehigkeitFehlt,
        geraet_tauschen,
        geraet_zuordnen,
    )

    zone = zone_anlegen(session, "tauschzone")
    ventil = _mit_faehigkeit(session, "altes-ventil", "switch")
    sensor = _mit_faehigkeit(session, "neuer-sensor", "temperature")
    geraet_zuordnen(session, zone, ventil, rolle(session, "actuator"), akteur_id=None)

    with pytest.raises(FaehigkeitFehlt, match="Schaltausgang"):
        geraet_tauschen(session, zone, ventil, sensor, akteur_id=None)


def test_abgelehnter_tausch_laesst_nichts_halb_stehen(session: Session) -> None:
    """Erst pruefen, dann schreiben. Sonst bliebe die Messquelle beim neuen Geraet und
    die Rolle beim alten."""
    from thermoctl.domain.geraetezuordnung import (
        FaehigkeitFehlt,
        geraet_tauschen,
        geraet_zuordnen,
        messquelle_setzen,
    )

    zone = zone_anlegen(session, "halbzone")
    kombi = _mit_faehigkeit(session, "kann-beides", "temperature", "switch")
    nur_sensor = _mit_faehigkeit(session, "kann-nur-messen", "temperature")
    messquelle_setzen(session, zone, kombi, akteur_id=None)
    geraet_zuordnen(session, zone, kombi, rolle(session, "actuator"), akteur_id=None)

    with pytest.raises(FaehigkeitFehlt):
        geraet_tauschen(session, zone, kombi, nur_sensor, akteur_id=None)
    session.expire_all()
    assert zone.temperature_source_device_id == kombi.id


def test_die_ansicht_zeigt_den_grund_statt_eines_fehlers(client_als, session: Session) -> None:
    """Ein 500 waere hier die schlechteste Antwort: Der Benutzer hat nichts falsch
    gemacht ausser dem Falschen, und er soll erfahren, was gefehlt hat."""
    zone = zone_anlegen(session, "ansichtszone")
    sensor = _mit_faehigkeit(session, "ansichts-sensor", "temperature")
    c = client_als([("device.read", None), ("device.manage", None), ("zone.read", None)])
    antwort = c.post(
        f"/zonen/{zone.id}/geraete/zuordnen",
        data={"device_id": str(sensor.id), "role_id": str(rolle(session, "actuator").id)},
        headers=_csrf(c),
    )
    assert antwort.status_code == 200
    assert "Schaltausgang" in antwort.text


def test_zugeordnete_karten_tragen_ihre_kennung(client_als, session: Session) -> None:
    """Ohne sie liesse sich ein Geraet zwar hineinziehen, aber nicht wieder heraus --
    der Weg hinein und der Weg hinaus waeren zwei verschiedene Handgriffe."""
    from thermoctl.db.models.device import ZoneDevice

    zone = zone_anlegen(session, "kennungszone")
    geraet = geraet_anlegen(session, "kennungsgeraet")
    zuordnung = ZoneDevice(
        zone_id=zone.id, device_id=geraet.id, device_role_id=rolle(session, "actuator").id
    )
    session.add(zuordnung)
    zone.temperature_source_device_id = geraet.id
    session.flush()

    client = client_als(
        [("device.read", None), ("device.manage", None), ("zone.read", None)]
    )
    seite = client.get(f"/zonen/{zone.id}/geraete")
    assert f'data-zuordnung="{zuordnung.id}"' in seite.text
    assert 'data-messquelle="ja"' in seite.text
    assert 'data-ziel="entfernen"' in seite.text


def test_ohne_device_manage_ist_nichts_herausziehbar(client_als, session: Session) -> None:
    """Gegenprobe: Wer nicht aendern darf, sieht dieselbe Karte ohne Griff."""
    from thermoctl.db.models.device import ZoneDevice

    zone = zone_anlegen(session, "lesezone")
    geraet = geraet_anlegen(session, "lesegeraet")
    session.add(
        ZoneDevice(
            zone_id=zone.id, device_id=geraet.id, device_role_id=rolle(session, "actuator").id
        )
    )
    session.flush()

    seite = client_als([("device.read", None), ("zone.read", None)]).get(
        f"/zonen/{zone.id}/geraete"
    )
    assert "tc-ziehbar" not in seite.text
    assert 'data-ziel="entfernen"' not in seite.text


def test_das_anlagenbild_traegt_keine_griffe(client_als, session: Session) -> None:
    """Dort gibt es keine Formulare, die ein Herausziehen abschicken koennten."""
    from thermoctl.db.models.device import ZoneDevice

    zone = zone_anlegen(session, "bildzone-griffe")
    geraet = geraet_anlegen(session, "bildgeraet")
    session.add(
        ZoneDevice(
            zone_id=zone.id, device_id=geraet.id, device_role_id=rolle(session, "actuator").id
        )
    )
    session.flush()

    seite = client_als(
        [("device.read", None), ("device.manage", None), ("zone.read", None)]
    ).get("/anlage")
    assert "tc-ziehbar" not in seite.text


def _bedienbefehle(session: Session) -> None:
    from thermoctl.db.models.lookup import CONTROLLER_COMMANDS, ControllerCommand

    for code, bezeichnung in CONTROLLER_COMMANDS:
        session.add(ControllerCommand(code=code, label=bezeichnung))
    session.add(DeviceCapability(code="action", label="Tastendruck"))
    session.flush()


def test_die_seite_zeigt_die_tasten_die_wirklich_ankamen(client_als, session: Session) -> None:
    """Nichts geraten: Wie ein Geraet seine Tasten nennt, entscheidet Zigbee2MQTT.

    Ohne diese Liste muesste jemand das Datenblatt seines Modells lesen -- und bei einem
    Tippfehler taete die Taste stumm nichts.
    """
    import json

    from thermoctl.services.ingest import nachricht_verarbeiten

    _bedienbefehle(session)
    zone = zone_anlegen(session, "tastenzone")
    geraet = geraet_anlegen(session, "wandschalter")
    _zuordnen(session, zone.id, geraet.id, "controller")
    nachricht_verarbeiten(
        session,
        f"zigbee2mqtt/{geraet.external_id}",
        json.dumps({"action": "button_1_single"}).encode(),
        basis="zigbee2mqtt",
        empfangen_am=datetime(2026, 8, 31, 8, 0),
    )

    antwort = client_als([("device.read", zone.id)]).get(f"/zonen/{zone.id}/geraete")

    assert antwort.status_code == 200
    assert "Tastenbelegung" in antwort.text
    assert "button_1_single" in antwort.text
    assert "Nächste Schaltung vorziehen" in antwort.text


def test_ohne_bediengeraet_gibt_es_keine_tastenbelegung(client_als, session: Session) -> None:
    """Gegenprobe: Ein Abschnitt, der bei jeder Zone steht, traegt keine Auskunft."""
    _bedienbefehle(session)
    zone = zone_anlegen(session, "tastenlos")
    geraet = geraet_anlegen(session, "ventil")
    _zuordnen(session, zone.id, geraet.id, "actuator")

    antwort = client_als([("device.read", zone.id)]).get(f"/zonen/{zone.id}/geraete")

    assert "Tastenbelegung" not in antwort.text


def test_eine_taste_laesst_sich_belegen_und_wieder_freigeben(
    client_als, session: Session
) -> None:
    from thermoctl.db.models.device import ControllerBinding

    quelle(session)
    _bedienbefehle(session)
    zone = zone_anlegen(session, "belegzone")
    geraet = geraet_anlegen(session, "schalter")
    _zuordnen(session, zone.id, geraet.id, "controller")
    client = client_als([("device.manage", zone.id), ("device.read", zone.id)])
    client.get(f"/zonen/{zone.id}/geraete")

    antwort = client.post(
        f"/zonen/{zone.id}/geraete/taste",
        headers=_csrf(client),
        data={
            "device_id": str(geraet.id),
            "aktion": "single_plus",
            "befehl": "setpoint_up",
            "schritt_k": "1,0",
        },
        follow_redirects=False,
    )
    assert antwort.status_code == 303
    belegung = session.scalars(select(ControllerBinding)).one()
    # Komma wie im Formular ueblich, Punkt in der Datenbank.
    assert belegung.step_k == Decimal("1.0")

    client.post(
        f"/zonen/{zone.id}/geraete/taste",
        headers=_csrf(client),
        data={"device_id": str(geraet.id), "aktion": "single_plus", "befehl": ""},
        follow_redirects=False,
    )
    assert session.scalars(select(ControllerBinding)).all() == []


def test_unbrauchbare_tastenbelegungen_werden_abgewiesen(client_als, session: Session) -> None:
    quelle(session)
    _bedienbefehle(session)
    zone = zone_anlegen(session, "fehlzone")
    geraet = geraet_anlegen(session, "fehlschalter")
    _zuordnen(session, zone.id, geraet.id, "controller")
    client = client_als([("device.manage", zone.id), ("device.read", zone.id)])
    client.get(f"/zonen/{zone.id}/geraete")

    ohne_aktion = client.post(
        f"/zonen/{zone.id}/geraete/taste",
        headers=_csrf(client),
        data={"device_id": str(geraet.id), "aktion": "", "befehl": "boost"},
    )
    assert ohne_aktion.status_code == 400

    krumme_zahl = client.post(
        f"/zonen/{zone.id}/geraete/taste",
        headers=_csrf(client),
        data={
            "device_id": str(geraet.id), "aktion": "single_plus",
            "befehl": "setpoint_up", "schritt_k": "warm",
        },
    )
    assert "Zahl sein" in krumme_zahl.text

    zu_genau = client.post(
        f"/zonen/{zone.id}/geraete/taste",
        headers=_csrf(client),
        data={
            "device_id": str(geraet.id), "aktion": "single_plus",
            "befehl": "setpoint_up", "schritt_k": "0,25",
        },
    )
    assert "Nachkommastelle" in zu_genau.text


def test_tastenbelegung_braucht_device_manage(client_als, session: Session) -> None:
    _bedienbefehle(session)
    zone = zone_anlegen(session, "rechtezone")
    geraet = geraet_anlegen(session, "rechteschalter")
    _zuordnen(session, zone.id, geraet.id, "controller")
    client = client_als([("device.read", zone.id)])
    client.get(f"/zonen/{zone.id}/geraete")

    antwort = client.post(
        f"/zonen/{zone.id}/geraete/taste",
        headers=_csrf(client),
        data={"device_id": str(geraet.id), "aktion": "single_plus", "befehl": "boost"},
    )
    assert antwort.status_code == 404
