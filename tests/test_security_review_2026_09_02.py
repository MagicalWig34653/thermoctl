"""Regressionstests zu den Befunden der Sicherheitsdurchsicht vom 2026-09-02.

Sie sind als Beweis-Tests der Lücken entstanden und halten jetzt die Korrektur fest.
Jeder von ihnen ist einmal grün gewesen, weil der Angriff funktionierte -- das ist
der Grund, warum sie hier stehen und nicht bloß bestätigen, was der Code ohnehin
tut. Die Durchsicht selbst steht in `docs/sicherheitsdurchsicht-2026-09-02.md`.
"""

from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.helpers import (
    create_device,
    create_zone,
    role,
    source,
    user_with_permissions,
)
from thermoctl.auth.csrf import CSRF_HEADER, csrf_token
from thermoctl.auth.sessions import COOKIE_NAME
from thermoctl.config import get_settings
from thermoctl.db.models.device import (
    ControllerChannel,
    DeviceProperty,
    ZoneDevice,
)
from thermoctl.db.models.lookup import ChannelKind
from thermoctl.domain.kiosk import issue_kiosk_token
from thermoctl.integrations.notification import _NoRedirectHandler
from thermoctl.mcp import server as mcp_server


def test_kiosk_token_gilt_nicht_als_rest_bearer(
    client: TestClient, session: Session
) -> None:
    """Wer das Wandtablett hat, hat das Token -- und darf damit nur, was das Kiosk kann.

    Vor der Korrektur nahm die allgemeine REST-API dasselbe Token an und legte damit
    eine unbefristete Übersteuerung auf 35 Grad an. Die Kiosk-Oberfläche bietet so
    etwas gar nicht an, sie verstellt in festen Schritten. Die enge Bedienfläche war
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
            ("override.cancel", zone.id),
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




def test_offen_webhook_redirect_nimmt_authorization_an_internes_ziel_mit() -> None:
    """BEHOBEN -- hielt eine offene Lücke fest, hält jetzt die Korrektur fest.

    Der Standard-`HTTPRedirectHandler` (unten weiter benutzt, um die Lücke selbst
    zu belegen) behält bei einem 302 auch über einen Hostwechsel hinweg alle
    Header außer `Content-*` bei -- `Authorization` eingeschlossen. Genau deshalb
    benutzt `integrations/notification.py` ihn nicht mehr: `_send_webhook` geht
    seit der Korrektur über einen eigenen Opener mit
    `notification._NoRedirectHandler`, der eine solche Weiterleitung gar nicht erst
    in eine neue Anfrage übersetzt, sondern ablehnt. Der zweite Teil dieses Tests
    beweist genau das.
    """
    original = Request(
        "https://webhook.example/meldung",
        data=b"{}",
        headers={"Authorization": "Bearer webhook-geheimnis"},
        method="POST",
    )

    # Der Standardfall, unverändert: das ist die Lücke, die es zu vermeiden galt.
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

    # Der von thermoctl tatsächlich benutzte Handler lehnt dieselbe Weiterleitung
    # ab, statt eine Anfrage zu bauen, die den Header mitnehmen könnte.
    with pytest.raises(HTTPError, match="Webhook-Weiterleitung abgelehnt"):
        _NoRedirectHandler().redirect_request(
            original,
            None,
            302,
            "Found",
            {},
            "http://127.0.0.1:8080/intern",
        )








def test_quellgeraet_eines_kanals_muss_lesbar_sein(client_als, session: Session) -> None:
    """Auch das Quellgerät eines Kanals wird gegen den Principal geprüft.

    Ein Sensorkanal zeigt den Messwert des Quellgeräts auf dem Bediengerät an. Ohne
    Prüfung hätte ein auf eine Zone beschränkter Nutzer die Temperatur eines
    fremden Raums auf sein eigenes Display holen können.
    """
    zone_a = create_zone(session, "eigene-zone")
    zone_b = create_zone(session, "fremde-zone")
    device = create_device(session, "wandregler")
    fremder_fuehler = create_device(session, "fremder-fühler")
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
    session.add(ChannelKind(code="sensor_temperature", label="Fühlertemperatur"))
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
    """Derselbe Riegel wie bei REST -- MCP bietet dieselbe freie Übersteuerung an."""
    zone = create_zone(session, "kiosk-zone")
    owner = user_with_permissions(
        session,
        "mcp-kiosk-owner",
        [
            ("zone.read", zone.id),
            ("setpoint.write", zone.id),
            ("override.create", zone.id),
            ("override.cancel", zone.id),
        ],
    )
    _token, plaintext = issue_kiosk_token(
        session, owner, "Wandtablett", [zone.id], control_allowed=True, expires_at=None
    )

    with pytest.raises(PermissionError, match="Kiosk"):
        mcp_server.list_zones(session, plaintext)


