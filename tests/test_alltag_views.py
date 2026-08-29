from datetime import datetime
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.hilfen import einstellungen_anlegen, quelle, zone_anlegen
from thermoctl.auth.csrf import CSRF_HEADER, csrf_token
from thermoctl.auth.sessions import COOKIE_NAME
from thermoctl.config import get_settings
from thermoctl.db.models.override import ZoneOverride
from thermoctl.db.models.zustand import ShadowDecision


def _grundlage(session: Session):
    quelle(session, "web")
    quelle(session, "api")
    einstellungen_anlegen(session)
    return zone_anlegen(session, "wohnzimmer")


def _csrf(client: TestClient) -> dict[str, str]:
    sitzung = client.cookies.get(COOKIE_NAME)
    assert sitzung is not None
    return {CSRF_HEADER: csrf_token(sitzung, get_settings().secret_key.get_secret_value())}


def test_parameterseite_zeigt_geerbte_werte_und_leer_stellt_vererbung_wieder_her(
    session: Session, client_als
) -> None:
    zone = _grundlage(session)
    zone.hysteresis_k = Decimal("0.70")
    client = client_als([("zone.manage", zone.id)])

    antwort = client.post(
        f"/zonen/{zone.id}/parameter",
        data={
            "hysteresis_k": "",
            "min_on_seconds": "",
            "min_off_seconds": "",
            "sensor_timeout_seconds": "",
            "temperature_offset_k": "",
            "window_resume_delay_seconds": "",
        },
        headers=_csrf(client),
        follow_redirects=True,
    )

    assert antwort.status_code == 200
    assert zone.hysteresis_k is None
    assert "Derzeit 0.30 K aus dem globalen Standard" in antwort.text


def test_negative_hysterese_wird_abgewiesen_offset_aber_gespeichert(
    session: Session, client_als
) -> None:
    zone = _grundlage(session)
    client = client_als([("zone.manage", zone.id)])
    pfad = f"/zonen/{zone.id}/parameter"

    fehler = client.post(pfad, data={"hysteresis_k": "-0.2"}, headers=_csrf(client))
    assert fehler.status_code == 200
    assert "darf nicht negativ" in fehler.text
    assert zone.hysteresis_k is None

    antwort = client.post(
        pfad,
        data={"temperature_offset_k": "-1.25"},
        headers=_csrf(client),
        follow_redirects=False,
    )
    assert antwort.status_code == 303
    assert zone.temperature_offset_k == Decimal("-1.25")


def test_parameter_fremder_zone_ist_404(session: Session, client_als) -> None:
    eigene = _grundlage(session)
    fremde = zone_anlegen(session, "fremd")
    client = client_als([("zone.manage", eigene.id)])
    assert client.get(f"/zonen/{fremde.id}/parameter").status_code == 404
    assert (
        client.post(f"/zonen/{fremde.id}/parameter", data={}, headers=_csrf(client)).status_code
        == 404
    )


def test_uebersteuerung_der_oberflaeche_nutzt_dasselbe_datenmodell_wie_rest(
    session: Session, client_als
) -> None:
    zone = _grundlage(session)
    client = client_als([("zone.read", zone.id), ("override.create", zone.id)])

    antwort = client.post(
        f"/zonen/{zone.id}/uebersteuerung",
        data={"temperature_c": "21.5", "ende": "dauerhaft"},
        headers=_csrf(client),
        follow_redirects=False,
    )

    assert antwort.status_code == 303
    eintrag = session.scalar(select(ZoneOverride).where(ZoneOverride.zone_id == zone.id))
    assert eintrag is not None
    # REST gibt genau diese persistierte ZoneOverride-Zeile zurück; die Webansicht
    # ruft dieselbe Domänenfunktion auf und erzeugt keinen zweiten UI-Datentyp.
    assert (eintrag.temperature_c, eintrag.ends_at, eintrag.cancelled_at) == (
        Decimal("21.5"),
        None,
        None,
    )


def test_uebersteuerung_anzeigen_und_aufheben(session: Session, client_als) -> None:
    zone = _grundlage(session)
    client = client_als(
        [("zone.read", zone.id), ("override.create", zone.id), ("override.cancel", zone.id)]
    )
    client.post(
        f"/zonen/{zone.id}/uebersteuerung",
        data={"temperature_c": "22", "ende": "dauerhaft"},
        headers=_csrf(client),
    )
    seite = client.get("/")
    assert "Übersteuerung auf 22.0 °C" in seite.text
    assert "manuell gewählte feste Temperatur" in seite.text

    antwort = client.post(
        f"/zonen/{zone.id}/uebersteuerung/aufheben",
        headers=_csrf(client),
        follow_redirects=False,
    )
    assert antwort.status_code == 303
    eintrag = session.scalar(select(ZoneOverride).where(ZoneOverride.zone_id == zone.id))
    assert eintrag is not None and eintrag.cancelled_at is not None


def test_uebersteuerung_fremder_zone_ist_404(session: Session, client_als) -> None:
    eigene = _grundlage(session)
    fremde = zone_anlegen(session, "fremd")
    client = client_als(
        [("zone.read", eigene.id), ("override.create", eigene.id), ("override.cancel", eigene.id)]
    )
    assert (
        client.post(
            f"/zonen/{fremde.id}/uebersteuerung",
            data={"temperature_c": "20", "ende": "dauerhaft"},
            headers=_csrf(client),
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/zonen/{fremde.id}/uebersteuerung/aufheben", headers=_csrf(client)
        ).status_code
        == 404
    )


def test_uebersicht_erklaert_fehlenden_messwert_und_zeigt_entscheidung(
    session: Session, client_als
) -> None:
    zone = _grundlage(session)
    session.add(
        ShadowDecision(
            decided_at=datetime(2026, 8, 29, 7),
            zone_id=zone.id,
            temperature_c=None,
            setpoint_c=Decimal("16.0"),
            setpoint_reason="Frostschutz",
            would_heat=False,
            previous_would_heat=None,
            outcome_code="sensor_missing",
            reason="Kein verwertbarer Messwert",
        )
    )
    session.flush()
    client = client_als([("zone.read", zone.id)])

    antwort = client.get("/")

    assert antwort.status_code == 200
    assert "kein Messwert für die Temperatur" in antwort.text
    assert "None" not in antwort.text
    assert ">0 °C" not in antwort.text
    assert "Kein verwertbarer Messwert" in antwort.text
    assert "Betriebsart: auto" in antwort.text
