"""Regressionstests zu den Befunden der Sicherheitsdurchsicht vom 2026-09-02.

Sie sind als Beweis-Tests der Luecken entstanden und halten jetzt die Korrektur fest.
Jeder von ihnen ist einmal gruen gewesen, weil der Angriff funktionierte -- das ist
der Grund, warum sie hier stehen und nicht bloss bestaetigen, was der Code ohnehin
tut. Die Durchsicht selbst steht in `docs/sicherheitsdurchsicht-2026-09-02.md`.
"""

from datetime import datetime
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.helpers import (
    create_device,
    create_zone,
    operating_mode,
    role,
    source,
    user_with_permissions,
)
from thermoctl.auth.csrf import CSRF_HEADER, csrf_token
from thermoctl.auth.sessions import COOKIE_NAME
from thermoctl.config import get_settings
from thermoctl.db.models.device import (
    ControllerBinding,
    ControllerChannel,
    DeviceProperty,
    ZoneDevice,
)
from thermoctl.db.models.lookup import ChannelKind, ControllerCommand
from thermoctl.domain.controller_channels import apply_read_channels
from thermoctl.domain.kiosk import issue_kiosk_token
from thermoctl.integrations.notification import _NoRedirectHandler
from thermoctl.mcp import server as mcp_server


def test_kiosk_token_gilt_nicht_als_rest_bearer(
    client: TestClient, session: Session
) -> None:
    """Wer das Wandtablett hat, hat das Token -- und darf damit nur, was das Kiosk kann.

    Vor der Korrektur nahm die allgemeine REST-API dasselbe Token an und legte damit
    eine unbefristete Uebersteuerung auf 35 Grad an. Die Kiosk-Oberflaeche bietet so
    etwas gar nicht an, sie verstellt in festen Schritten. Die enge Bedienflaeche war
    die Sicherheitseigenschaft, und nur das Kiosk hat sie durchgesetzt.
    """
    source(session, "kiosk")
    zone = create_zone(session, "kiosk-zone")
    owner = user_with_permissions(
        session,
        "kiosk-owner",
        [
            ("zone.read", zone.id),
            ("setpoint.write", zone.id),
            ("override.create", zone.id),
        ],
    )
    _token, plaintext = issue_kiosk_token(
        session,
        owner,
        "Wandtablett",
        [zone.id],
        control_allowed=True,
        expires_at=None,
    )

    response = client.post(
        f"/api/v1/zones/{zone.id}/override",
        headers={"Authorization": f"Bearer {plaintext}"},
        json={"temperature_c": "35.0"},
    )

    assert response.status_code == 401
    assert "Kiosk" in response.json()["detail"]


