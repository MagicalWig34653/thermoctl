from collections.abc import Callable
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.hilfen import (
    benutzer_mit_rechten,
    betriebsart,
    einstellungen_anlegen,
    modus_anlegen,
    quelle,
    zone_anlegen,
)
from thermoctl.auth.csrf import CSRF_HEADER, csrf_token
from thermoctl.auth.sessions import COOKIE_NAME
from thermoctl.auth.tokens import token_ausstellen
from thermoctl.config import get_settings
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.zone import Zone, ZoneSetpoint


@pytest.fixture
def api_token(session: Session) -> Callable[[list[tuple[str, int | None]]], dict[str, str]]:
    quelle(session, "web")
    zaehler = 0

    def erstellen(rechte: list[tuple[str, int | None]]) -> dict[str, str]:
        nonlocal zaehler
        zaehler += 1
        nutzer = benutzer_mit_rechten(session, f"konfig-api-{zaehler}", rechte)
        _token, klartext = token_ausstellen(
            session, nutzer, f"Konfiguration {zaehler}", rechte, None
        )
        return {"Authorization": f"Bearer {klartext}"}

    return erstellen


def test_zonen_anlegen_aendern_und_loeschen(
    client: TestClient, session: Session, api_token
) -> None:
    art = betriebsart(session)
    kopf = api_token([("zone.manage", None), ("zone.read", None)])
    daten = {
        "name": "api-zone",
        "display_name": "API-Zone",
        "operating_mode_id": art.id,
        "sort_order": 4,
        "temperature_source_device_id": None,
    }

    angelegt = client.post("/api/v1/zones", headers=kopf, json=daten)
    assert angelegt.status_code == 201
    zone_id = angelegt.json()["id"]
    daten["display_name"] = "Geänderte API-Zone"
    assert (
        client.put(f"/api/v1/zones/{zone_id}", headers=kopf, json=daten).json()["display_name"]
        == "Geänderte API-Zone"
    )
    assert client.delete(f"/api/v1/zones/{zone_id}", headers=kopf).status_code == 204
    assert session.get(Zone, zone_id) is None


def test_zonenmutation_braucht_recht_und_meldet_doppelten_namen(
    client: TestClient, session: Session, api_token
) -> None:
    zone = zone_anlegen(session, "schon-da-api")
    daten = {
        "name": zone.name,
        "display_name": "Doppelt",
        "operating_mode_id": zone.operating_mode_id,
    }
    ohne_recht = api_token([("zone.read", None)])
    assert client.post("/api/v1/zones", headers=ohne_recht, json=daten).status_code == 403
    mit_recht = api_token([("zone.read", None), ("zone.manage", None)])
    antwort = client.post("/api/v1/zones", headers=mit_recht, json=daten)
    assert antwort.status_code == 422
    assert "name" in antwort.json()["detail"]


def test_aendern_und_loeschen_pruefen_ihr_jeweiliges_recht(
    client: TestClient, session: Session, api_token
) -> None:
    zone = zone_anlegen(session, "rechte-api-zone")
    modus = modus_anlegen(session, "rechte-api-modus")
    punkt = SchedulePoint(zone_id=zone.id, weekday=1, minute_of_day=0, setpoint_mode_id=modus.id)
    session.add(punkt)
    session.flush()
    kopf = api_token([("zone.read", zone.id)])
    zonendaten = {
        "name": zone.name,
        "display_name": zone.display_name,
        "operating_mode_id": zone.operating_mode_id,
    }

    assert client.put(f"/api/v1/zones/{zone.id}", headers=kopf, json=zonendaten).status_code == 403
    assert client.delete(f"/api/v1/zones/{zone.id}", headers=kopf).status_code == 403
    assert (
        client.delete(f"/api/v1/zones/{zone.id}/schedule/{punkt.id}", headers=kopf).status_code
        == 403
    )


def test_modi_lesen_und_anlegen(client: TestClient, session: Session, api_token) -> None:
    zone = zone_anlegen(session, "modus-api-zone")
    kopf = api_token([("zone.read", zone.id), ("mode.manage", None)])
    assert client.get("/api/v1/modes", headers=kopf).status_code == 200
    antwort = client.post(
        "/api/v1/modes",
        headers=kopf,
        json={"code": "urlaub-api", "name": "Urlaub", "sort_order": 7},
    )
    assert antwort.status_code == 201
    assert antwort.json()["code"] == "urlaub-api"

    ohne_recht = api_token([("zone.read", zone.id)])
    assert (
        client.post(
            "/api/v1/modes", headers=ohne_recht, json={"code": "x", "name": "X"}
        ).status_code
        == 403
    )
    fehler = client.post("/api/v1/modes", headers=kopf, json={"code": " ", "name": "X"})
    assert fehler.status_code == 422
    assert "code" in fehler.json()["detail"]


