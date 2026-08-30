from datetime import datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.helpers import (
    create_device,
    create_mode,
    create_zone,
    rolle,
    source,
)
from thermoctl.auth.csrf import CSRF_HEADER, csrf_token
from thermoctl.auth.sessions import COOKIE_NAME
from thermoctl.config import get_settings
from thermoctl.db.models.device import ZoneDevice
from thermoctl.db.models.lookup import DeviceCapability
from thermoctl.db.models.measurement import Measurement
from thermoctl.db.models.operations import AuditEvent
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.zone import ZoneSetpoint
from thermoctl.domain.device_assignment import swap_device


def _csrf(client: TestClient) -> dict[str, str]:
    http_session = client.cookies.get(COOKIE_NAME)
    assert http_session is not None
    return {
        CSRF_HEADER: csrf_token(
            http_session, get_settings().secret_key.get_secret_value()
        )
    }


def _assign(session: Session, zone_id: int, device_id: int, rollencode: str) -> None:
    session.add(
        ZoneDevice(
            zone_id=zone_id,
            device_id=device_id,
            device_role_id=rolle(session, rollencode).id,
        )
    )
    session.flush()


def test_tausch_erhaelt_zonenkonfiguration_und_uebernimmt_alle_rollen(
    session: Session,
) -> None:
    source(session)
    zone = create_zone(session, "wohnzimmer")
    zone.hysteresis_k = Decimal("0.45")
    zone.min_on_seconds = 420
    zone.sensor_timeout_seconds = 900
    mode = create_mode(session, "tag")
    session.add_all(
        [
            ZoneSetpoint(
                zone_id=zone.id,
                setpoint_mode_id=mode.id,
                temperature_c=Decimal("21.5"),
            ),
            SchedulePoint(
                zone_id=zone.id,
                weekday=1,
                minute_of_day=360,
                setpoint_mode_id=mode.id,
            ),
        ]
    )
    altes = create_device(session, "alt")
    neues = create_device(session, "neu")
    _assign(session, zone.id, altes.id, "actuator")
    _assign(session, zone.id, altes.id, "controller")
    session.flush()
    vorher_setpoints = list(
        session.execute(
            select(
                ZoneSetpoint.zone_id,
                ZoneSetpoint.setpoint_mode_id,
                ZoneSetpoint.temperature_c,
            ).where(ZoneSetpoint.zone_id == zone.id)
        )
    )
    vorher_schedule = list(
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

    swap_device(session, zone, altes, neues, akteur_id=None)

    assert list(
        session.execute(
            select(
                ZoneSetpoint.zone_id,
                ZoneSetpoint.setpoint_mode_id,
                ZoneSetpoint.temperature_c,
            ).where(ZoneSetpoint.zone_id == zone.id)
        )
    ) == vorher_setpoints
    assert list(
        session.execute(
            select(
                SchedulePoint.zone_id,
                SchedulePoint.weekday,
                SchedulePoint.minute_of_day,
                SchedulePoint.setpoint_mode_id,
            ).where(SchedulePoint.zone_id == zone.id)
        )
    ) == vorher_schedule
    assert (
        zone.hysteresis_k,
        zone.min_on_seconds,
        zone.min_off_seconds,
        zone.sensor_timeout_seconds,
        zone.temperature_offset_k,
        zone.window_resume_delay_seconds,
    ) == vorher_parameter
    new_rolen = set(
        session.scalars(
            select(ZoneDevice.device_role_id).where(
                ZoneDevice.zone_id == zone.id, ZoneDevice.device_id == neues.id
            )
        )
    )
    assert new_rolen == {rolle(session, "actuator").id, rolle(session, "controller").id}
    assert session.scalar(
        select(ZoneDevice).where(
            ZoneDevice.zone_id == zone.id, ZoneDevice.device_id == altes.id
        )
    ) is None


def test_tausch_laesst_messhistorie_beim_alten_geraet(session: Session) -> None:
    source(session)
    zone = create_zone(session, "bad")
    altes = create_device(session, "sensor-alt")
    neues = create_device(session, "sensor-neu")
    zone.temperature_source_device_id = altes.id
    capability = DeviceCapability(code="temperature", label="Temperatur")
    session.add(capability)
    session.flush()
    measurement = Measurement(
        device_id=altes.id,
        capability_id=capability.id,
        value_numeric=Decimal("19.750"),
        measured_at=datetime(2026, 8, 29, 10, 0),
        received_at=datetime(2026, 8, 29, 10, 0),
    )
    session.add(measurement)
    session.flush()

    swap_device(session, zone, altes, neues, akteur_id=None)

    assert zone.temperature_source_device_id == neues.id
    assert session.get(Measurement, measurement.id).device_id == altes.id
    assert session.scalar(
        select(Measurement).where(Measurement.device_id == neues.id)
    ) is None


def test_tausch_in_einer_zone_laesst_zweite_zone_unberuehrt(session: Session) -> None:
    source(session)
    zone_a = create_zone(session, "a")
    zone_b = create_zone(session, "b")
    altes = create_device(session, "alt-gemeinsam")
    neues = create_device(session, "neu-a")
    _assign(session, zone_a.id, altes.id, "window_contact")
    _assign(session, zone_b.id, altes.id, "window_contact")

    swap_device(session, zone_a, altes, neues, akteur_id=None)

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
    source(session)
    zone = create_zone(session, "audit-zone")
    altes = create_device(session, "audit-alt")
    neues = create_device(session, "audit-neu")
    _assign(session, zone.id, altes.id, "actuator")

    swap_device(session, zone, altes, neues, akteur_id=None)

    entry = session.scalar(
        select(AuditEvent).where(
            AuditEvent.object_type == "zone_device", AuditEvent.action == "replace"
        )
    )
    assert entry is not None
    assert "audit-alt" in entry.summary
    assert "audit-neu" in entry.summary


def test_seite_zeigt_zuordnungen_und_messquelle(client_als, session: Session) -> None:
    zone = create_zone(session, "anzeige")
    device = create_device(session, "thermostat")
    zone.temperature_source_device_id = device.id
    _assign(session, zone.id, device.id, "controller")

    response = client_als([("device.read", zone.id)]).get(
        f"/zones/{zone.id}/devices"
    )

    assert response.status_code == 200
    assert "thermostat" in response.text
    assert "controller" in response.text
    assert "Gerät tauschen" not in response.text


def test_doppelte_rolle_zeigt_verstaendliche_meldung(client_als, session: Session) -> None:
    source(session)
    zone = create_zone(session, "doppelt")
    device = create_device(session, "kontakt")
    devicesrolle = rolle(session, "window_contact")
    _assign(session, zone.id, device.id, "window_contact")
    client = client_als([("device.manage", zone.id)])

    response = client.post(
        f"/zones/{zone.id}/devices/assign",
        data={"device_id": device.id, "role_id": devicesrolle.id},
        headers=_csrf(client),
    )

    assert response.status_code == 200
    assert "in dieser Rolle bereits zugeordnet" in response.text


def test_aendernde_wege_und_rechte(client_als, session: Session) -> None:
    source(session)
    eigene = create_zone(session, "eigene")
    fremde = create_zone(session, "fremde")
    altes = create_device(session, "weg")
    neues = create_device(session, "hin")
    client = client_als([("device.manage", eigene.id)])
    kopf = _csrf(client)

    zuordnen = client.post(
        f"/zones/{eigene.id}/devices/assign",
        data={"device_id": altes.id, "role_id": rolle(session, "actuator").id},
        headers=kopf,
        follow_redirects=False,
    )
    assert zuordnen.status_code == 303
    assignment = session.scalar(
        select(ZoneDevice).where(ZoneDevice.zone_id == eigene.id)
    )
    assert assignment is not None

    temperature_source = client.post(
        f"/zones/{eigene.id}/devices/source",
        data={"device_id": altes.id},
        headers=kopf,
        follow_redirects=False,
    )
    assert temperature_source.status_code == 303
    assert eigene.temperature_source_device_id == altes.id

    swap = client.post(
        f"/zones/{eigene.id}/devices/swap",
        data={"old_device_id": altes.id, "new_device_id": neues.id},
        headers=kopf,
        follow_redirects=False,
    )
    assert swap.status_code == 303
    new_assignment = session.scalar(
        select(ZoneDevice).where(
            ZoneDevice.zone_id == eigene.id, ZoneDevice.device_id == neues.id
        )
    )
    assert new_assignment is not None

    # Die Kennung steht im Rumpf, nicht im Pfad: hx-boost liest die `action` eines
    # Formulars einmal, deshalb benutzen Tabelle und Herausziehen denselben Endpunkt.
    loesen = client.post(
        f"/zones/{eigene.id}/devices/detach",
        data={"assignment_id": str(new_assignment.id)},
        headers=kopf,
        follow_redirects=False,
    )
    assert loesen.status_code == 303
    assert session.get(ZoneDevice, new_assignment.id) is None

    assert client.get(f"/zones/{fremde.id}/devices").status_code == 404
    assert client.post(
        f"/zones/{fremde.id}/devices/source",
        data={"device_id": neues.id},
        headers=kopf,
    ).status_code == 404


def test_ungueltige_eingaben_bei_der_zuordnung(client_als, session: Session) -> None:
    """Jeder Fehlerweg der Zuordnungsseite — bisher war nur der Erfolgsfall belegt."""
    source(session)
    zone = create_zone(session, "zone-fehlerwege")
    device = create_device(session, "vorhanden")
    client = client_als([("device.manage", None), ("device.read", None)])
    kopf = _csrf(client)

    # Unbekanntes Geraet, unbekannte Rolle, gar keine Angabe.
    for daten in (
        {"device_id": "999999", "role_id": str(rolle(session, "actuator").id)},
        {"device_id": str(device.id), "role_id": "999999"},
        {"device_id": "", "role_id": ""},
        {"device_id": "kein Geraet", "role_id": "1"},
    ):
        response = client.post(f"/zones/{zone.id}/devices/assign", data=daten, headers=kopf)
        assert response.status_code == 200, daten
        assert session.scalar(
            select(ZoneDevice).where(ZoneDevice.zone_id == zone.id)
        ) is None, daten


def test_messquelle_laesst_sich_wieder_loesen(client_als, session: Session) -> None:
    """Leeres Feld heisst 'keine Messquelle' — die Zone gilt danach als ohne Quelle."""
    source(session)
    zone = create_zone(session, "zone-messquelle-weg")
    device = create_device(session, "quelle-weg")
    client = client_als([("device.manage", None), ("device.read", None)])
    kopf = _csrf(client)
    client.post(
        f"/zones/{zone.id}/devices/source", data={"device_id": str(device.id)},
        headers=kopf,
    )
    assert zone.temperature_source_device_id == device.id
    response = client.post(
        f"/zones/{zone.id}/devices/source", data={"device_id": ""},
        headers=kopf, follow_redirects=False,
    )
    assert response.status_code == 303
    assert zone.temperature_source_device_id is None


def test_unbekannte_messquelle_bleibt_ohne_wirkung(client_als, session: Session) -> None:
    source(session)
    zone = create_zone(session, "zone-messquelle-unbekannt")
    client = client_als([("device.manage", None), ("device.read", None)])
    response = client.post(
        f"/zones/{zone.id}/devices/source", data={"device_id": "999999"},
        headers=_csrf(client),
    )
    assert response.status_code == 200
    assert zone.temperature_source_device_id is None


def test_tausch_mit_unsinnigen_geraeten_meldet_verstaendlich(
    client_als, session: Session
) -> None:
    """Drei Faelle: gleiches Geraet, unbekanntes Geraet, ein Geraet ohne Zuordnung."""
    source(session)
    zone = create_zone(session, "zone-tausch-unsinn")
    eines = create_device(session, "eines")
    anderes = create_device(session, "anderes")
    client = client_als([("device.manage", None), ("device.read", None)])
    kopf = _csrf(client)

    for daten in (
        {"old_device_id": str(eines.id), "new_device_id": str(eines.id)},
        {"old_device_id": str(eines.id), "new_device_id": "999999"},
        {"old_device_id": str(eines.id), "new_device_id": str(anderes.id)},
    ):
        response = client.post(f"/zones/{zone.id}/devices/swap", data=daten, headers=kopf)
        assert response.status_code == 200, daten
        assert session.scalar(
            select(ZoneDevice).where(ZoneDevice.zone_id == zone.id)
        ) is None, daten


def test_fremde_zuordnung_laesst_sich_nicht_loesen(client_als, session: Session) -> None:
    """Eine Zuordnung einer anderen Zone ergibt 404, nicht 403."""
    source(session)
    eigene = create_zone(session, "eigene-loesen")
    fremde = create_zone(session, "fremde-loesen")
    device = create_device(session, "fremdgeraet")
    foreign_assignment = ZoneDevice(
        zone_id=fremde.id, device_id=device.id,
        device_role_id=rolle(session, "actuator").id,
    )
    session.add(foreign_assignment)
    session.flush()
    client = client_als([("device.manage", None), ("device.read", None)])
    response = client.post(
        f"/zones/{eigene.id}/devices/detach",
        data={"assignment_id": str(foreign_assignment.id)},
        headers=_csrf(client),
    )
    assert response.status_code == 404
    assert session.get(ZoneDevice, foreign_assignment.id) is not None


def test_loesen_einer_fremden_zuordnung_wird_in_der_domaene_abgewiesen(
    session: Session,
) -> None:
    """Die Ansicht faengt den Fall schon mit 404 ab. Die Domaene prueft trotzdem selbst:

    Sie wird spaeter auch von REST und MCP aufgerufen, und eine Regel, die nur in einem
    Adapter steht, gilt nicht fuer die anderen.
    """
    import pytest

    from thermoctl.domain.device_assignment import detach_device

    source(session)
    eine = create_zone(session, "zone-loesen-a")
    andere = create_zone(session, "zone-loesen-b")
    device = create_device(session, "geraet-loesen")
    assignment = ZoneDevice(
        zone_id=andere.id, device_id=device.id,
        device_role_id=rolle(session, "actuator").id,
    )
    session.add(assignment)
    session.flush()
    with pytest.raises(ValueError, match="gehört nicht zu dieser Zone"):
        detach_device(session, eine, assignment, akteur_id=None)
    assert session.get(ZoneDevice, assignment.id) is not None


def test_ablegeziele_nur_mit_device_manage(client_als, session: Session) -> None:
    """Das Ziehen ist eine zweite Bedienart derselben Aenderung -- es muss an derselben
    Rechtepruefung haengen wie die Formulare. Ein Ablegeziel, das man sieht und nicht
    benutzen darf, ist eine Einladung zu einer 403."""
    zone = create_zone(session, "ziehzone")
    # Ohne ein Geraet gibt es nichts zu ziehen -- die Karten entstehen aus der Liste.
    create_device(session, "ziehbares-geraet")

    darf = client_als([("device.read", None), ("device.manage", zone.id), ("zone.read", None)])
    page = darf.get(f"/zones/{zone.id}/devices")
    assert page.status_code == 200
    assert 'data-ziel="messquelle"' in page.text
    assert "tc-ziehbar" in page.text

    read_only = client_als([("device.read", None), ("zone.read", None)])
    page = read_only.get(f"/zones/{zone.id}/devices")
    assert page.status_code == 200
    assert "data-ziel=" not in page.text
    assert "tc-ziehbar" not in page.text


def test_anlagenbild_bietet_keine_ablegeziele(client_als, session: Session) -> None:
    """Gegenprobe: Auf dem Anlagenbild waere ein Ablegeziel eine Zusage, die die Seite
    nicht einloest -- dort gibt es keine Formulare, die es abschicken koennte."""
    create_zone(session, "bildzone")
    page = client_als([("device.read", None), ("device.manage", None), ("zone.read", None)]).get(
        "/plant"
    )
    assert page.status_code == 200
    assert "data-ziel=" not in page.text


# --- Faehigkeitspruefung ----------------------------------------------------


def _with_capability(session: Session, name: str, *codes: str):
    """Ein Geraet, dessen Faehigkeiten bekannt sind."""
    from tests.helpers import capability
    from thermoctl.db.models.device import DeviceCapabilityLink

    device = create_device(session, name)
    for code in codes:
        session.add(
            DeviceCapabilityLink(
                device_id=device.id, capability_id=capability(session, code).id
            )
        )
    session.flush()
    return device


def test_sensor_laesst_sich_nicht_als_aktor_zuordnen(session: Session) -> None:
    """Vorher ging das. Die Zuordnung sah danach richtig aus, das Anlagenbild zeigte
    einen vollstaendigen Weg, und geschaltet haette trotzdem nie etwas -- ein Fehler, der
    erst im Winter auffaellt und dann nach einem Regelungsfehler aussieht."""
    from thermoctl.domain.device_assignment import CapabilityMissing, assign_device

    zone = create_zone(session, "faehigkeitszone")
    sensor = _with_capability(session, "nur-thermometer", "temperature", "battery")
    with pytest.raises(CapabilityMissing, match="Schaltausgang"):
        assign_device(
            session, zone, sensor, rolle(session, "actuator"), akteur_id=None
        )


def test_ventil_laesst_sich_als_aktor_zuordnen(session: Session) -> None:
    """Gegenprobe. Ohne sie waere der Test oben auch von einer Fassung erfuellt, die
    jede Zuordnung ablehnt."""
    from thermoctl.domain.device_assignment import assign_device

    zone = create_zone(session, "ventilzone")
    ventil = _with_capability(session, "echtes-ventil", "switch")
    assignment = assign_device(
        session, zone, ventil, rolle(session, "actuator"), akteur_id=None
    )
    assert assignment.device_id == ventil.id


def test_geraet_ohne_bekannte_faehigkeiten_wird_durchgelassen(session: Session) -> None:
    """Die Faehigkeiten stammen aus der Geraeteliste der Bruecke. Wer ein Geraet
    einbindet, das sich dort sparsam beschreibt, soll seine Anlage trotzdem einrichten
    koennen -- abgewiesen wird nur ein nachweislicher Widerspruch."""
    from thermoctl.domain.device_assignment import assign_device

    zone = create_zone(session, "unbekanntzone")
    schweigsam = create_device(session, "sagt-nichts-ueber-sich")
    assign_device(session, zone, schweigsam, rolle(session, "actuator"), akteur_id=None)


def test_messquelle_muss_temperatur_messen(session: Session) -> None:
    from thermoctl.domain.device_assignment import CapabilityMissing, set_temperature_source

    zone = create_zone(session, "messquellenzone")
    ventil = _with_capability(session, "ventil-als-messquelle", "switch")
    with pytest.raises(CapabilityMissing, match="Temperatur"):
        set_temperature_source(session, zone, ventil, akteur_id=None)


def test_fensterkontakt_muss_einen_kontakt_melden(session: Session) -> None:
    from thermoctl.domain.device_assignment import CapabilityMissing, assign_device

    zone = create_zone(session, "kontaktzone")
    ventil = _with_capability(session, "ventil-als-kontakt", "switch")
    with pytest.raises(CapabilityMissing, match="Kontakt"):
        assign_device(
            session, zone, ventil, rolle(session, "window_contact"), akteur_id=None
        )


def test_tausch_prueft_jede_stelle_die_uebergeht(session: Session) -> None:
    """Der stillste Weg, ein unpassendes Geraet an eine Stelle zu setzen: Man waehlt zwei
    Namen aus und sieht gar nicht, welche Rollen dabei mitgehen."""
    from thermoctl.domain.device_assignment import (
        CapabilityMissing,
        assign_device,
        swap_device,
    )

    zone = create_zone(session, "tauschzone")
    ventil = _with_capability(session, "altes-ventil", "switch")
    sensor = _with_capability(session, "neuer-sensor", "temperature")
    assign_device(session, zone, ventil, rolle(session, "actuator"), akteur_id=None)

    with pytest.raises(CapabilityMissing, match="Schaltausgang"):
        swap_device(session, zone, ventil, sensor, akteur_id=None)


def test_abgelehnter_tausch_laesst_nichts_halb_stehen(session: Session) -> None:
    """Erst pruefen, dann schreiben. Sonst bliebe die Messquelle beim neuen Geraet und
    die Rolle beim alten."""
    from thermoctl.domain.device_assignment import (
        CapabilityMissing,
        assign_device,
        set_temperature_source,
        swap_device,
    )

    zone = create_zone(session, "halbzone")
    kombi = _with_capability(session, "kann-beides", "temperature", "switch")
    nur_sensor = _with_capability(session, "kann-nur-messen", "temperature")
    set_temperature_source(session, zone, kombi, akteur_id=None)
    assign_device(session, zone, kombi, rolle(session, "actuator"), akteur_id=None)

    with pytest.raises(CapabilityMissing):
        swap_device(session, zone, kombi, nur_sensor, akteur_id=None)
    session.expire_all()
    assert zone.temperature_source_device_id == kombi.id


def test_die_ansicht_zeigt_den_grund_statt_eines_fehlers(client_als, session: Session) -> None:
    """Ein 500 waere hier die schlechteste Antwort: Der Benutzer hat nichts falsch
    gemacht ausser dem Falschen, und er soll erfahren, was gefehlt hat."""
    zone = create_zone(session, "ansichtszone")
    sensor = _with_capability(session, "ansichts-sensor", "temperature")
    c = client_als([("device.read", None), ("device.manage", None), ("zone.read", None)])
    response = c.post(
        f"/zones/{zone.id}/devices/assign",
        data={"device_id": str(sensor.id), "role_id": str(rolle(session, "actuator").id)},
        headers=_csrf(c),
    )
    assert response.status_code == 200
    assert "Schaltausgang" in response.text


def test_zugeordnete_karten_tragen_ihre_kennung(client_als, session: Session) -> None:
    """Ohne sie liesse sich ein Geraet zwar hineinziehen, aber nicht wieder heraus --
    der Weg hinein und der Weg hinaus waeren zwei verschiedene Handgriffe."""
    from thermoctl.db.models.device import ZoneDevice

    zone = create_zone(session, "kennungszone")
    device = create_device(session, "kennungsgeraet")
    assignment = ZoneDevice(
        zone_id=zone.id, device_id=device.id, device_role_id=rolle(session, "actuator").id
    )
    session.add(assignment)
    zone.temperature_source_device_id = device.id
    session.flush()

    client = client_als(
        [("device.read", None), ("device.manage", None), ("zone.read", None)]
    )
    page = client.get(f"/zones/{zone.id}/devices")
    assert f'data-zuordnung="{assignment.id}"' in page.text
    assert 'data-messquelle="ja"' in page.text
    assert 'data-ziel="entfernen"' in page.text


def test_ohne_device_manage_ist_nichts_herausziehbar(client_als, session: Session) -> None:
    """Gegenprobe: Wer nicht aendern darf, sieht dieselbe Karte ohne Griff."""
    from thermoctl.db.models.device import ZoneDevice

    zone = create_zone(session, "lesezone")
    device = create_device(session, "lesegeraet")
    session.add(
        ZoneDevice(
            zone_id=zone.id, device_id=device.id, device_role_id=rolle(session, "actuator").id
        )
    )
    session.flush()

    page = client_als([("device.read", None), ("zone.read", None)]).get(
        f"/zones/{zone.id}/devices"
    )
    assert "tc-ziehbar" not in page.text
    assert 'data-ziel="entfernen"' not in page.text


def test_das_anlagenbild_traegt_keine_griffe(client_als, session: Session) -> None:
    """Dort gibt es keine Formulare, die ein Herausziehen abschicken koennten."""
    from thermoctl.db.models.device import ZoneDevice

    zone = create_zone(session, "bildzone-griffe")
    device = create_device(session, "bildgeraet")
    session.add(
        ZoneDevice(
            zone_id=zone.id, device_id=device.id, device_role_id=rolle(session, "actuator").id
        )
    )
    session.flush()

    page = client_als(
        [("device.read", None), ("device.manage", None), ("zone.read", None)]
    ).get("/plant")
    assert "tc-ziehbar" not in page.text


def _controller_commands(session: Session) -> None:
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

    from thermoctl.services.ingest import process_message

    _controller_commands(session)
    zone = create_zone(session, "tastenzone")
    device = create_device(session, "wandschalter")
    _assign(session, zone.id, device.id, "controller")
    process_message(
        session,
        f"zigbee2mqtt/{device.external_id}",
        json.dumps({"action": "button_1_single"}).encode(),
        basis="zigbee2mqtt",
        empfangen_am=datetime(2026, 8, 31, 8, 0),
    )

    response = client_als([("device.read", zone.id)]).get(f"/zones/{zone.id}/devices")

    assert response.status_code == 200
    assert "Tastenbelegung" in response.text
    assert "button_1_single" in response.text
    assert "Nächste Schaltung vorziehen" in response.text


def test_ohne_bediengeraet_gibt_es_keine_tastenbelegung(client_als, session: Session) -> None:
    """Gegenprobe: Ein Abschnitt, der bei jeder Zone steht, traegt keine Auskunft."""
    _controller_commands(session)
    zone = create_zone(session, "tastenlos")
    device = create_device(session, "ventil")
    _assign(session, zone.id, device.id, "actuator")

    response = client_als([("device.read", zone.id)]).get(f"/zones/{zone.id}/devices")

    assert "Tastenbelegung" not in response.text


def test_eine_taste_laesst_sich_belegen_und_wieder_freigeben(
    client_als, session: Session
) -> None:
    from thermoctl.db.models.device import ControllerBinding

    source(session)
    _controller_commands(session)
    zone = create_zone(session, "belegzone")
    device = create_device(session, "schalter")
    _assign(session, zone.id, device.id, "controller")
    client = client_als([("device.manage", zone.id), ("device.read", zone.id)])
    client.get(f"/zones/{zone.id}/devices")

    response = client.post(
        f"/zones/{zone.id}/devices/button",
        headers=_csrf(client),
        data={
            "device_id": str(device.id),
            "action_code": "single_plus",
            "command": "setpoint_up",
            "step_k": "1,0",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    binding = session.scalars(select(ControllerBinding)).one()
    # Komma wie im Formular ueblich, Punkt in der Datenbank.
    assert binding.step_k == Decimal("1.0")

    client.post(
        f"/zones/{zone.id}/devices/button",
        headers=_csrf(client),
        data={"device_id": str(device.id), "action_code": "single_plus", "command": ""},
        follow_redirects=False,
    )
    assert session.scalars(select(ControllerBinding)).all() == []


def test_unbrauchbare_tastenbelegungen_werden_abgewiesen(client_als, session: Session) -> None:
    source(session)
    _controller_commands(session)
    zone = create_zone(session, "fehlzone")
    device = create_device(session, "fehlschalter")
    _assign(session, zone.id, device.id, "controller")
    client = client_als([("device.manage", zone.id), ("device.read", zone.id)])
    client.get(f"/zones/{zone.id}/devices")

    ohne_aktion = client.post(
        f"/zones/{zone.id}/devices/button",
        headers=_csrf(client),
        data={"device_id": str(device.id), "action_code": "", "command": "boost"},
    )
    assert ohne_aktion.status_code == 400

    krumme_number = client.post(
        f"/zones/{zone.id}/devices/button",
        headers=_csrf(client),
        data={
            "device_id": str(device.id), "action_code": "single_plus",
            "command": "setpoint_up", "step_k": "warm",
        },
    )
    assert "Zahl sein" in krumme_number.text

    zu_genau = client.post(
        f"/zones/{zone.id}/devices/button",
        headers=_csrf(client),
        data={
            "device_id": str(device.id), "action_code": "single_plus",
            "command": "setpoint_up", "step_k": "0,25",
        },
    )
    assert "Nachkommastelle" in zu_genau.text


def test_tastenbelegung_braucht_device_manage(client_als, session: Session) -> None:
    _controller_commands(session)
    zone = create_zone(session, "rechtezone")
    device = create_device(session, "rechteschalter")
    _assign(session, zone.id, device.id, "controller")
    client = client_als([("device.read", zone.id)])
    client.get(f"/zones/{zone.id}/devices")

    response = client.post(
        f"/zones/{zone.id}/devices/button",
        headers=_csrf(client),
        data={"device_id": str(device.id), "action_code": "single_plus", "command": "boost"},
    )
    assert response.status_code == 404
