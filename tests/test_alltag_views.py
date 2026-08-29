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


def test_uebersteuerung_bis_zur_naechsten_schaltung(session: Session, client_als) -> None:
    """Das Ende wird beim Anlegen ausgerechnet und abgelegt, nicht als Regel gemerkt —
    eine spaetere Zeitplanaenderung verschiebt eine laufende Uebersteuerung nicht."""
    from tests.hilfen import modus_anlegen
    from thermoctl.db.models.schedule import SchedulePoint

    zone = _grundlage(session)
    modus = modus_anlegen(session, "tag-uebersteuerung", "Tag")
    session.add(
        SchedulePoint(zone_id=zone.id, weekday=1, minute_of_day=360, setpoint_mode_id=modus.id)
    )
    session.flush()
    client = client_als([("override.create", None), ("zone.read", None)])
    antwort = client.post(
        f"/zonen/{zone.id}/uebersteuerung",
        data={"temperature_c": "21,5", "ende": "naechste_schaltung"},
        headers=_csrf(client), follow_redirects=False,
    )
    assert antwort.status_code == 303
    eintrag = session.scalar(select(ZoneOverride).where(ZoneOverride.zone_id == zone.id))
    assert eintrag is not None and eintrag.ends_at is not None


def test_uebersteuerung_fuer_eine_dauer(session: Session, client_als) -> None:
    zone = _grundlage(session)
    client = client_als([("override.create", None), ("zone.read", None)])
    antwort = client.post(
        f"/zonen/{zone.id}/uebersteuerung",
        data={"temperature_c": "20.0", "ende": "dauer", "dauer_minuten": "120"},
        headers=_csrf(client), follow_redirects=False,
    )
    assert antwort.status_code == 303
    eintrag = session.scalar(select(ZoneOverride).where(ZoneOverride.zone_id == zone.id))
    assert eintrag is not None and eintrag.ends_at is not None


def test_uebersteuerung_ohne_zeitplan_gilt_dauerhaft(session: Session, client_als) -> None:
    """Ohne Schaltpunkt gibt es keine naechste Schaltung — dann gilt sie, bis jemand sie
    aufhebt. Stillschweigend gar nichts zu tun waere die schlechtere Antwort."""
    zone = _grundlage(session)
    client = client_als([("override.create", None), ("zone.read", None)])
    client.post(
        f"/zonen/{zone.id}/uebersteuerung",
        data={"temperature_c": "20.0", "ende": "naechste_schaltung"},
        headers=_csrf(client),
    )
    eintrag = session.scalar(select(ZoneOverride).where(ZoneOverride.zone_id == zone.id))
    assert eintrag is not None and eintrag.ends_at is None


def test_unsinnige_uebersteuerungen_werden_abgewiesen(session: Session, client_als) -> None:
    """Eine Heizung, die eine unsinnige Eingabe zurechtbiegt, ist schlimmer als eine, die
    widerspricht."""
    zone = _grundlage(session)
    client = client_als([("override.create", None), ("zone.read", None)])
    for daten in (
        {"temperature_c": "warm", "ende": "dauerhaft"},
        {"temperature_c": "2.0", "ende": "dauerhaft"},
        {"temperature_c": "50", "ende": "dauerhaft"},
        {"temperature_c": "20", "ende": "dauer", "dauer_minuten": "0"},
        {"temperature_c": "20", "ende": "dauer", "dauer_minuten": "keine Zahl"},
        {"temperature_c": "20", "ende": "irgendwas"},
    ):
        antwort = client.post(
            f"/zonen/{zone.id}/uebersteuerung", data=daten,
            headers=_csrf(client), follow_redirects=False,
        )
        assert antwort.status_code == 303, daten
        assert "uebersteuerungsfehler" in (antwort.headers.get("location") or ""), daten
    assert session.scalar(select(ZoneOverride).where(ZoneOverride.zone_id == zone.id)) is None


def test_unsinnige_regelparameter_bleiben_im_formular(session: Session, client_als) -> None:
    zone = _grundlage(session)
    client = client_als([("zone.manage", None), ("zone.read", None)])
    for feld, wert in (("hysteresis_k", "keine Zahl"), ("min_on_seconds", "-5")):
        antwort = client.post(
            f"/zonen/{zone.id}/parameter", data={feld: wert}, headers=_csrf(client)
        )
        assert antwort.status_code == 200, feld
    assert zone.hysteresis_k is None and zone.min_on_seconds is None


def test_uebersteuerung_mit_zwei_nachkommastellen_wird_abgewiesen(
    session: Session, client_als
) -> None:
    """Die Oberflaeche prueft nicht mehr selbst — sie faengt nur noch ab, was die Domaene
    sagt. Vorher liess sie zwei Nachkommastellen durch, die REST-Schnittstelle nicht."""
    zone = _grundlage(session)
    client = client_als([("override.create", None), ("zone.read", None)])
    antwort = client.post(
        f"/zonen/{zone.id}/uebersteuerung",
        data={"temperature_c": "21,55", "ende": "dauerhaft"},
        headers=_csrf(client), follow_redirects=False,
    )
    assert antwort.status_code == 303
    assert "uebersteuerungsfehler" in (antwort.headers.get("location") or "")
    assert session.scalar(select(ZoneOverride).where(ZoneOverride.zone_id == zone.id)) is None