def test_sollwerte_lesen_und_schreiben_wie_die_domaene(
    client: TestClient, client_als, session: Session, api_token
) -> None:
    zone = zone_anlegen(session, "sollwert-api-zone")
    web_zone = zone_anlegen(session, "sollwert-web-zone")
    modus = modus_anlegen(session, "komfort-api", "Komfort")
    kopf = api_token([("zone.read", zone.id), ("setpoint.write", zone.id)])
    daten = {"setpoints": [{"mode_id": modus.id, "temperature_c": "21.5"}]}
    antwort = client.put(f"/api/v1/zones/{zone.id}/setpoints", headers=kopf, json=daten)
    assert antwort.status_code == 200
    zeile = session.get(ZoneSetpoint, (zone.id, modus.id))
    assert zeile is not None and zeile.temperature_c == Decimal("21.5")

    web_client = client_als([("setpoint.write", web_zone.id)])
    sitzung = web_client.cookies[COOKIE_NAME]
    csrf = csrf_token(sitzung, get_settings().secret_key.get_secret_value())
    assert (
        web_client.post(
            f"/zonen/{web_zone.id}/sollwerte",
            data={f"sollwert_{modus.id}": "21.5"},
            headers={CSRF_HEADER: csrf},
            follow_redirects=False,
        ).status_code
        == 303
    )
    web_zeile = session.get(ZoneSetpoint, (web_zone.id, modus.id))
    assert web_zeile is not None
    assert web_zeile.temperature_c == zeile.temperature_c
    assert client.get(f"/api/v1/zones/{zone.id}/setpoints", headers=kopf).status_code == 200

    ohne_recht = api_token([("zone.read", zone.id)])
    assert (
        client.put(f"/api/v1/zones/{zone.id}/setpoints", headers=ohne_recht, json=daten).status_code
        == 403
    )
    daten["setpoints"][0]["temperature_c"] = "40.0"
    fehler = client.put(f"/api/v1/zones/{zone.id}/setpoints", headers=kopf, json=daten)
    assert fehler.status_code == 422
    assert "temperature_c" in fehler.json()["detail"]


def test_zeitplan_lesen_anlegen_und_loeschen(
    client: TestClient, session: Session, api_token
) -> None:
    zone = zone_anlegen(session, "zeitplan-api-zone")
    modus = modus_anlegen(session, "nacht-api", "Nacht")
    kopf = api_token([("zone.read", zone.id), ("schedule.manage", zone.id)])
    daten = {"weekday": 1, "minute_of_day": 1320, "mode_id": modus.id}
    antwort = client.post(f"/api/v1/zones/{zone.id}/schedule", headers=kopf, json=daten)
    assert antwort.status_code == 201
    punkt_id = antwort.json()["id"]
    assert (
        client.get(f"/api/v1/zones/{zone.id}/schedule", headers=kopf).json()[0]["mode_name"]
        == "Nacht"
    )
    assert (
        client.delete(f"/api/v1/zones/{zone.id}/schedule/{punkt_id}", headers=kopf).status_code
        == 204
    )
    assert session.get(SchedulePoint, punkt_id) is None

    ohne_recht = api_token([("zone.read", zone.id)])
    assert (
        client.post(f"/api/v1/zones/{zone.id}/schedule", headers=ohne_recht, json=daten).status_code
        == 403
    )
    fehler = client.post(
        f"/api/v1/zones/{zone.id}/schedule", headers=kopf, json={**daten, "weekday": 8}
    )
    assert fehler.status_code == 422
    assert "weekday" in str(fehler.json()["detail"])


