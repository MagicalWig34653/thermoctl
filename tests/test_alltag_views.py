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
    # Komma, nicht Punkt: Die Oberflaeche ist deutsch, und "22.0 °C" liest sich hier
    # wie ein Tippfehler. Eingabefelder behalten den Punkt -- ein <input type="number">
    # verwirft einen Wert mit Komma still.
    assert "Übersteuerung auf 22,0 °C" in seite.text
    # Frueher stand hier zusaetzlich der feste Satz "manuell gewaehlte feste Temperatur".
    # Die Begruendung kommt jetzt aus der Domaene selbst -- derselbe Text, den auch das
    # Schattenprotokoll und die REST-Antwort tragen, statt einer zweiten Formulierung
    # allein fuer diese Seite.
    assert "Uebersteuerung (feste Temperatur)" in seite.text

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
    assert "kein Messwert" in antwort.text
    assert "None" not in antwort.text
    assert ">0 °C" not in antwort.text
    assert "Kein verwertbarer Messwert" in antwort.text
    # Die Betriebsart steht nur da, wenn sie vom Regelfall abweicht: "Automatik" unter
    # jeder Zone waere Rauschen, "Aus" dagegen der Grund, warum es dort kalt bleibt.
    # Siehe test_betriebsart_steht_nur_da_wenn_sie_abweicht.
    assert "Betriebsart" not in antwort.text


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


def test_betriebsart_steht_nur_da_wenn_sie_abweicht(session: Session, client_als) -> None:
    """Gegenprobe zur Zeile oben. Eine Zone auf "Aus" sieht sonst aus wie jede andere --
    und genau sie ist der Grund, warum ein Raum kalt bleibt."""
    from thermoctl.db.models.lookup import OperatingMode

    zone = _grundlage(session)
    aus = OperatingMode(code="off", label="Aus")
    session.add(aus)
    session.flush()
    zone.operating_mode_id = aus.id
    session.flush()

    antwort = client_als([("zone.read", zone.id)]).get("/")
    assert "Betriebsart: Aus" in antwort.text


# --- Thermostat auf der Startseite -----------------------------------------


def _zone_mit_modus(session: Session, temperatur: str = "21.0"):
    """Eine Zone, deren geltender Sollwert aus einem Zeitplanmodus kommt."""
    from tests.hilfen import modus_anlegen
    from thermoctl.db.models.schedule import SchedulePoint
    from thermoctl.db.models.zone import ZoneSetpoint

    zone = _grundlage(session)
    modus = modus_anlegen(session, "thermostat-tag", "Tag")
    session.add(
        ZoneSetpoint(
            zone_id=zone.id, setpoint_mode_id=modus.id, temperature_c=Decimal(temperatur)
        )
    )
    session.add(
        SchedulePoint(
            zone_id=zone.id, weekday=1, minute_of_day=0, setpoint_mode_id=modus.id
        )
    )
    session.flush()
    return zone, modus


def test_thermostat_hebt_den_sollwert_des_laufenden_modus(
    session: Session, client_als
) -> None:
    """Nicht eine Uebersteuerung: Der Klick aendert den hinterlegten Sollwert des Modus
    dauerhaft. Deshalb steht auf der Seite auch daneben, welcher Modus gemeint ist."""
    from thermoctl.db.models.zone import ZoneSetpoint

    zone, modus = _zone_mit_modus(session)
    client = client_als([("zone.read", zone.id), ("setpoint.write", zone.id)])

    antwort = client.post(
        f"/zonen/{zone.id}/thermostat",
        data={"modus_id": str(modus.id), "richtung": "hoch"},
        headers=_csrf(client),
        follow_redirects=False,
    )
    assert antwort.status_code == 303
    zeile = session.scalars(
        select(ZoneSetpoint).where(
            ZoneSetpoint.zone_id == zone.id, ZoneSetpoint.setpoint_mode_id == modus.id
        )
    ).one()
    assert zeile.temperature_c == Decimal("21.5")


def test_zwei_klicks_sind_zwei_stufen(session: Session, client_als) -> None:
    """Die Stufe wird auf den aktuellen Wert gerechnet, nicht auf den, den die Seite
    beim Rendern kannte -- sonst waere der zweite Klick wirkungslos."""
    from thermoctl.db.models.zone import ZoneSetpoint

    zone, modus = _zone_mit_modus(session)
    client = client_als([("zone.read", zone.id), ("setpoint.write", zone.id)])
    for _ in range(2):
        client.post(
            f"/zonen/{zone.id}/thermostat",
            data={"modus_id": str(modus.id), "richtung": "runter"},
            headers=_csrf(client),
        )
    zeile = session.scalars(
        select(ZoneSetpoint).where(ZoneSetpoint.setpoint_mode_id == modus.id)
    ).one()
    assert zeile.temperature_c == Decimal("20.0")


