import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy.orm import Session

from tests.hilfen import (
    einstellungen_anlegen,
    geraet_anlegen,
    geraetezustand_anlegen,
    rolle,
    zone_anlegen,
)
from thermoctl.db.base import utcnow
from thermoctl.db.models.device import DeviceCapabilityLink, ZoneDevice
from thermoctl.db.models.lookup import DeviceCapability


def test_geraeteseite_braucht_device_read(client_als) -> None:
    assert client_als([("zone.read", None)]).get("/geraete").status_code == 403


def test_leere_geraeteseite_erklaert_den_normalfall(client_als) -> None:
    antwort = client_als([("device.read", None)]).get("/geraete")
    assert antwort.status_code == 200
    assert "MQTT nicht eingerichtet" in antwort.text
    assert "Gerätenachricht eingegangen" in antwort.text


def test_geraeteseite_zeigt_lebenszeichen_faehigkeit_und_zone(
    client_als, session: Session
) -> None:
    beispiele = json.loads(
        (Path(__file__).parent / "daten/anlage-beispiele.json").read_text(encoding="utf-8")
    )
    geraet = geraet_anlegen(session, beispiele["geraete"][1])
    geraet.display_name = "Prüfsensor"
    geraet.model = "Modell A"
    geraet.is_group = True
    zone = zone_anlegen(session, "pruefzone")
    zone.temperature_source_device_id = geraet.id
    zweite_zone = zone_anlegen(session, "zweite-pruefzone")
    session.add(
        ZoneDevice(
            zone_id=zweite_zone.id,
            device_id=geraet.id,
            device_role_id=rolle(session, "controller").id,
        )
    )
    faehigkeit = DeviceCapability(code="temperature", label="Temperaturmessung")
    session.add(faehigkeit)
    session.flush()
    session.add(DeviceCapabilityLink(device_id=geraet.id, capability_id=faehigkeit.id))
    zustand = geraetezustand_anlegen(session, geraet)
    zustand.battery_percent = Decimal("71.50")
    zustand.link_quality = 88
    zustand.availability = "online"
    session.flush()

    antwort = client_als([("device.read", None)]).get("/geraete")

    assert antwort.status_code == 200
    for erwartet in (
        "Prüfsensor",
        "Modell A",
        "Temperaturmessung",
        "Pruefzone",
        "Zweite-pruefzone",
        "Gruppe",
    ):
        assert erwartet in antwort.text
    # Gerundet: Ein Hundertstel Prozent Batterie ist Rauschen, und die Zahl steht hier,
    # damit man sieht, ob bald eine Zelle faellig ist.
    assert "72 %" in antwort.text
    assert "71.50" not in antwort.text
    assert "LQI 88" in antwort.text
    # Erreichbarkeit steht nur noch da, wenn sie ein Problem ist. Eine Spalte, in der bei
    # jedem gesunden Geraet "online" steht, traegt keine Auskunft -- sie verdeckt die
    # zwei Zeilen, auf die es ankommt.
    assert "die Brücke führt es als offline" not in antwort.text


def test_startseite_zeigt_zonenzustand_ohne_nulltemperatur(client_als, session: Session) -> None:
    from thermoctl.db.models.lookup import SensorStatus
    from thermoctl.db.models.zustand import ZoneState

    einstellungen_anlegen(session)
    zone_mit_wert = zone_anlegen(session, "mit-wert")
    zone_ohne_wert = zone_anlegen(session, "ohne-wert")
    zone_ohne_zustand = zone_anlegen(session, "ohne-zustand")
    status = SensorStatus(code="ok", label="In Ordnung")
    session.add(status)
    session.flush()
    zeitpunkt = datetime(2026, 8, 29, 8, 15)
    session.add_all(
        [
            ZoneState(
                zone_id=zone_mit_wert.id,
                temperature_c=Decimal("20.25"),
                measured_at=zeitpunkt,
                sensor_status_id=status.id,
                updated_at=zeitpunkt,
            ),
            ZoneState(
                zone_id=zone_ohne_wert.id,
                temperature_c=None,
                measured_at=None,
                sensor_status_id=status.id,
                updated_at=zeitpunkt,
            ),
        ]
    )
    session.flush()

    antwort = client_als([("zone.read", None)]).get("/")

    assert antwort.status_code == 200
    # Auf eine Nachkommastelle gerundet: Ein Wohnraum ist nicht auf ein Hundertstel Grad
    # bestimmt, und die zweite Stelle ist Rauschen im wichtigsten Wert der Seite.
    assert "20,2" in antwort.text
    assert "20.25" not in antwort.text
    assert "20,25" not in antwort.text
    # Beide Faelle -- Zustandszeile ohne Wert und gar keine Zustandszeile -- sagen dem
    # Leser dasselbe und stehen deshalb gleich da.
    assert antwort.text.count("kein Messwert") == 2
    assert zone_ohne_zustand.display_name in antwort.text