def test_device_manage_einer_zone_erreicht_keine_fremde_zone(
    client_als, session: Session
) -> None:
    """``device.manage`` fuer Zone A darf keinen Kanal auf Zone B richten.

    Vor der Korrektur wurde nur geprueft, ob das *Geraet* in einer verwaltbaren Zone
    haengt, die aus dem Formular kommende Zielzone dagegen gar nicht. Ein bewusst auf
    eine Zone beschraenkter Nutzer konnte damit einen fremden Raum abschalten -- bei
    jeder Drehung am Regler erneut.
    """
    zone_a = create_zone(session, "eigene-zone")
    zone_b = create_zone(session, "fremde-zone")
    operating_mode(session, "off")
    device = create_device(session, "wandregler")
    session.add(
        ZoneDevice(
            zone_id=zone_a.id,
            device_id=device.id,
            device_role_id=role(session, "controller").id,
        )
    )
    session.add(
        DeviceProperty(
            device_id=device.id,
            name="system_mode",
            value_type="text",
            is_readable=True,
            is_writable=False,
        )
    )
    session.add(ChannelKind(code="operating_mode", label="Betriebsart"))
    session.flush()
    client = client_als([("device.manage", zone_a.id)])
    session_secret = client.cookies.get(COOKIE_NAME)
    assert session_secret is not None

    response = client.post(
        "/controllers/channel",
        data={
            "device_id": str(device.id),
            "property_name": "system_mode",
            "direction": "read",
            "kind": "operating_mode",
            "zone_id": str(zone_b.id),
        },
        headers={
            CSRF_HEADER: csrf_token(
                session_secret, get_settings().secret_key.get_secret_value()
            )
        },
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert session.query(ControllerChannel).filter_by(device_id=device.id).count() == 0

    # Und ohne Kanal bewegt auch die naechste Geraetemeldung die fremde Zone nicht.
    apply_read_channels(
        session,
        device,
        {"system_mode": "off"},
        datetime(2026, 9, 2, 12, 0),
    )

    assert zone_b.operating_mode.code == "auto"


def test_offen_webhook_redirect_nimmt_authorization_an_internes_ziel_mit() -> None:
    """BEHOBEN -- hielt eine offene Luecke fest, haelt jetzt die Korrektur fest.

    Der Standard-`HTTPRedirectHandler` (unten weiter benutzt, um die Luecke selbst
    zu belegen) behaelt bei einem 302 auch ueber einen Hostwechsel hinweg alle
    Header ausser `Content-*` bei -- `Authorization` eingeschlossen. Genau deshalb
    benutzt `integrations/notification.py` ihn nicht mehr: `_send_webhook` geht
    seit der Korrektur ueber einen eigenen Opener mit
    `notification._NoRedirectHandler`, der eine solche Weiterleitung gar nicht erst
    in eine neue Anfrage uebersetzt, sondern ablehnt. Der zweite Teil dieses Tests
    beweist genau das.
    """
    original = Request(
        "https://webhook.example/meldung",
        data=b"{}",
        headers={"Authorization": "Bearer webhook-geheimnis"},
        method="POST",
    )

    # Der Standardfall, unveraendert: das ist die Luecke, die es zu vermeiden galt.
    redirected = HTTPRedirectHandler().redirect_request(
        original,
        None,
        302,
        "Found",
        {},
        "http://127.0.0.1:8080/intern",
    )
    assert redirected is not None
    assert redirected.full_url == "http://127.0.0.1:8080/intern"
    assert redirected.get_method() == "GET"
    assert redirected.get_header("Authorization") == "Bearer webhook-geheimnis"

    # Der von thermoctl tatsaechlich benutzte Handler lehnt dieselbe Weiterleitung
    # ab, statt eine Anfrage zu bauen, die den Header mitnehmen koennte.
    with pytest.raises(HTTPError, match="Webhook-Weiterleitung abgelehnt"):
        _NoRedirectHandler().redirect_request(
            original,
            None,
            302,
            "Found",
            {},
            "http://127.0.0.1:8080/intern",
        )


def test_tastenbelegung_verlangt_das_recht_fuer_jede_zone_des_geraets(
    client_als, session: Session
) -> None:
    """Ein Tastendruck wirkt in *allen* Zonen des Bediengeraets, die Rechtepruefung auch.

    `execute_action` in `domain/controller.py` fuehrt die Belegung in jeder Zone aus,
    in der das Geraet als Bediengeraet haengt. Geprueft wurde vorher nur, ob es in
    *einer* verwaltbaren Zone haengt -- der geteilte Flurregler reichte damit in jedes
    Zimmer, an dem er ebenfalls haengt.
    """
    zone_a = create_zone(session, "eigene-zone")
    zone_b = create_zone(session, "fremde-zone")
    device = create_device(session, "flurregler")
    for zone in (zone_a, zone_b):
        session.add(
            ZoneDevice(
                zone_id=zone.id,
                device_id=device.id,
                device_role_id=role(session, "controller").id,
            )
        )
    session.add(ControllerCommand(code="setpoint_up", label="Waermer"))
    session.flush()
    client = client_als([("device.manage", zone_a.id)])
    session_secret = client.cookies.get(COOKIE_NAME)
    assert session_secret is not None

    response = client.post(
        "/controllers/button",
        data={
            "device_id": str(device.id),
            "action_code": "single",
            "command": "setpoint_up",
            "step_k": "0.5",
        },
        headers={
            CSRF_HEADER: csrf_token(
                session_secret, get_settings().secret_key.get_secret_value()
            )
        },
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert session.query(ControllerBinding).count() == 0


def test_bediengeraeteseite_verraet_keine_fremden_geraetenamen(
    client_als, session: Session
) -> None:
    """Die Quellgeraeteliste zeigte den gesamten Geraetebestand der Anlage.

    Geraetenamen tragen in diesem Projekt Raum-, Bewohner- und Integrationsbezuege.
    Wer `device.read` fuer eine einzige Zone hatte, erfuhr vorher die Namen aller
    uebrigen -- die Seite war zonengefiltert, diese eine Liste nicht.
    """
    zone_a = create_zone(session, "eigene-zone")
    zone_b = create_zone(session, "fremde-zone")
    eigenes = create_device(session, "eigener-fuehler")
    fremdes = create_device(session, "fremder-fuehler")
    session.add(
        ZoneDevice(
            zone_id=zone_a.id,
            device_id=eigenes.id,
            device_role_id=role(session, "sensor").id,
        )
    )
    session.add(
        ZoneDevice(
            zone_id=zone_b.id,
            device_id=fremdes.id,
            device_role_id=role(session, "sensor").id,
        )
    )
    session.flush()

    seite = client_als([("device.read", zone_a.id)]).get("/controllers")

    assert seite.status_code == 200
    assert "eigener-fuehler" in seite.text
    assert "fremder-fuehler" not in seite.text


def test_bediengeraeteseite_verlangt_geraeteleserecht(client_als) -> None:
    """Die Navigation hat `device.read` immer behauptet, die Seite nie geprueft."""
    assert client_als([("zone.read", None)]).get("/controllers").status_code == 403


def test_quellgeraet_eines_kanals_muss_lesbar_sein(client_als, session: Session) -> None:
    """Auch das Quellgeraet eines Kanals wird gegen den Principal geprueft.

    Ein Sensorkanal zeigt den Messwert des Quellgeraets auf dem Bediengeraet an. Ohne
    Pruefung haette ein auf eine Zone beschraenkter Nutzer die Temperatur eines
    fremden Raums auf sein eigenes Display holen koennen.
    """
    zone_a = create_zone(session, "eigene-zone")
    zone_b = create_zone(session, "fremde-zone")
    device = create_device(session, "wandregler")
    fremder_fuehler = create_device(session, "fremder-fuehler")
    session.add(
        ZoneDevice(
            zone_id=zone_a.id,
            device_id=device.id,
            device_role_id=role(session, "controller").id,
        )
    )
    session.add(
        ZoneDevice(
            zone_id=zone_b.id,
            device_id=fremder_fuehler.id,
            device_role_id=role(session, "sensor").id,
        )
    )
    session.add(
        DeviceProperty(
            device_id=device.id,
            name="local_temperature",
            value_type="numeric",
            is_readable=True,
            is_writable=True,
        )
    )
    session.add(ChannelKind(code="sensor_temperature", label="Fuehlertemperatur"))
    session.flush()
    client = client_als([("device.manage", zone_a.id), ("device.read", zone_a.id)])
    session_secret = client.cookies.get(COOKIE_NAME)
    assert session_secret is not None

    response = client.post(
        "/controllers/channel",
        data={
            "device_id": str(device.id),
            "property_name": "local_temperature",
            "direction": "write",
            "kind": "sensor_temperature",
            "source_device_id": str(fremder_fuehler.id),
        },
        headers={
            CSRF_HEADER: csrf_token(
                session_secret, get_settings().secret_key.get_secret_value()
            )
        },
        follow_redirects=False,
    )

    assert response.status_code == 404
    assert session.query(ControllerChannel).count() == 0


def test_kiosk_token_gilt_auch_bei_mcp_nicht(session: Session) -> None:
    """Derselbe Riegel wie bei REST -- MCP bietet dieselbe freie Uebersteuerung an."""
    zone = create_zone(session, "kiosk-zone")
    owner = user_with_permissions(
        session,
        "mcp-kiosk-owner",
        [
            ("zone.read", zone.id),
            ("setpoint.write", zone.id),
            ("override.create", zone.id),
        ],
    )
    _token, plaintext = issue_kiosk_token(
        session, owner, "Wandtablett", [zone.id], control_allowed=True, expires_at=None
    )

    with pytest.raises(PermissionError, match="Kiosk"):
        mcp_server.list_zones(session, plaintext)


def test_der_erlaubte_fall_geht_weiterhin(client_als, session: Session) -> None:
    """Die Gegenprobe zu den Riegeln: Wer die Rechte hat, richtet den Kanal wie bisher ein.

    Ohne diesen Test belegt die Datei nur, was jetzt verboten ist. Eine Rechtepruefung,
    die auch den erlaubten Fall abweist, waere aber genauso kaputt -- nur unauffaelliger.
    """
    zone = create_zone(session, "eigene-zone")
    device = create_device(session, "wandregler")
    fuehler = create_device(session, "eigener-fuehler")
    for geraet, rolle in ((device, "controller"), (fuehler, "sensor")):
        session.add(
            ZoneDevice(
                zone_id=zone.id,
                device_id=geraet.id,
                device_role_id=role(session, rolle).id,
            )
        )
    session.add(
        DeviceProperty(
            device_id=device.id,
            name="local_temperature",
            value_type="numeric",
            is_readable=True,
            is_writable=True,
        )
    )
    session.add(ChannelKind(code="sensor_temperature", label="Fuehlertemperatur"))
    session.flush()
    client = client_als([("device.manage", zone.id), ("device.read", zone.id)])
    session_secret = client.cookies.get(COOKIE_NAME)
    assert session_secret is not None

    response = client.post(
        "/controllers/channel",
        data={
            "device_id": str(device.id),
            "property_name": "local_temperature",
            "direction": "write",
            "kind": "sensor_temperature",
            "source_device_id": str(fuehler.id),
        },
        headers={
            CSRF_HEADER: csrf_token(
                session_secret, get_settings().secret_key.get_secret_value()
            )
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    kanal = session.query(ControllerChannel).one()
    assert kanal.source_device_id == fuehler.id