def test_thermostat_bleibt_an_der_grenze_stehen(session: Session, client_als) -> None:
    """35 Grad ist das Ende des Weges, kein Fehlerzustand -- die Domaenengrenze gilt,
    und die Seite zeigt danach den unveraenderten Wert mit einem Hinweis."""
    from thermoctl.db.models.zone import ZoneSetpoint

    zone, modus = _zone_mit_modus(session, "35.0")
    client = client_als([("zone.read", zone.id), ("setpoint.write", zone.id)])
    antwort = client.post(
        f"/zonen/{zone.id}/thermostat",
        data={"modus_id": str(modus.id), "richtung": "hoch"},
        headers=_csrf(client),
        follow_redirects=False,
    )
    assert antwort.status_code == 303
    assert "thermostatfehler" in antwort.headers["location"]
    zeile = session.scalars(
        select(ZoneSetpoint).where(ZoneSetpoint.setpoint_mode_id == modus.id)
    ).one()
    assert zeile.temperature_c == Decimal("35.0")


def test_thermostat_braucht_setpoint_write(session: Session, client_als) -> None:
    zone, modus = _zone_mit_modus(session)
    client = client_als([("zone.read", zone.id)])
    antwort = client.post(
        f"/zonen/{zone.id}/thermostat",
        data={"modus_id": str(modus.id), "richtung": "hoch"},
        headers=_csrf(client),
    )
    assert antwort.status_code == 404


def test_ohne_setpoint_write_steht_kein_thermostat_auf_der_seite(
    session: Session, client_als
) -> None:
    """Gegenprobe zur Anzeige: Wer nicht verstellen darf, sieht den Sollwert, aber keine
    Stufentasten."""
    zone, _modus = _zone_mit_modus(session)
    nur_lesen = client_als([("zone.read", zone.id)]).get("/")
    assert "tc-stufe" not in nur_lesen.text

    darf = client_als([("zone.read", zone.id), ("setpoint.write", zone.id)]).get("/")
    assert "tc-stufe" in darf.text


def test_thermostat_wirkt_auch_ohne_hinterlegten_sollwert(
    session: Session, client_als
) -> None:
    """Der Zustand einer frisch eingerichteten Anlage: keine Sollwerte gepflegt, kein
    Zeitplan. Die Seite zeigt dann den Frostschutz-Notnagel von 16 Grad -- und das
    Thermostat suchte eine Zeile, die es nicht gibt, und antwortete mit 404. Auf der
    Seite sah es aus, als passiere beim Druecken nichts.
    """
    from sqlalchemy import select

    from thermoctl.db.base import utcnow
    from thermoctl.db.models.zone import ZoneSetpoint
    from thermoctl.domain.schedule import aufgeloester_sollwert

    zone = _grundlage(session)
    session.query(ZoneSetpoint).filter_by(zone_id=zone.id).delete()
    session.flush()
    angezeigt = aufgeloester_sollwert(session, zone, utcnow())
    assert angezeigt.modus_id is not None

    client = client_als([("zone.read", zone.id), ("setpoint.write", zone.id)])
    antwort = client.post(
        f"/zonen/{zone.id}/thermostat",
        data={"modus_id": str(angezeigt.modus_id), "richtung": "hoch"},
        headers=_csrf(client),
        follow_redirects=False,
    )
    assert antwort.status_code == 303

    zeile = session.scalars(
        select(ZoneSetpoint).where(
            ZoneSetpoint.zone_id == zone.id,
            ZoneSetpoint.setpoint_mode_id == angezeigt.modus_id,
        )
    ).one()
    assert zeile.temperature_c == angezeigt.temperature_c + Decimal("0.5")


def test_thermostat_fuer_einen_fremden_modus_bleibt_ein_404(
    session: Session, client_als
) -> None:
    """Gegenprobe: Der Notnagel gilt nur fuer den Modus, den die Seite gerade anzeigt.
    Ein beliebiger anderer bekommt weiter eine klare Absage statt eines aus dem Nichts
    erfundenen Sollwerts."""
    from tests.hilfen import modus_anlegen

    zone = _grundlage(session)
    fremder = modus_anlegen(session, "nie-benutzt")
    client = client_als([("zone.read", zone.id), ("setpoint.write", zone.id)])
    antwort = client.post(
        f"/zonen/{zone.id}/thermostat",
        data={"modus_id": str(fremder.id), "richtung": "hoch"},
        headers=_csrf(client),
    )
    assert antwort.status_code == 404