def test_regelparameter_lesen_und_schreiben(
    client: TestClient, session: Session, api_token
) -> None:
    einstellungen_anlegen(session)
    quelle(session, "web")
    zone = zone_anlegen(session, "parameter-api-zone")
    kopf = api_token([("zone.read", zone.id), ("zone.manage", zone.id)])
    daten = {
        "hysteresis_k": "0.45",
        "min_on_seconds": 120,
        "min_off_seconds": None,
        "sensor_timeout_seconds": None,
        "temperature_offset_k": "-0.20",
        "window_resume_delay_seconds": None,
    }
    antwort = client.put(f"/api/v1/zones/{zone.id}/parameters", headers=kopf, json=daten)
    assert antwort.status_code == 200
    assert antwort.json()["hysteresis_k"] == "0.45"
    assert client.get(f"/api/v1/zones/{zone.id}/parameters", headers=kopf).status_code == 200

    ohne_recht = api_token([("zone.read", zone.id)])
    assert (
        client.put(
            f"/api/v1/zones/{zone.id}/parameters", headers=ohne_recht, json=daten
        ).status_code
        == 403
    )
    fehler = client.put(
        f"/api/v1/zones/{zone.id}/parameters", headers=kopf, json={**daten, "min_on_seconds": -1}
    )
    assert fehler.status_code == 422
    assert "min_on_seconds" in str(fehler.json()["detail"])


@pytest.mark.parametrize("pfad", ["setpoints", "schedule", "parameters"])
def test_fremde_zone_bleibt_fuer_neue_wege_verborgen(
    pfad: str, client: TestClient, session: Session, api_token
) -> None:
    eigene = zone_anlegen(session, f"eigene-{pfad}")
    fremde = zone_anlegen(session, f"fremde-{pfad}")
    kopf = api_token([("zone.read", eigene.id)])
    assert client.get(f"/api/v1/zones/{fremde.id}/{pfad}", headers=kopf).status_code == 404


def test_modi_lesen_ohne_sichtbare_zone_wird_verweigert(client, api_token, session) -> None:
    """Wer keine einzige Zone sehen darf, hat auch nichts in der Modusliste zu suchen —
    sonst waere sie eine Auskunft ueber die Anlage an jemanden ohne jedes Zonenrecht."""
    zone_anlegen(session, "unsichtbare-zone")
    kopf = api_token([("token.self", None)])
    assert client.get("/api/v1/modes", headers=kopf).status_code == 403


def test_umbenennen_auf_vergebenen_namen_ergibt_422(client, api_token, session) -> None:
    quelle(session, "api")
    art = betriebsart(session, "auto")
    zone_anlegen(session, "belegt")
    andere = zone_anlegen(session, "wird-umbenannt")
    kopf = api_token([("zone.manage", None), ("zone.read", None)])
    antwort = client.put(
        f"/api/v1/zones/{andere.id}",
        json={
            "name": "belegt", "display_name": "Andere",
            "operating_mode_id": art.id, "sort_order": 0,
            "temperature_source_device_id": None,
        },
        headers=kopf,
    )
    assert antwort.status_code == 422
    assert "bereits vergeben" in antwort.text
    assert andere.name == "wird-umbenannt"


def test_doppelter_zeitplanpunkt_ergibt_422_mit_meldung(client, api_token, session) -> None:
    """Ein fachlicher Fehler der Domaene wird zu 422 mit Feldnamen, nicht zu 500.

    Absichtlich ein Fall, den die Schema-Pruefung durchlaesst: Zwei Punkte am selben
    Zeitpunkt sind formal gueltig und scheitern erst an der Regel.
    """
    quelle(session, "api")
    zone = zone_anlegen(session, "zone-api-zeitplan")
    modus = modus_anlegen(session, "api-tag", "Tag")
    kopf = api_token([("schedule.manage", None), ("zone.read", None)])
    nutzlast = {"weekday": 1, "minute_of_day": 360, "mode_id": modus.id}
    assert client.post(
        f"/api/v1/zones/{zone.id}/schedule", json=nutzlast, headers=kopf
    ).status_code in (200, 201)
    antwort = client.post(
        f"/api/v1/zones/{zone.id}/schedule", json=nutzlast, headers=kopf
    )
    assert antwort.status_code == 422
    assert "500" not in str(antwort.status_code)


def test_fremder_zeitplanpunkt_ergibt_404(client, api_token, session) -> None:
    quelle(session, "api")
    eigene = zone_anlegen(session, "eigene-api")
    fremde = zone_anlegen(session, "fremde-api")
    modus = modus_anlegen(session, "api-fremd", "Tag")
    punkt = SchedulePoint(
        zone_id=fremde.id, weekday=1, minute_of_day=360, setpoint_mode_id=modus.id
    )
    session.add(punkt)
    session.flush()
    kopf = api_token([("schedule.manage", None), ("zone.read", None)])
    antwort = client.delete(
        f"/api/v1/zones/{eigene.id}/schedule/{punkt.id}", headers=kopf
    )
    assert antwort.status_code == 404
    assert session.get(SchedulePoint, punkt.id) is not None