def test_alter_in_worten_beantwortet_die_frage_nach_der_frische() -> None:
    """Ein roher Zeitstempel mit Mikrosekunden verlangt Kopfrechnen.

    Fuer eine Heizungssteuerung lautet die Frage aber immer: frisch oder liegengeblieben?
    Deshalb steht in der Uebersicht das Alter und der Zeitstempel nur im Tooltip.
    """
    from datetime import datetime, timedelta

    from thermoctl.web import alter_in_worten

    jetzt = datetime(2026, 8, 29, 12, 0, 0)
    assert alter_in_worten(None) == "noch nie"
    assert alter_in_worten(jetzt - timedelta(seconds=1), jetzt) == "vor 1 Sekunde"
    assert alter_in_worten(jetzt - timedelta(seconds=42), jetzt) == "vor 42 Sekunden"
    assert alter_in_worten(jetzt - timedelta(minutes=1), jetzt) == "vor 1 Minute"
    assert alter_in_worten(jetzt - timedelta(minutes=59), jetzt) == "vor 59 Minuten"
    assert alter_in_worten(jetzt - timedelta(hours=3), jetzt) == "vor 3 Stunden"
    assert alter_in_worten(jetzt - timedelta(days=1), jetzt) == "vor 1 Tag"
    assert alter_in_worten(jetzt - timedelta(days=9), jetzt) == "vor 9 Tagen"
    # Ein leicht falsch gestellter Sensor darf nicht 'in -3 Minuten' anzeigen.
    assert alter_in_worten(jetzt + timedelta(minutes=3), jetzt) == "gerade eben"
    # Ohne ausdruecklichen Jetzt-Zeitpunkt greift die Uhr des Projekts.
    assert alter_in_worten(datetime(2000, 1, 1)).endswith("Tagen")


def test_geraeteseite_stellt_auffaellige_geraete_nach_oben(client_als, session: Session) -> None:
    """Die Gegenprobe zur Rundung oben: Was nicht stimmt, muss auch wirklich dastehen.

    Zwei Geraete, eines gesund und alphabetisch zuerst, eines mit leerer Batterie und von
    der Bruecke als offline gefuehrt. Die Seite ist erst dann brauchbar, wenn das zweite
    oben steht und seinen Befund im Klartext traegt.
    """
    beispiele = json.loads(
        (Path(__file__).parent / "daten/anlage-beispiele.json").read_text(encoding="utf-8")
    )
    gesund = geraet_anlegen(session, beispiele["geraete"][0])
    gesund.display_name = "Alpha gesund"
    gesunder_zustand = geraetezustand_anlegen(session, gesund)
    gesunder_zustand.last_payload_at = utcnow()
    gesunder_zustand.availability = "online"

    krank = geraet_anlegen(session, beispiele["geraete"][1])
    krank.display_name = "Zulu krank"
    kranker_zustand = geraetezustand_anlegen(session, krank)
    kranker_zustand.last_payload_at = utcnow()
    kranker_zustand.battery_percent = Decimal("7.00")
    kranker_zustand.availability = "offline"
    session.flush()

    text = client_als([("device.read", None)]).get("/geraete").text

    assert "Batterie bei 7 %" in text
    assert "die Brücke führt es als offline" in text
    assert text.index("Zulu krank") < text.index("Alpha gesund")
    assert "1 davon fällt auf" in text


def test_batterie_und_funk_stehen_als_zahl_und_nicht_als_kaertchen(
    client_als, session: Session
) -> None:
    """Ein Kaertchen "Batteriestand" neben "58 %" sagt nichts, was die Zahl nicht sagt.

    Die Gegenprobe steckt im zweiten Teil: Wer die Kaertchen unterdrueckt, darf daraus
    nicht "keine Faehigkeiten gemeldet" machen. Ein Fernbedienungsknopf, der genau
    Batterie und Funkguete meldet, kann etwas -- es steht nur schon rechts.
    """
    beispiele = json.loads(
        (Path(__file__).parent / "daten/anlage-beispiele.json").read_text(encoding="utf-8")
    )
    nur_batterie = geraet_anlegen(session, beispiele["geraete"][0])
    nur_batterie.display_name = "Nur Batterie"
    stumm = geraet_anlegen(session, beispiele["geraete"][1])
    stumm.display_name = "Meldet nichts"
    for zeitpunkt in (nur_batterie, stumm):
        zustand = geraetezustand_anlegen(session, zeitpunkt)
        zustand.last_payload_at = utcnow()
    batterie = DeviceCapability(code="battery", label="Batteriestand")
    session.add(batterie)
    session.flush()
    session.add(DeviceCapabilityLink(device_id=nur_batterie.id, capability_id=batterie.id))
    session.flush()

    text = client_als([("device.read", None)]).get("/geraete").text

    assert "Batteriestand" not in text
    # Genau einmal -- fuer das Geraet ohne jede Faehigkeit, nicht fuer das mit Batterie.
    assert text.count("keine Fähigkeiten gemeldet") == 1
